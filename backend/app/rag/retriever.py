"""BGE + Milvus 检索层：子切片检索 + 父子回溯，数据源为 MySQL 切片表。

父子切片 v2.0：Milvus 只入子切片（chunk_type=child），检索命中子切片后回溯父切片
（H3 完整功能模块）作为 LLM 上下文。启动时从 MySQL chunks 表加载两张映射：
- _parents：父切片 id -> 父原文（含全部图片引用）；
- _child_to_parent：子切片 id -> 父切片 id。
"""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote

from pymilvus import Collection, connections, utility

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.models import SearchResult, SourceChunk

logger = get_logger(__name__)

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def clean_for_embedding(text: str) -> str:
    """保留图片说明文字，去掉图片实际路径，减少路径噪声。"""
    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片：\1]", text)


class ManualRetriever:
    """操作手册检索器：BGE 编码 + Milvus 子切片 COSINE 检索 + 父切片回溯。"""

    def __init__(self, settings: Settings, db=None):
        self.settings = settings
        self.db = db
        self._model = None
        self._collection: Collection | None = None
        # 父切片映射：parent_id -> {content, doc, path, chunk_index}
        self._parents: dict[str, dict] = {}
        # 子切片映射：child_id -> parent_id
        self._child_to_parent: dict[str, str] = {}

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
        self._load_child_collection_sync()
        logger.info(
            "retriever_started",
            collection=self.settings.milvus_child_collection,
            num_entities=(
                self._collection.num_entities if self._collection is not None else 0
            ),
        )

    def _load_child_collection_sync(self) -> None:
        try:
            if utility.has_collection(self.settings.milvus_child_collection):
                self._collection = Collection(self.settings.milvus_child_collection)
                self._collection.load()
            else:
                self._collection = None
        except Exception as e:
            logger.warning("child_collection_load_failed", error=str(e))
            self._collection = None

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
        """重新加载父子映射：优先 MySQL 切片表，无数据时回退旧 chunks.json。"""
        self._parents = {}
        self._child_to_parent = {}
        if not await self._load_from_db():
            self._load_from_chunks_json()
        logger.info(
            "chunk_mappings_loaded",
            parents=len(self._parents),
            children=len(self._child_to_parent),
        )

    async def _load_from_db(self) -> bool:
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
            return len(self._parents) > 0
        except Exception as e:
            logger.warning("chunk_mappings_db_load_failed", error=str(e))
            return False

    def _load_from_chunks_json(self) -> None:
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
        logger.info("chunks_json_loaded", count=len(self._parents))

    # ---------- 检索 ----------

    async def search(
        self, query: str, doc: str | None = None, top_k: int | None = None
    ) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, doc, top_k)

    def _search_sync(
        self, query: str, doc: str | None, top_k: int | None
    ) -> list[SearchResult]:
        if self._model is None or self._collection is None:
            return []

        k = top_k or self.settings.retrieve_top_k
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

        results = self._collection.search(**search_params)

        hits: list[SearchResult] = []
        for hit in results[0]:
            hits.append(
                SearchResult(
                    chunk_id=str(hit.id),
                    content=hit.entity.get("content"),
                    doc=hit.entity.get("doc"),
                    path=hit.entity.get("path"),
                    score=float(hit.score),
                )
            )
        return hits

    def filter_by_threshold(self, hits: list[SearchResult]) -> list[SearchResult]:
        return [h for h in hits if h.score >= self.settings.score_threshold]

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
        """拼接 LLM 上下文：子→父回溯、按父去重、按 doc+chunk_index 文档序排序。"""
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
            if pid not in best:
                best[pid] = {
                    "doc": parent["doc"],
                    "chunk_index": parent["chunk_index"],
                    "path": parent["path"],
                    "content": parent["content"],
                    "score": h.score,
                }
                order.append(pid)
            elif h.score > best[pid]["score"]:
                best[pid]["score"] = h.score

        order.sort(key=lambda pid: (best[pid]["doc"], best[pid]["chunk_index"]))

        parts = []
        for i, pid in enumerate(order, 1):
            info = best[pid]
            content, _ = self._rewrite_content(info["doc"], info["content"])
            parts.append(
                f"【切片{i}】来源：{info['doc']} > {info['path']}"
                f"（相似度 {info['score']:.3f}）\n{content}"
            )
        return "\n\n".join(parts)

    # ---------- 向量化入库能力（供 knowledge_service 复用） ----------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("embedding 模型未初始化")
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    async def ensure_child_collection(self) -> None:
        await asyncio.to_thread(self._ensure_child_collection_sync)

    def _ensure_child_collection_sync(self) -> None:
        name = self.settings.milvus_child_collection
        if utility.has_collection(name):
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
        ]
        schema = CollectionSchema(
            fields=fields, description="操作手册 RAG 子切片（父子切片 v2.0）",
        )
        collection = Collection(name, schema)
        collection.create_index(
            field_name="vector",
            index_params={"index_type": "FLAT", "metric_type": "COSINE", "params": {}},
        )
        logger.info("child_collection_created", collection=name)

    async def upsert_children(self, rows: list[dict]) -> None:
        """向子切片 collection 幂等 upsert。rows 元素含 id/doc/path/parent_id/content/vector。"""
        await asyncio.to_thread(self._upsert_children_sync, rows)

    def _upsert_children_sync(self, rows: list[dict]) -> None:
        if not rows:
            return
        self._ensure_child_collection_sync()
        if self._collection is None:
            self._collection = Collection(self.settings.milvus_child_collection)
            self._collection.load()
        self._collection.upsert(
            [
                [r["id"] for r in rows],
                [r["doc"] for r in rows],
                [r["path"] for r in rows],
                [r["parent_id"] for r in rows],
                [r["content"] for r in rows],
                [r["vector"] for r in rows],
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
    """从操作手册知识库检索与问题相关的切片（子切片命中，返回子 path + 父 content）。"""
    if _retriever is None:
        raise RuntimeError("retriever 未初始化")
    hits = await _retriever.search(query, doc=doc, top_k=top_k)
    return [_retriever.to_source_chunk(h).model_dump() for h in hits]
