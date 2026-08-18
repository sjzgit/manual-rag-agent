"""用户反馈与工单管理接口：反馈列表 / 工单 CRUD / 反馈-工单关联。"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.feedback_service import FeedbackService

feedback_router = APIRouter(prefix="/admin/api/feedbacks", tags=["admin-feedback"])
ticket_router = APIRouter(prefix="/admin/api/tickets", tags=["admin-ticket"])


def _svc(request: Request) -> FeedbackService:
    return request.app.state.feedback_service


class TicketCreate(BaseModel):
    title: str
    priority: Literal["high", "medium", "low"] = "medium"
    feedback_ids: list[int] = []


class TicketUpdate(BaseModel):
    title: str | None = None
    status: Literal["pending", "processing", "resolved", "closed"] | None = None
    priority: Literal["high", "medium", "low"] | None = None
    result: str | None = None


class TicketLink(BaseModel):
    feedback_ids: list[int]


@feedback_router.get("")
async def list_feedbacks(request: Request, offset: int = 0, limit: int = 50):
    return await _svc(request).list_feedbacks(offset, limit)


@ticket_router.get("")
async def list_tickets(request: Request, offset: int = 0, limit: int = 50):
    return await _svc(request).list_tickets(offset, limit)


@ticket_router.post("")
async def create_ticket(req: TicketCreate, request: Request):
    ticket = await _svc(request).create_ticket(
        req.title, req.priority, req.feedback_ids
    )
    if ticket is None:
        raise HTTPException(status_code=503, detail="数据库不可用，工单创建失败")
    return {"ticket": ticket}


@ticket_router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, request: Request):
    detail = await _svc(request).get_ticket(ticket_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return detail


@ticket_router.put("/{ticket_id}")
async def update_ticket(ticket_id: int, req: TicketUpdate, request: Request):
    ok = await _svc(request).update_ticket(
        ticket_id,
        title=req.title,
        status=req.status,
        priority=req.priority,
        result=req.result,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="工单不存在或数据库不可用")
    return {"status": "ok"}


@ticket_router.post("/{ticket_id}/feedbacks")
async def link_feedbacks(ticket_id: int, req: TicketLink, request: Request):
    linked = await _svc(request).link_feedbacks(ticket_id, req.feedback_ids)
    if linked is None:
        raise HTTPException(status_code=404, detail="工单不存在或数据库不可用")
    return {"status": "ok", "linked": linked}


@ticket_router.delete("/{ticket_id}/feedbacks/{feedback_id}")
async def unlink_feedback(ticket_id: int, feedback_id: int, request: Request):
    ok = await _svc(request).unlink_feedback(ticket_id, feedback_id)
    if not ok:
        raise HTTPException(status_code=404, detail="关联不存在或数据库不可用")
    return {"status": "ok"}
