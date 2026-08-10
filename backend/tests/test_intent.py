"""意图识别层测试：规则降级、三分类、JSON 解析兜底、澄清模板。

小模型未配置时走规则路径，不依赖外部服务。
"""
import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.intent.models import IntentResult
from app.intent.recognizer import IntentRecognizer


@pytest_asyncio.fixture(scope="module")
async def recognizer():
    r = IntentRecognizer(get_settings())
    await r.startup()
    return r


def test_meta_index_loaded(recognizer):
    assert len(recognizer._meta_index) == 19
    assert '"keywords"' in recognizer._meta_json_str


@pytest.mark.asyncio
async def test_rules_precise(recognizer):
    """含独特关键词的问题应唯一匹配切片。"""
    # 强制走规则路径
    recognizer.settings.intent_llm_api_key = ""
    batch = await recognizer.recognize("一键签退怎么操作？")
    assert batch.used_llm is False
    assert batch.all_precise
    top = batch.intents[0]
    assert "学生管理" in " ".join(
        m["path"] for m in recognizer._meta_index if m["id"] in top.chunk_id_list
    )


@pytest.mark.asyncio
async def test_rules_vague(recognizer):
    """"角色权限"命中多个手册的相似切片，应判模糊。"""
    recognizer.settings.intent_llm_api_key = ""
    batch = await recognizer.recognize("角色权限有哪些？")
    intent = batch.intents[0]
    assert intent.intent_type == "vague"
    assert len(intent.chunk_id_list) >= 2


@pytest.mark.asyncio
async def test_rules_irrelevant(recognizer):
    recognizer.settings.intent_llm_api_key = ""
    batch = await recognizer.recognize("今天天气怎么样适合出去玩吗")
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_clarify_template(recognizer):
    vague = [
        IntentResult(
            input="角色权限有哪些？",
            intent_type="vague",
            chunk_id_list=[m["id"] for m in recognizer._meta_index[:3]],
        )
    ]
    question = await recognizer.generate_clarify_question(vague)
    assert "哪个" in question
    assert len(question) < 120


def test_parse_intents_tolerant(recognizer):
    """小模型输出带 markdown 包裹/杂质时仍能解析。"""
    raw = '''以下是结果：
```json
{"intents": [{"input": "q1", "simple_input": "q1", "module": "上体附中系统",
"role": "管理员", "description": "重置学生卡", "chunk_id_list": ["bad-id"],
"intent_type": "precise", "intent_reason": "要素齐全"}]}
```'''
    results = recognizer._parse_intents(raw, "q1")
    assert len(results) == 1
    assert results[0].intent_type == "precise"
    assert results[0].chunk_id_list == []  # 非法 id 被过滤


def test_parse_intents_broken_fallback(recognizer):
    results = recognizer._parse_intents("这不是JSON", "原问题")
    assert len(results) == 1
    assert results[0].intent_type == "vague"
    assert results[0].input == "原问题"


# ---------- 结合会话上下文识别 ----------

@pytest.mark.asyncio
async def test_rules_context_continuation(recognizer):
    """承接性提问：当前输入无关键词，但历史已确定模块，应结合上下文补全而非拒答。"""
    recognizer.settings.intent_llm_api_key = ""
    history = [
        {"role": "user", "content": "会议室预约怎么发起？"},
        {"role": "assistant", "content": "您可以进入「实验会议室预约操作手册 > 会议室门禁（移动端）」，点击新增预约申请…"},
    ]
    batch = await recognizer.recognize("那审批流程呢？", history=history)
    assert not batch.has_irrelevant
    intent = batch.intents[0]
    assert intent.intent_type == "precise"
    assert intent.module == "实验会议室预约操作手册"
    assert "实验会议室预约操作手册" in intent.simple_input


@pytest.mark.asyncio
async def test_rules_context_no_history_unchanged(recognizer):
    """无历史时承接性提问仍应拒答（保持原有行为）。"""
    recognizer.settings.intent_llm_api_key = ""
    batch = await recognizer.recognize("那审批流程呢？")
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_rules_context_disambiguate(recognizer):
    """"角色权限"多手册同分本应 vague；历史指向上体附中后应加权消歧为 precise。"""
    recognizer.settings.intent_llm_api_key = ""
    history = [{"role": "user", "content": "上体附中系统怎么登录？"}]
    batch = await recognizer.recognize("角色权限有哪些？", history=history)
    intent = batch.intents[0]
    assert intent.intent_type == "precise"
    assert intent.module == "上体附中系统操作手册"


@pytest.mark.asyncio
async def test_rules_context_chitchat_not_followup(recognizer):
    """闲聊/礼貌短句即使有历史模块上下文，也不应误判为承接追问。"""
    recognizer.settings.intent_llm_api_key = ""
    history = [{"role": "user", "content": "会议室预约怎么发起？"}]
    batch = await recognizer.recognize("好的，谢谢", history=history)
    assert batch.has_irrelevant


@pytest.mark.asyncio
async def test_llm_history_passed(recognizer, monkeypatch):
    """LLM 路径：历史应作为多轮消息传给 _chat，由 _chat 注入 prompt。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    captured: dict = {}

    async def fake_chat(self, system, user, json_mode=False, max_tokens=None, history=None):
        captured["history"] = history
        captured["user"] = user
        return (
            '{"intents": [{"input": "q", "simple_input": "q", "module": "实验会议室预约操作手册", '
            '"intent_type": "precise", "chunk_id_list": []}]}'
        )

    monkeypatch.setattr(IntentRecognizer, "_chat", fake_chat)
    history = [{"role": "user", "content": "会议室预约怎么发起？"}]
    batch = await recognizer.recognize("那审批流程呢？", history=history)
    assert batch.used_llm
    assert batch.all_precise
    assert captured["history"] is history


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
