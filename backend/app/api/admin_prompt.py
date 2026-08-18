"""提示词模板管理接口：查看与编辑 RAG 各环节提示词。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.prompt_service import PromptService

router = APIRouter(prefix="/admin/api/prompts", tags=["admin-prompt"])


def _svc(request: Request) -> PromptService:
    return request.app.state.prompt_service


class PromptUpdate(BaseModel):
    content: str


@router.get("")
async def list_prompts(request: Request):
    return {"prompts": await _svc(request).list()}


@router.put("/{key}")
async def update_prompt(key: str, body: PromptUpdate, request: Request):
    if not await _svc(request).update(key, body.content):
        raise HTTPException(status_code=404, detail="模板标识不存在或数据库不可用")
    return {"status": "ok"}
