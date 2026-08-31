"""主 LLM 流式客户端：OpenAI 兼容接口（DeepSeek 默认，配置可切换）。

Generic 模式：chat_service 组装好 system+上下文+历史后直接流式调用；
Agentic 模式（第5步）：search_manual 注册为 AgentScope ReAct 工具，见 services/agent_runner。
"""
from collections.abc import AsyncGenerator

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ManualAnswerAgent:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_answer(
        self, system: str, messages: list[dict]
    ) -> AsyncGenerator[str, None]:
        """流式生成回答，逐段 yield 文本增量。"""
        s = self.settings
        if not s.llm_configured:
            yield "（LLM 未配置 API Key，请在 .env 中填写 LLM_API_KEY 后重试。）"
            return

        payload = {
            "model": s.llm_model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": s.llm_temperature,
            "max_tokens": s.llm_max_tokens,
            "stream": True,
        }
        url = f"{s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {s.llm_api_key}"}

        total = 0
        chunks = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        import json

                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        text = delta.get("content")
                        if text:
                            total += len(text)
                            yield text
                    except (ValueError, KeyError, IndexError):
                        continue
                    chunks += 1
        logger.info(
            "llm_stream_done",
            model=s.llm_model,
            chunks=chunks,
            total_chars=total,
        )
        if total == 0:
            logger.warning("llm_stream_empty", model=s.llm_model, url=url)
