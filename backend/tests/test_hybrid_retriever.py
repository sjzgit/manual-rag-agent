"""混合检索单测：降级分支、阈值语义、父代表、search_and_rerank 三态（fake 基建）。"""
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import Settings
from app.rag.models import SearchResult


def make_settings(**kw) -> Settings:
    base = {
        "enable_keyword_search": True,
        "score_threshold": 0.4,
        "keyword_pass_rank": 3,
        "recall_top_k": 20,
        "rerank_top_n": 5,
        "fusion_dense_weight": 0.7,
        "fusion_sparse_weight": 0.3,
        "rrf_k": 60,
    }
    base.update(kw)
    return Settings(**base)


def make_hit(chunk_id: str, score: float, **kw) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content=f"内容{chunk_id}", doc="d", path="p", score=score, **kw)


def make_retriever(settings=None, **settings_kw):
    """构造不连 Milvus/MySQL 的 retriever，行为字段手工装配。"""
    from app.rag.retriever import ManualRetriever

    r = ManualRetriever(settings or make_settings(**settings_kw), db=None)
    return r


# ---------- 阈值过滤新语义 ----------

def test_filter_dense_only_hits_use_dense_threshold():
    r = make_retriever()
    hits = [
        make_hit("a", 0.5, dense_score=0.5),
        make_hit("b", 0.3, dense_score=0.3),
    ]
    passed = r.filter_by_threshold(hits)
    assert [h.chunk_id for h in passed] == ["a"]


def test_filter_sparse_strong_hit_passes_without_dense():
    r = make_retriever()
    hits = [
        make_hit("b", 3.0, sparse_score=3.0, sparse_rank=2),  # 纯稀疏，排名 2 ≤ 3
        make_hit("c", 1.0, sparse_score=1.0, sparse_rank=7),  # 排名 7 > 3，滤除
    ]
    passed = r.filter_by_threshold(hits)
    assert [h.chunk_id for h in passed] == ["b"]


def test_filter_dense_below_threshold_and_sparse_weak_filtered():
    r = make_retriever()
    hits = [
        make_hit("a", 0.2, dense_score=0.2, sparse_score=0.5, sparse_rank=8),
    ]
    assert r.filter_by_threshold(hits) == []


def test_filter_legacy_semantics_unchanged_when_no_hybrid_fields():
    """稀疏未生效（无 dense/sparse 扩展字段）时与旧语义一致：score >= 阈值。"""
    r = make_retriever()
    hits = [make_hit("a", 0.9), make_hit("b", 0.1)]
    passed = r.filter_by_threshold(hits)
    assert [h.chunk_id for h in passed] == ["a"]


# ---------- 父代表去重 ----------

def test_parent_representatives_keeps_best_fused_per_parent():
    r = make_retriever()
    r._child_to_parent = {"c1": "p1", "c2": "p1", "c3": "p2", "c4": "c4"}
    hits = [
        make_hit("c1", 0.5, fused_score=0.02),
        make_hit("c2", 0.9, fused_score=0.03),  # 同父更高融合分
        make_hit("c3", 0.8, fused_score=0.025),
        make_hit("c4", 0.7, fused_score=0.01),  # 无父映射视为自身
    ]
    reps = r.parent_representatives(hits)
    assert [h.chunk_id for h in reps] == ["c2", "c3", "c4"]


def test_parent_representatives_fallback_to_score_without_fused():
    r = make_retriever()
    r._child_to_parent = {"c1": "p1", "c2": "p1"}
    hits = [
        make_hit("c1", 0.5),
        make_hit("c2", 0.9),  # 无 fused_score，用 score 比较
    ]
    reps = r.parent_representatives(hits)
    assert [h.chunk_id for h in reps] == ["c2"]


# ---------- search_and_rerank 三态 ----------

