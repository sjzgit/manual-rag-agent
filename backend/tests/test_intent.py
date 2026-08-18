"""意图识别层测试：规则降级、JSON 校验/修复、LLM 重试兜底、澄清生成。

小模型未配置时走规则路径，不依赖外部服务与 meta_data.json。
"""
import pytest
from app.core.config import get_settings
from app.intent.models import IntentResult
from app.intent.recognizer import IntentRecognizer


@pytest.fixture
def recognizer():
    return IntentRecognizer(get_settings())


def _force_rules(monkeypatch, recognizer):
    """强制走规则降级路径。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "")


# ---------- 规则降级路径 ----------


@pytest.mark.asyncio
async def test_rules_empty_input(recognizer, monkeypatch):
    _force_rules(monkeypatch, recognizer)
    batch = await recognizer.recognize("  ")
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_rules_chitchat(recognizer, monkeypatch):
    """闲聊/礼貌短句应拒答，即使有历史模块上下文也不强行继承。"""
    _force_rules(monkeypatch, recognizer)
    history = [{"role": "user", "content": "会议室预约怎么发起？"}]
    batch = await recognizer.recognize("好的，谢谢", history=history)
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_rules_irrelevant_domain(recognizer, monkeypatch):
    """明显无关领域词（天气等）应拒答。"""
    _force_rules(monkeypatch, recognizer)
    batch = await recognizer.recognize("今天天气怎么样适合出去玩吗")
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_rules_followup_no_history_vague(recognizer, monkeypatch):
    """模糊指代（"这个怎么审核？"）且无历史可补全 → 模糊澄清，而非拒答。"""
    _force_rules(monkeypatch, recognizer)
    batch = await recognizer.recognize("这个怎么审核？")
    intent = batch.intents[0]
    assert intent.intent_type == "vague"
    assert "module" in intent.missing_fields
    assert intent.clarify_question


@pytest.mark.asyncio
async def test_rules_precise(recognizer, monkeypatch):
    """形态完整的操作问题 → 精确（是否收录由向量检索决定）。"""
    _force_rules(monkeypatch, recognizer)
    batch = await recognizer.recognize("一键签退怎么操作？")
    assert batch.used_llm is False
    assert batch.all_precise


@pytest.mark.asyncio
async def test_rules_context_continuation(recognizer, monkeypatch):
    """承接性提问：历史已提及模块，应结合上下文补全而非拒答。"""
    _force_rules(monkeypatch, recognizer)
    history = [
        {"role": "user", "content": "会议室预约怎么发起？"},
        {"role": "assistant", "content": "您可以进入「实验会议室预约操作手册 > 会议室门禁（移动端）」，点击新增预约申请…"},
    ]
    batch = await recognizer.recognize("那审批流程呢？", history=history)
    assert not batch.has_irrelevant
    intent = batch.intents[0]
    assert intent.intent_type == "precise"
    assert intent.module == "实验会议室预约"
    assert "实验会议室预约" in intent.simple_input


@pytest.mark.asyncio
async def test_rules_context_system_module(recognizer, monkeypatch):
    """历史提及「xx系统」也应能提取模块补全。"""
    _force_rules(monkeypatch, recognizer)
    history = [{"role": "user", "content": "上体附中系统怎么登录？"}]
    batch = await recognizer.recognize("角色权限有哪些？", history=history)
    intent = batch.intents[0]
    assert intent.intent_type == "precise"
    assert intent.module == "上体附中"


# ---------- JSON 校验与修复 ----------


def test_load_json_repairs(recognizer):
    """markdown 代码块包裹 + 前后杂质文本 + 尾逗号，均应修复后解析成功。"""
    raw = '''以下是结果：
```json
{"intents": [{"input": "q1", "intent_type": "precise",},],}
```
以上。'''
    data = recognizer._load_json(raw)
    assert len(data["intents"]) == 1


def test_load_json_broken_raises(recognizer):
    with pytest.raises(ValueError):
        recognizer._load_json("这不是JSON")


def test_parse_intents_valid(recognizer):
    raw = (
        '{"intents": [{"input": "这个怎么审核？", "simple_input": "怎么审核", "module": "", '
        '"role": "", "description": "审核操作", "intent_type": "vague", '
        '"missing_fields": ["module"], "clarify_question": "请问审核哪个模块？", '
        '"intent_reason": "未明确审核对象"}]}'
    )
    results = recognizer._parse_intents(raw, "这个怎么审核？")
    assert len(results) == 1
    r = results[0]
    assert r.intent_type == "vague"
    assert r.missing_fields == ["module"]
    assert r.clarify_question == "请问审核哪个模块？"


def test_parse_intents_field_tolerant(recognizer):
    """非法 intent_type 修正为 vague；非法 missing_fields 值被过滤；缺字段容错。"""
    raw = (
        '{"intents": [{"input": "q", "intent_type": "unknown", '
        '"missing_fields": ["module", "hacker"], "clarify_question": " x "}]}'
    )
    results = recognizer._parse_intents(raw, "q")
    r = results[0]
    assert r.intent_type == "vague"
    assert r.missing_fields == ["module"]
    assert r.clarify_question == "x"
    assert r.simple_input == "q"  # 缺省回退为原问题


def test_parse_intents_empty_raises(recognizer):
    with pytest.raises(ValueError):
        recognizer._parse_intents('{"intents": []}', "q")
    with pytest.raises(ValueError):
        recognizer._parse_intents('{"other": 1}', "q")


# ---------- LLM 路径：校验-修复-重试-兜底 ----------

_GOOD_JSON = (
    '{"intents": [{"input": "q", "simple_input": "实验会议室预约：审批流程", '
    '"module": "实验会议室预约", "description": "审批流程", '
    '"intent_type": "precise", "missing_fields": [], "clarify_question": "", '
    '"intent_reason": "要素齐全"}]}'
)


@pytest.mark.asyncio
async def test_llm_valid_first_attempt(recognizer, monkeypatch):
    """首次输出合法 JSON → 直接成功。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")

    async def fake_chat(self, system, user, json_mode=False, max_tokens=None, history=None):
        return _GOOD_JSON, []

    monkeypatch.setattr(IntentRecognizer, "_chat", fake_chat)
    batch = await recognizer.recognize("那审批流程呢？")
    assert batch.used_llm
    assert batch.all_precise
    assert batch.intents[0].module == "实验会议室预约"


