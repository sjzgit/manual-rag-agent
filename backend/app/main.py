"""FastAPI 入口：lifespan 初始化全局单例，注册路由。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.manual_agent import ManualAnswerAgent
from app.api import admin as admin_api
from app.api import chat as chat_api
from app.api import feedback as feedback_api
from app.api import images as images_api
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import Database
from app.intent.recognizer import IntentRecognizer
from app.rag import retriever as retriever_module
from app.rag.retriever import ManualRetriever
from app.services.cache import RedisService
from app.services.chat_service import ChatService
from app.services.session_service import SessionService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.debug)
    app.state.settings = settings

    retriever = ManualRetriever(settings)
    await retriever.startup()
    retriever_module.set_retriever(retriever)
    app.state.retriever = retriever

    recognizer = IntentRecognizer(settings)
    await recognizer.startup()
    app.state.recognizer = recognizer

    db = Database(settings)
    await db.startup()
    app.state.db = db
    session_service = SessionService(db)
    app.state.session_service = session_service

    redis_service = RedisService(settings)
    await redis_service.startup()
    app.state.redis = redis_service

    agent = ManualAnswerAgent(settings)
    agentic = None
    if settings.rag_mode == "agentic":
        from app.services.agent_runner import AgenticRunner

        agentic = AgenticRunner(settings)
    app.state.chat_service = ChatService(
        settings, retriever, recognizer, agent, session_service, redis_service, agentic
    )

    logger.info(
        "app_started",
        rag_mode=settings.rag_mode,
        intent_llm=settings.intent_llm_configured,
        llm=settings.llm_configured,
        mysql=db.available,
        redis=redis_service.available,
    )

    yield

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

    return app


app = create_app()
