"""BM25 稀疏向量编码与加权 RRF 融合（关键词检索，混合检索的稀疏路）。

- 分词：jieba 精确模式 + 内置精简中文停用词表（模块常量，不依赖外部文件）；
- token id：blake2b 稳定 hash → uint32（禁用内置 hash()，PYTHONHASHSEED 随机化导致
  进程间不一致；32 位空间下约 3 万词表碰撞期望 ≈0.1 次，可接受，且新词入库不影响
  旧词 id，天然支持增量写入）；
- 编码：文档侧权重 = idf × tf 饱和项（BM25 公式），查询侧 = 词频，
  两者内积 ≈ BM25 得分（Milvus SPARSE_FLOAT_VECTOR + IP 度量）。

纯 CPU 同步实现，调用方须包 asyncio.to_thread；BM25Encoder 构建完成后不可变
（retriever.reload 时整体替换实例），线程安全。
"""

import hashlib
import math
import re
from collections import Counter

from app.rag.models import SearchResult

# 精简中文停用词表：高频虚词 + 手册问答场景常见疑问/操作泛词
_STOPWORDS: frozenset[str] = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "那", "他", "她", "它", "们", "什么",
    "怎么", "怎样", "如何", "哪", "哪些", "哪个", "哪里", "为什么", "谁",
    "进行", "操作", "使用", "可以", "能够", "需要", "应该", "必须", "一下",
    "请问", "还有", "以及", "或者", "但是", "因为", "所以", "如果",
    "这个", "那个", "这些", "那些", "时候", "地方", "东西", "情况", "问题",
    "想", "做", "弄", "搞", "把", "被", "让", "给", "从", "向", "对", "跟",
    "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "嘛", "啦", "么", "之", "其",
    "等", "等等", "之类", "方面", "方式", "方法", "关于", "对于", "通过",
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "in", "on",
    "for", "with", "how", "what", "which", "where", "why", "do", "does",
})

# 纯标点/符号 token 过滤（jieba 可能切出标点与空白）
_TOKEN_RE = re.compile(r"^[\w一-鿿]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """jieba 精确模式分词 → 小写化 → 去标点/符号 → 停用词过滤。"""
    import jieba

    tokens = jieba.lcut(text, HMM=True)
    return [t.lower() for t in tokens if _TOKEN_RE.match(t) and t.lower() not in _STOPWORDS]


def token_id(token: str) -> int:
    """稳定 hash → uint32：blake2b 摘要前 4 字节大端序（进程间一致）。"""
    return int.from_bytes(
        hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big"
    )


def weighted_rrf_fuse(
    dense: list[SearchResult],
    sparse: list[SearchResult],
    dense_weight: float,
    sparse_weight: float,
    k: int = 60,
) -> list[SearchResult]:
    """按 chunk_id 合并两路检索结果（加权 RRF）。

    fused_score = Σ w_i/(k+rank_i)，rank 从 1 起，仅计该 chunk 出现过的通道；
    回填 dense/sparse 分数与排名，score 暂取 dense_score（无则 sparse_score）。
    单路为空时退化为另一路排名序。
    """
    by_id: dict[str, SearchResult] = {}
    for rank, h in enumerate(dense, 1):
        r = h.model_copy()
        r.dense_score = h.score
        r.dense_rank = rank
        by_id[r.chunk_id] = r
    for rank, h in enumerate(sparse, 1):
        existing = by_id.get(h.chunk_id)
        if existing is None:
            existing = h.model_copy()
            by_id[h.chunk_id] = existing
        existing.sparse_score = h.score
        existing.sparse_rank = rank

    for r in by_id.values():
        fused = 0.0
        if r.dense_rank is not None:
            fused += dense_weight / (k + r.dense_rank)
        if r.sparse_rank is not None:
            fused += sparse_weight / (k + r.sparse_rank)
        r.fused_score = fused
        if r.dense_score is not None:
            r.score = r.dense_score
        elif r.sparse_score is not None:
            r.score = r.sparse_score

    return sorted(
        by_id.values(), key=lambda r: r.fused_score or 0.0, reverse=True
    )


class BM25Encoder:
    """BM25 稀疏向量编码器（不可变：构建完成后整体替换）。

    文档侧：w(t,d) = idf(t) · tf·(k1+1) / (tf + k1·(1-b+b·|d|/avgdl))
    查询侧：w(t,q) = tf（标准 BM25 不做查询侧饱和）
    """

    def __init__(self, k1: float, b: float) -> None:
        self.k1 = k1
        self.b = b
        self._df: Counter[int] = Counter()
        self._num_docs = 0
        self._avgdl = 0.0

    @classmethod
    def from_corpus(cls, texts: list[str], k1: float, b: float) -> "BM25Encoder":
        """从语料构建全局统计（N / df / avgdl，doc 长度按 token 数计）。"""
        import jieba

        jieba.initialize()  # 首次加载词典 ~1s，由调用方放入 to_thread
        enc = cls(k1, b)
        total_len = 0
        for text in texts:
            tokens = tokenize(text)
            total_len += len(tokens)
            for tid in {token_id(t) for t in tokens}:
                enc._df[tid] += 1
        enc._num_docs = len(texts)
        enc._avgdl = (total_len / len(texts)) if texts else 0.0
        return enc

    @property
    def num_docs(self) -> int:
        """0 表示统计未就绪（空库）。"""
        return self._num_docs

    def _idf(self, tid: int) -> float:
        """Lucene 变体 idf，恒正。"""
        df = self._df.get(tid, 0)
        return math.log(1 + (self._num_docs - df + 0.5) / (df + 0.5))

    def encode_document(self, text: str) -> dict[int, float]:
        """文档侧稀疏向量：token id → BM25 权重。统计未就绪时返回空 dict。

        Milvus 稀疏向量要求索引升序且值为 float，故此处排序 + 显式转 float。
        """
        if self._num_docs == 0:
            return {}
        tokens = tokenize(text)
        if not tokens:
            return {}
        tf_map = Counter(token_id(t) for t in tokens)
        dl = len(tokens)
        norm = self.k1 * (1 - self.b + self.b * dl / self._avgdl) if self._avgdl > 0 else self.k1
        weights: dict[int, float] = {}
        for tid in sorted(tf_map):
            tf = tf_map[tid]
            weights[tid] = float(self._idf(tid) * tf * (self.k1 + 1) / (tf + norm))
        return weights

    def encode_query(self, text: str) -> dict[int, float]:
        """查询侧稀疏向量：token id → 词频（索引升序、值为 float）。"""
        return {
            tid: float(tf)
            for tid, tf in sorted(Counter(token_id(t) for t in tokenize(text)).items())
        }
