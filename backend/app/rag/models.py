"""检索结果与对话相关数据结构。"""
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Milvus 检索命中结果。"""

    chunk_id: str
    content: str  # Milvus 中的 clean 文本（图片为 [图片：alt] 占位）
    doc: str
    path: str
    score: float


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
