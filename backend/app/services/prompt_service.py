"""提示词模板服务：seed / 内存缓存 / 读取 / 更新，DB 不可用时回退代码常量。

提示词与 RAG 架构绑定，key 固定枚举，不支持手动新增（见 prompt_templates 表 key）。
"""
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import PromptTemplate
from app.prompts.intent import INTENT_SYSTEM_PROMPT
from app.prompts.manual import (
    AGENTIC_SYSTEM_PROMPT,
    ANSWER_SYSTEM_PROMPT,
    CLARIFY_EXHAUSTED_PREFIX,
    IRRELEVANT_REPLY,
    NOT_FOUND_REPLY,
)

logger = get_logger(name=__name__)

# key -> (显示名, 代码默认值)
_DEFAULTS: dict[str, tuple[str, str]] = {
    "intent_system": ("意图识别提示词", INTENT_SYSTEM_PROMPT),
    "answer_system": ("回答生成提示词", ANSWER_SYSTEM_PROMPT),
    "agentic_system": ("Agentic 模式提示词", AGENTIC_SYSTEM_PROMPT),
    "not_found_reply": ("未找到兜底话术", NOT_FOUND_REPLY),
    "irrelevant_reply": ("无关问题拒答话术", IRRELEVANT_REPLY),
    "clarify_exhausted_prefix": ("澄清超限前缀", CLARIFY_EXHAUSTED_PREFIX),
}


class PromptService:
    def __init__(self, db):
        self.db = db
        self._cache: dict[str, str] = {}

    async def startup(self) -> None:
        await self._seed()
        await self.reload()

    async def _seed(self) -> None:
        """首次启动用代码常量填充模板表。"""
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                existing = set((await s.execute(select(PromptTemplate.key))).scalars())
                for key, (name, content) in _DEFAULTS.items():
                    if key not in existing:
                        s.add(PromptTemplate(key=key, name=name, content=content))
                await s.commit()
        except Exception as e:
            logger.warning("prompt_seed_failed", error=str(e))

    async def reload(self) -> None:
        self._cache = {}
        if not self.db.available:
            return
        try:
            async with self.db.session() as s:
                rows = (await s.execute(select(PromptTemplate))).scalars()
                self._cache = {r.key: r.content for r in rows}
        except Exception as e:
            logger.warning("prompt_load_failed", error=str(e))

    def get(self, key: str) -> str:
        """同步读取（读内存缓存）；未命中回退代码默认值。"""
        if key in self._cache:
            return self._cache[key]
        default = _DEFAULTS.get(key)
        return default[1] if default else ""

    async def list(self) -> list[dict]:
        return [
            {
                "key": key,
                "name": name,
                "content": self.get(key),
                "is_customized": key in self._cache,
            }
            for key, (name, _) in _DEFAULTS.items()
        ]

    async def update(self, key: str, content: str) -> bool:
        if key not in _DEFAULTS:
            return False
        if not self.db.available:
            return False
        try:
            async with self.db.session() as s:
                row = (
                    await s.execute(
                        select(PromptTemplate).where(PromptTemplate.key == key)
                    )
                ).scalar_one_or_none()
                if row is None:
                    name, _ = _DEFAULTS[key]
                    s.add(PromptTemplate(key=key, name=name, content=content))
                else:
                    row.content = content
                await s.commit()
            self._cache[key] = content
            return True
        except Exception as e:
            logger.warning("prompt_update_failed", key=key, error=str(e))
            return False