@pytest.mark.asyncio
async def test_llm_retry_on_bad_json(recognizer, monkeypatch):
    """首次输出坏 JSON → 用 Prompt 重试一次，第二次成功。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_chat(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        return ("坏输出", []) if len(calls) == 1 else (_GOOD_JSON, [])

    monkeypatch.setattr(IntentRecognizer, "_chat", fake_chat)
    batch = await recognizer.recognize("问题")
    assert batch.used_llm
    assert batch.all_precise
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_llm_retry_exhausted_falls_to_vague(recognizer, monkeypatch):
    """两次均解析失败 → 判 vague，clarify_question 为友好补充提示。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_chat(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        return "仍然不是JSON", []

    monkeypatch.setattr(IntentRecognizer, "_chat", fake_chat)
    batch = await recognizer.recognize("问题")
    assert batch.used_llm
    intent = batch.intents[0]
    assert intent.intent_type == "vague"
    assert intent.clarify_question  # 友好提示非空
    assert len(calls) == 2  # 恰好重试一次


# ---------- 澄清问题生成 ----------


@pytest.mark.asyncio
async def test_clarify_prefers_llm_question(recognizer):
    """vague 意图已带 clarify_question 时直接使用，不再调用 LLM。"""
    intents = [
        IntentResult(
            input="这个怎么审核？",
            intent_type="vague",
            missing_fields=["module"],
            clarify_question="请问您想审核哪个系统或功能模块中的内容？",
        )
    ]
    question = await recognizer.generate_clarify_question(intents)
    assert question == "请问您想审核哪个系统或功能模块中的内容？"


@pytest.mark.asyncio
async def test_clarify_template_fallback(recognizer):
    """无 clarify_question（规则降级路径）时按缺失要素生成模板追问。"""
    intents = [
        IntentResult(input="这个怎么审核？", intent_type="vague", missing_fields=["module"])
    ]
    question = await recognizer.generate_clarify_question(intents)
    assert "模块" in question
    assert len(question) < 120


@pytest.mark.asyncio
async def test_clarify_template_multi_fields(recognizer):
    intents = [
        IntentResult(
            input="怎么操作",
            intent_type="vague",
            missing_fields=["module", "description"],
        )
    ]
    question = await recognizer.generate_clarify_question(intents)
    assert "模块" in question
    assert "操作" in question


# ---------- _chat 历史注入 ----------


@pytest.mark.asyncio
async def test_llm_history_passed(recognizer, monkeypatch):
    """LLM 路径：历史应作为多轮消息传给 _chat，由 _chat 注入 prompt。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    captured: dict = {}

    async def fake_chat(self, system, user, json_mode=False, max_tokens=None, history=None):
        captured["history"] = history
        captured["system"] = system
        return _GOOD_JSON, []

    monkeypatch.setattr(IntentRecognizer, "_chat", fake_chat)
    history = [{"role": "user", "content": "会议室预约怎么发起？"}]
    batch = await recognizer.recognize("那审批流程呢？", history=history)
    assert batch.used_llm
    assert captured["history"] is history
    # 使用新 Prompt（纯文本规则，无 meta_data 索引注入）
    assert "意图识别器" in captured["system"]
    assert "{" not in captured["system"] or "intents" in captured["system"]


@pytest.mark.asyncio
async def test_llm_history_truncation(recognizer, monkeypatch):
    """_chat 注入历史时：超过条数/长度应截断，且始终以当前问题收尾。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["messages"] = json["messages"]
            return _FakeResp()

    monkeypatch.setattr("app.intent.recognizer.httpx.AsyncClient", _FakeClient)
    history = [
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "y" * 1000},
        {"role": "user", "content": "z" * 1000},
    ]
    await recognizer._chat("sys", "当前问题", history=history)
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "当前问题"}
    hist_msgs = messages[1:-1]
    assert len(hist_msgs) == 3
    assert all(len(m["content"]) == 500 for m in hist_msgs)
