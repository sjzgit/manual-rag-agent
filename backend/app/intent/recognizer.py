"""意图识别：小模型（OpenAI 兼容）+ meta_data 关键词匹配；未配置时规则降级。"""
import asyncio
import json
import re
from pathlib import Path

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.intent.models import IntentBatch, IntentResult
from app.prompts.intent import CLARIFY_SYSTEM_PROMPT, INTENT_SYSTEM_PROMPT

logger = get_logger(__name__)


class IntentRecognizer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._meta_index: list[dict] = []
        self._meta_json_str: str = "[]"

    # ---------- 生命周期 ----------

    async def startup(self) -> None:
        await asyncio.to_thread(self._load_meta)

    def _load_meta(self) -> None:
        meta_file = Path(self.settings.meta_data_path)
        if meta_file.exists():
            self._meta_index = json.loads(meta_file.read_text(encoding="utf-8"))
            # 注入 prompt 用的精简索引
            compact = [
                {"id": m["id"], "doc": m["doc"], "path": m["path"], "keywords": m["keywords"]}
                for m in self._meta_index
            ]
            self._meta_json_str = json.dumps(compact, ensure_ascii=False, indent=1)
            logger.info("meta_data_loaded", count=len(self._meta_index))
        else:
            logger.warning("meta_data_missing", path=str(meta_file))

    # ---------- 主入口 ----------

    async def recognize(self, question: str) -> IntentBatch:
        """识别用户输入，返回逐子问题的意图结果。"""
        if self.settings.intent_llm_configured:
            try:
                return await self._recognize_by_llm(question)
            except Exception as e:  # noqa: BLE001
                logger.warning("intent_llm_failed_fallback_rules", error=str(e))
        return self._recognize_by_rules(question)

    async def generate_clarify_question(self, intents: list[IntentResult]) -> str:
        """根据模糊意图生成追问内容。"""
        fallback = self._template_clarify(intents)
        if not self.settings.intent_llm_configured:
            return fallback
        try:
            intents_json = json.dumps(
                [i.model_dump() for i in intents], ensure_ascii=False, indent=1
            )
            text = await self._chat(
                CLARIFY_SYSTEM_PROMPT.format(intents=intents_json),
                "请生成追问。",
                max_tokens=200,
            )
            text = (text or "").strip()
            return text if text else fallback
        except Exception as e:  # noqa: BLE001
            logger.warning("clarify_llm_failed", error=str(e))
            return fallback

    # ---------- 小模型路径 ----------

    async def _recognize_by_llm(self, question: str) -> IntentBatch:
        system = INTENT_SYSTEM_PROMPT.format(meta_data=self._meta_json_str)
        raw = await self._chat(system, question, json_mode=True)
        intents = self._parse_intents(raw, question)
        return IntentBatch(intents=intents, used_llm=True)

    async def _chat(
        self, system: str, user: str, json_mode: bool = False, max_tokens: int | None = None
    ) -> str:
        s = self.settings
        payload: dict = {
            "model": s.intent_llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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
        return data["choices"][0]["message"]["content"] or ""

    def _parse_intents(self, raw: str, question: str) -> list[IntentResult]:
        """解析小模型 JSON 输出，带容错兜底。"""
        text = raw.strip()
        # 去除可能的 markdown 代码块包裹
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            data = json.loads(text)
            items = data.get("intents", [])
            valid_ids = {m["id"] for m in self._meta_index}
            results = []
            for item in items:
                ids = [i for i in item.get("chunk_id_list", []) if i in valid_ids]
                intent_type = item.get("intent_type", "vague")
                if intent_type not in ("irrelevant", "precise", "vague"):
                    intent_type = "vague"
                results.append(
                    IntentResult(
                        input=item.get("input", question),
                        simple_input=item.get("simple_input", question),
                        module=item.get("module", "") or "",
                        role=item.get("role", "") or "",
                        description=item.get("description", "") or "",
                        chunk_id_list=ids,
                        intent_type=intent_type,
                        intent_reason=item.get("intent_reason", "") or "",
                    )
                )
            if results:
                return results
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning("intent_json_parse_failed", error=str(e), raw=raw[:200])
        # 解析失败：整体降级为模糊，触发澄清
        return [
            IntentResult(
                input=question,
                simple_input=question,
                intent_type="vague",
                intent_reason="意图解析失败，需用户补充说明",
            )
        ]

    # ---------- 规则降级路径 ----------

    def _recognize_by_rules(self, question: str) -> IntentBatch:
        """关键词重叠度匹配 meta_data；命中多则模糊，未命中且不相关词则拒答。"""
        q = question.strip()
        if not q:
            return IntentBatch(
                intents=[IntentResult(input=q, intent_type="irrelevant", intent_reason="空输入")],
                used_llm=False,
            )

        scored: list[tuple[int, dict]] = []
        for meta in self._meta_index:
            # 关键词命中（权重 2）+ 手册名/路径段命中（权重 1）
            score = sum(2 for kw in meta.get("keywords", []) if kw and kw in q)
            tokens = [meta.get("doc", "")] + [
                t.strip() for t in meta.get("path", "").split(">")
            ]
            score += sum(1 for t in tokens if t and len(t) >= 2 and t in q)
            if score > 0:
                scored.append((score, meta))
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            intent_type = "irrelevant"
            reason = "问题与手册知识库无关键词匹配"
        elif len(scored) == 1 or scored[0][0] > scored[1][0]:
            intent_type = "precise"
            reason = "关键词唯一匹配切片"
        else:
            intent_type = "vague"
            reason = "关键词匹配多个切片，无法区分"

        top = scored[:2] if scored else []
        intent = IntentResult(
            input=q,
            simple_input=q,
            module=top[0][1]["doc"] if top else "",
            description=q,
            chunk_id_list=[m["id"] for _, m in top],
            intent_type=intent_type,
            intent_reason=reason,
        )
        return IntentBatch(intents=[intent], used_llm=False)

    def _template_clarify(self, intents: list[IntentResult]) -> str:
        vague = [i for i in intents if i.intent_type == "vague"]
        if not vague:
            return "请问您想咨询哪个功能的操作？"
        docs = sorted(
            {m["doc"] for i in vague for m in self._meta_index if m["id"] in i.chunk_id_list}
        )
        if docs:
            options = "、".join(docs[:5])
            return f"请问您咨询的是哪个系统/功能模块？（可选：{options}）"
        return "请问您想咨询哪个功能模块的什么操作？（请说明系统名称和具体功能点）"
