"""主 LLM 流式客户端：OpenAI 兼容接口（DeepSeek 默认，配置可切换）。

Generic 模式：chat_service 组装好固定 system + 历史后直接流式调用（检索上下文附在末条 user 消息）；
Agentic 模式（第5步）：search_manual 注册为 AgentScope ReAct 工具，见 services/agent_runner。
"""
import json
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
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式生成回答，逐段 yield (kind, text)，kind ∈ {"reasoning", "content"}。

        reasoning 模型（deepseek-v4-flash 等）把思考过程放 reasoning_content、正式回答放
        content，二者分开发送，供上层分别推送思考过程与正文。
        """
        s = self.settings
        if not s.llm_configured:
            yield ("content", "（LLM 未配置 API Key，请在 .env 中填写 LLM_API_KEY 后重试。）")
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
        reasoning_chars = 0
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
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        rc = delta.get("reasoning_content")
                        if rc:
                            reasoning_chars += len(rc)
                            yield ("reasoning", rc)
                        text = delta.get("content")
                        if text:
                            total += len(text)
                            yield ("content", text)
                    except (ValueError, KeyError, IndexError):
                        continue
                    chunks += 1
        logger.info(
            "llm_stream_done",
            model=s.llm_model,
            chunks=chunks,
            reasoning_chars=reasoning_chars,
            total_chars=total,
        )
        if total == 0:
            # 兜底：reasoning 模型思考耗尽 max_tokens 时流式 content 全程为空，非流式重试一次
            logger.warning("llm_stream_empty", model=s.llm_model, url=url)
            try:
                reasoning_text, content_text = await self._non_stream_answer(system, messages)
                if reasoning_text:
                    yield ("reasoning", reasoning_text)
                if content_text:
                    total = len(content_text)
                    yield ("content", content_text)
                    logger.info("llm_fallback_used", model=s.llm_model, chars=total)
            except Exception as e:
                logger.error("llm_fallback_failed", model=s.llm_model, error=str(e))

    async def _non_stream_answer(self, system: str, messages: list[dict]) -> tuple[str, str]:
        """非流式兜底：流式 content 为空时，用足额 max_tokens 重试，返回 (reasoning, content)。"""
        s = self.settings
        payload = {
            "model": s.llm_model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": s.llm_temperature,
            "max_tokens": max(s.llm_max_tokens, 8192),
            "stream": False,
        }
        url = f"{s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {s.llm_api_key}"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            message = data["choices"][0].get("message", {})
            return message.get("reasoning_content") or "", message.get("content") or ""
