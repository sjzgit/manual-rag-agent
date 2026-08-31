"""FastAPI 入口：lifespan 初始化全局单例，注册路由。"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.manual_agent import ManualAnswerAgent
from app.api import admin as admin_api
from app.api import admin_feedback as admin_feedback_api
from app.api import admin_kb as admin_kb_api
from app.api import admin_prompt as admin_prompt_api
from app.api import admin_session as admin_session_api
from app.api import chat as chat_api
from app.api import feedback as feedback_api
from app.api import images as images_api
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import Database
from app.intent.recognizer import IntentRecognizer
from app.rag import retriever as retriever_module
from app.rag.reranker import SiliconFlowReranker
from app.rag.retriever import ManualRetriever
from app.services.cache import RedisService
from app.services.chat_service import ChatService
from app.services.feedback_service import FeedbackService
from app.services.knowledge_service import KnowledgeService
from app.services.prompt_service import PromptService
from app.services.session_service import SessionService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.hf_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    setup_logging(settings.debug)
    app.state.settings = settings

    db = Database(settings)
    await db.startup()
    app.state.db = db
    session_service = SessionService(db)
    app.state.session_service = session_service

    feedback_service = FeedbackService(db)
    app.state.feedback_service = feedback_service

    prompt_service = PromptService(db)
    await prompt_service.startup()
    app.state.prompt_service = prompt_service

    retriever = ManualRetriever(settings, db)
    await retriever.startup()
    retriever_module.set_retriever(retriever)
    app.state.retriever = retriever

    reranker = SiliconFlowReranker(settings)
    await reranker.startup()
    app.state.reranker = reranker

    knowledge = KnowledgeService(settings, db, retriever)
    app.state.knowledge = knowledge

    recognizer = IntentRecognizer(settings, prompt_service)
    await recognizer.startup()
    app.state.recognizer = recognizer

    redis_service = RedisService(settings)
    await redis_service.startup()
    app.state.redis = redis_service

    agent = ManualAnswerAgent(settings)
    agentic = None
    if settings.rag_mode == "agentic":
        from app.services.agent_runner import AgenticRunner

        agentic = AgenticRunner(settings, prompt_service)
    app.state.chat_service = ChatService(
        settings,
        retriever,
        recognizer,
        agent,
        session_service,
        redis_service,
        agentic,
        prompt_service,
        reranker,
    )

    logger.info(
        "app_started",
        rag_mode=settings.rag_mode,
        intent_llm=settings.intent_llm_configured,
        llm=settings.llm_configured,
        rerank=settings.rerank_configured,
        keyword_search=settings.enable_keyword_search,
        mysql=db.available,
        redis=redis_service.available,
    )

    yield

    await reranker.shutdown()
    await retriever.shutdown()
    await db.shutdown()
    await redis_service.shutdown()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_api.router)
    app.include_router(images_api.router)
    app.include_router(feedback_api.router)
    app.include_router(admin_api.router)
    app.include_router(admin_kb_api.router)
    app.include_router(admin_prompt_api.router)
    app.include_router(admin_session_api.router)
    app.include_router(admin_feedback_api.feedback_router)
    app.include_router(admin_feedback_api.ticket_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # 本地开发入口：端口 8001 与 vite 代理一致；生产走 Docker（8000），不经过此入口。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
