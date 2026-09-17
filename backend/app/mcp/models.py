"""MCP 工具出参模型：既是返回结构也是对外契约（改动须同步 docs/MCP服务.md）。"""
from typing import Literal

from pydantic import BaseModel, Field


class SourceItem(BaseModel):
    """检索切片：子切片定位（doc/path）+ 父切片完整内容。

    content 中图片引用已改写为 /api/images/{doc}/{filename}（配置
    mcp_public_base_url 后为绝对 URL），images 为图片链接清单。
    """

    chunk_id: str
    doc: str
    path: str
    score: float
    content: str
    images: list[str] = Field(default_factory=list)


class AskResult(BaseModel):
    """manual_ask 出参。

    status="answered"：answer 为完整回答（irrelevant 拒答 / 手册未找到时
    sources 为空，answer 为固定兜底话术）；status="clarify" 时 answer 为空，
    调用方须带 session_id + clarify_answer 重入（≤3 轮，超限自动直答）。
    """

    status: Literal["answered", "clarify"]
    answer: str = ""
    sources: list[SourceItem] = Field(default_factory=list)
    session_id: str = ""
    message_id: str | None = None
    clarify_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class SearchPayload(BaseModel):
    """manual_search 出参：检索未调用 LLM；mode 反映实际生效的检索方式。"""

    mode: str  # hybrid-rerank | hybrid | dense
    total: int
    results: list[SourceItem] = Field(default_factory=list)


class DocInfo(BaseModel):
    doc: str
    parents: int = Field(description="父切片数量（≈章节数量级）")


class DocsPayload(BaseModel):
    """manual_docs 出参：当前知识库中可检索的全部手册。"""

    total: int
    docs: list[DocInfo] = Field(default_factory=list)
