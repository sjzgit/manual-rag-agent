"""意图识别数据结构。"""
from typing import Literal

from pydantic import BaseModel, Field

IntentType = Literal["irrelevant", "precise", "vague"]


class IntentResult(BaseModel):
    """单个子问题的意图识别结果（纯 Prompt 生成，不做切片匹配，收录与否由向量检索决定）。"""

    input: str  # 原始（子）问题
    simple_input: str = ""  # 去寒暄、结合上下文补全后的检索问题
    module: str = ""  # 功能模块名称或系统名称
    role: str = ""  # 用户角色
    description: str = ""  # 功能点描述
    intent_type: IntentType = "vague"
    missing_fields: list[str] = Field(default_factory=list)  # vague 时缺失的要素(module/role/description)
    clarify_question: str = ""  # vague 时 LLM 生成的澄清问题
    intent_reason: str = ""
    memory_answer_docs: list[str] = Field(default_factory=list)  # 无需检索、可直接凭关联文档作答时，列出覆盖该子问题的文档名称；空=需检索


class IntentBatch(BaseModel):
    """一次提问的完整识别结果。"""

    intents: list[IntentResult]
    used_llm: bool  # True=小模型识别，False=规则降级
    llm_calls: list[dict] = Field(default_factory=list)  # LLM 原始调用 trace（含重试）

    @property
    def has_irrelevant(self) -> bool:
        return any(i.intent_type == "irrelevant" for i in self.intents)

    @property
    def vague_intents(self) -> list[IntentResult]:
        return [i for i in self.intents if i.intent_type == "vague"]

    @property
    def precise_intents(self) -> list[IntentResult]:
        return [i for i in self.intents if i.intent_type == "precise"]

    @property
    def all_precise(self) -> bool:
        return bool(self.intents) and all(
            i.intent_type == "precise" for i in self.intents
        )

    @property
    def all_memory_sufficient(self) -> bool:
        """全部子问题都能凭关联文档直接作答（每个都有覆盖文档）时为 True，可整体跳过检索。"""
        return bool(self.intents) and all(i.memory_answer_docs for i in self.intents)

    @property
    def memory_answer_docs(self) -> list[str]:
        """全部子问题的可直接作答文档名称并集（去重保序），供直答环节取文档内容。"""
        merged: list[str] = []
        for i in self.intents:
            for name in i.memory_answer_docs:
                if name not in merged:
                    merged.append(name)
        return merged


class ClarifyState(BaseModel):
    """会话级澄清状态：记录进行中的澄清轮次与待补充意图。"""

    round: int = 0
    original_question: str = ""
    pending_intents: list[IntentResult] = Field(default_factory=list)
    clarify_question: str = ""
    missing_fields: list[str] = Field(default_factory=list)
