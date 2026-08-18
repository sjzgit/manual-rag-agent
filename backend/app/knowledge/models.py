"""知识库管理相关数据结构：父子切片规格与转换结果。"""
from pydantic import BaseModel


class ChunkSpec(BaseModel):
    """父子切片规格：chunker 产出，knowledge_service 负责持久化与向量化。

    chunk_type 为 parent 时 vector_state / vector_id 为空（父切片不入向量库）。
    """

    id: str
    doc_id: str
    doc: str
    chunk_type: str  # parent | child
    parent_id: str | None = None
    child_index: int = 0
    path: str
    level: int = 2
    chunk_index: int = 0
    content: str
    char_count: int = 0
    has_images: bool = False
    image_count: int = 0
    vector_state: str | None = None  # pending | success | failure（仅子切片）
    vector_id: str | None = None


class ConvertResult(BaseModel):
    """docx 转换产物描述。"""

    md_text: str
    preview_html: str
    image_count: int = 0
