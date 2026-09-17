"""MCP 工具实现：薄层编排，复用 chat_service / retriever 服务层，无 HTTP/FastAPI 依赖。

manual_ask 复用 ChatService.handle_chat 的完整流水线（意图识别/澄清/检索/生成），
把 SSE 事件流聚合为结构化 AskResult；manual_search/manual_docs 直接包检索层。
"""
import json
from collections.abc import AsyncGenerator
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from app.core.bootstrap import Services
from app.mcp.models import AskResult, DocInfo, DocsPayload, SearchPayload, SourceItem
from app.rag.models import ChatRequest

# ---- SSE 解析与聚合 ----


def _parse_sse_block(block: str) -> tuple[str, dict[str, Any]]:
    """解析单个 SSE 块（event: x\\ndata: {json}），返回 (事件名, 数据)。"""
    event = ""
    data: dict[str, Any] = {}
    for line in block.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                data = {}
    return event, data


async def collect_ask(events: AsyncGenerator[str]) -> AskResult:
    """消费 handle_chat 的 SSE 事件流，聚合为 AskResult。

    token 顺序拼接为 answer；meta 取会话/消息 id；sources/clarify 透传；
    error 事件转 ToolError（协议层 isError=true）；过程事件一律丢弃。
    """
    answer_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    session_id = ""
    message_id: str | None = None
    clarify_question: str | None = None
    missing_fields: list[str] = []

    buf = ""
    async for raw in events:
        buf += raw
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event, data = _parse_sse_block(block)
            if event == "meta":
                session_id = str(data.get("session_id", ""))
                message_id = data.get("message_id")
            elif event == "token":
                answer_parts.append(str(data.get("text", "")))
            elif event == "sources":
                sources = list(data.get("sources", []))
            elif event == "clarify":
                clarify_question = str(data.get("question", ""))
                missing_fields = list(data.get("missing_fields", []))
            elif event == "error":
                raise ToolError(f"{data.get('code', 'UNKNOWN')}: {data.get('message', '')}")

    try:
        result = AskResult(
            status="clarify" if clarify_question is not None else "answered",
            answer="".join(answer_parts),
            sources=[SourceItem.model_validate(s) for s in sources],
            session_id=session_id,
            message_id=message_id,
            clarify_question=clarify_question,
            missing_fields=missing_fields,
        )
    except ValidationError as e:
        raise ToolError(f"sources 数据结构异常: {e}") from e
    return result


# ---- 工具实现（接收 Services，供 build_mcp_server 注册与测试直接调用） ----


async def manual_ask(
    services: Services,
    question: str,
    doc: str | None = None,
    session_id: str | None = None,
    clarify_answer: str | None = None,
) -> AskResult:
    """端到端问答实现；参数语义见 build_mcp_server 中注册的 tool docstring。"""
    req = ChatRequest(
        session_id=session_id,
        question=question,
        doc=doc,
        clarify_answer=clarify_answer,
    )
    return await collect_ask(services.chat_service.handle_chat(req))


async def manual_search(
    services: Services,
    question: str,
    doc: str | None = None,
    top_n: int | None = None,
) -> SearchPayload:
    """纯检索实现；参数语义见 build_mcp_server 中注册的 tool docstring。"""
    outcome = await services.retriever.search_and_rerank(
        question, doc=doc, reranker=services.reranker, top_n=top_n
    )
    items = [_to_source_item(services, h) for h in outcome.final_hits]
    return SearchPayload(mode=outcome.mode, total=len(items), results=items)


async def manual_docs(services: Services) -> DocsPayload:
    """文档列表实现；参数语义见 build_mcp_server 中注册的 tool docstring。"""
    docs = services.retriever.list_docs()
    return DocsPayload(
        total=len(docs),
        docs=[DocInfo(doc=d["doc"], parents=d["parents"]) for d in docs],
    )


def _to_source_item(services: Services, hit: Any) -> SourceItem:
    """命中结果 → SourceItem，按配置把图片相对路径改写为绝对 URL。"""
    chunk = services.retriever.to_source_chunk(hit)
    item = SourceItem(
        chunk_id=chunk.chunk_id,
        doc=chunk.doc,
        path=chunk.path,
        score=chunk.score,
        content=chunk.content,
        images=chunk.images,
    )
    base = services.settings.mcp_public_base_url
    if base:
        item.content = item.content.replace("/api/images/", f"{base}/api/images/")
        item.images = [u.replace("/api/images/", f"{base}/api/images/") for u in item.images]
    return item
