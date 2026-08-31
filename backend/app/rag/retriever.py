"""BGE + Milvus 检索层：混合检索（稠密 + BM25 稀疏）+ 父子回溯，数据源为 MySQL 切片表。

父子切片 v2.0：Milvus 只入子切片（chunk_type=child），检索命中子切片后回溯父切片
（H3 完整功能模块）作为 LLM 上下文。启动时从 MySQL chunks 表加载两张映射：
- _parents：父切片 id -> 父原文（含全部图片引用）；
- _child_to_parent：子切片 id -> 父切片 id。

混合检索（v2.1）：子切片 collection 含稠密（vector, FLAT/COSINE）与稀疏
（sparse_vector, SPARSE_INVERTED_INDEX/IP）两个向量字段；BM25 统计随 reload()
全量重算；两路结果应用层加权 RRF 融合，可选 rerank 重排（失败降级融合序）。
稀疏路任一环节不可用（开关/schema 无字段/统计未就绪/检索异常）自动纯稠密降级。
"""
import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from pymilvus import Collection, connections, utility

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.bm25 import BM25Encoder, weighted_rrf_fuse
from app.rag.models import HybridSearchOutcome, SearchResult, SourceChunk

logger = get_logger(__name__)

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# 稀疏向量能力要求的最低服务端版本（SPARSE_FLOAT_VECTOR / 多向量索引）
_MIN_MILVUS_VERSION = (2, 4)


def clean_for_embedding(text: str) -> str:
    """保留图片说明文字，去掉图片实际路径，减少路径噪声。"""
    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片：\1]", text)


def _parse_version(version: str) -> tuple[int, ...]:
    """'v2.5.4' → (2, 5, 4)；解析失败返回 ()（视为版本未知，不阻断）。"""
    digits = re.findall(r"\d+", version)
    return tuple(int(d) for d in digits)