@pytest.mark.asyncio
async def test_search_and_rerank_without_reranker():
    r = make_retriever()
    r.search = AsyncMock(return_value=[
        make_hit("a", 0.9, dense_score=0.9, fused_score=0.03),
        make_hit("b", 0.8, dense_score=0.8, fused_score=0.02),
        make_hit("c", 0.2, dense_score=0.2, fused_score=0.01),  # 低于阈值被滤
    ])
    outcome = await r.search_and_rerank("问题")
    assert outcome.mode == "dense"  # sparse_ready=False（无 collection/bm25）
    assert [h.chunk_id for h in outcome.final_hits] == ["a", "b"]
    # 纯稠密链路：dense_hits 即原始召回序，无 sparse/fused 快照内容差异
    assert [h.chunk_id for h in outcome.dense_hits] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_search_and_rerank_rerank_success():
    r = make_retriever()
    r._bm25 = MagicMock(num_docs=10)  # sparse_ready 所需
    r._has_sparse = True
    r._collection = MagicMock()  # search 已 mock，此值仅使 sparse_ready 通过
    r.search = AsyncMock(return_value=[
        make_hit("a", 0.9, dense_score=0.9, dense_rank=1, sparse_score=2.0,
                 sparse_rank=2, fused_score=0.03),
        make_hit("b", 0.8, dense_score=0.8, dense_rank=2, sparse_score=5.0,
                 sparse_rank=1, fused_score=0.02),
    ])

    reranker = MagicMock()
    reranker.configured = True
    reranker.rerank = AsyncMock(return_value=[(1, 0.99), (0, 0.12)])

    outcome = await r.search_and_rerank("问题", reranker=reranker, top_n=5)
    assert outcome.mode == "hybrid-rerank"
    assert [h.chunk_id for h in outcome.final_hits] == ["b", "a"]
    assert outcome.final_hits[0].rerank_score == 0.99
    assert outcome.final_hits[0].score == 0.99
    # rerank 输入为命中 content
    docs = reranker.rerank.await_args.args[1]
    assert docs == ["内容a", "内容b"]
    # 四阶段列表齐备：两路快照按各自排名序还原
    assert [h.chunk_id for h in outcome.dense_hits] == ["a", "b"]
    assert [h.chunk_id for h in outcome.sparse_hits] == ["b", "a"]
    assert [h.chunk_id for h in outcome.fused_hits] == ["a", "b"]


@pytest.mark.asyncio
async def test_search_and_rerank_rerank_failure_fallback():
    r = make_retriever()
    r.search = AsyncMock(return_value=[
        make_hit("a", 0.9, dense_score=0.9, fused_score=0.03),
        make_hit("b", 0.8, dense_score=0.8, fused_score=0.02),
    ])
    reranker = MagicMock()
    reranker.configured = True
    reranker.rerank = AsyncMock(side_effect=RuntimeError("boom"))

    outcome = await r.search_and_rerank("问题", reranker=reranker)
    # 降级：融合序，无 rerank_score，不抛异常
    assert outcome.mode == "dense"
    assert [h.chunk_id for h in outcome.final_hits] == ["a", "b"]
    assert all(h.rerank_score is None for h in outcome.final_hits)


@pytest.mark.asyncio
async def test_search_and_rerank_unconfigured_reranker():
    r = make_retriever()
    r.search = AsyncMock(return_value=[make_hit("a", 0.9, dense_score=0.9)])
    reranker = MagicMock()
    reranker.configured = False

    outcome = await r.search_and_rerank("问题", reranker=reranker)
    reranker.rerank.assert_not_called()
    assert outcome.mode == "dense" and len(outcome.final_hits) == 1


# ---------- collection not loaded 自动重载重试 ----------

