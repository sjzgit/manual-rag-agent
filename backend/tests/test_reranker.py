"""SiliconFlow Reranker 单元测试（httpx.MockTransport，不发真实请求）。"""
import json

import httpx
import pytest
from app.core.config import Settings
from app.rag.reranker import RerankError, SiliconFlowReranker


def make_settings(**kw) -> Settings:
    base = {
        "rerank_api_key": "sk-test",
        "rerank_base_url": "https://api.siliconflow.cn/v1",
        "rerank_model": "Qwen/Qwen3-Reranker-8B",
        "rerank_timeout": 5.0,
    }
    base.update(kw)
    return Settings(**base)


def make_reranker(handler, settings=None) -> SiliconFlowReranker:
    transport = httpx.MockTransport(handler)
    return SiliconFlowReranker(settings or make_settings(), transport=transport)


@pytest.mark.asyncio
async def test_rerank_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["model"] == "Qwen/Qwen3-Reranker-8B"
        assert body["documents"] == ["文档一", "文档二"]
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={
            "results": [
                {"index": 1, "relevance_score": 0.98},
                {"index": 0, "relevance_score": 0.42},
            ]
        })

    r = make_reranker(handler)
    await r.startup()
    pairs = await r.rerank("查询", ["文档一", "文档二"])
    await r.shutdown()
    assert pairs == [(1, 0.98), (0, 0.42)]


@pytest.mark.asyncio
async def test_rerank_top_n_passthrough():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["top_n"] = json.loads(request.content.decode())["top_n"]
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    r = make_reranker(handler)
    await r.startup()
    await r.rerank("查询", ["a", "b", "c"], top_n=2)
    await r.shutdown()
    assert captured["top_n"] == 2


@pytest.mark.asyncio
async def test_rerank_non_2xx_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    r = make_reranker(handler)
    await r.startup()
    with pytest.raises(RerankError, match="非 2xx"):
        await r.rerank("查询", ["a"])
    await r.shutdown()


@pytest.mark.asyncio
async def test_rerank_malformed_response_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    r = make_reranker(handler)
    await r.startup()
    with pytest.raises(RerankError, match="响应结构异常"):
        await r.rerank("查询", ["a"])
    await r.shutdown()


@pytest.mark.asyncio
async def test_rerank_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    r = make_reranker(handler)
    await r.startup()
    with pytest.raises(RerankError, match=r"超时|失败"):
        await r.rerank("查询", ["a"])
    await r.shutdown()


@pytest.mark.asyncio
async def test_rerank_empty_documents():
    r = make_reranker(lambda req: httpx.Response(200, json={}))
    await r.startup()
    assert await r.rerank("查询", []) == []
    await r.shutdown()


def test_configured_property():
    assert make_settings().rerank_configured is True
    assert make_settings(rerank_api_key="").rerank_configured is False
