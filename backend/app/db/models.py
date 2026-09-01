"""SQLAlchemy 表模型：会话/消息/反馈/意图日志/检索日志/审计/源文档/切片/提示词。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="新会话")
    doc_filter: Mapped[str | None] = mapped_column(String(256), nullable=True)
    clarify_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    memory_docs: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 会话关联文档 id 列表（≤3）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 思考过程步骤（StepEvent JSON 数组）
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM 推理思维链（reasoning_content）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer)  # 1 / -1
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IntentLog(Base):
    __tablename__ = "intent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), default="")
    sub_question: Mapped[dict] = mapped_column(JSON)  # IntentResult dump
    intent_type: Mapped[str] = mapped_column(String(16), index=True)
    intent_reason: Mapped[str] = mapped_column(String(512), default="")
    used_llm: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), default="")
    sub_question: Mapped[str] = mapped_column(String(1024))
    chunk_id: Mapped[str] = mapped_column(String(512))
    doc: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(1024))
    score: Mapped[float] = mapped_column()  # 最终展示分（rerank 优先）
    # ---- 混合检索扩展（V005，旧数据为 NULL） ----
    dense_score: Mapped[float | None] = mapped_column(nullable=True)
    sparse_score: Mapped[float | None] = mapped_column(nullable=True)
    fused_score: Mapped[float | None] = mapped_column(nullable=True)
    rerank_score: Mapped[float | None] = mapped_column(nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="dense")  # dense | hybrid | hybrid-rerank
    # ---- 分阶段记录（V006）：同一子问题的各阶段命中列表 ----
    stage: Mapped[str] = mapped_column(String(16), default="final")  # dense | sparse | fused | final
    hit_rank: Mapped[int] = mapped_column(Integer, default=1)  # 该阶段内的排名（从 1 起）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="user")  # user | admin
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    original_file_path: Mapped[str] = mapped_column(String(512))
    md_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    doc: Mapped[str] = mapped_column(String(256), index=True)
    chunk_type: Mapped[str] = mapped_column(String(16))  # parent | child
    parent_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    child_index: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(1024))
    level: Mapped[int] = mapped_column(Integer, default=2)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    has_images: Mapped[bool] = mapped_column(Boolean, default=False)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class LlmCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    call_type: Mapped[str] = mapped_column(String(32))  # intent | answer
    system_prompt: Mapped[str] = mapped_column(Text)
    messages: Mapped[list] = mapped_column(JSON)
    output: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | processing | resolved | closed
    priority: Mapped[str] = mapped_column(String(16), default="medium")  # high | medium | low
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FeedbackTicket(Base):
    __tablename__ = "feedback_tickets"
    __table_args__ = (
        UniqueConstraint("feedback_id", "ticket_id", name="uk_feedback_ticket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[int] = mapped_column(Integer)  # 左前缀被唯一键覆盖
    ticket_id: Mapped[int] = mapped_column(Integer, index=True)  # 对应 idx_ticket
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
