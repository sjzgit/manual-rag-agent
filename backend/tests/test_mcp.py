"""MCP 工具层与协议层测试：SSE 聚合、检索/文档工具、鉴权中间件（mock 服务层，无外部依赖）。"""
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.mcp import tools
from app.mcp.models import DocsPayload
from app.mcp.server import ApiKeyMiddleware, build_mcp_server
from app.mcp.tools import collect_ask
from app.rag.models import HybridSearchOutcome, SearchResult, SourceChunk
from app.utils import sse
from mcp.server.fastmcp.exceptions import ToolError


def make_services(chat_service=None, retriever=None, reranker=None, settings=None) -> Any:
    """组装 fake Services（仅含工具层用到的属性），不触发 init_services。

    返回 Any：测试仅提供工具层用到的属性子集，非完整 Services。
    """
    return SimpleNamespace(
        chat_service=chat_service or MagicMock(),
        retriever=retriever or MagicMock(),
        reranker=reranker or MagicMock(),
        settings=settings or SimpleNamespace(mcp_public_base_url=""),
    )


def make_hit(chunk_id="c1", doc="手册A", path="模块 > 功能", score=0.9) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, doc=doc, path=path, score=score, content="内容")


async def make_gen(*items: str):
    for it in items:
        yield it


def make_chat_service(*events: str) -> MagicMock:
    """handle_chat 返回按序产出 events 的 async generator 的 mock。"""
    svc = MagicMock()
    svc.handle_chat = MagicMock(side_effect=lambda req: make_gen(*events))
    return svc


def make_retriever_with_hit() -> MagicMock:
    retriever = MagicMock()
    hit = make_hit(score=0.85)
    hit.dense_score = 0.85
    hit.fused_score = 0.02
    retriever.search_and_rerank = AsyncMock(
        return_value=HybridSearchOutcome(
            mode="hybrid-rerank",
            dense_hits=[hit],
            sparse_hits=[hit],
            fused_hits=[hit],
            final_hits=[hit],
        )
    )
    retriever.to_source_chunk = lambda h: SourceChunk(
        chunk_id=h.chunk_id,
        doc=h.doc,
        path=h.path,
        score=h.score,
        content="原文",
        images=["/api/images/手册A/x.png"],
    )
    return retriever


# ---- SSE 聚合器 ----


async def test_collect_ask_aggregates_tokens_and_sources():
    events = make_gen(
        sse.sse_event("meta", {"session_id": "s1", "message_id": "m1"}),
        sse.token("回答"),
        sse.token("内容"),
        sse.sse_event("sources", {"sources": [
            {"chunk_id": "c1", "doc": "手册A", "path": "模块 > 功能", "score": 0.9,
             "content": "原文", "images": []},
        ]}),
        sse.done("m1"),
    )
    result = await collect_ask(events)
    assert result.status == "answered"
    assert result.answer == "回答内容"
    assert result.session_id == "s1"
    assert result.message_id == "m1"
    assert len(result.sources) == 1
    assert result.sources[0].doc == "手册A"
    assert result.clarify_question is None


async def test_collect_ask_clarify_status():
    events = make_gen(
        sse.sse_event("meta", {"session_id": "s1", "message_id": "m1"}),
        sse.clarify("请问是哪个模块？", ["module"], [{"input": "问题"}]),
    )
    result = await collect_ask(events)
    assert result.status == "clarify"
    assert result.clarify_question == "请问是哪个模块？"
    assert result.missing_fields == ["module"]
    assert result.answer == ""
    assert result.session_id == "s1"


async def test_collect_ask_error_event_raises_tool_error():
    events = make_gen(
        sse.sse_event("meta", {"test_collect_ask_error_placeholder": False}),
        sse.error("RATE_LIMITED", "请求过于频繁"),
    )
    with pytest.raises(ToolError, match="RATE_LIMITED"):
        await collect_ask(events)


# ---- 工具层（mock 服务层） ----


async def test_manual_ask_delegates_to_chat_service():
    chat_service = make_chat_service(
        sse.sse_event("meta", {"session_id": "s9", "message_id": "m9"}),
        sse.token("答"),
        sse.done("m9"),
    )
    services = make_services(chat_service=chat_service)
    result = await tools.manual_ask(services, "问题", session_id="s0", clarify_answer="补充")
    # ChatRequest 参数透传断言
    req = chat_service.handle_chat.call_args[0][0]
    assert req.session_id == "s0"
    assert req.question == "问题"
    assert req.clarify_answer == "补充"
    assert result.session_id == "s9"
    assert result.answer == "答"
    assert result.status == "answered"


