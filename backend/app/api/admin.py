"""管理接口：登录、文档增量更新、统计。需管理员权限。"""
import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import hash_password, issue_token, require_admin, verify_password
from app.db.models import User
from app.rag.retriever import ManualRetriever

router = APIRouter(prefix="/admin", tags=["admin"])

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]+\)")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    db = request.app.state.db
    if not db.available:
        raise HTTPException(status_code=503, detail="数据库不可用")
    from sqlalchemy import select

    async with db.session() as s:
        user = (
            await s.execute(select(User).where(User.username == req.username))
        ).scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": issue_token(user.id, user.role), "role": user.role}


@router.post("/users", dependencies=[Depends(require_admin)])
async def create_user(req: LoginRequest, request: Request, role: str = "user"):
    import uuid

    db = request.app.state.db
    if not db.available:
        raise HTTPException(status_code=503, detail="数据库不可用")
    async with db.session() as s:
        s.add(
            User(
                id=uuid.uuid4().hex,
                username=req.username,
                password_hash=hash_password(req.password),
                role=role,
            )
        )
        await s.commit()
    return {"status": "ok"}


def _clean_for_embedding(text: str) -> str:
    return _IMG_PATTERN.sub(r"[图片：\1]", text)


@router.post("/reingest", dependencies=[Depends(require_admin)])
async def reingest(request: Request):
    """文档增量更新：重读 chunks.json，对 Milvus 执行幂等 upsert，并刷新原文映射。"""
    settings = request.app.state.settings
    retriever: ManualRetriever = request.app.state.retriever

    chunks_file = Path(settings.chunks_path)
    if not chunks_file.exists():
        raise HTTPException(status_code=404, detail=f"chunks 文件不存在：{chunks_file}")

    chunks = json.loads(chunks_file.read_text(encoding="utf-8"))

    def _upsert() -> int:
        assert retriever._model is not None and retriever._collection is not None
        count = 0
        batch_size = 16
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            contents = [_clean_for_embedding(c["content"]) for c in batch]
            vecs = retriever._model.encode(contents, normalize_embeddings=True)
            retriever._collection.upsert(
                [
                    [c["id"] for c in batch],
                    [c["doc"] for c in batch],
                    [c["path"] for c in batch],
                    contents,
                    vecs,
                ]
            )
            count += len(batch)
        retriever._collection.flush()
        return count

    count = await asyncio.to_thread(_upsert)
    # 刷新内存原文映射
    retriever._raw_chunks = {c["id"]: c for c in chunks}
    return {"status": "ok", "upserted": count}


@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats(request: Request):
    db = request.app.state.db
    retriever: ManualRetriever = request.app.state.retriever
    result: dict = {
        "collection_entities": (
            retriever._collection.num_entities if retriever._collection else 0
        ),
        "raw_chunks": len(retriever._raw_chunks),
        "mysql": db.available,
        "redis": request.app.state.redis.available,
    }
    if db.available:
        from sqlalchemy import func, select

        from app.db.models import Feedback, IntentLog, Message, RetrievalLog, Session

        async with db.session() as s:
            for name, model in [
                ("sessions", Session),
                ("messages", Message),
                ("feedbacks", Feedback),
                ("intent_logs", IntentLog),
                ("retrieval_logs", RetrievalLog),
            ]:
                result[name] = (
                    await s.execute(select(func.count()).select_from(model))
                ).scalar_one()
    return result
