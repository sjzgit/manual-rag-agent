"""检索结果与对话相关数据结构。"""
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Milvus 检索命中结果。

    score 为最终展示/落库分：rerank_score 优先（0~1，与 COSINE 同量纲），
    否则 dense_score，纯稀疏命中取 sparse_score（原始 BM25 分，可能 >1）。
    """

    chunk_id: str
    content: str  # Milvus 中的 clean 文本（图片为 [图片：alt] 占位）
    doc: str
    path: str
    score: float
    # ---- 混合检索扩展（全部可选，纯稠密链路为 None） ----
    dense_score: float | None = None  # 稠密路 COSINE 原始分
    sparse_score: float | None = None  # 稀疏路 BM25 原始分（内积）
    dense_rank: int | None = None
    sparse_rank: int | None = None
    fused_score: float | None = None  # 加权 RRF 融合分
    rerank_score: float | None = None  # 重排相关度分（0~1）


class HybridSearchOutcome(BaseModel):
    """一次子问题混合检索的全阶段结果（供过程展示与分阶段日志落库）。

    - dense_hits：稠密路（语义检索）原始召回，按 COSINE 降序；
    - sparse_hits：稀疏路（关键词检索）原始召回，按 BM25 内积降序；
    - fused_hits：RRF 融合 + 阈值过滤 + 按父去重后的代表列表（rerank 输入）；
    - final_hits：最终返回列表（rerank 后前 N；未重排时即融合序前 N）；
    - mode：dense | hybrid | hybrid-rerank（稀疏路未生效时无 sparse/fused 两阶段）；
    - scope：global | memory（本次检索范围：全库 / 会话关联手册优先段）。
    """

    mode: str
    scope: str = "global"
    dense_hits: list[SearchResult] = Field(default_factory=list)
    sparse_hits: list[SearchResult] = Field(default_factory=list)
    fused_hits: list[SearchResult] = Field(default_factory=list)
    final_hits: list[SearchResult] = Field(default_factory=list)


class SourceChunk(BaseModel):
    """下发前端的来源切片：原文 markdown + 图片 URL。"""

    chunk_id: str
    doc: str
    path: str
    score: float
    content: str = ""  # chunks.json 原文 markdown（图片 URL 已改写）
    images: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str | None = None
    question: str
    doc: str | None = None  # 按手册名过滤
    clarify_answer: str | None = None  # 澄清补充回答


class FeedbackRequest(BaseModel):
    message_id: str
    score: int  # 1 赞 / -1 踩
    comment: str = ""


class RenameSessionRequest(BaseModel):
    title: str


class DeleteMessagesRequest(BaseModel):
    message_ids: list[str]
