import json
import re
from pathlib import Path

from pymilvus import (
    connections, utility, FieldSchema, CollectionSchema,
    DataType, Collection
)
from sentence_transformers import SentenceTransformer


MILVUS_HOST = "192.168.0.215"
MILVUS_PORT = "19530"
COLLECTION_NAME = "manual_rag_chunks"
MODEL_NAME = "BAAI/bge-base-zh-v1.5"
BATCH_SIZE = 16


def clean_for_embedding(text: str) -> str:
    """保留图片说明文字，去掉图片实际路径，减少路径噪声。"""
    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片：\1]", text)


# 1. 读取切片
chunks_file = next(Path("../../文档切片").rglob("chunks.json"))
chunks = json.loads(chunks_file.read_text(encoding="utf-8"))

texts = [clean_for_embedding(chunk["content"]) for chunk in chunks]

# 2. 加载向量模型、生成归一化向量
model = SentenceTransformer(MODEL_NAME)
vectors = model.encode(
    texts,
    batch_size=BATCH_SIZE,
    normalize_embeddings=True,
    show_progress_bar=True,
)

print(f"切片数量：{len(chunks)}")
print(f"向量维度：{vectors.shape[1]}")  # 应为 768

# 3. 连接 Milvus
connections.connect(
    alias="default",
    host=MILVUS_HOST,
    port=MILVUS_PORT,
)

# 4. 首次运行时创建 collection
if not utility.has_collection(COLLECTION_NAME):
    fields = [
        FieldSchema(
            name="id", dtype=DataType.VARCHAR,
            is_primary=True, auto_id=False, max_length=512
        ),
        FieldSchema(name="doc", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="path", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=768),
    ]

    schema = CollectionSchema(
        fields=fields,
        description="操作手册 RAG 文档切片",
    )

    collection = Collection(COLLECTION_NAME, schema)

    collection.create_index(
        field_name="vector",
        index_params={
            "index_type": "FLAT",
            "metric_type": "COSINE",
            "params": {},
        },
    )
else:
    collection = Collection(COLLECTION_NAME)

# 5. 写入或更新数据；以 chunk 的 id 作为唯一标识
data = [
    [chunk["id"] for chunk in chunks],
    [chunk["doc"] for chunk in chunks],
    [chunk["path"] for chunk in chunks],
    [chunk["content"] for chunk in chunks],
    vectors.tolist(),
]

collection.upsert(data)
collection.flush()
collection.load()

print(f"入库完成：{collection.num_entities} 条记录")