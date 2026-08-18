"""切片图片服务：映射 {MEDIA_ROOT}/{doc}/media/{filename}，防路径穿越。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/api/images", tags=["images"])

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@router.get("/{doc}/{filename}")
async def get_image(doc: str, filename: str, settings: Settings = Depends(get_settings)):
    # 防路径穿越
    if ".." in doc or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法路径")
    if Path(filename).suffix.lower() not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="不支持的文件类型")

    # 优先取管理端上传目录，回退只读知识库目录（兼容切换窗口期旧数据）
    candidates = [
        Path(settings.upload_root),
        Path(settings.media_root),
    ]
    for root in candidates:
        root = root.resolve()
        file_path = (root / doc / "media" / filename).resolve()
        if not str(file_path).startswith(str(root)):
            continue
        if file_path.is_file():
            return FileResponse(file_path)

    raise HTTPException(status_code=404, detail="图片不存在")
