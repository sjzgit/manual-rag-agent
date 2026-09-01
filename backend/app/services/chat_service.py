"""对话流水线状态机：意图识别 → 澄清循环 → 逐子问题检索 → LLM 流式生成。"""
import asyncio
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
    MEMORY_ANSWER_SYSTEM_PROMPT,
    NEED_RETRIEVAL_MARKER,
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
        knowledge_service=None,
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
        self.knowledge_service = knowledge_service

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

            # 会话记忆直接作答：读取关联文档完整内容（doc_id → md，Redis 短期缓存），
            # 注入意图识别供其判断能否凭关联文档直接作答（memory_answer_docs）。
            # 开关关闭时不读取不注入，意图识别输出 memory_answer_docs 恒为空，后续自然走检索。
            memory_docs: list[dict] = []
            if self.sessions and self.settings.memory_direct_answer:
                memory_doc_ids = await self.sessions.get_memory_docs(session_id)
                if memory_doc_ids:
                    memory_docs = await self._load_memory_doc_contents(memory_doc_ids)

            # 1) 意图识别（结合会话上下文：指代/承接性提问根据历史补全模块）
            yield _step_payload(steps, "intent", "意图识别中…")
            history = await self._history(session_id)
            # 刚保存的当前输入不视为"历史"，移除避免自我参照
            if history and history[-1]["role"] == "user":
                history = history[:-1]
            batch = await self.recognizer.recognize(
                question, history=history, memory_docs=memory_docs
            )
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

            # 4) 全部精确 → 记忆直答（跳过检索，依据不足自动回退）或 检索 + 回答
            await self._set_clarify_state(session_id, None)
            # 按意图识别指出的可直答文档名，从已加载的关联文档中取对应内容作答
            answer_docs = (
                [d for d in memory_docs if d["doc_name"] in batch.memory_answer_docs]
                if memory_docs and batch.all_memory_sufficient
                else []
            )
            if answer_docs:
                async for chunk in self._answer_from_memory(
                    session_id, question, answer_docs, batch.precise_intents, req.doc,
                    message_id, steps, history=history,
                ):
                    yield chunk
            else:
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
        if self.agentic is not None:
            # Agentic 模式：Agent 自主调用 search_manual 工具
            yield _step_payload(steps, "thinking", "生成回答中…")
            full_text = prefix
            reasoning_text = ""
            if prefix:
                yield sse.token(prefix)
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
        else:
            # Generic 模式：固定注入检索上下文 + 会话历史
            context = self.retriever.build_context(all_hits)
            source_chunks = [self.retriever.to_source_chunk(h) for h in all_hits]
            async for chunk in self._generate_generic(
                session_id, question, message_id, steps, history, context, source_chunks,
                prefix=prefix,
            ):
                yield chunk

        # 会话记忆：检索作答后关联命中文档（doc_name → doc_id，上限 3）
        await self._remember_docs(session_id, all_hits)

    # ---------- 生成（generic 固定上下文） ----------

    async def _generate_generic(
        self,
        session_id: str,
        question: str,
        message_id: str,
        steps: list[dict],
        history: list[dict] | None,
        context: str,
        source_chunks: list[SourceChunk],
        prefix: str = "",
    ) -> AsyncGenerator[str]:
        """Generic 模式：固定注入检索上下文 + 会话历史，流式生成并落库。"""
        yield _step_payload(steps, "thinking", "生成回答中…")
        full_text = prefix
        reasoning_text = ""
        if prefix:
            yield sse.token(prefix)
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
        async for event in self._finalize_answer(
            session_id, message_id, full_text, source_chunks, steps,
            reasoning_text=reasoning_text, system=system, messages=messages,
            context_len=len(context),
        ):
            yield event

    async def _finalize_answer(
        self,
        session_id: str,
        message_id: str,
        full_text: str,
        source_chunks: list[SourceChunk],
        steps: list[dict],
        reasoning_text: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        context_len: int = 0,
    ) -> AsyncGenerator[str]:
        """生成收尾（检索/记忆直答共用）：日志、LLM 调用落库、来源卡片与 assistant 消息持久化。"""
        logger.info(
            "answer_generated",
            session_id=session_id,
            message_id=message_id,
            context_len=context_len,
            answer_len=len(full_text),
        )
        if not full_text.strip():
            logger.warning(
                "answer_empty",
                session_id=session_id,
                message_id=message_id,
                context_len=context_len,
            )
        if self.sessions and system:
            await self.sessions.log_llm_call(
                session_id, message_id, "answer", system, messages or [], full_text
            )
        source_dicts = [s.model_dump() for s in source_chunks]
        await self._save_assistant(
            session_id, message_id, full_text, source_dicts, steps, reasoning=reasoning_text
        )
        yield sse.sources(source_dicts)
        yield sse.done(message_id)

    async def _answer_from_memory(
        self,
        session_id: str,
        question: str,
        memory_docs: list[dict],
        intents,
        doc_filter: str | None,
        message_id: str,
        steps: list[dict],
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str]:
        """会话记忆直答：跳过检索，用已关联文档完整内容作答。

        生成时若 LLM 判定依据不足（输出 NEED_RETRIEVAL 标记），回退检索流程重新作答。
        标记检测用首段缓冲：内容仍是标记前缀时暂不下发，一旦发散立即放行恢复流式输出，
        保证正常直答的首 token 延迟只有标记长度的量级。
        """
        yield _step_payload(
            steps,
            "retrieve",
            "命中会话记忆，跳过检索",
            {"memory_docs": [d["doc_name"] for d in memory_docs]},
        )
        context = self._build_memory_context(memory_docs)
        system = self._prompt("memory_answer_system", MEMORY_ANSWER_SYSTEM_PROMPT).format(
            context=context
        )
        messages = [*(history or []), {"role": "user", "content": question}]
        yield _step_payload(steps, "thinking", "生成回答中…")

        full_text = ""
        reasoning_text = ""
        pending = ""  # 标记检测缓冲
        marker_possible = True
        need_retrieval = False
        stream = self.agent.stream_answer(system, messages)
        try:
            async for kind, text in stream:
                if kind == "reasoning":
                    reasoning_text += text
                    yield sse.reasoning(text)
                    continue
                if marker_possible:
                    probe = (pending + text).lstrip()
                    if probe.startswith(NEED_RETRIEVAL_MARKER):
                        need_retrieval = True
                        break  # 依据不足 → 中止直答，转检索
                    if NEED_RETRIEVAL_MARKER.startswith(probe):
                        pending += text
                        continue
                    # 与标记发散：放行缓冲内容，恢复流式输出
                    marker_possible = False
                    if pending.strip():
                        full_text += pending
                        yield sse.token(pending)
                        pending = ""
                full_text += text
                yield sse.token(text)
        finally:
            # 中途 break 也要关闭底层流（httpx 连接及时归还）
            await stream.aclose()

        if need_retrieval:
            yield _step_payload(steps, "retrieve", "记忆内容不足以回答，转检索流程")
            async for chunk in self._answer_flow(
                session_id, question, intents, doc_filter, message_id, steps=steps,
                history=history,
            ):
                yield chunk
            return

        # 流结束时缓冲恰为完整标记（未触发 break 的场景）→ 同样回退
        if pending.lstrip() == NEED_RETRIEVAL_MARKER:
            yield _step_payload(steps, "retrieve", "记忆内容不足以回答，转检索流程")
            async for chunk in self._answer_flow(
                session_id, question, intents, doc_filter, message_id, steps=steps,
                history=history,
            ):
                yield chunk
            return

        full_text += pending
        if pending.strip():
            yield sse.token(pending)

        async for event in self._finalize_answer(
            session_id, message_id, full_text, self._memory_source_chunks(memory_docs), steps,
            reasoning_text=reasoning_text, system=system, messages=messages,
            context_len=len(context),
        ):
            yield event

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

    # ---------- 会话记忆 ----------

    async def _load_memory_doc_contents(self, memory_doc_ids: list[str]) -> list[dict]:
        """按 doc_id 取 md 内容（Redis 短期缓存优先，未命中读文件并回写）。
        文档已失效（被删）则跳过，避免把陈旧内容注入上下文。"""
        docs: list[dict] = []
        for doc_id in memory_doc_ids:
            entry = None
            if self.redis:
                entry = await self.redis.get_doc_content(doc_id)
            if entry is None and self.knowledge_service:
                doc = await self.knowledge_service.get_document(doc_id)
                if doc:
                    md_path = await self.knowledge_service.get_document_path(doc_id, "md")
                    if md_path and md_path.exists():
                        content = await asyncio.to_thread(
                            md_path.read_text, encoding="utf-8"
                        )
                        entry = {"doc_name": doc["doc_name"], "content": content}
                        if self.redis:
                            await self.redis.set_doc_content(
                                doc_id, entry["doc_name"], content
                            )
            if entry:
                docs.append({"doc_id": doc_id, **entry})
        return docs

    async def _remember_docs(
        self, session_id: str, all_hits: list[SearchResult]
    ) -> None:
        """检索作答后，把最终命中来源涉及的文档 id 合并进会话记忆（上限 3）。"""
        if not self.sessions or not self.knowledge_service:
            return
        doc_ids: list[str] = []
        for doc_name in {h.doc for h in all_hits if h.doc}:
            doc_id = await self.knowledge_service.get_document_id_by_name(doc_name)
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
        if doc_ids:
            await self.sessions.save_memory_docs(session_id, doc_ids)

    @staticmethod
    def _build_memory_context(memory_docs: list[dict]) -> str:
        parts = [f"## 文档：{d['doc_name']}\n\n{d['content']}" for d in memory_docs]
        return "\n\n".join(parts)

    @staticmethod
    def _memory_source_chunks(memory_docs: list[dict]) -> list[SourceChunk]:
        return [
            SourceChunk(
                chunk_id=d["doc_id"],
                doc=d["doc_name"],
                path=d["doc_name"],
                score=1.0,
                content="",
                images=[],
            )
            for d in memory_docs
        ]

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
