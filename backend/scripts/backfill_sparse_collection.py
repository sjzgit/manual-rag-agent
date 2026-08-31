"""Backfill 脚本：从 MySQL chunks 表读取存量子切片，重建含稀疏向量的 v2 collection。

用法：
    cd backend
    python scripts/backfill_sparse_collection.py --dry-run   # 冒烟：统计 + 临时 collection 稀疏 API 验证
    python scripts/backfill_sparse_collection.py             # 正式：分批灌入 --collection 指向的 collection
    python scripts/backfill_sparse_collection.py --collection manual_rag_child_chunks_v2 --batch-size 256

说明：
- 不动 MySQL（chunk_id / vector_state / vector_id 不变）；
- 稠密向量用 BGE 重新编码（确定性模型，结果一致；不搬移旧库向量，保持单一编码路径）；
- 正式执行完成后，改 .env 的 MILVUS_CHILD_COLLECTION 指向新 collection 并重启服务。
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, cast

# 使 backend 目录可导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.models import Chunk
from app.db.session import Database
from app.rag.retriever import ManualRetriever, clean_for_embedding

logger = get_logger(__name__)


async def load_children(db: Database) -> list[Chunk]:
    from sqlalchemy import select

    async with db.session() as s:
        rows = (
            (
                await s.execute(
                    select(Chunk)
                    .where(Chunk.chunk_type == "child", Chunk.vector_state == "success")
                    .order_by(Chunk.chunk_index, Chunk.child_index)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def smoke_sparse_api(retriever: ManualRetriever) -> None:
    """dry-run 冒烟：建临时小 collection 验证稀疏 upsert/search/expr，完毕删除。"""
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    name = "sparse_smoke_test_tmp"
    if utility.has_collection(name):
        utility.drop_collection(name)
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="doc", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=768),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]
    col = Collection(name, CollectionSchema(fields=fields, description="稀疏冒烟"))
    col.create_index("vector", {"index_type": "FLAT", "metric_type": "COSINE", "params": {}})
    col.create_index(
        "sparse_vector",
        {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP",
         "params": {"drop_ratio_build": 0.0}},
    )
    try:
        dense = [0.1] * 768
        sparse = {1: 1.0, 2: 2.0}
        col.upsert([["s1", "s2"], ["手册A", "手册B"], [dense, dense], [sparse, {3: 1.0}]])
        col.flush()
        col.load()
        res = cast(Any, col.search(
            data=[sparse], anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {}},
            limit=2, output_fields=["doc"],
        ))
        assert len(res[0]) >= 1, "稀疏检索无结果"
        res2 = cast(Any, col.search(
            data=[sparse], anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {}},
            limit=2, expr='doc == "手册A"', output_fields=["doc"],
        ))
        assert all(
            hit.entity.get("doc") == "手册A" for hit in list(res2[0])
        ), "稀疏路 expr 过滤失效"
        logger.info("sparse_smoke_ok", hits=len(res[0]), expr_hits=len(list(res2[0])))
    finally:
        utility.drop_collection(name)
        logger.info("sparse_smoke_collection_dropped")


async def main() -> None:
    parser = argparse.ArgumentParser(description="backfill 稀疏向量 v2 collection")
    parser.add_argument("--collection", default=None, help="目标 collection（默认取 settings）")
    parser.add_argument("--dry-run", action="store_true", help="只统计 + 稀疏 API 冒烟，不灌数据")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.debug)
    if args.collection:
        settings.milvus_child_collection = args.collection

    logger.info(
        "backfill_start",
        collection=settings.milvus_child_collection,
        dry_run=args.dry_run,
        server_version="probe-after-start",
    )

    db = Database(settings)
    await db.startup()
    try:
        retriever = ManualRetriever(settings, db)
        await retriever.startup()

        children = await load_children(db)
        texts = [clean_for_embedding(c.content) for c in children]
        total_chars = sum(len(t) for t in texts)
        logger.info(
            "children_loaded",
            count=len(children),
            total_chars=total_chars,
            batches=(len(children) + args.batch_size - 1) // args.batch_size,
        )

        if args.dry_run:
            await retriever.reload()  # BM25 词表规模统计
            if retriever._bm25 is not None:
                logger.info(
                    "bm25_stats",
                    num_docs=retriever._bm25.num_docs,
                    k1=settings.bm25_k1,
                    b=settings.bm25_b,
                )
            await smoke_sparse_api(retriever)
            logger.info("dry_run_done")
            return

        await retriever.ensure_child_collection()
        await retriever.reload()  # BM25 统计就绪（含全部存量切片）

        for i in range(0, len(children), args.batch_size):
            batch = children[i : i + args.batch_size]
            batch_texts = texts[i : i + args.batch_size]
            vectors = await retriever.embed(batch_texts)
            sparse = await asyncio.to_thread(retriever.encode_sparse_sync, batch_texts)
            rows = [
                {
                    "id": c.id,
                    "doc": c.doc,
                    "path": c.path,
                    "parent_id": c.parent_id or "",
                    "content": batch_texts[j],
                    "vector": vectors[j],
                    "sparse_vector": sparse[j],
                }
                for j, c in enumerate(batch)
            ]
            await retriever.upsert_children(rows)
            logger.info("batch_upserted", offset=i, size=len(batch))

        # 对账
        col = retriever._collection
        if col is not None:
            col.flush()
        num = col.num_entities if col is not None else 0
        logger.info(
            "backfill_done",
            collection=settings.milvus_child_collection,
            num_entities=num,
            mysql_children=len(children),
            consistent=(num == len(children)),
        )
        if num != len(children):
            logger.warning(
                "backfill_count_mismatch", milvus=num, mysql=len(children)
            )
        print(
            f"\n完成：{settings.milvus_child_collection} 实体数 {num} / MySQL 子切片 {len(children)}。\n"
            f"下一步：确认 .env 中 MILVUS_CHILD_COLLECTION={settings.milvus_child_collection} 后重启服务。"
        )
    finally:
        await retriever.shutdown()
        await db.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
