"""反馈与会话管理接口。"""
from fastapi import APIRouter, HTTPException, Request

from app.rag.models import (
    DeleteMessagesRequest,
    FeedbackRequest,
    RenameSessionRequest,
)

router = APIRouter(tags=["feedback"])


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request):
    if req.score not in (1, -1):
        raise HTTPException(status_code=400, detail="score 只能为 1 或 -1")
    ok = await request.app.state.session_service.save_feedback(
        req.message_id, req.score, req.comment
    )
    if not ok:
        raise HTTPException(status_code=503, detail="数据库不可用，反馈未保存")
    return {"status": "ok"}


@router.post("/sessions")
async def create_session(request: Request):
    import uuid

    session_id = uuid.uuid4().hex
    await request.app.state.session_service.ensure_session(session_id)
    return {"session_id": session_id}


@router.get("/sessions")
async def list_sessions(request: Request):
    return {"sessions": await request.app.state.session_service.list_sessions()}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, request: Request):
    return {
        "messages": await request.app.state.session_service.get_messages(session_id)
    }


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameSessionRequest, request: Request):
    ok = await request.app.state.session_service.rename_session(session_id, req.title)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在或数据库不可用")
    return {"status": "ok"}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    ok = await request.app.state.session_service.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在或数据库不可用")
    return {"status": "ok"}


@router.delete("/sessions/{session_id}/messages")
async def delete_messages(session_id: str, req: DeleteMessagesRequest, request: Request):
    ok = await request.app.state.session_service.delete_messages(
        session_id, req.message_ids
    )
    if not ok:
        raise HTTPException(status_code=503, detail="数据库不可用")
    return {"status": "ok"}
