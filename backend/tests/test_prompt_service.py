"""提示词模板服务单元测试：seed / 读取 / 更新 / DB 不可用回退。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.prompt_service import PromptService


def _db(available=True):
    db = MagicMock()
    db.available = available
    if available:
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        result = MagicMock()
        result.scalars = MagicMock(return_value=[])
        result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        session.add = MagicMock()
        db.session = MagicMock(return_value=session)
    return db


@pytest.mark.asyncio
async def test_get_falls_back_to_default():
    svc = PromptService(_db(available=True))
    await svc.reload()  # cache 空
    assert svc.get("not_found_reply")
    assert "手册中未找到" in svc.get("not_found_reply")


@pytest.mark.asyncio
async def test_update_writes_and_caches():
    svc = PromptService(_db(available=True))
    assert await svc.update("not_found_reply", "自定义话术")
    assert svc.get("not_found_reply") == "自定义话术"


@pytest.mark.asyncio
async def test_update_unknown_key_fails():
    svc = PromptService(_db(available=True))
    assert not await svc.update("unknown_key", "x")


@pytest.mark.asyncio
async def test_db_unavailable_degrades():
    svc = PromptService(_db(available=False))
    await svc.startup()
    assert svc.get("irrelevant_reply")  # 回退代码常量
    assert not await svc.update("irrelevant_reply", "x")  # 写库失败返回 False


@pytest.mark.asyncio
async def test_list_returns_all_bound_keys():
    svc = PromptService(_db(available=True))
    await svc.reload()
    items = await svc.list()
    keys = {i["key"] for i in items}
    assert {"intent_system", "answer_system", "agentic_system", "not_found_reply",
            "irrelevant_reply", "clarify_exhausted_prefix"} <= keys
