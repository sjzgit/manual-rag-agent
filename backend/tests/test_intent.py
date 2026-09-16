"""意图识别层测试：规则降级、JSON 校验/修复、LLM 重试兜底、澄清生成、思维链流式。

小模型未配置时走规则路径，不依赖外部服务与 meta_data.json。
"""
import json

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

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        yield ("content", _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    batch = await recognizer.recognize("那审批流程呢？")
    assert batch.used_llm
    assert batch.all_precise
    assert batch.intents[0].module == "实验会议室预约"


@pytest.mark.asyncio
async def test_llm_retry_on_bad_json(recognizer, monkeypatch):
    """首次输出坏 JSON → 用 Prompt 重试一次，第二次成功。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        yield ("content", "坏输出" if len(calls) == 1 else _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    batch = await recognizer.recognize("问题")
    assert batch.used_llm
    assert batch.all_precise
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_llm_retry_exhausted_falls_to_vague(recognizer, monkeypatch):
    """两次均解析失败 → 判 vague，clarify_question 为友好补充提示。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        yield ("content", "仍然不是JSON")

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
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
    """LLM 路径：历史应原样传给流式调用 _chat_stream，由其注入 prompt。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    captured: dict = {}

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        captured["history"] = history
        captured["system"] = system
        yield ("content", _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    history = [{"role": "user", "content": "会议室预约怎么发起？"}]
    batch = await recognizer.recognize("那审批流程呢？", history=history)
    assert batch.used_llm
    assert captured["history"] is history
    # 使用新 Prompt（纯文本规则，无 meta_data 索引注入）
    assert "意图识别器" in captured["system"]
    assert "{" not in captured["system"] or "intents" in captured["system"]


@pytest.mark.asyncio
async def test_llm_history_full_injection(recognizer, monkeypatch):
    """_chat_stream 注入历史时：全部历史完整注入（不截断条数与长度），且始终以当前问题收尾。"""
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    captured: dict = {}

    class _FakeStreamResp:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: {"choices": [{"delta": {"content": "ok"}}]}'
            yield "data: [DONE]"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResp()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            captured["messages"] = json["messages"]
            return _FakeStreamCtx()

    monkeypatch.setattr("app.intent.recognizer.httpx.AsyncClient", _FakeClient)
    history = [
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "y" * 1000},
        {"role": "user", "content": "z" * 1000},
    ]
    parts = [t async for _, t in recognizer._chat_stream("sys", "当前问题", history=history)]
    assert parts == ["ok"]
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "当前问题"}
    hist_msgs = messages[1:-1]
    assert len(hist_msgs) == 3
    assert [m["content"] for m in hist_msgs] == ["x" * 1000, "y" * 1000, "z" * 1000]


# ---------- _chat_stream 思维链（reasoning）流式捕获 ----------


def _make_fake_stream_client(deltas: list[dict]):
    """构造 mock httpx.AsyncClient：stream 返回逐行 data: {"choices":[{"delta": ...}]} 的流。"""

    class _FakeStreamResp:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for d in deltas:
                yield f"data: {json.dumps({'choices': [{'delta': d}]}, ensure_ascii=False)}"
            yield "data: [DONE]"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResp()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _FakeStreamCtx()

    return _FakeClient


@pytest.mark.asyncio
async def test_chat_stream_parses_reasoning_content(recognizer, monkeypatch):
    """【思维链流式-reasoning 模型】流式 delta 含 reasoning_content 时逐段实时 yield。

    场景：意图小模型为 reasoning 模型，思维链分两段、正文一段到达。
    前置：mock httpx 流式响应 delta 序列 [rc1, rc2, content]。
    期望：_chat_stream 按到达顺序 yield ("reasoning", …)×2 + ("content", …)×1，
          片段顺序与粒度保持原样（供 C 端实时打字机展示）。
    """
    monkeypatch.setattr(
        "app.intent.recognizer.httpx.AsyncClient",
        _make_fake_stream_client(
            [
                {"reasoning_content": "思考"},
                {"reasoning_content": "中…"},
                {"content": "ok"},
            ]
        ),
    )
    parts = [item async for item in recognizer._chat_stream("sys", "问题")]
    assert parts == [
        ("reasoning", "思考"),
        ("reasoning", "中…"),
        ("content", "ok"),
    ]


@pytest.mark.asyncio
async def test_chat_stream_reasoning_defaults_empty(recognizer, monkeypatch):
    """【思维链流式-普通模型】流式 delta 无 reasoning_content 时只 yield content（不报错）。"""
    monkeypatch.setattr(
        "app.intent.recognizer.httpx.AsyncClient",
        _make_fake_stream_client([{"content": "ok"}]),
    )
    parts = [item async for item in recognizer._chat_stream("sys", "问题")]
    assert parts == [("content", "ok")]


@pytest.mark.asyncio
async def test_llm_calls_record_reasoning(recognizer, monkeypatch):
    """【思维链流式-trace 落库】LLM 路径每次调用的思维链拼接全文记入 llm_calls trace（含重试）。

    场景：首次输出坏 JSON 触发重试，两次调用的思维链分段不同。
    前置：mock _chat_stream 第一次 yield reasoning 两段 + 坏 content，
          第二次 yield reasoning 一段 + 合法 JSON。
    期望：batch.llm_calls 恰好两条，reasoning 为各次调用思维链片段的拼接全文，
          供 chat_service 在意图 step detail 中落库回看。
    """
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        if len(calls) == 1:
            yield ("reasoning", "第一次")
            yield ("reasoning", "思考")
            yield ("content", "坏输出")
        else:
            yield ("reasoning", "第二次思考")
            yield ("content", _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    batch = await recognizer.recognize("问题")
    assert len(batch.llm_calls) == 2
    assert batch.llm_calls[0]["reasoning"] == "第一次思考"
    assert batch.llm_calls[1]["reasoning"] == "第二次思考"


@pytest.mark.asyncio
async def test_recognize_stream_yields_reasoning_then_result(recognizer, monkeypatch):
    """【思维链流式-recognize_stream 时序】思维链先逐段实时 yield，result 必为最后一个产出。

    场景：reasoning 模型正常识别（一次调用成功）。
    前置：mock _chat_stream yield reasoning 两段 + 合法 JSON content。
    期望：
      1. recognize_stream 产出序列为 reasoning×2 → result（result 必在最后）；
      2. result 为 IntentBatch 且 used_llm=True；
      3. 早期 yield 的 reasoning 片段与 result 中 llm_calls 记录的全文一致。
    """
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        yield ("reasoning", "用户在问")
        yield ("reasoning", "会议室审批")
        yield ("content", _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    items = [item async for item in recognizer.recognize_stream("那审批流程呢？")]
    kinds = [k for k, _ in items]
    assert kinds == ["reasoning", "reasoning", "result"]
    batch = items[-1][1]
    assert batch.used_llm
    assert batch.llm_calls[-1]["reasoning"] == "用户在问会议室审批"


@pytest.mark.asyncio
async def test_recognize_stream_retry_separator(recognizer, monkeypatch):
    """【思维链流式-重试分隔】首次解析失败重试时，额外 yield 一条分隔提示片段。

    场景：第一次调用输出坏 JSON，第二次成功。
    前置：mock _chat_stream 第一次 yield 坏 content，第二次 yield 合法 JSON。
    期望：产出序列含 4 项——第一次 content 无 reasoning、分隔提示（含"重试"字样）、
          第二次调用内容、result；前端把分隔文本并入思维流避免两段拼接混淆。
    """
    monkeypatch.setattr(recognizer.settings, "intent_llm_api_key", "test-key")
    calls: list[str] = []

    async def fake_stream(self, system, user, json_mode=False, max_tokens=None, history=None):
        calls.append(user)
        if len(calls) == 1:
            yield ("content", "坏输出")
        else:
            yield ("reasoning", "重新思考")
            yield ("content", _GOOD_JSON)

    monkeypatch.setattr(IntentRecognizer, "_chat_stream", fake_stream)
    items = [item async for item in recognizer.recognize_stream("问题")]
    kinds = [k for k, _ in items]
    assert kinds == ["reasoning", "reasoning", "result"]
    assert "重试" in items[0][1]
    assert items[1][1] == "重新思考"
