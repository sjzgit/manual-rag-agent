"""知识库管理接口：源文档上传/列表/详情/预览/切片/重新处理/删除。"""
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.logging import get_logger
from app.services.knowledge_service import KnowledgeService

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/api/documents", tags=["admin-kb"])


def _svc(request: Request) -> KnowledgeService:
    return request.app.state.knowledge


def _doc_name_from_filename(filename: str) -> str:
    name = Path(filename).stem
    return name.strip()


_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _rewrite_image_src(doc_name: str, content: str) -> str:
    """将 markdown 中的 ./media/x.png 相对路径改写为 /api/images/{doc}/{filename} 绝对链接。"""

    def _replace(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        filename = src.split("/")[-1]
        return f"![{alt}](/api/images/{quote(doc_name)}/{quote(filename)})"

    return _IMG_PATTERN.sub(_replace, content)


@router.post("")
async def upload_document(request: Request, file: UploadFile):
    """上传 docx，立即返回 doc_id，后台异步转换/切片/向量化。"""
    svc = _svc(request)
    if not file.filename or not file.filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(status_code=400, detail="仅支持 .docx / .doc 文件")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    doc_name = _doc_name_from_filename(file.filename)
    if not doc_name:
        raise HTTPException(status_code=400, detail="文件名不合法")
    try:
        return await svc.create_document(doc_name, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("")
async def list_documents(request: Request, offset: int = 0, limit: int = 50):
    return await _svc(request).list_documents(offset, limit)


@router.get("/{doc_id}")
async def get_document(doc_id: str, request: Request):
    doc = await _svc(request).get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/{doc_id}/preview")
async def preview_document(doc_id: str, request: Request):
    """返回 word 预览 HTML 文件（供前端 iframe 加载）。"""
    path = await _svc(request).get_document_path(doc_id, "preview")
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="预览文件不存在（可能尚未转换完成）")
    return FileResponse(path, media_type="text/html")


@router.get("/{doc_id}/md")
async def get_md(doc_id: str, request: Request):
    path = await _svc(request).get_document_path(doc_id, "md")
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="md 文件不存在（可能尚未转换完成）")
    content = path.read_text(encoding="utf-8")
    doc = await _svc(request).get_document(doc_id)
    if doc is not None:
        content = _rewrite_image_src(doc["doc_name"], content)
    return {"content": content}


@router.get("/{doc_id}/chunks")
async def list_chunks(doc_id: str, request: Request):
    doc = await _svc(request).get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"chunks": await _svc(request).list_chunks(doc_id)}


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str, request: Request):
    if not await _svc(request).reprocess(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "ok"}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    if not await _svc(request).delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "ok"}
