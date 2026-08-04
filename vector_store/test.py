import re
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer

# ==================== 配置（与 ingest_chunks.py 保持一致）====================
MILVUS_HOST = "192.168.0.215"
MILVUS_PORT = "19530"
COLLECTION_NAME = "manual_rag_chunks"
MODEL_NAME = "BAAI/bge-base-zh-v1.5"
TOP_K = 5


def clean_for_embedding(text: str) -> str:
    """保留图片说明文字，去掉图片实际路径，减少路径噪声。"""
    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片：\1]", text)


# ==================== 1. 连接 Milvus ====================
connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
collection = Collection(COLLECTION_NAME)
collection.load()

print(f"集合状态：{collection.num_entities} 条记录已加载\n")

# ==================== 2. 加载向量模型 ====================
model = SentenceTransformer(MODEL_NAME)

# ==================== 3. 测试检索 ====================
test_questions = [
    "如何新增会议室预约申请？",
    "怎么修改密码？",
    "请假流程是什么？",
]

for question in test_questions:
    print(f"查询：{question}")
    print("-" * 60)

    query_vector = model.encode(
        [question],
        normalize_embeddings=True,
    ).tolist()

    results = collection.search(
        data=query_vector,
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {}},
        limit=TOP_K,
        output_fields=["doc", "path", "content"],
    )

    for i, hit in enumerate(results[0]):
        similarity = round(hit.score, 4)
        doc = hit.entity.get("doc")
        path = hit.entity.get("path")
        content = hit.entity.get("content")
        # 截取前 120 字显示
        snippet = clean_for_embedding(content)[:120].replace("\n", " ")

        print(f"  [{i + 1}] 相似度: {similarity} | 来源: {doc}")
        print(f"      路径: {path}")
        print(f"      内容: {snippet}...")
        print()

    print()