async def test_manual_search_uses_reranker_and_top_n():
    retriever = make_retriever_with_hit()
    settings = SimpleNamespace(mcp_public_base_url="http://10.0.0.5:8001")
    services = make_services(retriever=retriever, settings=settings)
    payload = await tools.manual_search(services, "重置学生卡", top_n=3)
    # reranker 单例与 top_n / doc 透传断言
    _, kwargs = retriever.search_and_rerank.call_args
    assert kwargs.get("reranker") is services.reranker
    assert kwargs.get("top_n") == 3
    assert kwargs.get("doc") is None
    assert payload.mode == "hybrid-rerank"
    assert payload.total == 1
    assert payload.results[0].images[0].startswith("http://10.0.0.5:8001/api/images/")
    assert payload.results[0].doc == "手册A"


async def test_manual_search_without_base_url_keeps_relative():
    retriever = make_retriever_with_hit()
    services = make_services(retriever=retriever)
    payload = await tools.manual_search(services, "重置学生卡")
    assert payload.results[0].images == ["/api/images/手册A/x.png"]
    assert "/api/images/" in payload.results[0].content or payload.results[0].content == "原文"


async def test_manual_docs():
    retriever = MagicMock()
    retriever.list_docs = MagicMock(
        return_value=[
            {"doc": "手册A", "parents": 12},
            {"doc": "手册B", "parents": 3},
        ]
    )
    services = make_services(retriever=retriever)
    payload = await tools.manual_docs(services)
    assert isinstance(payload, DocsPayload)
    assert payload.total == 2
    assert payload.docs[0].doc == "手册A"
    assert payload.docs[0].parents == 12


# ---- 协议层（mcp SDK 内存传输） ----


async def test_call_tool_over_mcp_protocol():
    from app.core.config import get_settings

    retriever = make_retriever_with_hit()
    services = make_services(retriever=retriever, settings=get_settings())
    mcp = build_mcp_server(services)
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(mcp._mcp_server) as session:  # pyright: ignore[reportPrivateUsage]
        res = await session.call_tool("manual_search", {"question": "重置学生卡"})
        assert not res.isError
        assert res.structuredContent is not None
        assert res.structuredContent["total"] == 1
        assert res.structuredContent["results"][0]["doc"] == "手册A"


# ---- 鉴权中间件 ----


class FakeSend:
    def __init__(self):
        self.messages: list = []

    async def __call__(self, message):
        self.messages.append(message)


class FakeReceive:
    async def __call__(self):
        return {"type": "http.disconnect"}


async def test_api_key_middleware_disabled_when_empty():
    """mcp_api_key 为空时直接放行（不校验）。"""
    inner_called = []

    async def app(scope, receive, send):
        inner_called.append(True)

    mw = ApiKeyMiddleware(app, "")
    await mw({"type": "http", "headers": []}, FakeReceive(), FakeSend())
    assert inner_called == [True]


async def test_api_key_middleware_accepts_and_rejects():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    send = FakeSend()
    mw = ApiKeyMiddleware(app, "secret")

    # 正确 key（X-API-Key 头）放行
    ok_headers = [(b"x-api-key", b"secret")]
    await mw({"type": "http", "headers": ok_headers}, FakeReceive(), send)
    assert send.messages[0]["status"] == 200

    # 正确 key（Authorization: Bearer 头）放行
    send2 = FakeSend()
    bearer_headers = [(b"authorization", b"Bearer secret")]
    await mw({"type": "http", "headers": bearer_headers}, FakeReceive(), send2)
    assert send2.messages[0]["status"] == 200

    # 错误 key → 401 JSON 短路
    send3 = FakeSend()
    bad_headers = [(b"x-api-key", b"wrong")]
    await mw({"type": "http", "headers": bad_headers}, FakeReceive(), send3)
    assert send3.messages[0]["status"] == 401

    # 缺失 key → 401
    send4 = FakeSend()
    await mw({"type": "http", "headers": []}, FakeReceive(), send4)
    assert send4.messages[0]["status"] == 401
