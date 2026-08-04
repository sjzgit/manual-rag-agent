"""/chat SSE 流水线测试：事件序列、引用完整、未找到兜底、clarify 流程（mock LLM/检索）。"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.intent.models import IntentResult
from app.rag.models import ChatRequest, SearchResult, SourceChunk
from app.services.chat_service import ChatService


def make_intent(intent_type="precise", q="问题", **kw) -> IntentResult:
    return IntentResult(input=q, simple_input=q, intent_type=intent_type, **kw)


def make_hit(chunk_id="c1", doc="手册A", path="模块 > 功能", score=0.9) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content="内容", doc=doc, path=path, score=score)


@pytest.fixture
def service():
    settings = get_settings()
    retriever = MagicMock()
    retriever.search = AsyncMock(return_value=[make_hit()])
    retriever.filter_by_threshold = lambda h: h
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
    retriever.search = AsyncMock(return_value=[])
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
            intents=[make_intent("vague", module="", chunk_id_list=["a", "b"])],
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
            intents=[make_intent("vague", chunk_id_list=["a"])], used_llm=False
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
