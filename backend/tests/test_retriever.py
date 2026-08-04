"""10 问召回验证：断言 top_k 命中预期 doc/path，输出相似度分布。

运行：cd code && python -m pytest tests/test_retriever.py -v -s
需要可访问 Milvus（192.168.0.215:19530）与 BGE 模型。
"""
import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.rag.retriever import ManualRetriever

EVAL_FILE = Path(__file__).parent / "eval_questions.json"


@pytest_asyncio.fixture(scope="module")
async def retriever():
    r = ManualRetriever(get_settings())
    await r.startup()
    yield r
    await r.shutdown()


@pytest.mark.asyncio
async def test_recall_10_questions(retriever):
    cases = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    passed, failed = 0, []

    for case in cases:
        hits = await retriever.search(case["question"])
        print(f"\nQ: {case['question']}")
        for h in hits:
            print(f"  [{h.score:.4f}] {h.doc} > {h.path}")

        ok = any(
            (not case["expect_doc"] or h.doc == case["expect_doc"])
            and case["expect_path_contains"] in h.path
            for h in hits
        )
        if ok:
            passed += 1
        else:
            failed.append(case["question"])

    print(f"\n召回通过 {passed}/{len(cases)}")
    if failed:
        print("未命中:", failed)
    assert passed == len(cases), f"{len(failed)} 个问题未召回预期切片: {failed}"


@pytest.mark.asyncio
async def test_source_chunk_images(retriever):
    """命中含图切片时，来源卡片应包含改写后的图片 URL。"""
    hits = await retriever.search("班牌端首页有哪些功能入口")
    assert hits, "检索无结果"
    source = retriever.to_source_chunk(hits[0])
    assert source.content, "来源原文为空"
    if source.images:
        assert all(u.startswith("/api/images/") for u in source.images)
