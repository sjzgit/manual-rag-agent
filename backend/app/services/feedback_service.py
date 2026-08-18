"""用户反馈与工单的 MySQL 读写。数据库不可用时静默降级。"""
from datetime import datetime

from sqlalchemy import delete, func, select

from app.core.logging import get_logger
from app.db.models import Feedback, FeedbackTicket, Message, Session, Ticket
from app.db.session import Database

logger = get_logger(__name__)


def _dt(v):
    return v.isoformat() if v is not None else None


def _feedback_dict(fb, msg, sess) -> dict:
    return {
        "id": fb.id,
        "score": fb.score,
        "comment": fb.comment,
        "created_at": _dt(fb.created_at),
        "message_id": fb.message_id,
        "session_id": (
            sess.id if sess is not None else (msg.session_id if msg is not None else None)
        ),
        "session_title": sess.title if sess is not None else None,
        "message_content": msg.content if msg is not None else None,
    }


def _ticket_dict(t) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "priority": t.priority,
        "result": t.result,
        "processed_at": _dt(t.processed_at),
        "created_at": _dt(t.created_at),
        "updated_at": _dt(t.updated_at),
    }


class FeedbackService:
    def __init__(self, db: Database):
        self.db = db

    # ---------- 反馈 ----------

    async def list_feedbacks(self, offset: int = 0, limit: int = 50) -> dict:
        if not self.db.available:
            return {"feedbacks": [], "total": 0}
        try:
            async with self.db.session() as s:
                total = (
                    await s.execute(select(func.count()).select_from(Feedback))
                ).scalar_one()
                rows = (
                    await s.execute(
                        select(Feedback, Message, Session)
                        .outerjoin(Message, Feedback.message_id == Message.id)
                        .outerjoin(Session, Message.session_id == Session.id)
                        .order_by(Feedback.created_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
                return {
                    "feedbacks": [_feedback_dict(fb, msg, sess) for fb, msg, sess in rows],
                    "total": total,
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("list_feedbacks_failed", error=str(e))
            return {"feedbacks": [], "total": 0}

    # ---------- 工单 ----------

    async def list_tickets(self, offset: int = 0, limit: int = 50) -> dict:
        if not self.db.available:
            return {"tickets": [], "total": 0}
        try:
            async with self.db.session() as s:
                total = (
                    await s.execute(select(func.count()).select_from(Ticket))
                ).scalar_one()
                rows = (
                    await s.execute(
                        select(Ticket, func.count(FeedbackTicket.id))
                        .outerjoin(FeedbackTicket, FeedbackTicket.ticket_id == Ticket.id)
                        .group_by(Ticket.id)
                        .order_by(Ticket.created_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
                return {
                    "tickets": [
                        {**_ticket_dict(t), "feedback_count": cnt} for t, cnt in rows
                    ],
                    "total": total,
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("list_tickets_failed", error=str(e))
            return {"tickets": [], "total": 0}

    async def get_ticket(self, ticket_id: int) -> dict | None:
        if not self.db.available:
            return None
        try:
            async with self.db.session() as s:
                ticket = await s.get(Ticket, ticket_id)
                if ticket is None:
                    return None
                rows = (
                    await s.execute(
                        select(Feedback, Message, Session)
                        .join(FeedbackTicket, FeedbackTicket.feedback_id == Feedback.id)
                        .outerjoin(Message, Feedback.message_id == Message.id)
                        .outerjoin(Session, Message.session_id == Session.id)
                        .where(FeedbackTicket.ticket_id == ticket_id)
                        .order_by(Feedback.created_at.desc())
                    )
                ).all()
                return {
                    "ticket": _ticket_dict(ticket),
                    "feedbacks": [_feedback_dict(fb, msg, sess) for fb, msg, sess in rows],
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("get_ticket_failed", error=str(e))
            return None

    async def create_ticket(
        self,
        title: str,
        priority: str,
        feedback_ids: list[int],
        status: str = "pending",
    ) -> dict | None:
        if not self.db.available:
            return None
        try:
            async with self.db.session() as s:
                t = Ticket(title=title, priority=priority, status=status)
                s.add(t)
                await s.flush()  # 拿 t.id
                for fid in set(feedback_ids):
                    s.add(FeedbackTicket(ticket_id=t.id, feedback_id=fid))
                await s.commit()
                await s.refresh(t)  # 回读 server_default 生成的时间
                return _ticket_dict(t)
        except Exception as e:  # noqa: BLE001
            logger.warning("create_ticket_failed", error=str(e))
            return None

    async def update_ticket(
        self,
        ticket_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        result: str | None = None,
    ) -> bool:
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                t = await s.get(Ticket, ticket_id)
                if t is None:
                    return False
                if title is not None:
                    t.title = title
                if priority is not None:
                    t.priority = priority
                if result is not None:
                    t.result = result
                if status is not None:
                    t.status = status
                    # 自由切换：进入已处理/已关闭记录处理时间，回到待处理/处理中清空
                    t.processed_at = datetime.now() if status in ("resolved", "closed") else None
                await s.commit()
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("update_ticket_failed", error=str(e))
            return False

    async def link_feedbacks(self, ticket_id: int, feedback_ids: list[int]) -> int | None:
        """「加入已有工单」，去重后返回实际新增关联条数。"""
        if not self.db.available:
            return None
        try:
            async with self.db.session() as s:
                ticket = await s.get(Ticket, ticket_id)
                if ticket is None:
                    return None
                ids = set(feedback_ids)
                if not ids:
                    return 0
                existing = (
                    await s.execute(
                        select(FeedbackTicket.feedback_id).where(
                            FeedbackTicket.ticket_id == ticket_id,
                            FeedbackTicket.feedback_id.in_(ids),
                        )
                    )
                ).scalars()
                new_ids = ids - set(existing)
                for fid in new_ids:
                    s.add(FeedbackTicket(ticket_id=ticket_id, feedback_id=fid))
                await s.commit()
                return len(new_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("link_feedbacks_failed", error=str(e))
            return None

    async def unlink_feedback(self, ticket_id: int, feedback_id: int) -> bool:
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                await s.execute(
                    delete(FeedbackTicket).where(
                        FeedbackTicket.ticket_id == ticket_id,
                        FeedbackTicket.feedback_id == feedback_id,
                    )
                )
                await s.commit()
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("unlink_feedback_failed", error=str(e))
            return False
