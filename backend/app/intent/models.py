"""意图识别数据结构。"""
from typing import Literal

from pydantic import BaseModel, Field

IntentType = Literal["irrelevant", "precise", "vague"]


class IntentResult(BaseModel):
    """单个子问题的意图识别结果。"""

    input: str  # 原始（子）问题
    simple_input: str = ""  # 优化后的精简问题
    module: str = ""  # 功能模块名称或系统名称
    role: str = ""  # 用户角色
    description: str = ""  # 功能点描述
    chunk_id_list: list[str] = Field(default_factory=list)  # meta_data 匹配的切片
    intent_type: IntentType = "vague"
    intent_reason: str = ""


class IntentBatch(BaseModel):
    """一次提问的完整识别结果。"""

    intents: list[IntentResult]
    used_llm: bool  # True=小模型识别，False=规则降级

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


class ClarifyState(BaseModel):
    """会话级澄清状态：记录进行中的澄清轮次与待补充意图。"""

    round: int = 0
    original_question: str = ""
    pending_intents: list[IntentResult] = Field(default_factory=list)
    clarify_question: str = ""
    missing_fields: list[str] = Field(default_factory=list)
