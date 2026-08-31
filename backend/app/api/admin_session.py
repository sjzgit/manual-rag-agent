"""会话管理接口：会话列表 / 详情（消息+来源+步骤）/ 环节日志（意图+检索）。"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.db.models import IntentLog, LlmCallLog, Message, RetrievalLog, Session

router = APIRouter(prefix="/admin/api/sessions", tags=["admin-session"])


def _dt(v):
    return v.isoformat() if v is not None else None


@router.get("")
async def list_sessions(request: Request, offset: int = 0, limit: int = 50):
    db = request.app.state.db
    if not db.available:
        return {"sessions": [], "total": 0}
    async with db.session() as s:
        total = (
            await s.execute(select(func.count()).select_from(Session))
        ).scalar_one()
        rows = (
            await s.execute(
                select(Session)
                .order_by(Session.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
        return {
            "sessions": [
                {
                    "id": r.id,
                    "title": r.title,
                    "doc_filter": r.doc_filter,
                    "created_at": _dt(r.created_at),
                    "updated_at": _dt(r.updated_at),
                }
                for r in rows
            ],
            "total": total,
        }


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    db = request.app.state.db
    if not db.available:
        raise HTTPException(status_code=503, detail="数据库不可用")
    async with db.session() as s:
        session = await s.get(Session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = (
            await s.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
        ).scalars()
        return {
            "session": {
                "id": session.id,
                "title": session.title,
                "doc_filter": session.doc_filter,
                "clarify_state": session.clarify_state,
                "created_at": _dt(session.created_at),
                "updated_at": _dt(session.updated_at),
            },
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "sources": m.sources or [],
                    "steps": m.steps or [],
                    "reasoning": m.reasoning,
                    "created_at": _dt(m.created_at),
                }
                for m in messages
            ],
        }


@router.get("/{session_id}/logs")
async def get_session_logs(session_id: str, request: Request):
    db = request.app.state.db
    if not db.available:
        raise HTTPException(status_code=503, detail="数据库不可用")
    async with db.session() as s:
        intents = (
            await s.execute(
                select(IntentLog)
                .where(IntentLog.session_id == session_id)
                .order_by(IntentLog.created_at)
            )
        ).scalars()
        retrievals = (
            await s.execute(
                select(RetrievalLog)
                .where(RetrievalLog.session_id == session_id)
                .order_by(RetrievalLog.created_at)
            )
        ).scalars()
        llm_calls = (
            await s.execute(
                select(LlmCallLog)
                .where(LlmCallLog.session_id == session_id)
                .order_by(LlmCallLog.created_at)
            )
        ).scalars()
        return {
            "intent_logs": [
                {
                    "id": r.id,
                    "message_id": r.message_id,
                    "sub_question": r.sub_question,
                    "intent_type": r.intent_type,
                    "intent_reason": r.intent_reason,
                    "used_llm": r.used_llm,
                    "created_at": _dt(r.created_at),
                }
                for r in intents
            ],
            "retrieval_logs": [
                {
                    "id": r.id,
                    "message_id": r.message_id,
                    "sub_question": r.sub_question,
                    "chunk_id": r.chunk_id,
                    "doc": r.doc,
                    "path": r.path,
                    "score": r.score,
                    "dense_score": r.dense_score,
                    "sparse_score": r.sparse_score,
                    "fused_score": r.fused_score,
                    "rerank_score": r.rerank_score,
                    "mode": r.mode,
                    "stage": r.stage,
                    "hit_rank": r.hit_rank,
                    "created_at": _dt(r.created_at),
                }
                for r in retrievals
            ],
            "llm_call_logs": [
                {
                    "id": r.id,
                    "message_id": r.message_id,
                    "call_type": r.call_type,
                    "system_prompt": r.system_prompt,
                    "messages": r.messages,
                    "output": r.output,
                    "created_at": _dt(r.created_at),
                }
                for r in llm_calls
            ],
        }
