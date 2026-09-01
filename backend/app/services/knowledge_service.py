"""知识库编排：docx 上传 → 转换 → 父子切片 → 向量化入库 → 状态回写。

上传接口立即返回 doc_id，后台任务异步跑转换/切片/向量化流水线，前端轮询状态。
转换（mammoth）与向量化（BGE）为同步调用，均用 asyncio.to_thread 包装。
"""
import asyncio
import shutil
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Chunk, SourceDocument
from app.knowledge.chunker import chunk
from app.knowledge.converter import convert_docx
from app.knowledge.models import ChunkSpec
from app.rag.retriever import ManualRetriever, clean_for_embedding

logger = get_logger(__name__)


def _rel_path(upload_root: Path, p: Path) -> str:
    return p.relative_to(upload_root).as_posix()


class KnowledgeService:
    def __init__(self, settings: Settings, db, retriever: ManualRetriever):
        self.settings = settings
        self.db = db
        self.retriever = retriever
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def upload_root(self) -> Path:
        return Path(self.settings.upload_root)

    # ---------- 上传 ----------

    async def create_document(self, doc_name: str, content: bytes) -> dict:
        """保存 docx 并启动后台处理任务；同名文档覆盖旧数据。"""
        if not doc_name or ".." in doc_name or "/" in doc_name or "\\" in doc_name:
            raise ValueError("非法文档名")
        # 同名覆盖：清理旧记录/切片/向量/文件
        await self._delete_by_name(doc_name)

        doc_id = uuid.uuid4().hex
        dest_dir = self.upload_root / doc_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        docx_path = dest_dir / "source.docx"
        docx_path.write_bytes(content)

        doc = SourceDocument(
            id=doc_id,
            doc_name=doc_name,
            original_file_path=_rel_path(self.upload_root, docx_path),
            file_size=len(content),
            status="pending",
        )
        await self._save_document(doc)

        task = asyncio.create_task(self._process(doc_id))
        self._tasks[doc_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(doc_id, None))
        return {"id": doc_id, "doc_name": doc_name, "status": "pending"}

    # ---------- 后台处理 ----------

    async def _process(self, doc_id: str) -> None:
        await self._update_document(doc_id, status="processing")
        try:
            doc = await self._get_document(doc_id)
            if doc is None:
                return
            dest_dir = self.upload_root / doc.doc_name
            docx_path = dest_dir / "source.docx"

            # 1. 转换（同步，图片提取到 media/，预览 HTML 落盘）
            md_text, _ = await asyncio.to_thread(convert_docx, docx_path, dest_dir)
            md_path = dest_dir / f"{doc.doc_name}.md"
            md_path.write_text(md_text, encoding="utf-8")

            # 2. 父子切片（纯代码）
            specs = chunk(md_text, doc.doc_name, doc_id)

            # 3. 持久化切片 + 向量化子切片
            await self._persist_chunks(doc_id, specs)

            # 4. 回写成功状态
            await self._update_document(
                doc_id,
                status="success",
                md_file_path=_rel_path(self.upload_root, md_path),
                preview_file_path=_rel_path(self.upload_root, dest_dir / "preview.html"),
                chunk_count=len(specs),
            )
            logger.info("document_processed", doc_id=doc_id, chunks=len(specs))
        except Exception as e:
            logger.error("document_process_failed", doc_id=doc_id, error=str(e))
            await self._update_document(doc_id, status="failure", error_message=str(e))

    async def _persist_chunks(self, doc_id: str, specs: list[ChunkSpec]) -> None:
        await self._save_chunks(specs)
        children = [s for s in specs if s.chunk_type == "child"]
        if not children:
            return
        try:
            await self.retriever.ensure_child_collection()
            # 先 reload 再编码：父子映射与 BM25 统计已含新切片，消除写入期 idf 过期
            await self.retriever.reload()
            texts = [clean_for_embedding(s.content) for s in children]
            vectors = await self.retriever.embed(texts)
            sparse = await asyncio.to_thread(self.retriever.encode_sparse_sync, texts)
            rows = [
                {
                    "id": s.id,
                    "doc": s.doc,
                    "path": s.path,
                    "parent_id": s.parent_id or "",
                    "content": clean_for_embedding(s.content),
                    "vector": vectors[i],
                    "sparse_vector": sparse[i],
                }
                for i, s in enumerate(children)
            ]
            await self.retriever.upsert_children(rows)
            await self._update_vector_states(
                [(s.id, "success", s.id) for s in children]
            )
        except Exception as e:
            logger.error("vectorize_failed", doc_id=doc_id, error=str(e))
            await self._update_vector_states(
                [(s.id, "failure", None) for s in children]
            )
            raise
        finally:
            await self.retriever.reload()

    # ---------- 查询 ----------

    async def list_documents(self, offset: int = 0, limit: int = 50) -> dict:
        if not self.db.available:
            return {"documents": [], "total": 0}
        async with self.db.session() as s:
            total = (
                await s.execute(select(func.count()).select_from(SourceDocument))
            ).scalar_one()
            rows = (
                await s.execute(
                    select(SourceDocument)
                    .order_by(SourceDocument.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            return {
                "documents": [
                    {
                        "id": r.id,
                        "doc_name": r.doc_name,
                        "file_size": r.file_size,
                        "status": r.status,
                        "error_message": r.error_message,
                        "chunk_count": r.chunk_count,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ],
                "total": total,
            }

    async def get_document(self, doc_id: str) -> dict | None:
        doc = await self._get_document(doc_id)
        if doc is None:
            return None
        return {
            "id": doc.id,
            "doc_name": doc.doc_name,
            "original_file_path": doc.original_file_path,
            "md_file_path": doc.md_file_path,
            "preview_file_path": doc.preview_file_path,
            "file_size": doc.file_size,
            "status": doc.status,
            "error_message": doc.error_message,
            "chunk_count": doc.chunk_count,
        }

    async def get_document_id_by_name(self, doc_name: str) -> str | None:
        """按文档名查源文档 id（doc_name 唯一）。检索命中 hit.doc 为文档名，用于映射到 doc_id。"""
        if not self.db.available:
            return None
        async with self.db.session() as s:
            return (
                await s.execute(
                    select(SourceDocument.id).where(SourceDocument.doc_name == doc_name)
                )
            ).scalar_one_or_none()

    async def get_document_path(self, doc_id: str, kind: str) -> Path | None:
        """按 kind（md/preview/original）返回绝对文件路径。"""
        doc = await self._get_document(doc_id)
        if doc is None:
            return None
        rel = {
            "md": doc.md_file_path,
            "preview": doc.preview_file_path,
            "original": doc.original_file_path,
        }.get(kind)
        if not rel:
            return None
        return self.upload_root / rel

    async def list_chunks(self, doc_id: str) -> list[dict]:
        if not self.db.available:
            return []
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    select(Chunk)
                    .where(Chunk.doc_id == doc_id)
                    .order_by(Chunk.chunk_index, Chunk.child_index)
                )
            ).scalars()
            return [
                {
                    "id": r.id,
                    "chunk_type": r.chunk_type,
                    "parent_id": r.parent_id,
                    "child_index": r.child_index,
                    "path": r.path,
                    "level": r.level,
                    "chunk_index": r.chunk_index,
                    "content": r.content,
                    "char_count": r.char_count,
                    "has_images": r.has_images,
                    "image_count": r.image_count,
                    "vector_state": r.vector_state,
                    "vector_id": r.vector_id,
                }
                for r in rows
            ]

    # ---------- 重新处理 / 删除 ----------

    async def reprocess(self, doc_id: str) -> bool:
        doc = await self._get_document(doc_id)
        if doc is None:
            return False
        # 清理旧切片与向量（保留文档记录与源文件，供重新转换/切片/向量化）
        if self.db.available:
            async with self.db.session() as s:
                await s.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
                await s.commit()
        try:
            await self.retriever.delete_children_by_doc(doc.doc_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("milvus_delete_failed", doc=doc.doc_name, error=str(e))
        await self._update_document(doc_id, status="pending", error_message=None)
        task = asyncio.create_task(self._process(doc_id))
        self._tasks[doc_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(doc_id, None))
        return True

    async def delete_document(self, doc_id: str) -> bool:
        doc = await self._get_document(doc_id)
        if doc is None:
            return False
        await self._delete_by_name(doc.doc_name)
        await self.retriever.reload()
        return True

    # ---------- 内部 DB 操作 ----------

    async def _get_document(self, doc_id: str) -> SourceDocument | None:
        if not self.db.available:
            return None
        async with self.db.session() as s:
            return await s.get(SourceDocument, doc_id)

    async def _save_document(self, doc: SourceDocument) -> None:
        if not self.db.available:
            return
        async with self.db.session() as s:
            s.add(doc)
            await s.commit()

    async def _update_document(self, doc_id: str, **values) -> None:
        if not self.db.available:
            return
        async with self.db.session() as s:
            doc = await s.get(SourceDocument, doc_id)
            if doc is None:
                return
            for k, v in values.items():
                setattr(doc, k, v)
            await s.commit()

    async def _save_chunks(self, specs: list[ChunkSpec]) -> None:
        if not self.db.available:
            return
        async with self.db.session() as s:
            for spec in specs:
                s.add(
                    Chunk(
                        id=spec.id,
                        doc_id=spec.doc_id,
                        doc=spec.doc,
                        chunk_type=spec.chunk_type,
                        parent_id=spec.parent_id,
                        child_index=spec.child_index,
                        path=spec.path,
                        level=spec.level,
                        chunk_index=spec.chunk_index,
                        content=spec.content,
                        char_count=spec.char_count,
                        has_images=spec.has_images,
                        image_count=spec.image_count,
                        vector_state=spec.vector_state,
                        vector_id=spec.vector_id,
                    )
                )
            await s.commit()

    async def _update_vector_states(self, states: list[tuple[str, str, str | None]]) -> None:
        """回写子切片向量化状态与 vector_id。"""
        if not self.db.available:
            return
        async with self.db.session() as s:
            for chunk_id, state, vector_id in states:
                row = await s.get(Chunk, chunk_id)
                if row is not None:
                    row.vector_state = state
                    row.vector_id = vector_id
            await s.commit()

    async def _delete_by_name(self, doc_name: str) -> None:
        """按文档名删除旧记录/切片/向量/文件（同名覆盖与删除共用）。"""
        if self.db.available:
            async with self.db.session() as s:
                old = (
                    await s.execute(
                        select(SourceDocument).where(SourceDocument.doc_name == doc_name)
                    )
                ).scalar_one_or_none()
                if old is not None:
                    await s.execute(
                        delete(Chunk).where(Chunk.doc_id == old.id)
                    )
                    await s.execute(
                        delete(SourceDocument).where(SourceDocument.id == old.id)
                    )
                    await s.commit()
        # 清理 Milvus 旧向量与上传文件
        try:
            await self.retriever.delete_children_by_doc(doc_name)
        except Exception as e:
            logger.warning("milvus_delete_failed", doc=doc_name, error=str(e))
        # 删除文件目录
        dest_dir = self.upload_root / doc_name
        if dest_dir.exists():
            await asyncio.to_thread(shutil.rmtree, dest_dir, True)
