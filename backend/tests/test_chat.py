"""/chat SSE 流水线测试：事件序列、引用完整、未找到兜底、clarify 流程（mock LLM/检索）。"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import get_settings
from app.intent.models import IntentResult
from app.rag.models import ChatRequest, HybridSearchOutcome, SearchResult, SourceChunk
from app.services.chat_service import ChatService


def make_intent(intent_type="precise", q="问题", **kw) -> IntentResult:
    return IntentResult(input=q, simple_input=q, intent_type=intent_type, **kw)


def make_hit(chunk_id="c1", doc="手册A", path="模块 > 功能", score=0.9) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content="内容", doc=doc, path=path, score=score)


@pytest.fixture
def service():
    settings = get_settings()
    retriever = MagicMock()
    hybrid_hit = make_hit()
    hybrid_hit.dense_score = 0.9
    hybrid_hit.fused_score = 0.02
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(
            mode="hybrid-rerank",
            dense_hits=[hybrid_hit],
            sparse_hits=[hybrid_hit],
            fused_hits=[hybrid_hit],
            final_hits=[hybrid_hit],
        )
    )
    retriever.build_context = lambda h: "上下文"
    retriever.to_source_chunk = lambda h: SourceChunk(
        chunk_id=h.chunk_id, doc=h.doc, path=h.path, score=h.score,
        content="原文", images=["/api/images/手册A/x.png"],
    )
    recognizer = MagicMock()
    recognizer.generate_clarify_question = AsyncMock(return_value="请问是哪个模块？")
    agent = MagicMock()

    async def fake_stream(system, messages):
        yield "回答内容"
        yield "来源：手册A > 模块 > 功能"

    agent.stream_answer = fake_stream
    svc = ChatService(settings, retriever, recognizer, agent)
    return svc, recognizer, retriever


async def collect_events(gen) -> list[tuple[str, dict]]:
    events = []
    async for raw in gen:
        lines = raw.strip().split("\n")
        event = lines[0].replace("event: ", "")
        data = json.loads(lines[1].replace("data: ", ""))
        events.append((event, data))
    return events


@pytest.mark.asyncio
async def test_precise_flow(service):
    svc, recognizer, _ = service
    from app.intent.models import IntentBatch

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="如何重置学生卡")))

    types = [e for e, _ in events]
    assert "step" in types and "token" in types and "sources" in types and "done" in types
    assert types.index("token") < types.index("sources") < types.index("done")

    src = next(d for e, d in events if e == "sources")["sources"][0]
    assert src["doc"] == "手册A"
    assert src["images"] == ["/api/images/手册A/x.png"]


@pytest.mark.asyncio
async def test_irrelevant_reject(service):
    svc, recognizer, _ = service
    from app.intent.models import IntentBatch

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent("irrelevant")], used_llm=False)
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="今天天气如何")))

    texts = "".join(d["text"] for e, d in events if e == "token")
    assert "无关" in texts
    assert not any(e == "sources" for e, _ in events)


@pytest.mark.asyncio
async def test_not_found(service):
    svc, recognizer, retriever = service
    from app.intent.models import IntentBatch

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(mode="dense")
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="不存在功能")))

    texts = "".join(d["text"] for e, d in events if e == "token")
    assert "手册中未找到" in texts


@pytest.mark.asyncio
async def test_clarify_flow(service):
    svc, recognizer, _ = service
    from app.intent.models import IntentBatch

    # 第一轮：模糊 → clarify 事件
    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(
            intents=[make_intent("vague", module="")],
            used_llm=False,
        )
    )
    events = await collect_events(
        svc.handle_chat(ChatRequest(session_id="s1", question="角色权限有哪些？"))
    )
    clarify_event = next((d for e, d in events if e == "clarify"), None)
    assert clarify_event is not None
    assert "module" in clarify_event["missing_fields"]
    assert not any(e == "token" for e, _ in events)

    # 第二轮：用户补充 → 合并后精确 → 正常回答
    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    events = await collect_events(
        svc.handle_chat(
            ChatRequest(session_id="s1", question="", clarify_answer="上体附中系统")
        )
    )
    assert any(e == "sources" for e, _ in events)


@pytest.mark.asyncio
async def test_clarify_max_rounds(service):
    svc, recognizer, _ = service
    from app.intent.models import IntentBatch

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(
            intents=[make_intent("vague")], used_llm=False
        )
    )
    sid = "s-max"
    # 填满澄清轮次
    for _ in range(3):
        await collect_events(
            svc.handle_chat(
                ChatRequest(session_id=sid, question="q", clarify_answer="仍模糊")
            )
        )
    # 超限后应直接回答而非继续澄清
    events = await collect_events(
        svc.handle_chat(
            ChatRequest(session_id=sid, question="q", clarify_answer="再补充")
        )
    )
    assert not any(e == "clarify" for e, _ in events)
    assert any(e == "token" for e, _ in events)


@pytest.mark.asyncio
async def test_steps_persisted(service):
    """思考过程步骤应随 assistant 消息持久化，供历史会话回看展示（含澄清/拒答之外的主流程）。"""
    svc, recognizer, _ = service
    from app.intent.models import IntentBatch

    sessions = MagicMock()
    sessions.ensure_session = AsyncMock()
    sessions.save_message = AsyncMock()
    sessions.log_intents = AsyncMock()
    sessions.log_retrievals = AsyncMock()
    sessions.log_llm_call = AsyncMock()
    sessions.get_clarify_state = AsyncMock(return_value=None)
    sessions.save_clarify_state = AsyncMock()
    sessions.get_messages = AsyncMock(return_value=[])
    svc.sessions = sessions

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    await collect_events(svc.handle_chat(ChatRequest(question="如何重置学生卡")))

    assistant_calls = [
        c for c in sessions.save_message.await_args_list if c.args[2] == "assistant"
    ]
    assert assistant_calls, "assistant 消息应被持久化"
    call = assistant_calls[-1]
    assert call.args[3]  # content 非空
    assert call.args[4]  # sources 同步持久化
    step_types = [s["type"] for s in call.kwargs["steps"]]
    assert "intent" in step_types
    assert "retrieve" in step_types
    assert "thinking" in step_types


@pytest.mark.asyncio
async def test_hybrid_step_detail_and_log_mode(service):
    """混合检索 step 事件应含 mode 与各路分数；retrieval_logs 分阶段落库。"""
    svc, recognizer, retriever = service
    from app.intent.models import IntentBatch

    hit = make_hit()
    hit.dense_score = 0.9
    hit.sparse_score = 4.2
    hit.fused_score = 0.0188
    hit.rerank_score = 0.87
    hit.sparse_rank = 1
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(
            mode="hybrid-rerank",
            dense_hits=[hit],
            sparse_hits=[hit],
            fused_hits=[hit],
            final_hits=[hit],
        )
    )

    sessions = MagicMock()
    sessions.ensure_session = AsyncMock()
    sessions.save_message = AsyncMock()
    sessions.log_intents = AsyncMock()
    sessions.log_retrievals = AsyncMock()
    sessions.log_llm_call = AsyncMock()
    sessions.get_clarify_state = AsyncMock(return_value=None)
    sessions.save_clarify_state = AsyncMock()
    sessions.get_messages = AsyncMock(return_value=[])
    svc.sessions = sessions

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="实验会议室预约")))

    retrieve_steps = [
        d for e, d in events if e == "step" and d.get("type") == "retrieve" and d.get("detail", {}).get("hits")
    ]
    assert retrieve_steps, "应存在含 hits 的 retrieve step"
    detail = retrieve_steps[-1]["detail"]
    assert detail["mode"] == "hybrid-rerank"
    h = detail["hits"][0]
    assert h["dense_score"] == 0.9 and h["sparse_score"] == 4.2
    assert h["rerank_score"] == 0.87 and h["sparse_rank"] == 1

    # 分阶段落库：dense/sparse/fused/final 四次调用，mode 与 stage 正确透传
    stage_calls = [
        (c.args[5] if len(c.args) > 5 else c.kwargs.get("stage"), c.args[4])
        for c in sessions.log_retrievals.await_args_list
    ]
    assert stage_calls == [
        ("dense", "hybrid-rerank"),
        ("sparse", "hybrid-rerank"),
        ("fused", "hybrid-rerank"),
        ("final", "hybrid-rerank"),
    ]


@pytest.mark.asyncio
async def test_dense_mode_logs_only_final_stage(service):
    """纯稠密模式（稀疏路降级）：仅落 final 一阶段，无 sparse/fused。"""
    svc, recognizer, retriever = service
    from app.intent.models import IntentBatch

    hit = make_hit()
    hit.dense_score = 0.9
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(
            mode="dense", dense_hits=[hit], final_hits=[hit]
        )
    )

    sessions = MagicMock()
    sessions.ensure_session = AsyncMock()
    sessions.save_message = AsyncMock()
    sessions.log_intents = AsyncMock()
    sessions.log_retrievals = AsyncMock()
    sessions.log_llm_call = AsyncMock()
    sessions.get_clarify_state = AsyncMock(return_value=None)
    sessions.save_clarify_state = AsyncMock()
    sessions.get_messages = AsyncMock(return_value=[])
    svc.sessions = sessions

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    await collect_events(svc.handle_chat(ChatRequest(question="重置学生卡")))

    stages = [
        c.args[5] if len(c.args) > 5 else c.kwargs.get("stage")
        for c in sessions.log_retrievals.await_args_list
    ]
    assert stages == ["dense", "final"]  # dense 阶段列表 + final 最终结果


@pytest.mark.asyncio
async def test_rerank_failure_stream_still_answers(service):
    """rerank 失败已在 retriever 内降级（融合序返回），chat 流不应产生 error 事件。"""
    svc, recognizer, retriever = service
    from app.intent.models import IntentBatch

    degraded = make_hit()
    degraded.dense_score = 0.9
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(
            mode="hybrid", dense_hits=[degraded], final_hits=[degraded]
        )
    )
    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="重置学生卡")))
    assert not any(e == "error" for e, _ in events)
    assert any(e == "sources" for e, _ in events)


@pytest.mark.asyncio
async def test_answer_flow_passes_history_to_llm(service):
    """Generic 模式生成回答时应把会话历史传给 LLM（BUG 回归）。"""
    svc, recognizer, retriever = service
    from app.intent.models import IntentBatch

    hit = make_hit()
    hit.dense_score = 0.9
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(mode="dense", dense_hits=[hit], final_hits=[hit])
    )

    sessions = MagicMock()
    sessions.ensure_session = AsyncMock()
    sessions.save_message = AsyncMock()
    sessions.log_intents = AsyncMock()
    sessions.log_retrievals = AsyncMock()
    sessions.log_llm_call = AsyncMock()
    sessions.get_clarify_state = AsyncMock(return_value=None)
    sessions.save_clarify_state = AsyncMock()
    # 历史含两条：一条历史 user + 一条历史 assistant（当前输入已在 handle_chat 内移除）
    sessions.get_messages = AsyncMock(
        return_value=[
            {"id": "m1", "role": "user", "content": "实验会议室怎么预约？", "sources": [], "steps": [], "created_at": "t1"},
            {"id": "m2", "role": "assistant", "content": "请选择模块", "sources": [], "steps": [], "created_at": "t2"},
            {"id": "m3", "role": "user", "content": "审批流程呢？", "sources": [], "steps": [], "created_at": "t3"},
        ]
    )
    svc.sessions = sessions

    captured_messages = {}

    async def fake_stream(system, messages):
        captured_messages["messages"] = messages
        yield "回答"

    svc.agent.stream_answer = fake_stream

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=False)
    )
    await collect_events(svc.handle_chat(ChatRequest(question="审批流程呢？")))

    msgs = captured_messages["messages"]
    # 历史应含前两轮（user + assistant），当前问题放在最后
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "实验会议室怎么预约？"
    assert msgs[-1]["content"] == "审批流程呢？"
