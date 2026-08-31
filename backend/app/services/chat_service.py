"""对话流水线状态机：意图识别 → 澄清循环 → 逐子问题检索 → LLM 流式生成。"""
import uuid
from collections.abc import AsyncGenerator

from ..agent.manual_agent import ManualAnswerAgent
from ..core.config import Settings
from ..core.logging import get_logger
from ..intent.models import ClarifyState
from ..intent.recognizer import IntentRecognizer
from ..prompts.manual import (
    AGENTIC_SYSTEM_PROMPT,
    ANSWER_SYSTEM_PROMPT,
    CLARIFY_EXHAUSTED_PREFIX,
    IRRELEVANT_REPLY,
    NOT_FOUND_REPLY,
)
from ..rag.models import ChatRequest, SearchResult, SourceChunk
from ..rag.retriever import ManualRetriever
from ..utils import sse
from .session_service import SessionService

logger = get_logger(__name__)

# 数据库不可用时的内存澄清状态兜底
_clarify_states: dict[str, ClarifyState] = {}


def _step_payload(
    steps: list[dict], step_type: str, title: str, detail: dict | None = None
) -> str:
    """记录思考过程步骤，并生成对应 SSE step 事件（供流式推送 + 历史会话回看）。"""
    steps.append({"type": step_type, "title": title, "detail": detail or {}})
    return sse.step(step_type, title, detail)


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
        prompt_service=None,
        reranker=None,
    ):
        self.settings = settings
        self.retriever = retriever
        self.recognizer = recognizer
        self.agent = agent
        self.sessions = session_service
        self.redis = redis
        self.agentic = agentic
        self.prompt_service = prompt_service
        self.reranker = reranker

    def _prompt(self, key: str, default: str) -> str:
        """从提示词服务取模板，未注入时回退代码常量。"""
        return self.prompt_service.get(key) if self.prompt_service else default

    # ---------- 主流水线 ----------

    async def handle_chat(self, req: ChatRequest) -> AsyncGenerator[str]:
        session_id = req.session_id or uuid.uuid4().hex
        message_id = uuid.uuid4().hex
        # SSE 头事件：告知前端本次会话/消息 id
        yield sse.sse_event("meta", {"session_id": session_id, "message_id": message_id})

        try:
            # 思考过程步骤收集（随 assistant 消息持久化，供历史会话回看）
            steps: list[dict] = []
            # 限流（Redis 不可用时直通）
            if self.redis:
                from .cache import RateLimitExceeded

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
                # 用户消息 id 采用「assistant 消息 id + _u」确定性后缀，便于前端按「一组问答」分组删除
                await self.sessions.save_message(
                    f"{message_id}_u", session_id, "user", req.clarify_answer or req.question
                )

            # 1) 意图识别（结合会话上下文：指代/承接性提问根据历史补全模块）
            yield _step_payload(steps, "intent", "意图识别中…")
            history = await self._history(session_id)
            # 刚保存的当前输入不视为"历史"，移除避免自我参照
            if history and history[-1]["role"] == "user":
                history = history[:-1]
            batch = await self.recognizer.recognize(question, history=history)
            yield _step_payload(
                steps,
                "intent",
                "意图识别完成",
                {
                    "used_llm": batch.used_llm,
                    "intents": [i.model_dump() for i in batch.intents],
                },
            )
            if self.sessions:
                await self.sessions.log_intents(session_id, message_id, batch)
                for call in batch.llm_calls:
                    await self.sessions.log_llm_call(
                        session_id,
                        message_id,
                        "intent",
                        call["system_prompt"],
                        call["messages"],
                        call["output"],
                    )

            # 2) 不相关 → 拒答
            if batch.has_irrelevant:
                reply = self._prompt("irrelevant_reply", IRRELEVANT_REPLY)
                yield sse.token(reply)
                await self._set_clarify_state(session_id, None)
                await self._save_assistant(session_id, message_id, reply, None, steps)
                yield sse.done(message_id)
                return

            # 3) 模糊 → 澄清
            if not batch.all_precise:
                vague = batch.vague_intents
                missing = self._missing_fields(vague)

                if state.round >= self.settings.max_clarify_rounds:
                    # 超限：按最相关切片直接回答
                    yield _step_payload(
                        steps, "intent", f"澄清超过 {state.round} 轮，按最相关切片回答"
                    )
                    await self._set_clarify_state(session_id, None)
                    async for chunk in self._answer_flow(
                        session_id, question, vague, req.doc, message_id, steps=steps,
                        prefix=self._prompt("clarify_exhausted_prefix", CLARIFY_EXHAUSTED_PREFIX),
                        history=history,
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
                await self._save_assistant(session_id, message_id, question_text, None, steps)
                yield sse.done(message_id)
                return

            # 4) 全部精确 → 检索 + 回答
            await self._set_clarify_state(session_id, None)
            async for chunk in self._answer_flow(
                session_id, question, batch.precise_intents, req.doc, message_id, steps=steps,
                history=history,
            ):
                yield chunk

        except Exception as e:
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
        steps: list[dict],
        prefix: str = "",
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str]:
        # 逐子问题混合检索 + 重排
        yield _step_payload(steps, "retrieve", "混合检索中…")
        all_hits: list[SearchResult] = []
        seen: set[str] = set()
        for intent in intents:
            q = intent.simple_input or intent.input
            outcome = await self.retriever.search_and_rerank(
                q, doc=doc_filter, reranker=self.reranker
            )
            hits, mode = outcome.final_hits, outcome.mode
            title = "混合检索+重排" if mode == "hybrid-rerank" else (
                "混合检索" if mode == "hybrid" else "向量检索"
            )
            yield _step_payload(
                steps,
                "retrieve",
                f"{title}「{q}」",
                {
                    "sub_question": q,
                    "mode": mode,
                    "hits": [self._hit_summary(h) for h in hits],
                },
            )
            if self.sessions:
                # 分阶段落库：稠密路 / 稀疏路 / RRF 融合 / 最终结果（各为独立列表）
                await self.sessions.log_retrievals(
                    session_id, message_id, q, outcome.dense_hits, mode, stage="dense"
                )
                if outcome.sparse_hits:
                    await self.sessions.log_retrievals(
                        session_id, message_id, q, outcome.sparse_hits, mode, stage="sparse"
                    )
                if outcome.fused_hits:
                    await self.sessions.log_retrievals(
                        session_id, message_id, q, outcome.fused_hits, mode, stage="fused"
                    )
                await self.sessions.log_retrievals(
                    session_id, message_id, q, outcome.final_hits, mode, stage="final"
                )
            for h in hits:
                if h.chunk_id not in seen:
                    seen.add(h.chunk_id)
                    all_hits.append(h)

        # 无命中 → 未找到
        if not all_hits:
            logger.info(
                "retrieval_no_hits",
                session_id=session_id,
                message_id=message_id,
                question=question,
            )
            not_found = self._prompt("not_found_reply", NOT_FOUND_REPLY)
            yield sse.token(not_found)
            await self._save_assistant(session_id, message_id, not_found, None, steps)
            yield sse.done(message_id)
            return

        logger.info(
            "retrieval_hits",
            session_id=session_id,
            message_id=message_id,
            question=question,
            num_hits=len(all_hits),
        )

        # LLM 流式生成
        yield _step_payload(steps, "thinking", "生成回答中…")
        full_text = prefix
        reasoning_text = ""
        if prefix:
            yield sse.token(prefix)

        if self.agentic is not None:
            # Agentic 模式：Agent 自主调用 search_manual 工具
            yield _step_payload(steps, "tool_call", "Agent 自主检索模式")
            async for text in self.agentic.stream_answer(question, history or []):
                full_text += text
                yield sse.token(text)
            if self.sessions:
                # Agent 内部多轮 tool call 不逐次展开，按单次 answer 调用记录
                agentic_system = self._prompt("agentic_system", AGENTIC_SYSTEM_PROMPT)
                await self.sessions.log_llm_call(
                    session_id,
                    message_id,
                    "answer",
                    agentic_system,
                    [{"role": "user", "content": question}],
                    full_text,
                )
        else:
            # Generic 模式：固定注入检索上下文 + 会话历史
            context = self.retriever.build_context(all_hits)
            system = self._prompt("answer_system", ANSWER_SYSTEM_PROMPT).format(
                context=context
            )
            messages = [*(history or []), {"role": "user", "content": question}]
            async for kind, text in self.agent.stream_answer(system, messages):
                if kind == "reasoning":
                    reasoning_text += text
                    yield sse.reasoning(text)
                else:
                    full_text += text
                    yield sse.token(text)
            logger.info(
                "answer_generated",
                session_id=session_id,
                message_id=message_id,
                context_len=len(context),
                answer_len=len(full_text),
            )
            if not full_text.strip():
                logger.warning(
                    "answer_empty",
                    session_id=session_id,
                    message_id=message_id,
                    context_len=len(context),
                )
            if self.sessions:
                await self.sessions.log_llm_call(
                    session_id, message_id, "answer", system, messages, full_text
                )

        # 来源卡片
        source_chunks: list[SourceChunk] = [
            self.retriever.to_source_chunk(h) for h in all_hits
        ]
        source_dicts = [s.model_dump() for s in source_chunks]
        await self._save_assistant(
            session_id, message_id, full_text, source_dicts, steps, reasoning=reasoning_text
        )
        yield sse.sources(source_dicts)
        yield sse.done(message_id)

    # ---------- 辅助 ----------

    @staticmethod
    def _hit_summary(h: SearchResult) -> dict:
        """命中结果 → step 事件 detail 摘要（分数 round 4 位，可 null）。"""
        return {
            "chunk_id": h.chunk_id,
            "doc": h.doc,
            "path": h.path,
            "score": round(h.score, 4),
            "dense_score": round(h.dense_score, 4) if h.dense_score is not None else None,
            "sparse_score": (
                round(h.sparse_score, 4) if h.sparse_score is not None else None
            ),
            "fused_score": round(h.fused_score, 4) if h.fused_score is not None else None,
            "rerank_score": (
                round(h.rerank_score, 4) if h.rerank_score is not None else None
            ),
            "sparse_rank": h.sparse_rank,
        }

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
        self,
        session_id: str,
        message_id: str,
        content: str,
        sources: list | None,
        steps: list | None = None,
        reasoning: str | None = None,
    ) -> None:
        if self.sessions:
            await self.sessions.save_message(
                message_id, session_id, "assistant", content, sources,
                steps=steps, reasoning=reasoning,
            )

    async def _history(self, session_id: str) -> list[dict]:
        if not self.sessions:
            return []
        rows = await self.sessions.get_messages(session_id)
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    @staticmethod
    def _missing_fields(vague_intents) -> list[str]:
        """汇总澄清需补充的字段：优先意图识别 LLM 给出的 missing_fields，
        其余按要素空缺推断；role 非必填，仅在 LLM 明确指出时追问。"""
        fields: set[str] = set()
        for i in vague_intents:
            fields.update(i.missing_fields)
            if not i.module:
                fields.add("module")
            if not i.description:
                fields.add("description")
        ordered = [f for f in ("module", "description", "role") if f in fields]
        return ordered or ["module"]
