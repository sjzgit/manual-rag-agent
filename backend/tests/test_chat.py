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
        yield ("content", "回答内容")
        yield ("content", "来源：手册A > 模块 > 功能")

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
    """【主流程-精确意图】用户提出精确问题时的完整 happy path。

    场景：意图识别结果为 precise（精确意图）。
    前置：mock 检索返回 1 条命中（含图片）、mock LLM 流式返回回答。
    期望：
      1. SSE 事件序列完整：step（步骤进度）→ token（流式回答）→ sources（引用来源）→ done（结束）；
      2. 事件顺序正确：token 全部发完后才发 sources，最后 done；
      3. sources 里透出命中的文档名与图片路径，供前端展示引用。
    """
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
    """【拒答-无关问题】与手册无关的提问应礼貌拒答，不走检索。

    场景：意图识别结果为 irrelevant（与操作手册无关，如闲聊/天气）。
    前置：mock 意图为 irrelevant，其余依赖同主流程。
    期望：
      1. 仍通过 token 事件流式输出含"无关"字样的拒答话术；
      2. 不产生 sources 事件（没有检索就没有引用，防止编造来源）。
    """
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
    """【兜底-检索未命中】手册中查不到内容时应给出"未找到"提示而非报错。

    场景：意图为精确提问，但混合检索最终结果为空（final_hits 无内容）。
    前置：mock search_and_rerank 返回空的 HybridSearchOutcome。
    期望：流式回答中包含"手册中未找到"类兜底话术，流程正常 done 不抛错。
    """
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
    """【澄清-两轮完整流程】问题模糊时先反问，用户补充后合并回答。

    场景：第一轮问题缺少 module（模块）字段被判为 vague（模糊）。
    前置：第一轮 mock 意图 vague 且 module 为空；第二轮 mock 意图为精确。
    期望：
      第一轮——发出 clarify 事件（含 missing_fields=["module"] 指明缺什么），
              不发 token（还没到回答阶段）；
      第二轮——用户带 clarify_answer 补充"上体附中系统"后，
              与原问题合并重新识别为精确意图，正常走完回答并给出 sources。
    """
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
    """【澄清-轮次上限】连续多轮仍模糊时不再无限反问，强制回答。

    场景：同一 session 中用户反复补充信息但意图始终是 vague。
    前置：mock 意图恒为 vague；先用 3 轮填满澄清轮次上限，再发起第 4 轮。
    期望：达到上限后不再发 clarify 事件，直接走回答流程输出 token。
    （防止死循环式追问，保证用户最终能得到答案。）
    """
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
    sessions.get_memory_docs = AsyncMock(return_value=[])
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
    sessions.get_memory_docs = AsyncMock(return_value=[])
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
    sessions.get_memory_docs = AsyncMock(return_value=[])
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
    sessions.get_memory_docs = AsyncMock(return_value=[])
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
        yield ("content", "回答")

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


@pytest.mark.asyncio
async def test_memory_direct_answer_skips_retrieval(service):
    """会话记忆命中且意图输出 memory_answer_docs 时，应跳过检索、用记忆内容作答。"""
    svc, recognizer, retriever = service
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
    sessions.get_memory_docs = AsyncMock(return_value=["doc1"])
    sessions.save_memory_docs = AsyncMock()
    svc.sessions = sessions

    redis = MagicMock()
    redis.check_rate_limit = AsyncMock()
    redis.get_doc_content = AsyncMock(
        return_value={"doc_name": "手册A", "content": "这是手册A的完整内容"}
    )
    svc.redis = redis

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(
            intents=[make_intent(memory_answer_docs=["手册A"])], used_llm=True
        )
    )
    events = await collect_events(svc.handle_chat(ChatRequest(question="如何重置学生卡")))

    retriever.search_and_rerank.assert_not_called()  # 跳过检索
    assert not sessions.log_retrievals.await_args_list
    assert any(e == "sources" for e, _ in events)
    assert any(e == "done" for e, _ in events)
    # 来源卡片应为文档级条目（chunk_id=doc_id）
    src = next(d for e, d in events if e == "sources")["sources"][0]
    assert src["doc"] == "手册A" and src["chunk_id"] == "doc1"


@pytest.mark.asyncio
async def test_memory_answer_marker_falls_back_to_retrieval(service):
    """记忆直答时 LLM 判定依据不足（输出 NEED_RETRIEVAL 标记），应回退检索流程重新作答。"""
    svc, recognizer, retriever = service
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
    sessions.get_memory_docs = AsyncMock(return_value=["doc1"])
    sessions.save_memory_docs = AsyncMock()
    svc.sessions = sessions

    redis = MagicMock()
    redis.check_rate_limit = AsyncMock()
    redis.get_doc_content = AsyncMock(
        return_value={"doc_name": "手册A", "content": "这是手册A的完整内容"}
    )
    svc.redis = redis

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(
            intents=[make_intent(memory_answer_docs=["手册A"])], used_llm=True
        )
    )

    calls = {"n": 0}

    async def fake_stream(system, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一次（记忆直答）：输出回退标记
            yield ("content", "[[NEED_RETRIEVAL]]")
        else:
            # 第二次（检索后）：正常回答
            yield ("content", "检索后的回答")

    svc.agent.stream_answer = fake_stream

    events = await collect_events(svc.handle_chat(ChatRequest(question="如何重置学生卡")))

    # 应回退检索并重新生成
    retriever.search_and_rerank.assert_called()
    texts = "".join(d["text"] for e, d in events if e == "token")
    assert "[[NEED_RETRIEVAL]]" not in texts  # 标记不透出给用户
    assert "检索后的回答" in texts
    assert any(e == "sources" for e, _ in events)


@pytest.mark.asyncio
async def test_memory_insufficient_falls_back_to_retrieval(service):
    """有记忆文档但意图未输出 memory_answer_docs 时，应回退检索。"""
    svc, recognizer, retriever = service
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
    sessions.get_memory_docs = AsyncMock(return_value=["doc1"])
    sessions.save_memory_docs = AsyncMock()
    svc.sessions = sessions

    redis = MagicMock()
    redis.check_rate_limit = AsyncMock()
    redis.get_doc_content = AsyncMock(
        return_value={"doc_name": "手册A", "content": "内容"}
    )
    svc.redis = redis

    recognizer.recognize = AsyncMock(
        return_value=IntentBatch(intents=[make_intent()], used_llm=True)
    )
    await collect_events(svc.handle_chat(ChatRequest(question="如何重置学生卡")))

    retriever.search_and_rerank.assert_called()  # 回退检索
