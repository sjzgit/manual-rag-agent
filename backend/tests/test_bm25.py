"""BM25 编码与加权 RRF 融合单元测试（纯函数，无外部依赖）。

分词断言使用可控的英文语料（jieba 对英文按空白/标点切分，结果可精确预测），
中文仅做性质 smoke（不假设具体分词边界）。
"""
import hashlib
import math

from app.rag.bm25 import BM25Encoder, token_id, tokenize, weighted_rrf_fuse
from app.rag.models import SearchResult


def make_hit(chunk_id: str, score: float, **kw) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content="内容", doc="d", path="p", score=score, **kw)


# ---------- tokenize ----------

def test_tokenize_filters_stopwords_and_symbols():
    tokens = tokenize("如何 操作 的 吗 ！")
    assert all(t not in ("如何", "操作", "的", "吗") for t in tokens)


def test_tokenize_keeps_english_digits_and_lowercases():
    tokens = tokenize("PC 端 K8 H5")
    assert "pc" in tokens and "h5" in tokens
    assert all(t == t.lower() for t in tokens)
    assert all(t.strip() for t in tokens)


def test_tokenize_empty_and_pure_stopwords():
    assert tokenize("的 了 吗 ！") == []
    assert tokenize("") == []


# ---------- token_id ----------

def test_token_id_stable():
    assert token_id("学生卡") == token_id("学生卡")
    assert token_id("apple") != token_id("apply")


def test_token_id_known_value():
    # 已知值断言：blake2b 前 4 字节大端序
    expected = int.from_bytes(
        hashlib.blake2b(b"apple", digest_size=4).digest(), "big"
    )
    assert token_id("apple") == expected


# ---------- BM25Encoder（英文语料，分词可控） ----------

CORPUS = ["apple banana", "apple cherry", "dog food"]


def _encoder() -> BM25Encoder:
    return BM25Encoder.from_corpus(CORPUS, k1=1.5, b=0.75)


def test_from_corpus_stats():
    enc = _encoder()
    assert enc.num_docs == 3
    # 每个文档 2 个 token，avgdl = 2
    assert enc._avgdl == 2.0


def test_idf_positive_and_rare_term_higher():
    enc = _encoder()
    common = token_id("apple")  # df=2
    rare = token_id("dog")  # df=1
    assert enc._idf(rare) > enc._idf(common) > 0


def test_encode_query_is_term_freq():
    enc = _encoder()
    q = enc.encode_query("apple apple banana")
    assert q[token_id("apple")] == 2
    assert q[token_id("banana")] == 1


def test_encode_document_bm25_formula():
    """手算 BM25 权重精确值：语料 avgdl=2，doc 'apple apple banana' 长度 3。"""
    enc = _encoder()
    w = enc.encode_document("apple apple banana")

    # idf(apple)：df=2, N=3
    idf_apple = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    norm = 1.5 * (1 - 0.75 + 0.75 * 3 / 2.0)  # = 2.0625
    expected_apple = idf_apple * 2 * (1.5 + 1) / (2 + norm)
    assert abs(w[token_id("apple")] - expected_apple) < 1e-9

    idf_banana = math.log(1 + (3 - 1 + 0.5) / (1 + 0.5))  # df=1
    expected_banana = idf_banana * 1 * (1.5 + 1) / (1 + norm)
    assert abs(w[token_id("banana")] - expected_banana) < 1e-9


def test_encode_document_empty_when_stats_missing():
    enc = BM25Encoder(k1=1.5, b=0.75)
    assert enc.encode_document("任意文本") == {}


def test_encode_query_is_sorted_and_float():
    """稀疏向量必须索引升序且值为 float，否则 Milvus 拒绝（search_data illegal）。"""
    enc = _encoder()
    q = enc.encode_query("banana apple banana")
    assert list(q.keys()) == sorted(q.keys())  # 索引升序
    assert all(isinstance(v, float) for v in q.values())  # 值为 float
    assert all(v > 0.0 for v in q.values())


def test_encode_document_is_sorted_and_float():
    enc = _encoder()
    w = enc.encode_document("banana apple banana")
    assert list(w.keys()) == sorted(w.keys())
    assert all(isinstance(v, float) for v in w.values())


def test_encode_document_empty_for_untokenizable_text():
    enc = _encoder()
    assert enc.encode_document("？ ！ 的") == {}


# ---------- weighted_rrf_fuse ----------

def test_fuse_merges_both_channels():
    dense = [make_hit("a", 0.9), make_hit("b", 0.8)]
    sparse = [make_hit("b", 5.2), make_hit("c", 4.0)]
    fused = weighted_rrf_fuse(dense, sparse, 0.7, 0.3, k=60)
    by_id = {r.chunk_id: r for r in fused}
    # b 双路命中，融合分最高
    assert by_id["b"].dense_rank == 2 and by_id["b"].sparse_rank == 1
    assert by_id["b"].fused_score == 0.7 / 62 + 0.3 / 61
    assert by_id["a"].fused_score == 0.7 / 61
    assert by_id["c"].fused_score == 0.3 / 62
    assert fused[0].chunk_id == "b"
    # score 取 dense_score（b 有 dense 分）
    assert by_id["b"].score == 0.8
    # c 纯稀疏命中，score 取 sparse_score
    assert by_id["c"].score == 4.0


def test_fuse_single_channel_fallback():
    dense = [make_hit("a", 0.9), make_hit("b", 0.8)]
    fused = weighted_rrf_fuse(dense, [], 0.7, 0.3)
    assert [r.chunk_id for r in fused] == ["a", "b"]
    assert all(r.fused_score is not None for r in fused)
    assert fused[0].score == 0.9


def test_fuse_empty_inputs():
    assert weighted_rrf_fuse([], [], 0.7, 0.3) == []
