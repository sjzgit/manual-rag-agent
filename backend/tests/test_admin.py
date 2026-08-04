"""管理接口与权限测试：鉴权拦截、图片路径穿越防护。"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_admin_requires_auth():
    """未带 token 访问管理接口应 401。"""
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # 不触发 lifespan（避免连接 Milvus），直接打路由验证依赖拦截
        r = await c.get("/admin/stats")
        assert r.status_code == 401
        r = await c.post("/admin/reingest")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_wrong_token():
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get(
            "/admin/stats", headers={"Authorization": "Bearer wrong-token"}
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_image_path_traversal():
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/images/..%2Fsecret/x.png")
        assert r.status_code in (400, 404)
        r = await c.get("/api/images/doc/..%2Fx.png")
        assert r.status_code in (400, 404)
        r = await c.get("/api/images/doc/evil.exe")
        assert r.status_code == 400
