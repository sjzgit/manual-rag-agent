"""对话流水线状态机：意图识别 → 澄清循环 → 逐子问题检索 → LLM 流式生成。"""
import uuid
from collections.abc import AsyncGenerator

from app.agent.manual_agent import ManualAnswerAgent
from app.core.config import Settings
from app.core.logging import get_logger
from app.intent.models import ClarifyState, IntentBatch
from app.intent.recognizer import IntentRecognizer
from app.prompts.manual import (
    ANSWER_SYSTEM_PROMPT,
    CLARIFY_EXHAUSTED_PREFIX,
    IRRELEVANT_REPLY,
    NOT_FOUND_REPLY,
)
from app.rag.models import ChatRequest, SearchResult, SourceChunk
from app.rag.retriever import ManualRetriever
from app.services.session_service import SessionService
from app.utils import sse

logger = get_logger(__name__)

# 数据库不可用时的内存澄清状态兜底
_clarify_states: dict[str, ClarifyState] = {}


class ChatService:
    def __init__(
        self,
        settings: Settings,
        retriever: ManualRetriever,
        recognizer: IntentRecognizer,
        agent: ManualAnswerAgent,
        session_service: SessionService | None = None,
        redis=None,
        agentic=None,
    ):
        self.settings = settings
        self.retriever = retriever
        self.recognizer = recognizer
        self.agent = agent
        self.sessions = session_service
        self.redis = redis
        self.agentic = agentic

    # ---------- 主流水线 ----------

    async def handle_chat(self, req: ChatRequest) -> AsyncGenerator[str, None]:
        session_id = req.session_id or uuid.uuid4().hex
        message_id = uuid.uuid4().hex
        # SSE 头事件：告知前端本次会话/消息 id
        yield sse.sse_event("meta", {"session_id": session_id, "message_id": message_id})

        try:
            # 限流（Redis 不可用时直通）
            if self.redis:
                from app.services.cache import RateLimitExceeded

                try:
                    await self.redis.check_rate_limit(session_id)
                except RateLimitExceeded as e:
                    yield sse.error("RATE_LIMITED", str(e))
                    return

            if self.sessions:
                await self.sessions.ensure_session(session_id, req.question)

            # 澄清补充：与原问题合并
            state = await self._get_clarify_state(session_id)
            if req.clarify_answer and state:
                question = f"{state.original_question}（补充：{req.clarify_answer}）"
                state.round += 1
            else:
                question = req.question
                state = ClarifyState(original_question=question)

            if self.sessions:
                await self.sessions.save_message(
                    uuid.uuid4().hex, session_id, "user", req.clarify_answer or req.question
                )

            # 1) 意图识别
            yield sse.step("intent", "意图识别中…")
            batch = await self.recognizer.recognize(question)
            yield sse.step(
                "intent",
                "意图识别完成",
                {
                    "used_llm": batch.used_llm,
                    "intents": [i.model_dump() for i in batch.intents],
                },
            )
            if self.sessions:
                await self.sessions.log_intents(session_id, message_id, batch)

            # 2) 不相关 → 拒答
            if batch.has_irrelevant:
                yield sse.token(IRRELEVANT_REPLY)
                await self._set_clarify_state(session_id, None)
                await self._save_assistant(session_id, message_id, IRRELEVANT_REPLY, None)
                yield sse.done(message_id)
                return

            # 3) 模糊 → 澄清
            if not batch.all_precise:
                vague = batch.vague_intents
                missing = self._missing_fields(vague)

                if state.round >= self.settings.max_clarify_rounds:
                    # 超限：按最相关切片直接回答
                    yield sse.step(
                        "intent", f"澄清超过 {state.round} 轮，按最相关切片回答"
                    )
                    await self._set_clarify_state(session_id, None)
                    async for chunk in self._answer_flow(
                        session_id, question, vague, req.doc, message_id,
                        prefix=CLARIFY_EXHAUSTED_PREFIX,
                    ):
                        yield chunk
                    return

                state.pending_intents = vague
                state.missing_fields = missing
                question_text = await self.recognizer.generate_clarify_question(vague)
                state.clarify_question = question_text
                await self._set_clarify_state(session_id, state)
                yield sse.clarify(
                    question_text, missing, [i.model_dump() for i in vague]
                )
                await self._save_assistant(session_id, message_id, question_text, None)
                yield sse.done(message_id)
                return

            # 4) 全部精确 → 检索 + 回答
            await self._set_clarify_state(session_id, None)
            async for chunk in self._answer_flow(
                session_id, question, batch.precise_intents, req.doc, message_id
            ):
                yield chunk

        except Exception as e:  # noqa: BLE001
            logger.error("chat_pipeline_error", error=str(e), session_id=session_id)
            yield sse.error("PIPELINE_ERROR", str(e))

    # ---------- 检索 + 生成 ----------

    async def _answer_flow(
        self,
        session_id: str,
        question: str,
        intents,
        doc_filter: str | None,
        message_id: str,
        prefix: str = "",
    ) -> AsyncGenerator[str, None]:
        # 逐子问题检索（top_k=2）
        yield sse.step("retrieve", "向量检索中…")
        all_hits: list[SearchResult] = []
        seen: set[str] = set()
        for intent in intents:
            q = intent.simple_input or intent.input
            hits = await self.retriever.search(q, doc=doc_filter)
            hits = self.retriever.filter_by_threshold(hits)
            yield sse.step(
                "retrieve",
                f"检索「{q}」",
                {
                    "sub_question": q,
                    "hits": [
                        {"chunk_id": h.chunk_id, "doc": h.doc, "path": h.path, "score": round(h.score, 4)}
                        for h in hits
                    ],
                },
            )
            if self.sessions:
                await self.sessions.log_retrievals(session_id, message_id, q, hits)
            for h in hits:
                if h.chunk_id not in seen:
                    seen.add(h.chunk_id)
                    all_hits.append(h)

        # 无命中 → 未找到
        if not all_hits:
            yield sse.token(NOT_FOUND_REPLY)
            await self._save_assistant(session_id, message_id, NOT_FOUND_REPLY, None)
            yield sse.done(message_id)
            return

        # LLM 流式生成
        yield sse.step("thinking", "生成回答中…")
        full_text = prefix
        if prefix:
            yield sse.token(prefix)

        if self.agentic is not None:
            # Agentic 模式：Agent 自主调用 search_manual 工具
            yield sse.step("tool_call", "Agent 自主检索模式")
            history = await self._history(session_id)
            async for text in self.agentic.stream_answer(question, history):
                full_text += text
                yield sse.token(text)
        else:
            # Generic 模式：固定注入检索上下文
            context = self.retriever.build_context(all_hits)
            system = ANSWER_SYSTEM_PROMPT.format(context=context)
            messages = [{"role": "user", "content": question}]
            async for text in self.agent.stream_answer(system, messages):
                full_text += text
                yield sse.token(text)

        # 来源卡片
        source_chunks: list[SourceChunk] = [
            self.retriever.to_source_chunk(h) for h in all_hits
        ]
        source_dicts = [s.model_dump() for s in source_chunks]
        await self._save_assistant(session_id, message_id, full_text, source_dicts)
        yield sse.sources(source_dicts)
        yield sse.done(message_id)

    # ---------- 辅助 ----------

    async def _get_clarify_state(self, session_id: str) -> ClarifyState | None:
        if self.sessions:
            state = await self.sessions.get_clarify_state(session_id)
            if state:
                return state
        return _clarify_states.get(session_id)

    async def _set_clarify_state(
        self, session_id: str, state: ClarifyState | None
    ) -> None:
        if state is None:
            _clarify_states.pop(session_id, None)
        else:
            _clarify_states[session_id] = state
        if self.sessions:
            await self.sessions.save_clarify_state(session_id, state)

    async def _save_assistant(
        self, session_id: str, message_id: str, content: str, sources: list | None
    ) -> None:
        if self.sessions:
            await self.sessions.save_message(
                message_id, session_id, "assistant", content, sources
            )

    async def _history(self, session_id: str) -> list[dict]:
        if not self.sessions:
            return []
        rows = await self.sessions.get_messages(session_id, limit=10)
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    @staticmethod
    def _missing_fields(vague_intents) -> list[str]:
        fields: set[str] = set()
        for i in vague_intents:
            if len(i.chunk_id_list) > 1:
                # 多候选歧义：需要用户指定模块
                fields.add("module")
            if not i.module:
                fields.add("module")
            if not i.description:
                fields.add("description")
            if not i.role:
                fields.add("role")
        # role 缺失通常不阻塞，优先级最低
        ordered = [f for f in ("module", "description", "role") if f in fields]
        return ordered or ["module"]
