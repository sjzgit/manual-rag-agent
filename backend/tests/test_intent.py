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
