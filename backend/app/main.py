"""FastAPI 入口：lifespan 挂载全局单例，注册路由。单例初始化统一走 core/bootstrap.py。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin as admin_api
from app.api import admin_feedback as admin_feedback_api
from app.api import admin_kb as admin_kb_api
from app.api import admin_prompt as admin_prompt_api
from app.api import admin_session as admin_session_api
from app.api import chat as chat_api
from app.api import feedback as feedback_api
from app.api import images as images_api
from app.core.bootstrap import init_services
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务层单例初始化与关停统一在 init_services 内（顺序见 bootstrap 模块注释）；
    # 此处只负责把单例挂到 app.state，键名与历史版本一致，路由层零改动。
    async with init_services() as s:
        app.state.settings = s.settings
        app.state.db = s.db
        app.state.session_service = s.session_service
        app.state.feedback_service = s.feedback_service
        app.state.prompt_service = s.prompt_service
        app.state.retriever = s.retriever
        app.state.reranker = s.reranker
        app.state.knowledge = s.knowledge
        app.state.recognizer = s.recognizer
        app.state.redis = s.redis_service  # 历史键名（非 redis_service）
        app.state.chat_service = s.chat_service
        yield


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

    settings = get_settings()
    # 本地开发入口：host/port 由 .env 控制（本地默认 8001 与 vite 代理一致）；生产走 Docker（8000），不经过此入口。
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
