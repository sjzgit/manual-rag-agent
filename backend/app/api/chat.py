"""对话接口：POST /chat（SSE 流式）、GET /health。"""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.rag.models import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    service = request.app.state.chat_service

    async def event_stream():
        async for chunk in service.handle_chat(req):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    retriever = request.app.state.retriever
    return {
        "status": "ok",
        "collection": settings.milvus_collection,
        "num_entities": retriever._collection.num_entities if retriever._collection else 0,
        "intent_llm": "configured" if settings.intent_llm_configured else "rule-fallback",
        "llm": "configured" if settings.llm_configured else "missing-key",
        "rag_mode": settings.rag_mode,
    }