def test_search_retries_after_collection_not_loaded():
    """Milvus 服务端释放 collection（重启/淘汰）后检索报 collection not loaded：
    应自动重新 load 并重试一次检索成功，而不是把异常抛给对话流水线。"""
    r = make_retriever()
    r._model = SimpleNamespace(
        encode=lambda q, normalize_embeddings=True: [[0.1, 0.2, 0.3]]
    )
    calls = {"n": 0}

    def flaky_search(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # 首次检索抛出与线上一致的 not loaded 错误（code=101）
            raise RuntimeError(
                "(code=101, message=failed to search: collection not loaded"
                "[collection=468641932463151140])"
            )
        return [[]]  # 重试成功：单条空命中列表（results[0] 为空）

    r._collection = SimpleNamespace(search=flaky_search)
    r._load_child_collection_sync = lambda: None  # 模拟重新 load 成功（句柄仍可用）

    hits = r._search_sync("问题", None, None)
    assert hits == []
    assert calls["n"] == 2  # 首次失败 + 重试成功，共两次检索


def test_search_returns_empty_when_reload_fails():
    """not loaded 后重新 load 仍失败（collection 句柄置空）：返回空命中优雅降级，不抛异常。"""
    r = make_retriever()
    r._model = SimpleNamespace(
        encode=lambda q, normalize_embeddings=True: [[0.1, 0.2, 0.3]]
    )

    def not_loaded_search(**kw):
        raise RuntimeError("(code=101, message=failed to search: collection not loaded)")

    r._collection = SimpleNamespace(search=not_loaded_search)
    # 模拟重载失败：_load_child_collection_sync 置空句柄（与真实失败路径一致）
    r._load_child_collection_sync = lambda: setattr(r, "_collection", None)

    assert r._search_sync("问题", None, None) == []


def test_search_propagates_other_errors():
    """非 collection not loaded 的异常（如网络/参数错误）照常抛出，避免吞掉真实故障。"""
    r = make_retriever()
    r._model = SimpleNamespace(
        encode=lambda q, normalize_embeddings=True: [[0.1, 0.2, 0.3]]
    )
    r._collection = SimpleNamespace(
        search=lambda **kw: (_ for _ in ()).throw(RuntimeError("connection refused"))
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        r._search_sync("问题", None, None)


# ---------- build_context 相关度排序 ----------

def test_build_context_sorts_by_final_score():
    r = make_retriever()
    r._parents = {
        "p1": {"content": "父1", "doc": "手册A", "path": "路径1", "chunk_index": 1},
        "p2": {"content": "父2", "doc": "手册A", "path": "路径2", "chunk_index": 2},
    }
    r._child_to_parent = {"c1": "p1", "c2": "p2"}
    hits = [
        make_hit("c1", 0.9, rerank_score=0.95),
        make_hit("c2", 0.8, rerank_score=0.99),
    ]
    ctx = r.build_context(hits)
    assert ctx.index("路径2") < ctx.index("路径1")  # p2 rerank 分更高在前
    assert "（相关度" in ctx


def test_build_context_keyword_hit_label():
    r = make_retriever()
    r._child_to_parent = {"c1": "c1"}
    hits = [make_hit("c1", 3.0, sparse_score=3.0, sparse_rank=1)]
    ctx = r.build_context(hits)
    assert "（关键词命中，排名 1）" in ctx


# ---------- 稀疏路降级 ----------

def test_sparse_search_exception_returns_empty():
    r = make_retriever()
    r._bm25 = MagicMock()
    r._bm25.encode_query.side_effect = RuntimeError("boom")
    assert r._sparse_search("问题", None, 10) == []


def test_sparse_ready_requires_all_conditions():
    r = make_retriever()  # enable_keyword_search=True
    assert r.sparse_ready is False  # 无 _has_sparse / _bm25
    r._has_sparse = True
    assert r.sparse_ready is False  # 无 _bm25
    r._bm25 = MagicMock(num_docs=5)
    assert r.sparse_ready is True
    r2 = make_retriever(enable_keyword_search=False)
    r2._has_sparse = True
    r2._bm25 = MagicMock(num_docs=5)
    assert r2.sparse_ready is False


def test_refresh_sparse_flag_version_gate():
    from app.rag.retriever import ManualRetriever

    r = ManualRetriever(make_settings(), db=None)
    r._collection = cast(  # type: ignore[reportAttributeAccessIssue]
        Any,
        SimpleNamespace(
            schema=SimpleNamespace(
                fields=[SimpleNamespace(name=n) for n in ("id", "vector", "sparse_vector")]
            )
        ),
    )
    r._server_version = "2.3.21"
    r._refresh_sparse_flag()
    assert r._has_sparse is False  # 服务端版本不足 → 禁用

    r._server_version = "2.5.0"
    r._refresh_sparse_flag()
    assert r._has_sparse is True

    r._server_version = "unknown"
    r._refresh_sparse_flag()
    assert r._has_sparse is True  # 版本探测失败不阻断（Milvus 可用即信任 schema）
