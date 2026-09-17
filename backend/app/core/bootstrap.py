"""服务层单例统一初始化：main.py（FastAPI lifespan）与 MCP server（python -m app.mcp）共用。

初始化顺序即依赖顺序，禁止两处各写一份（漂移风险）：
HF_OFFLINE 环境变量 → 日志 → db → session/feedback/prompt_service
→ retriever（startup 内 asyncio.to_thread 加载 BGE/Milvus/父子映射，并 set_retriever
  供 agentic 工具路径使用）→ reranker → knowledge → recognizer → redis
→ ManualAnswerAgent →（agentic 模式时 AgenticRunner，惰性导入不加载 AgentScope）
→ ChatService。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent.manual_agent import ManualAnswerAgent
from app.core.config import Settings, get_settings
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

if TYPE_CHECKING:
    from app.services.agent_runner import AgenticRunner

logger = get_logger(__name__)


@dataclass
class Services:
    """全部服务层单例的容器，由 init_services() 构建并管理生命周期。"""

    settings: Settings
    db: Database
    session_service: SessionService
    feedback_service: FeedbackService
    prompt_service: PromptService
    retriever: ManualRetriever
    reranker: SiliconFlowReranker
    knowledge: KnowledgeService
    recognizer: IntentRecognizer
    redis_service: RedisService
    agent: ManualAnswerAgent
    agentic: AgenticRunner | None
    chat_service: ChatService


@asynccontextmanager
async def init_services() -> AsyncIterator[Services]:
    """初始化全部服务层单例；退出时按序关停（reranker → retriever → db → redis）。"""
    settings = get_settings()
    if settings.hf_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    setup_logging(settings.debug)

    db = Database(settings)
    await db.startup()
    session_service = SessionService(db)

    feedback_service = FeedbackService(db)

    prompt_service = PromptService(db)
    await prompt_service.startup()

    retriever = ManualRetriever(settings, db)
    await retriever.startup()
    retriever_module.set_retriever(retriever)

    reranker = SiliconFlowReranker(settings)
    await reranker.startup()

    knowledge = KnowledgeService(settings, db, retriever)

    recognizer = IntentRecognizer(settings, prompt_service)
    await recognizer.startup()

    redis_service = RedisService(settings)
    await redis_service.startup()

    agent = ManualAnswerAgent(settings)
    agentic = None
    if settings.rag_mode == "agentic":
        # 惰性导入：generic 模式不加载 AgentScope（与原 main.py 行为一致）
        from app.services.agent_runner import AgenticRunner

        agentic = AgenticRunner(settings, prompt_service)

    chat_service = ChatService(
        settings,
        retriever,
        recognizer,
        agent,
        session_service,
        redis_service,
        agentic,
        prompt_service,
        reranker,
        knowledge,
    )

    services = Services(
        settings=settings,
        db=db,
        session_service=session_service,
        feedback_service=feedback_service,
        prompt_service=prompt_service,
        retriever=retriever,
        reranker=reranker,
        knowledge=knowledge,
        recognizer=recognizer,
        redis_service=redis_service,
        agent=agent,
        agentic=agentic,
        chat_service=chat_service,
    )

    logger.info(
        "app_started",
        entry="bootstrap",
        rag_mode=settings.rag_mode,
        intent_llm=settings.intent_llm_configured,
        llm=settings.llm_configured,
        rerank=settings.rerank_configured,
        keyword_search=settings.enable_keyword_search,
        mysql=db.available,
        redis=redis_service.available,
    )

    yield services

    await reranker.shutdown()
    await retriever.shutdown()
    await db.shutdown()
    await redis_service.shutdown()
    logger.info("app_stopped")
