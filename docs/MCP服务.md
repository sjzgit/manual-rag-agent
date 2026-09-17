# MCP 服务：操作手册 RAG 能力开放

> 将本项目的 RAG 能力（意图识别 → 澄清 → 混合检索+重排 → LLM 生成）封装为 MCP（Model Context Protocol）服务，供其他智能体以标准协议调用。

## 1. 架构

```
其他智能体 ──Streamable HTTP(8002 /mcp)──▶ app/mcp/server.py（FastMCP + 可选 API Key 中间件）
                                              │ 闭包注入 Services
                                        app/mcp/tools.py（manual_ask / manual_search / manual_docs）
                                              │ 复用
                    ChatService.handle_chat / ManualRetriever.search_and_rerank / list_docs
                                              │
                    服务层单例由 core/bootstrap.py 的 init_services() 统一初始化
                    （与 FastAPI main.py 共用同一份序列，禁止另起一份）
```

- **独立进程**：与 API 服务（`python -m app.main`，8001）互不影响，共享 `backend/.env` 配置与同一套服务层代码。
- **传输**：Streamable HTTP，默认 `http://<host>:8002/mcp`，`mcp_stateless=true`（每请求独立传输，无会话亲和，适配多智能体并发）。
- **结构化输出**：工具返回 pydantic 模型，MCP 协议层自动产出 outputSchema / structuredContent，调用方拿到可编程结构而非纯文本。

## 2. 启动

```bash
# 本地（依赖同后端：cd backend && pip install -e ".[dev]" 已装）
cd backend
uv run python -m app.mcp          # 监听 MCP_HOST:MCP_PORT（默认 0.0.0.0:8002）

# Docker（与 backend 同镜像换启动命令）
cd docker && docker compose up -d mcp
```

启动日志 `mcp_server_started` 会带 host/port/path/auth 信息；服务层单例初始化失败自动降级（见 §6），不会崩溃。

## 3. 工具契约

### 3.1 `manual_ask` — 端到端问答

复用与 Web 端完全一致的流水线（意图识别 / 澄清 / 关联手册优先检索 / 混合检索+重排 / LLM 生成）。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| question | str | 用户的完整问题 |
| doc | str? | 限定检索某本手册名（用 manual_docs 查询可用清单） |
| session_id | str? | 多轮对话/澄清重入时传上次调用返回的会话 id |
| clarify_answer | str? | 澄清重入时传用户对上一轮澄清问题的补充回答 |

出参 `AskResult`（answered 场景示例）：

```json
{
  "status": "answered",
  "answer": "在移动端进入『资产管理』→ 点击学生卡 → 选择『重置密码』…",
  "sources": [
    {"chunk_id": "xxx::c2", "doc": "一卡通系统操作手册", "path": "系统管理 > 账号管理",
     "score": 0.767, "content": "（父切片完整 markdown）", "images": ["/api/images/…"]}
  ],
  "session_id": "abc123",
  "message_id": "def456",
  "clarify_question": null,
  "missing_fields": []
}
```

**澄清重入协议**（status="clarify" 时）：问题过于模糊会先澄清（≤3 轮，超限自动按最相关切片直答）。调用方应把 `clarify_question` 转述给用户，将用户补充作为 `clarify_answer`、连同返回的 `session_id` 再次调用：

```json
{ "question": "怎么打印", "clarify_answer": "成绩单", "session_id": "abc123" }
```

**兜底约定**：irrelevant（闲聊/无关）与"手册中未找到"场景均返回 `status="answered"` 且 `sources=[]`，`answer` 为固定兜底话术；调用方以 `sources` 是否为空判断回答有无引用支撑。

### 3.2 `manual_search` — 纯检索（不调用 LLM）

稠密(BGE) + 稀疏(BM25) 双路召回 → RRF 融合 → 阈值过滤 → 按父切片去重 → 重排（未配置 rerank 时降级融合序）。适合调用方自行组织上下文、引用溯源或二次加工。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| question | str | 检索问题 |
| doc | str? | 限定手册名 |
| top_n | int? | 返回条数上限（默认取服务端 `RERANK_TOP_N`） |

出参 `SearchPayload`：`{ "mode": "hybrid-rerank" | "hybrid" | "dense", "total": N, "results": [SourceItem…] }`，`mode` 反映实际生效的检索方式。

### 3.3 `manual_docs` — 文档列表

无入参。出参 `DocsPayload`：`{ "total": 38, "docs": [{"doc": "手册名", "parents": 父切片数} …] }`。数据取自检索层的父子映射，**有且只有当前可检索的手册**（MySQL 不可用时依然准确，不会列出向量化失败的文档）。

## 4. 调用方接入

- **URL / 鉴权**：`http://<host>:8002/mcp`；配置 `MCP_API_KEY` 后需带 `X-API-Key: <key>` 或 `Authorization: Bearer <key>` 头。
- **超时**：`manual_ask` 含完整 LLM 生成，建议客户端 `read_timeout_seconds` 调大（30~120s 量级）。
- **session 建议**：每个对话使用独立 `session_id`（限流按 session 计，`RATE_LIMIT_PER_MINUTE`）；连续追问保持同一 session 以获得澄清与多轮上下文。
- Python 客户端示例：

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://127.0.0.1:8002/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("manual_ask", {"question": "如何重置学生卡密码"})
            print(res.structuredContent)

asyncio.run(main())
```

Claude / Cursor 等宿主（streamable HTTP）：

```json
{
  "mcpServers": {
    "manual-rag": {
      "type": "http",
      "url": "http://127.0.0.1:8002/mcp",
      "headers": { "X-API-Key": "<key>" }
    }
  }
}
```

## 5. 配置项（backend/.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| MCP_HOST | 0.0.0.0 | 监听地址 |
| MCP_PORT | 8002 | 监听端口（本地 API 服务为 8001，互不冲突） |
| MCP_PATH | /mcp | Streamable HTTP 端点路径 |
| MCP_STATELESS | true | 每请求独立传输，无会话亲和 |
| MCP_API_KEY | 空 | 留空不鉴权；非空校验 X-API-Key / Bearer |
| MCP_PUBLIC_BASE_URL | 空 | 留空来源图片保持 `/api/images/...` 相对路径；配置后（如 `http://192.168.0.215:8001`）改写为绝对 URL，供跨机器调用方直接访问图片 |

## 6. 降级行为

| 依赖不可用 | 行为 |
| --- | --- |
| MySQL | 会话不落库、无跨调用多轮记忆；澄清状态内存兜底（同一 session_id 仍可 ≤3 轮澄清） |
| Milvus | 检索无结果，manual_ask 返回"手册中未找到"兜底话术，manual_search 返回空列表 |
| LLM API | manual_ask 生成阶段失败 → error 事件 → MCP 工具错误（isError=true）；manual_search / manual_docs 不受影响 |
| rerank Key | 自动降级融合序（mode 变为 "hybrid"） |
| Redis | 限流直通 |

## 7. 修改契约须知

- 工具出参模型在 `backend/app/mcp/models.py`，**改动须同步本文档**；
- 工具 description 即智能体看到的接口说明，改参数/行为须同步 `app/mcp/server.py` 的 docstring；
- 意图/兜底话术等上游契约变更（prompts、SSE 事件）会自动透传到 manual_ask，无需改 MCP 层，但需回归 `tests/test_mcp.py`。
