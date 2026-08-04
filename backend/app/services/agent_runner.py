"""Agentic RAG 运行器：AgentScope 2.0.5 Agent + search_manual 工具（RAG_MODE=agentic 时启用）。

Generic 模式走 chat_service 固定流水线；Agentic 模式由 Agent 自主决定何时调用检索工具。
每轮新建 Agent 并用 observe 注入历史（无状态服务 + 记忆重建）。
"""
import json
from collections.abc import AsyncGenerator

from agentscope.agent import Agent, ReActConfig
from agentscope.credential import DeepSeekCredential
from agentscope.event import TextBlockDeltaEvent
from agentscope.message import Msg, TextBlock
from agentscope.model import DeepSeekChatModel
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

from app.core.config import Settings
from app.core.logging import get_logger
from app.prompts.manual import AGENTIC_SYSTEM_PROMPT
from app.rag.retriever import search_manual

logger = get_logger(__name__)


async def _search_manual_tool(
    query: str, doc: str | None = None, top_k: int = 2
) -> ToolChunk:
    """从操作手册知识库检索与问题相关的切片。

    Args:
        query: 用户问题或检索语句
        doc: 可选，按手册名称过滤检索范围
        top_k: 返回的切片数量，默认 2
    """
    hits = await search_manual(query, doc=doc, top_k=top_k)
    if not hits:
        text = "未检索到相关手册切片。"
    else:
        parts = [
            f"【切片】来源：{h['doc']} > {h['path']}（相似度 {h['score']:.3f}）\n{h['content']}"
            for h in hits
        ]
        text = "\n\n".join(parts)
    return ToolChunk(content=[TextBlock(type="text", text=text)], is_last=True)


async def build_agent(settings: Settings) -> Agent:
    """构建注册了 search_manual 工具的 AgentScope Agent。"""
    toolkit = Toolkit()
    await toolkit.add_tool(FunctionTool(_search_manual_tool, name="search_manual"))

    model = DeepSeekChatModel(
        credential=DeepSeekCredential(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        ),
        model=settings.llm_model,
        parameters=DeepSeekChatModel.Parameters(
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        ),
        stream=True,
    )

    return Agent(
        name="manual-assistant",
        system_prompt=AGENTIC_SYSTEM_PROMPT,
        model=model,
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=6),
    )


def _to_msg(role: str, content: str) -> Msg:
    return Msg(
        name=role,
        role=role,
        content=[TextBlock(type="text", text=content)],
    )


class AgenticRunner:
    """Agentic 模式流式回答：Agent 自主调用检索工具。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_answer(
        self, question: str, history: list[dict]
    ) -> AsyncGenerator[str, None]:
        agent = await build_agent(self.settings)

        # 注入多轮历史（跳过正在处理的最后一条用户消息由 reply 传入）
        for h in history[-10:]:
            await agent.observe(_to_msg(h["role"], h["content"]))

        async for event in agent.reply_stream(_to_msg("user", question)):
            if isinstance(event, TextBlockDeltaEvent) and event.delta:
                yield event.delta
