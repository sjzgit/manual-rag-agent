"""意图识别：独立小模型（OpenAI 兼容）基于 Prompt 直接识别；未配置时规则降级。

LLM 输出 JSON 后的校验流程（见 chat/意图识别/改为prompt.md）：
代码校验格式 → 不正确先尝试修复 → 仍不正确用 Prompt 重试一次 →
第二次仍不正确则判定为 vague，友好提示用户补充详细信息。
"""
import json
import re

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.intent.models import IntentBatch, IntentResult
from app.prompts.intent import INTENT_SYSTEM_PROMPT

logger = get_logger(__name__)

_VALID_INTENT_TYPES = ("irrelevant", "precise", "vague")
_VALID_MISSING_FIELDS = ("module", "role", "description")

# LLM 输出两次解析均失败时的兜底澄清话术（判 vague，不阻断对话）
_PARSE_FAILED_CLARIFY = (
    "抱歉，我暂时没能准确理解您的问题。"
    "请补充说明您想查询的系统或功能模块，以及具体想进行的操作，"
    "例如「实验会议室预约：如何审批申请」。"
)


class IntentRecognizer:
    def __init__(self, settings: Settings, prompt_service=None):
        self.settings = settings
        self.prompt_service = prompt_service

    # ---------- 生命周期 ----------

    async def startup(self) -> None:
        """意图识别已改为纯 Prompt 驱动，无外部索引需要加载；保留钩子供 lifespan 调用。"""

    def _intent_prompt(self) -> str:
        """意图识别提示词：优先提示词服务，未注入时回退代码常量。"""
        return self.prompt_service.get("intent_system") if self.prompt_service else INTENT_SYSTEM_PROMPT

    # ---------- 主入口 ----------

    async def recognize(
        self,
        question: str,
        history: list[dict] | None = None,
    ) -> IntentBatch:
        """识别用户输入，返回逐子问题的意图结果。

        参数:
            question: 用户当前输入
            history: 会话历史消息（[{role, content}, ...]，不含当前输入），
                用于结合上下文识别：当前输入为指代/承接/省略模块时，
                依据历史最近确定的模块补全，避免误判为无关或模糊。
        """
        if self.settings.intent_llm_configured:
            return await self._recognize_by_llm(question, history=history)
        return self._recognize_by_rules(question, history=history)

    async def generate_clarify_question(self, intents: list[IntentResult]) -> str:
        """根据模糊意图生成追问内容。

        新 Prompt 已要求 vague 时直接输出 clarify_question，优先使用；
        缺失时（如规则降级路径）用模板兜底，不再二次调用 LLM。
        """
        questions = [i.clarify_question.strip() for i in intents if i.clarify_question.strip()]
        if questions:
            return "\n".join(questions)
        return self._template_clarify(intents)

    # ---------- 小模型路径 ----------

    async def _recognize_by_llm(
        self,
        question: str,
        history: list[dict] | None = None,
    ) -> IntentBatch:
        """LLM 识别意图。history 作为多轮消息注入，让模型结合上下文
        补全指代（如"那审批流程呢"）与省略的模块要素。

        输出校验：解析失败 → 重试一次 → 仍失败判 vague。
        """
        llm_calls: list[dict] = []
        last_error = ""
        for attempt in (1, 2):
            system = self._intent_prompt()
            raw, messages = await self._chat(
                system, question, json_mode=True, history=history
            )
            llm_calls.append(
                {"system_prompt": system, "messages": messages, "output": raw}
            )
            try:
                intents = self._parse_intents(raw, question)
                return IntentBatch(intents=intents, used_llm=True, llm_calls=llm_calls)
            except ValueError as e:
                last_error = str(e)
                logger.warning(
                    "intent_json_invalid", attempt=attempt, error=last_error, raw=raw[:200]
                )
        logger.warning("intent_json_retry_exhausted", question=question[:100])
        return IntentBatch(
            intents=[
                IntentResult(
                    input=question,
                    simple_input=question,
                    intent_type="vague",
                    missing_fields=["module", "description"],
                    clarify_question=_PARSE_FAILED_CLARIFY,
                    intent_reason=f"意图识别输出两次均无法解析（{last_error}），转澄清",
                )
            ],
            used_llm=True,
            llm_calls=llm_calls,
        )

    async def _chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        max_tokens: int | None = None,
        history: list[dict] | None = None,
    ) -> tuple[str, list[dict]]:
        s = self.settings
        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            # 注入全部历史（按正序），当前问题置于最后；暂不截断，上下文管理后续处理
            for msg in history:
                role = msg.get("role")
                content = (msg.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user})
        payload: dict = {
            "model": s.intent_llm_model,
            "messages": messages,
            "temperature": s.intent_llm_temperature,
            "max_tokens": max_tokens or s.intent_llm_max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{s.intent_llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {s.intent_llm_api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or "", messages

    def _parse_intents(self, raw: str, question: str) -> list[IntentResult]:
        """校验并解析小模型 JSON 输出；无法解析时抛 ValueError（由上层重试）。"""
        data = self._load_json(raw)
        items = data.get("intents") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("输出缺少非空 intents 数组")
        results = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("intents 元素不是 JSON 对象")
            intent_type = item.get("intent_type")
            if intent_type not in _VALID_INTENT_TYPES:
                intent_type = "vague"
            missing = [
                f for f in item.get("missing_fields", []) or [] if f in _VALID_MISSING_FIELDS
            ]
            results.append(
                IntentResult(
                    input=item.get("input") or question,
                    simple_input=item.get("simple_input") or question,
                    module=item.get("module") or "",
                    role=item.get("role") or "",
                    description=item.get("description") or "",
                    intent_type=intent_type,
                    missing_fields=missing,
                    clarify_question=(item.get("clarify_question") or "").strip(),
                    intent_reason=item.get("intent_reason") or "",
                )
            )
        return results

    @staticmethod
    def _load_json(raw: str) -> dict:
        """提取并修复 JSON：剥离 markdown 代码块与杂质文本、截取最外层对象、去尾逗号。"""
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("输出中未找到 JSON 对象")
        text = text[start : end + 1]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e

    # ---------- 规则降级路径（小模型未配置时） ----------

    # 闲聊/礼貌语（此类输入即使有历史模块上下文，也不强行继承，保持拒答）
    _CHITCHAT_WORDS = (
        "你好", "您好", "你好呀", "嗨", "hello", "hi",
        "谢谢", "感谢", "辛苦了", "再见", "拜拜", "在吗", "好的", "嗯嗯", "收到",
    )
    # 与操作手册明显无关的领域词（规则路径无关键词索引，靠词表保守拒答）
    _IRRELEVANT_WORDS = (
        "天气", "新闻", "股票", "基金", "理财", "笑话", "讲个故事", "写作文", "作文",
        "数学题", "脑筋急转弯", "明星", "电影", "电视剧", "菜谱", "做菜", "感冒", "生病",
        "医疗建议", "写诗", "写代码", "编程",
    )
    # 承接/指代特征词：无历史模块可补全时应澄清而非拒答
    _FOLLOWUP_WORDS = (
        "那", "这个", "那个", "它", "该", "下一步", "然后", "接着", "继续", "上面", "刚才", "前面",
    )
    # 从历史文本提取功能模块名：「实验会议室预约操作手册」「上体附中系统」→ 模块核心名
    _MODULE_PATTERN = re.compile(r"([一-龥A-Za-z0-9]{2,24}?)(?:操作手册|系统)")

    def _recognize_by_rules(
        self, question: str, history: list[dict] | None = None
    ) -> IntentBatch:
        """规则降级：无关键词索引可用，仅按输入形态保守分类，
        是否收录交由向量检索决定（与新 Prompt 的判定哲学一致）。

        - 空输入/闲聊/明显无关领域词 → irrelevant；
        - 模糊指代（"这个怎么审核"）且历史无法补全模块 → vague 澄清；
        - 承接性提问且历史能提取模块 → 补全 module 后判 precise；
        - 其余形态像操作问题 → precise，由检索结果兜底"未找到"。
        """
        q = question.strip()
        if not q:
            return IntentBatch(
                intents=[
                    IntentResult(input=q, intent_type="irrelevant", intent_reason="空输入")
                ],
                used_llm=False,
            )
        if self._looks_like_chitchat(q):
            return IntentBatch(
                intents=[
                    IntentResult(
                        input=q, intent_type="irrelevant", intent_reason="闲聊或礼貌用语"
                    )
                ],
                used_llm=False,
            )

        context_module = self._infer_context_module(history)
        if context_module:
            # 承接场景：历史已确定模块，当前输入按该模块补全
            return IntentBatch(
                intents=[
                    IntentResult(
                        input=q,
                        simple_input=f"{context_module}：{q}",
                        module=context_module,
                        description=q,
                        intent_type="precise",
                        intent_reason=f"结合会话历史模块「{context_module}」补全上下文",
                    )
                ],
                used_llm=False,
            )

        if any(w in q for w in self._IRRELEVANT_WORDS):
            return IntentBatch(
                intents=[
                    IntentResult(
                        input=q,
                        intent_type="irrelevant",
                        intent_reason="问题与操作手册领域明显无关",
                    )
                ],
                used_llm=False,
            )

        if any(w in q for w in self._FOLLOWUP_WORDS):
            # 模糊指代且无历史可补全：澄清而非拒答
            return IntentBatch(
                intents=[
                    IntentResult(
                        input=q,
                        intent_type="vague",
                        missing_fields=["module"],
                        clarify_question="请问您想咨询哪个系统或功能模块？（可直接补充完整问题）",
                        intent_reason="模糊指代且会话历史无法补全模块",
                    )
                ],
                used_llm=False,
            )

        return IntentBatch(
            intents=[
                IntentResult(
                    input=q,
                    intent_type="precise",
                    intent_reason="问题具备模块与操作描述，交由向量检索确认是否收录",
                )
            ],
            used_llm=False,
        )

    def _infer_context_module(self, history: list[dict] | None) -> str | None:
        """从会话历史文本提取最近提及的功能模块名。

        倒序扫描历史消息，用模式「xx操作手册」「xx系统」提取模块核心名
        （如"实验会议室预约操作手册"→"实验会议室预约"，"上体附中系统"→"上体附中"），
        用于承接性提问补全。
        """
        if not history:
            return None
        for msg in reversed(history):
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            m = self._MODULE_PATTERN.search(content)
            if m:
                return m.group(1)
        return None

    @classmethod
    def _looks_like_chitchat(cls, q: str) -> bool:
        """判断是否为闲聊/礼貌短句（此类输入不应结合历史模块误判为追问）。"""
        qq = q.strip().lower()
        if len(qq) > 12:
            return False
        return any(w in qq for w in cls._CHITCHAT_WORDS)

    def _template_clarify(self, intents: list[IntentResult]) -> str:
        """规则降级路径的追问模板：按缺失要素生成针对性问题。"""
        fields: set[str] = set()
        for i in intents:
            if i.intent_type != "vague":
                continue
            fields.update(i.missing_fields)
            if not i.module:
                fields.add("module")
            if not i.description:
                fields.add("description")
        parts: list[str] = []
        if "module" in fields:
            parts.append("请问您想咨询哪个系统或功能模块？")
        if "description" in fields:
            parts.append("您想了解该模块的哪项具体操作或功能？")
        if "role" in fields:
            parts.append("您的角色是？（如管理员、教职工）")
        return " ".join(parts) or "请问您想咨询哪个功能的操作？"
