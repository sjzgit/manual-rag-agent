"""BGE + Milvus 检索层：单例模型/连接，id→原文映射，图片 URL 改写。"""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote

from pymilvus import Collection, connections

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.models import SearchResult, SourceChunk

logger = get_logger(__name__)

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class ManualRetriever:
    """操作手册检索器：BGE 编码 + Milvus COSINE 稠密检索。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._collection: Collection | None = None
        # chunk_id -> chunks.json 原始记录（含带图 markdown）
        self._raw_chunks: dict[str, dict] = {}

    # ---------- 生命周期 ----------

    async def startup(self) -> None:
        await asyncio.to_thread(self._startup_sync)

    def _startup_sync(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.settings.embed_model_name)

        connections.connect(
            alias="default",
            host=self.settings.milvus_host,
            port=self.settings.milvus_port,
        )
        self._collection = Collection(self.settings.milvus_collection)
        self._collection.load()

        chunks_file = Path(self.settings.chunks_path)
        if chunks_file.exists():
            data = json.loads(chunks_file.read_text(encoding="utf-8"))
            self._raw_chunks = {c["id"]: c for c in data}
            logger.info("chunks_loaded", count=len(self._raw_chunks))
        else:
            logger.warning("chunks_file_missing", path=str(chunks_file))

        logger.info(
            "retriever_started",
            collection=self.settings.milvus_collection,
            num_entities=self._collection.num_entities,
        )

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._shutdown_sync)

    def _shutdown_sync(self) -> None:
        try:
            if self._collection is not None:
                self._collection.release()
            connections.disconnect("default")
        except Exception as e:  # noqa: BLE001
            logger.warning("retriever_shutdown_error", error=str(e))

    # ---------- 检索 ----------

    async def search(
        self, query: str, doc: str | None = None, top_k: int | None = None
    ) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, doc, top_k)

    def _search_sync(
        self, query: str, doc: str | None, top_k: int | None
    ) -> list[SearchResult]:
        if self._model is None or self._collection is None:
            raise RuntimeError("retriever 未初始化")

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

    def to_source_chunk(self, hit: SearchResult) -> SourceChunk:
        """命中结果 → 前端来源卡片：取 chunks.json 原文并改写图片 URL。"""
        raw = self._raw_chunks.get(hit.chunk_id)
        if raw is None:
            return SourceChunk(
                chunk_id=hit.chunk_id,
                doc=hit.doc,
                path=hit.path,
                score=hit.score,
                content=hit.content,
                images=[],
            )

        content: str = raw["content"]
        images: list[str] = []

        def _replace(m: re.Match) -> str:
            alt, src = m.group(1), m.group(2)
            filename = src.split("/")[-1]
            url = f"/api/images/{quote(hit.doc)}/{quote(filename)}"
            images.append(url)
            return f"![{alt}]({url})"

        content = _IMG_PATTERN.sub(_replace, content)
        return SourceChunk(
            chunk_id=hit.chunk_id,
            doc=hit.doc,
            path=hit.path,
            score=hit.score,
            content=content,
            images=images,
        )

    def build_context(self, hits: list[SearchResult]) -> str:
        """拼接 LLM 上下文（含来源标注）。

        复用 to_source_chunk 的图片 URL 改写：切片中的 ![](./media/xxx.png)
        原位替换为 /api/images/... 可用链接，让大模型在回答中能直接引用原文图片，
        实现图文混排回答（避免回答里丢图/重排）。
        """
        parts = []
        for i, h in enumerate(hits, 1):
            chunk = self.to_source_chunk(h)
            parts.append(
                f"【切片{i}】来源：{h.doc} > {h.path}（相似度 {h.score:.3f}）\n{chunk.content}"
            )
        return "\n\n".join(parts)


# ---------- AgentScope 工具函数 ----------

_retriever: ManualRetriever | None = None


def set_retriever(r: ManualRetriever) -> None:
    global _retriever
    _retriever = r


async def search_manual(
    query: str, doc: str | None = None, top_k: int = 2
) -> list[dict]:
    """从操作手册知识库检索与问题相关的切片。

    Args:
        query: 用户问题或检索语句
        doc: 可选，按手册名称过滤检索范围
        top_k: 返回的切片数量，默认 2

    Returns:
        切片列表，每项含 content、doc、path、score
    """
    if _retriever is None:
        raise RuntimeError("retriever 未初始化")
    hits = await _retriever.search(query, doc=doc, top_k=top_k)
    # 返回改写图片 URL 后的原文，保证 Agent 也能把 /api/images/... 图片链接带进回答
    return [_retriever.to_source_chunk(h).model_dump() for h in hits]
