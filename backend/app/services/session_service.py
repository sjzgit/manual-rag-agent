"""会话/消息/澄清状态/日志的 MySQL 读写。数据库不可用时静默跳过。"""
from sqlalchemy import delete, func, select, update

from app.core.logging import get_logger
from app.db.models import (
    Feedback,
    IntentLog,
    LlmCallLog,
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

    async def rename_session(self, session_id: str, title: str) -> bool:
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                row = await s.get(Session, session_id)
                if row is None:
                    return False
                row.title = title[:50] or "新会话"
                await s.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("rename_session_failed", error=str(e))
            return False

    async def delete_session(self, session_id: str) -> bool:
        """删除会话及其消息、反馈、日志（无外键，需手动级联清理）。"""
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                msg_ids = (
                    (
                        await s.execute(
                            select(Message.id).where(Message.session_id == session_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if msg_ids:
                    await s.execute(delete(Feedback).where(Feedback.message_id.in_(msg_ids)))
                    await s.execute(delete(IntentLog).where(IntentLog.message_id.in_(msg_ids)))
                    await s.execute(delete(RetrievalLog).where(RetrievalLog.message_id.in_(msg_ids)))
                    await s.execute(delete(LlmCallLog).where(LlmCallLog.message_id.in_(msg_ids)))
                await s.execute(delete(Message).where(Message.session_id == session_id))
                await s.execute(delete(Session).where(Session.id == session_id))
                await s.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("delete_session_failed", error=str(e))
            return False

    async def delete_messages(self, session_id: str, message_ids: list[str]) -> bool:
        """删除一组消息，并清理其关联反馈与日志。"""
        if not self.db.available or not message_ids:
            return False
        try:
            async with self.db.session() as s:
                await s.execute(delete(Feedback).where(Feedback.message_id.in_(message_ids)))
                await s.execute(delete(IntentLog).where(IntentLog.message_id.in_(message_ids)))
                await s.execute(delete(RetrievalLog).where(RetrievalLog.message_id.in_(message_ids)))
                await s.execute(delete(LlmCallLog).where(LlmCallLog.message_id.in_(message_ids)))
                await s.execute(
                    delete(Message).where(
                        Message.id.in_(message_ids),
                        Message.session_id == session_id,
                    )
                )
                await s.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("delete_messages_failed", error=str(e))
            return False

    async def get_messages(self, session_id: str, limit: int | None = None) -> list[dict]:
        if not self.db.available:
            return []
        async with self.db.session() as s:
            stmt = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = (await s.execute(stmt)).scalars()
            return [
                {
                    "id": r.id,
                    "role": r.role,
                    "content": r.content,
                    "sources": r.sources or [],
                    "steps": r.steps or [],
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
        steps: list | None = None,
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
                        steps=steps,
                    )
                )
                # 刷新会话活跃时间（会话列表按最近活跃排序）
                await s.execute(
                    update(Session)
                    .where(Session.id == session_id)
                    .values(updated_at=func.now())
                )
                # 仅首条用户消息写入标题，后续输入不再覆盖
                if role == "user":
                    await s.execute(
                        update(Session)
                        .where(
                            Session.id == session_id,
                            Session.title.in_(["", "新会话"]),
                        )
                        .values(title=content[:50])
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
        mode: str = "dense",
        stage: str = "final",
    ) -> None:
        """记录一个检索阶段的命中列表（stage：dense/sparse/fused/final）。

        同一子问题按阶段多次调用（稀疏路未启用时仅 final 一阶段）。
        """
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                for rank, h in enumerate(hits, 1):
                    s.add(
                        RetrievalLog(
                            session_id=session_id,
                            message_id=message_id,
                            sub_question=sub_question,
                            chunk_id=h.chunk_id,
                            doc=h.doc,
                            path=h.path,
                            score=h.score,
                            dense_score=h.dense_score,
                            sparse_score=h.sparse_score,
                            fused_score=h.fused_score,
                            rerank_score=h.rerank_score,
                            mode=mode,
                            stage=stage,
                            hit_rank=rank,
                        )
                    )
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("log_retrievals_failed", error=str(e))

    async def log_llm_call(
        self,
        session_id: str,
        message_id: str,
        call_type: str,
        system_prompt: str,
        messages: list[dict],
        output: str,
    ) -> None:
        """记录一次 LLM 调用的原始输入输出（intent / answer）。"""
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                s.add(
                    LlmCallLog(
                        session_id=session_id,
                        message_id=message_id,
                        call_type=call_type,
                        system_prompt=system_prompt,
                        messages=messages,
                        output=output,
                    )
                )
                await s.commit()
        except Exception as e:
            logger.warning("log_llm_call_failed", error=str(e))

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