class ManualRetriever:
    """操作手册检索器：BGE 稠密 + BM25 稀疏混合检索 + Milvus + 父切片回溯。"""

    def __init__(self, settings: Settings, db=None):
        self.settings = settings
        self.db = db
        self._model = None
        self._collection: Collection | None = None
        # 父切片映射：parent_id -> {content, doc, path, chunk_index}
        self._parents: dict[str, dict] = {}
        # 子切片映射：child_id -> parent_id
        self._child_to_parent: dict[str, str] = {}
        # BM25 稀疏编码器（统计随 reload 全量重建，不可变对象整体替换）
        self._bm25: BM25Encoder | None = None
        # collection 是否含 sparse_vector 字段且服务端版本满足要求
        self._has_sparse = False
        self._server_version = ""

    # ---------- 生命周期 ----------

    async def startup(self) -> None:
        await asyncio.to_thread(self._startup_sync)
        await self.reload()

    def _startup_sync(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.settings.embed_model_name)

        connections.connect(
            alias="default",
            host=self.settings.milvus_host,
            port=self.settings.milvus_port,
        )
        try:
            self._server_version = utility.get_server_version()
        except Exception as e:
            logger.warning("milvus_version_probe_failed", error=str(e))
            self._server_version = ""
        self._load_child_collection_sync()
        logger.info(
            "retriever_started",
            collection=self.settings.milvus_child_collection,
            server_version=self._server_version,
            has_sparse=self._has_sparse,
            num_entities=(
                self._collection.num_entities if self._collection is not None else 0
            ),
        )

    def _load_child_collection_sync(self) -> None:
        try:
            if utility.has_collection(self.settings.milvus_child_collection):
                self._collection = Collection(self.settings.milvus_child_collection)
                self._collection.load()
                self._refresh_sparse_flag()
            else:
                self._collection = None
                self._has_sparse = False
        except Exception as e:
            logger.warning("child_collection_load_failed", error=str(e))
            self._collection = None
            self._has_sparse = False

    def _refresh_sparse_flag(self) -> None:
        """检测 collection schema 是否含 sparse_vector 字段，且服务端版本满足要求。

        版本探测失败（空/无法解析出数字）时信任 schema 字段——能建出 sparse_vector
        字段本身即证明服务端支持稀疏能力，版本号仅作辅助告警。
        """
        if self._collection is None:
            self._has_sparse = False
            return
        has_field = any(
            f.name == "sparse_vector" for f in self._collection.schema.fields
        )
        parsed = _parse_version(self._server_version)
        version_ok = not parsed or parsed >= _MIN_MILVUS_VERSION
        self._has_sparse = has_field and version_ok
        if self.settings.enable_keyword_search and has_field and not version_ok:
            logger.warning(
                "sparse_disabled_server_version",
                server_version=self._server_version,
                require=">=2.4",
            )
        elif self.settings.enable_keyword_search and not has_field:
            logger.warning("sparse_field_missing_dense_only")

    @property
    def sparse_ready(self) -> bool:
        """稀疏路是否可用（开关开 + schema 有字段 + 版本满足）。"""
        return bool(
            self.settings.enable_keyword_search and self._has_sparse and self._bm25
        )

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._shutdown_sync)

    def _shutdown_sync(self) -> None:
        try:
            if self._collection is not None:
                self._collection.release()
            connections.disconnect("default")
        except Exception as e:
            logger.warning("retriever_shutdown_error", error=str(e))

    # ---------- 映射加载 ----------

    async def reload(self) -> None:
        """重新加载父子映射与 BM25 统计：优先 MySQL 切片表，无数据时回退旧 chunks.json。"""
        self._parents = {}
        self._child_to_parent = {}
        corpus: list[str] = []
        if not await self._load_from_db(corpus):
            self._load_from_chunks_json(corpus)
        logger.info(
            "chunk_mappings_loaded",
            parents=len(self._parents),
            children=len(self._child_to_parent),
        )
        await asyncio.to_thread(self._rebuild_bm25, corpus)

    def _rebuild_bm25(self, corpus: list[str]) -> None:
        """从子切片语料全量重建 BM25 统计（不可变编码器整体替换）。"""
        if not corpus:
            self._bm25 = None
            logger.info("bm25_stats_empty")
            return
        try:
            self._bm25 = BM25Encoder.from_corpus(
                corpus, self.settings.bm25_k1, self.settings.bm25_b
            )
            logger.info("bm25_stats_ready", num_docs=self._bm25.num_docs)
        except Exception as e:
            self._bm25 = None
            logger.warning("bm25_build_failed", error=str(e))

    async def _load_from_db(self, corpus: list[str]) -> bool:
        if self.db is None or not self.db.available:
            return False
        from sqlalchemy import select

        from app.db.models import Chunk

        try:
            async with self.db.session() as s:
                rows = (await s.execute(select(Chunk))).scalars().all()
            for r in rows:
                if r.chunk_type == "parent":
                    self._parents[r.id] = {
                        "content": r.content,
                        "doc": r.doc,
                        "path": r.path,
                        "chunk_index": r.chunk_index,
                    }
                elif r.parent_id:
                    self._child_to_parent[r.id] = r.parent_id
                    # BM25 语料必须与 Milvus content 列一致（clean 文本，无图片路径噪声）
                    corpus.append(clean_for_embedding(r.content))
            return len(self._parents) > 0
        except Exception as e:
            logger.warning("chunk_mappings_db_load_failed", error=str(e))
            return False

    def _load_from_chunks_json(self, corpus: list[str]) -> None:
        """旧 chunks.json 兜底：单层切片（父=子=自身）。"""
        chunks_file = Path(self.settings.chunks_path)
        if not chunks_file.exists():
            logger.warning("chunks_file_missing", path=str(chunks_file))
            return
        data = json.loads(chunks_file.read_text(encoding="utf-8"))
        for c in data:
            cid = c["id"]
            self._parents[cid] = {
                "content": c["content"],
                "doc": c["doc"],
                "path": c["path"],
                "chunk_index": c.get("chunk_index", 0),
            }
            self._child_to_parent[cid] = cid
            corpus.append(clean_for_embedding(c["content"]))
        logger.info("chunks_json_loaded", count=len(self._parents))

    # ---------- 检索 ----------

    async def search(
        self, query: str, doc: str | None = None, top_k: int | None = None
    ) -> list[SearchResult]:
        """混合检索：稠密 + 稀疏（可用时）两路召回 + 加权 RRF 融合。

        top_k 显式传入时作为每路召回数（如 agentic 工具 top_k=2），
        否则用 settings.recall_top_k（融合候选池）。
        """
        return await asyncio.to_thread(self._search_sync, query, doc, top_k)

    def _search_sync(
        self, query: str, doc: str | None, top_k: int | None
    ) -> list[SearchResult]:
        if self._model is None or self._collection is None:
            return []

        k = top_k or self.settings.recall_top_k
        dense = self._dense_search(query, doc, k)
        sparse: list[SearchResult] = []
        if self.sparse_ready:
            sparse = self._sparse_search(query, doc, k)
        if not sparse:
            # 纯稠密链路（含稀疏路降级）：直接返回，分数语义与旧版一致
            return dense
        return weighted_rrf_fuse(
            dense,
            sparse,
            self.settings.fusion_dense_weight,
            self.settings.fusion_sparse_weight,
            self.settings.rrf_k,
        )

    def _dense_search(
        self, query: str, doc: str | None, k: int
    ) -> list[SearchResult]:
        """稠密路：BGE 编码 + Milvus COSINE 检索（原链路）。"""
        if self._model is None or self._collection is None:
            return []
        q_vec = self._model.encode([query], normalize_embeddings=True)
        search_params: dict = {
            "data": q_vec,
            "anns_field": "vector",
            "param": {"metric_type": "COSINE", "params": {}},
            "limit": k,
            "output_fields": ["doc", "path", "content"],
        }
        if doc:
            search_params["expr"] = f'doc == "{doc}"'
        results = cast(Any, self._collection.search(**search_params))
        return [
            SearchResult(
                chunk_id=str(hit.id),
                content=hit.entity.get("content"),
                doc=hit.entity.get("doc"),
                path=hit.entity.get("path"),
                score=float(hit.score),
                dense_score=float(hit.score),
            )
            for hit in list(results[0])
        ]

    def _sparse_search(
        self, query: str, doc: str | None, k: int
    ) -> list[SearchResult]:
        """稀疏路：BM25 query 编码 + Milvus SPARSE/IP 检索；异常降级为空（不传播）。"""
        if self._collection is None:
            return []
        try:
            assert self._bm25 is not None  # 由 sparse_ready 保证
            q_vec = self._bm25.encode_query(query)
            if not q_vec:
                return []
            search_params: dict = {
                "data": [q_vec],
                "anns_field": "sparse_vector",
                "param": {"metric_type": "IP", "params": {}},
                "limit": k,
                "output_fields": ["doc", "path", "content"],
            }
            if doc:
                search_params["expr"] = f'doc == "{doc}"'
            results = cast(Any, self._collection.search(**search_params))
            return [
                SearchResult(
                    chunk_id=str(hit.id),
                    content=hit.entity.get("content"),
                    doc=hit.entity.get("doc"),
                    path=hit.entity.get("path"),
                    score=float(hit.score),
                    sparse_score=float(hit.score),
                )
                for hit in list(results[0])
            ]
        except Exception as e:
            logger.warning(
                "sparse_search_failed_dense_only",
                error=str(e),
                error_type=type(e).__name__,
                query=query,
            )
            return []

    def filter_by_threshold(self, hits: list[SearchResult]) -> list[SearchResult]:
        """阈值过滤：dense_score 达标，或稀疏路强命中（排名前 keyword_pass_rank）放行。

        hits 无混合扩展字段（dense_score / sparse_rank 均为 None，如纯稠密旧链路或
        外部构造）时回退旧语义：score >= score_threshold。
        """
        passed: list[SearchResult] = []
        for h in hits:
            if h.dense_score is not None:
                if h.dense_score >= self.settings.score_threshold:
                    passed.append(h)
            elif h.sparse_rank is not None:
                if h.sparse_rank <= self.settings.keyword_pass_rank:
                    passed.append(h)
            else:
                # 无混合扩展字段：旧语义兜底
                if h.score >= self.settings.score_threshold:
                    passed.append(h)
        return passed

    # ---------- 混合检索 + 重排编排 ----------

    async def search_and_rerank(
        self,
        query: str,
        doc: str | None = None,
        reranker=None,
        top_n: int | None = None,
    ) -> HybridSearchOutcome:
        """混合检索 + 重排编排：检索 → 融合 → 阈值过滤 → 按父去重 → rerank → 前 N。

        返回 HybridSearchOutcome（dense/sparse/fused/final 四阶段列表 + mode），
        mode ∈ "hybrid-rerank" | "hybrid" | "dense"（供 step 事件展示与日志落库）。
        rerank 失败降级融合序，绝不抛出。
        """
        fused = await self.search(query, doc=doc)
        mode = "hybrid" if self.sparse_ready else "dense"
        # 阶段快照：融合结果本身（含两路分数/排名），供日志分阶段展示
        fused_snapshot = [h.model_copy() for h in fused]
        # 两路原始召回从融合结果的分数/排名字段还原（search 内部已完成两路检索）
        dense_hits = self._dense_snapshot(fused, doc)
        sparse_hits = self._sparse_snapshot(fused)
        passed = self.filter_by_threshold(fused)
        reps = self.parent_representatives(passed)
        n = top_n or self.settings.rerank_top_n

        if reranker is None or not reranker.configured:
            final = reps[:n]
            return HybridSearchOutcome(
                mode=mode,
                dense_hits=dense_hits,
                sparse_hits=sparse_hits,
                fused_hits=[h.model_copy() for h in reps],
                final_hits=final,
            )

        try:
            pairs = await reranker.rerank(
                query, [h.content for h in reps], top_n=n
            )
        except Exception as e:
            logger.warning("rerank_failed_fallback_fused", error=str(e))
            return HybridSearchOutcome(
                mode=mode,
                dense_hits=dense_hits,
                sparse_hits=sparse_hits,
                fused_hits=[h.model_copy() for h in reps],
                final_hits=reps[:n],
            )

        reranked: list[SearchResult] = []
        for idx, rel_score in pairs:
            if 0 <= idx < len(reps):
                r = reps[idx].model_copy()
                r.rerank_score = rel_score
                r.score = rel_score
                reranked.append(r)
        if not reranked:
            logger.warning("rerank_empty_fallback_fused")
            return HybridSearchOutcome(
                mode=mode,
                dense_hits=dense_hits,
                sparse_hits=sparse_hits,
                fused_hits=[h.model_copy() for h in reps],
                final_hits=reps[:n],
            )
        return HybridSearchOutcome(
            mode="hybrid-rerank",
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            fused_hits=fused_snapshot if reps else [],
            final_hits=reranked[:n],
        )

    def _dense_snapshot(
        self, fused: list[SearchResult], doc: str | None
    ) -> list[SearchResult]:
        """从融合结果还原稠密路原始列表（按 dense_score 降序，仅含稠密命中的）。

        稯疏路未启用（纯稠密链路）时融合结果无 dense_rank，直接按序快照；
        启用时按 dense_rank 升序还原两路各自的原始排名序。
        """
        dense_only = all(h.dense_rank is None for h in fused)
        if dense_only:
            # 纯稠密：search 返回即稠密原始序
            return [h.model_copy() for h in fused if h.dense_score is not None] or [
                h.model_copy() for h in fused
            ]
        hits = [h for h in fused if h.dense_rank is not None]
        hits.sort(key=lambda h: h.dense_rank or 0)
        return [h.model_copy() for h in hits]

    def _sparse_snapshot(self, fused: list[SearchResult]) -> list[SearchResult]:
        """从融合结果还原稀疏路原始列表（按 sparse_rank 升序，仅含稀疏命中的）。"""
        hits = [h for h in fused if h.sparse_rank is not None]
        hits.sort(key=lambda h: h.sparse_rank or 0)
        return [h.model_copy() for h in hits]

    def parent_representatives(self, hits: list[SearchResult]) -> list[SearchResult]:
        """按父切片去重：每父保留 fused_score（无则 score）最高的子切片。

        无父映射的单层切片视为自身一组。
        """
        best: dict[str, SearchResult] = {}
        order: list[str] = []
        for h in hits:
            pid = self._child_to_parent.get(h.chunk_id, h.chunk_id)
            key_score = h.fused_score if h.fused_score is not None else h.score
            if pid not in best:
                best[pid] = h
                order.append(pid)
            else:
                cur = best[pid]
                cur_score = cur.fused_score if cur.fused_score is not None else cur.score
                if key_score > cur_score:
                    best[pid] = h
        return [best[pid] for pid in order]

    # ---------- 原文与图片 ----------

    def _rewrite_content(self, doc: str, content: str) -> tuple[str, list[str]]:
        """将 ./media/x.png 改写为 /api/images/... 可用链接，收集图片 URL 列表。"""
        images: list[str] = []

        def _replace(m: re.Match) -> str:
            alt, src = m.group(1), m.group(2)
            filename = src.split("/")[-1]
            url = f"/api/images/{quote(doc)}/{quote(filename)}"
            images.append(url)
            return f"![{alt}]({url})"

        return _IMG_PATTERN.sub(_replace, content), images

    def _parent_of(self, chunk_id: str) -> dict | None:
        """命中子切片回溯父切片；无父映射时视为单层切片返回 None。"""
        parent_id = self._child_to_parent.get(chunk_id)
        if parent_id is None:
            return None
        return self._parents.get(parent_id)

    def to_source_chunk(self, hit: SearchResult) -> SourceChunk:
        """命中结果 → 前端来源卡片：子切片 path 定位 + 父切片 content 完整展示。"""
        parent = self._parent_of(hit.chunk_id)
        if parent is None:
            return SourceChunk(
                chunk_id=hit.chunk_id,
                doc=hit.doc,
                path=hit.path,
                score=hit.score,
                content=hit.content,
                images=[],
            )
        content, images = self._rewrite_content(parent["doc"], parent["content"])
        return SourceChunk(
            chunk_id=hit.chunk_id,
            doc=hit.doc,
            path=hit.path,
            score=hit.score,
            content=content,
            images=images,
        )

    def build_context(self, hits: list[SearchResult]) -> str:
        """拼接 LLM 上下文：子→父回溯、按父去重、按最终相关度降序（同分按文档序）。

        v2.1：排序从文档序改为相关度序（rerank_score 优先，否则 fused/score），
        这是 rerank 生效的前提；分数标注按来源切换文案。
        """
        best: dict[str, dict] = {}
        order: list[str] = []
        for h in hits:
            pid = self._child_to_parent.get(h.chunk_id, h.chunk_id)
            parent = self._parents.get(pid)
            if parent is None:
                parent = {
                    "content": h.content,
                    "doc": h.doc,
                    "path": h.path,
                    "chunk_index": 0,
                }
            hit_score = h.rerank_score if h.rerank_score is not None else h.score
            if pid not in best:
                best[pid] = {
                    "doc": parent["doc"],
                    "chunk_index": parent["chunk_index"],
                    "path": parent["path"],
                    "content": parent["content"],
                    "hit": h,
                    "score": hit_score,
                }
                order.append(pid)
            elif hit_score > best[pid]["score"]:
                best[pid]["hit"] = h
                best[pid]["score"] = hit_score

        order.sort(
            key=lambda pid: (
                -best[pid]["score"],
                best[pid]["doc"],
                best[pid]["chunk_index"],
            )
        )

        def _score_label(hit: SearchResult, score: float) -> str:
            if hit.rerank_score is not None:
                return f"（相关度 {score:.3f}）"
            if hit.sparse_score is not None and hit.dense_score is None:
                rank = hit.sparse_rank if hit.sparse_rank is not None else 0
                return f"（关键词命中，排名 {rank}）"
            return f"（相似度 {score:.3f}）"

        parts = []
        for i, pid in enumerate(order, 1):
            info = best[pid]
            content, _ = self._rewrite_content(info["doc"], info["content"])
            parts.append(
                f"【切片{i}】来源：{info['doc']} > {info['path']}"
                f"{_score_label(info['hit'], info['score'])}\n{content}"
            )
        return "\n\n".join(parts)

    # ---------- 向量化入库能力（供 knowledge_service 复用） ----------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("embedding 模型未初始化")
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def encode_sparse_sync(self, texts: list[str]) -> list[dict[int, float]]:
        """子切片 BM25 稀疏编码（同步，调用方包 to_thread）；统计未就绪返回空 dict。"""
        if self._bm25 is None:
            return [{} for _ in texts]
        return [self._bm25.encode_document(t) for t in texts]

    async def ensure_child_collection(self) -> None:
        await asyncio.to_thread(self._ensure_child_collection_sync)

    def _ensure_child_collection_sync(self) -> None:
        name = self.settings.milvus_child_collection
        if utility.has_collection(name):
            if self._collection is None or self._collection.name != name:
                self._collection = Collection(name)
                self._collection.load()
                self._refresh_sparse_flag()
            return
        from pymilvus import CollectionSchema, DataType, FieldSchema

        fields = [
            FieldSchema(
                name="id", dtype=DataType.VARCHAR, is_primary=True,
                auto_id=False, max_length=512,
            ),
            FieldSchema(name="doc", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="path", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="parent_id", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=768),
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
        ]
        schema = CollectionSchema(
            fields=fields, description="操作手册 RAG 子切片（混合检索 v2.1：稠密+稀疏）",
        )
        collection = Collection(name, schema)
        collection.create_index(
            field_name="vector",
            index_params={"index_type": "FLAT", "metric_type": "COSINE", "params": {}},
        )
        collection.create_index(
            field_name="sparse_vector",
            index_params={
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "IP",
                "params": {"drop_ratio_build": 0.0},
            },
        )
        logger.info("child_collection_created", collection=name)
        self._collection = collection
        self._collection.load()
        self._refresh_sparse_flag()

    async def upsert_children(self, rows: list[dict]) -> None:
        """向子切片 collection 幂等 upsert。

        rows 元素含 id/doc/path/parent_id/content/vector/sparse_vector。
        """
        await asyncio.to_thread(self._upsert_children_sync, rows)

    def _upsert_children_sync(self, rows: list[dict]) -> None:
        if not rows:
            return
        self._ensure_child_collection_sync()
        if self._collection is None:
            self._collection = Collection(self.settings.milvus_child_collection)
            self._collection.load()
            self._refresh_sparse_flag()
        self._collection.upsert(
            [
                [r["id"] for r in rows],
                [r["doc"] for r in rows],
                [r["path"] for r in rows],
                [r["parent_id"] for r in rows],
                [r["content"] for r in rows],
                [r["vector"] for r in rows],
                [r.get("sparse_vector", {}) for r in rows],
            ]
        )
        self._collection.flush()

    async def delete_children_by_doc(self, doc: str) -> None:
        """按手册名删除子切片 collection 中的向量（同名覆盖时清理旧数据）。"""
        await asyncio.to_thread(self._delete_children_by_doc_sync, doc)

    def _delete_children_by_doc_sync(self, doc: str) -> None:
        if self._collection is None:
            self._load_child_collection_sync()
        if self._collection is None:
            return
        self._collection.delete(expr=f'doc == "{doc}"')
        self._collection.flush()


# ---------- AgentScope 工具函数 ----------

_retriever: ManualRetriever | None = None


def set_retriever(r: ManualRetriever) -> None:
    global _retriever
    _retriever = r


async def search_manual(
    query: str, doc: str | None = None, top_k: int = 8
) -> list[dict]:
    """从操作手册知识库检索与问题相关的切片（子切片命中，返回子 path + 父 content）。

    agentic 工具路径不做 rerank（Agent 每轮多次 tool call，逐次重排延迟不划算）。
    """
    if _retriever is None:
        raise RuntimeError("retriever 未初始化")
    hits = await _retriever.search(query, doc=doc, top_k=top_k)
    return [_retriever.to_source_chunk(h).model_dump() for h in hits]
