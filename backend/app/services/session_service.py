"""会话/消息/澄清状态/日志的 MySQL 读写。数据库不可用时静默跳过。"""
from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.models import (
    Feedback,
    IntentLog,
    Message,
    RetrievalLog,
    Session,
)
from app.db.session import Database
from app.intent.models import ClarifyState, IntentBatch
from app.rag.models import SearchResult

logger = get_logger(__name__)


class SessionService:
    def __init__(self, db: Database):
        self.db = db

    # ---------- 会话 ----------

    async def ensure_session(self, session_id: str, title: str = "") -> None:
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                exists = await s.get(Session, session_id)
                if exists is None:
                    s.add(Session(id=session_id, title=title[:50] or "新会话"))
                    await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("ensure_session_failed", error=str(e))

    async def list_sessions(self) -> list[dict]:
        if not self.db.available:
            return []
        async with self.db.session() as s:
            rows = (
                await s.execute(select(Session).order_by(Session.updated_at.desc()))
            ).scalars()
            return [
                {"id": r.id, "title": r.title, "updated_at": r.updated_at.isoformat()}
                for r in rows
            ]

    async def get_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        if not self.db.available:
            return []
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at)
                    .limit(limit)
                )
            ).scalars()
            return [
                {
                    "id": r.id,
                    "role": r.role,
                    "content": r.content,
                    "sources": r.sources or [],
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]

    async def save_message(
        self,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        sources: list | None = None,
    ) -> None:
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                s.add(
                    Message(
                        id=message_id,
                        session_id=session_id,
                        role=role,
                        content=content,
                        sources=sources,
                    )
                )
                # 更新会话时间与标题（首条用户消息）
                await s.execute(
                    update(Session)
                    .where(Session.id == session_id)
                    .values(
                        title=(
                            content[:50]
                            if role == "user"
                            else Session.__table__.c.title
                        )
                    )
                )
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("save_message_failed", error=str(e))

    # ---------- 澄清状态 ----------

    async def save_clarify_state(self, session_id: str, state: ClarifyState | None) -> None:
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                await s.execute(
                    update(Session)
                    .where(Session.id == session_id)
                    .values(clarify_state=state.model_dump() if state else None)
                )
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("save_clarify_failed", error=str(e))

    async def get_clarify_state(self, session_id: str) -> ClarifyState | None:
        if not self.db.available:
            return None
        try:
            async with self.db.session() as s:
                row = await s.get(Session, session_id)
                if row and row.clarify_state:
                    return ClarifyState(**row.clarify_state)
        except Exception as e:  # noqa: BLE001
            logger.warning("get_clarify_failed", error=str(e))
        return None

    # ---------- 日志 ----------

    async def log_intents(
        self, session_id: str, message_id: str, batch: IntentBatch
    ) -> None:
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                for i in batch.intents:
                    s.add(
                        IntentLog(
                            session_id=session_id,
                            message_id=message_id,
                            sub_question=i.model_dump(),
                            intent_type=i.intent_type,
                            intent_reason=i.intent_reason,
                            used_llm=batch.used_llm,
                        )
                    )
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("log_intents_failed", error=str(e))

    async def log_retrievals(
        self,
        session_id: str,
        message_id: str,
        sub_question: str,
        hits: list[SearchResult],
    ) -> None:
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                for h in hits:
                    s.add(
                        RetrievalLog(
                            session_id=session_id,
                            message_id=message_id,
                            sub_question=sub_question,
                            chunk_id=h.chunk_id,
                            doc=h.doc,
                            path=h.path,
                            score=h.score,
                        )
                    )
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("log_retrievals_failed", error=str(e))

    async def save_feedback(self, message_id: str, score: int, comment: str) -> bool:
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                s.add(Feedback(message_id=message_id, score=score, comment=comment))
                await s.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("save_feedback_failed", error=str(e))
            return False
