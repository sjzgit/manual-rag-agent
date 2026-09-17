"""MCP 服务端装配：FastMCP 工具注册 + 可选静态 API Key 鉴权 + Streamable HTTP 启动。

复用 core/bootstrap 的 init_services 单例序列（与 FastAPI main.py 共用，禁止另起一份）。
不用 FastMCP(lifespan=...)（services 经参数闭包注入），也不走 run_streamable_http_async()
（无法包鉴权中间件），而是取 streamable_http_app() 自起 uvicorn。
"""
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.bootstrap import Services, init_services
from app.core.config import Settings
from app.core.logging import get_logger
from app.mcp import tools
from app.mcp.models import AskResult, DocsPayload, SearchPayload

logger = get_logger(__name__)

_INSTRUCTIONS = """操作手册知识库服务，提供三类工具：
- manual_ask：端到端问答（意图识别 / 澄清 / 混合检索+重排 / LLM 生成回答与来源）。
- manual_search：仅检索不生成，返回相关手册切片（适合自行组织返回的结构化引用）。
- manual_docs：列出知识库中可检索的手册清单。

建议优先用 manual_search 了解知识库范围，再用 manual_ask 获取带来源的完整回答；
涉及某本手册的连续追问请保持同一 session_id。"""


def build_mcp_server(services: Services) -> FastMCP:
    """注册三个工具并返回 FastMCP 实例；工具经闭包捕获 services，无全局状态。"""
    settings = services.settings
    mcp = FastMCP(
        name="manual-rag",
        instructions=_INSTRUCTIONS,
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=settings.mcp_path,
        stateless_http=services.settings.mcp_stateless,
    )

    @mcp.tool()
    async def manual_ask(
        question: str,
        doc: str | None = None,
        session_id: str | None = None,
        clarify_answer: str | None = None,
    ) -> AskResult:
        """对《操作手册》知识库做端到端问答：意图识别 →（必要时澄清）→ 混合检索+重排 → LLM 生成回答与来源。

        返回 status="answered"（answer + sources）或 status="clarify"（clarify_question
        + missing_fields）。status="clarify" 时：把 clarify_question 转述给用户，
        将用户补充作为 clarify_answer、连同返回的 session_id 再次调用本工具
        （最多 3 轮，超限自动按最相关切片直答）。
        question 应为与企业系统操作相关的具体问题；doc 可限定某本手册名（可用 manual_docs 查询）；
        同一对话的连续调用请始终携带首次返回的 session_id（保持澄清状态与多轮上下文）。

        Args:
            question: 用户的完整问题
            doc: 可选，限定检索某本手册名（用 manual_docs 查询可用清单）
            session_id: 可选，多轮对话/澄清重入时传上次调用返回的会话 id
            clarify_answer: 可选，澄清重入时传用户对上一轮澄清问题的补充回答
        """
        return await tools.manual_ask(services, question, doc, session_id, clarify_answer)

    @mcp.tool()
    async def manual_search(
        question: str,
        doc: str | None = None,
        top_n: int | None = None,
    ) -> SearchPayload:
        """仅检索《操作手册》知识库，不调用 LLM 生成：稠密(BGE) + 稀疏(BM25) 双路召回 → RRF 融合 → 阈值过滤 → 按父切片去重 → 重排（未配置时降级融合序）。

        返回最相关切片列表（doc/path/score；content 为父切片完整内容，含图片链接）。
        适合调用方自行组织上下文、做引用溯源或对检索结果二次加工；
        想直接获得面向用户的完整回答请用 manual_ask。

        Args:
            question: 检索问题
            doc: 可选，限定检索某本手册名（用 manual_docs 查询可用清单）
            top_n: 可选，返回条数上限（默认取服务端 rerank_top_n 配置）
        """
        return await tools.manual_search(services, question, doc, top_n)

    @mcp.tool()
    async def manual_docs() -> DocsPayload:
        """列出知识库中当前可检索的全部手册名及各自父切片数量。

        用于让调用方了解知识库范围，或让用户指定 manual_search / manual_ask 的 doc 参数。
        """
        return await tools.manual_docs(services)

    return mcp


class ApiKeyMiddleware:
    """可选静态 API Key 鉴权（纯 ASGI 中间件）。

    mcp_api_key 为空直接放行；非空时要求请求头 X-API-Key 或 Authorization: Bearer。
    日志只记拒绝事件，禁止记录 key 本身。
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.api_key:
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        provided = headers.get("x-api-key", "")
        if not provided:
            auth = headers.get("authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[len("Bearer ") :]
        if provided != self.api_key:
            logger.warning("mcp_auth_rejected", path=scope.get("path", ""))
            await send_401(send)
            return
        await self.app(scope, receive, send)


async def send_401(send: Send) -> None:
    """鉴权失败的 401 JSON 短路响应。"""
    body = b'{"error": "unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_asgi_app(mcp: FastMCP, settings: Settings) -> ASGIApp:
    """构建 Streamable HTTP ASGI 应用并包一层可选鉴权中间件。"""
    app = mcp.streamable_http_app()
    return ApiKeyMiddleware(app, settings.mcp_api_key)


async def serve() -> None:
    """MCP 服务主入口：初始化服务单例 → 装配工具 → 起 Streamable HTTP 服务。"""
    async with init_services() as services:
        settings = services.settings
        mcp = build_mcp_server(services)
        app = create_asgi_app(mcp, settings)
        logger.info(
            "mcp_server_started",
            host=settings.mcp_host,
            port=settings.mcp_port,
            path=settings.mcp_path,
            stateless=settings.mcp_stateless,
            auth=bool(settings.mcp_api_key),
        )
        config = uvicorn.Config(
            app,
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level="info",
        )
        await uvicorn.Server(config).serve()
