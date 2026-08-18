# 操作手册 RAG 项目

> 本文件约束后续所有开发工作。修改代码前先读此文件；与 README/docs 冲突时以本文件与实际代码为准。

## 1. 项目简介

基于 RAG 的企业操作手册智能问答系统。核心流水线：**意图识别 → 多轮澄清(≤3轮) → Milvus 稠密检索(子切片 top_k=8 + 父切片回溯) → LLM 流式回答(SSE)**，前端全程过程可视化 + 来源卡片展示；另含后台管理端（知识库管理 / 提示词管理 / 会话管理，入口 `/admin`）。

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3.5 + TypeScript(strict) + Element Plus + Pinia + Vite 5 + Tailwind CSS 3.4 |
| 后端 | FastAPI + Python 3.13（全异步）+ Pydantic v2 / pydantic-settings |
| 检索 | Milvus(外部 192.168.0.215:19530) + BAAI/bge-base-zh-v1.5 |
| LLM | DeepSeek（OpenAI 兼容协议）；意图识别用独立小模型，未配置时规则降级 |
| Agent | AgentScope `>=2.0.5,<2.1`（ReAct），`RAG_MODE=agentic` 时启用，默认 generic |
| 持久化 | MySQL 8.0（SQLAlchemy 2.0 async + aiomysql）+ Redis（缓存/限流） |
| 部署 | Docker Compose（backend / web-Nginx / mysql / redis） |

## 2. 常用命令

```bash
# 后端（依赖在 pyproject.toml，无 requirements.txt）
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload   # 本地开发端口 8001（vite 代理指向它）

# 前端
cd frontend
npm ci
npm run dev        # Vite 开发服务器，/api /chat 等代理到 127.0.0.1:8001
npm run build      # vue-tsc 类型检查 + 构建

# 质量检查（提交前必过）
cd backend
ruff check app tests && ruff format --check app tests
pyright
pytest -v

# 启用 README 自动同步（首次 clone 后执行一次；之后每次 commit 自动把 AGENTS.md 同步为 README.md）
git config core.hooksPath .githooks

# 部署
cd docker && docker compose up -d --build    # http://localhost:80
```

## 3. 项目架构

### 3.1 分层与依赖方向

```
api（路由/SSE，薄层） → services（编排/流水线） → intent / agent / rag（能力层） → db（持久化）
                                            core（配置/认证/日志）与 prompts、utils 横切支撑
```

- **依赖单向向下**，禁止下层 import 上层；模块间通信用显式参数传递或 pydantic 模型。
- 全局单例在 [main.py](backend/app/main.py) `lifespan` 中初始化并挂到 `app.state`（settings / db / prompt_service / retriever / knowledge / recognizer / session_service / redis / chat_service），路由层通过 `request.app.state.xxx` 取用，**不重复实例化**。注意初始化顺序：db → prompt_service → retriever（retriever 需从 MySQL chunks 表加载父子映射）。
- 服务无状态：会话历史每轮从 MySQL 重建（支持水平扩展）；澄清状态存 sessions.clarify_state，DB 不可用时内存兜底。
- BGE 模型 / Milvus 连接为 `retriever` 持有的单例，知识库向量化入库复用该单例（`retriever.embed` / `retriever.upsert_children`），**不重复加载模型**。

### 3.2 目录结构与文件存放规则

```
code/
├── backend/
│   ├── app/
│   │   ├── api/          # 路由层：一个业务域一个文件（chat/images/feedback/admin_kb/admin_prompt/admin_session），只做参数校验与透传
│   │   ├── core/         # config.py(全部环境变量) / auth.py / logging.py
│   │   ├── db/           # models.py(全部 ORM 表) / session.py(engine/session 工厂)
│   │   ├── knowledge/    # 知识库能力：converter.py(docx→md) + chunker.py(父子切片) + models.py，纯代码非 LLM
│   │   ├── intent/       # 意图识别：recognizer.py + models.py(IntentResult/ClarifyState)
│   │   ├── agent/        # AgentScope Agent 工厂
│   │   ├── prompts/      # Prompt 模板：一个业务域一个文件，常量导出（兼作 prompt_templates 默认值）
│   │   ├── rag/          # retriever.py(子切片检索+父子回溯) + models.py(ChatRequest/SearchResult/SourceChunk)
│   │   ├── services/     # 业务编排：chat_service / knowledge_service / prompt_service / session_service / cache / agent_runner
│   │   └── utils/        # sse.py 等通用工具
│   ├── tests/            # pytest，与被测模块同名（test_chat/test_intent/test_retriever/test_admin）
│   ├── pyproject.toml    # 依赖 + ruff/pyright/pytest 配置，唯一事实来源
│   └── .env / .env.example
├── frontend/
│   └── src/
│       ├── api/          # 所有 HTTP/SSE 请求只在此层（chat.ts C端 / admin.ts 管理端）
│       ├── router/       # vue-router：/ → ChatView；/admin → AdminLayout（知识库/提示词/会话）
│       ├── components/   # 可复用组件，PascalCase.vue（MessageBubble/SourceCard/StepPanel/ClarifyCard…）
│       ├── stores/       # Pinia store（chat.ts），组件不直接发请求
│       ├── views/        # 页面级组件（ChatView.vue + admin/ 管理端三页面）
│       └── types.ts      # 全局类型唯一出处，与后端 SSE/管理端契约一一对应
├── sql/
│   ├── init.sql          # 全量基线（只增不改）
│   └── migrations/       # V00X__desc.sql 版本化增量（详见 migrations/README.md）
├── docker/               # Dockerfile.* 与 docker-compose.yml
├── vector_store/         # 现有入库脚本，【只读，禁止改动】
├── chat/                 # 需求文档
└── docs/                 # 开发规划等文档
```

**新文件存放**：后端按上述分层归位（新路由→`api/`，新业务编排→`services/`，新能力→对应能力目录并配 `models.py`），数据模型进所在模块的 `models.py`，ORM 表一律进 `db/models.py`；前端新组件进 `components/`（页面私有小组件可放 `views/` 同级目录），新接口进 `api/`，新状态进 `stores/`，跨文件类型进 `types.ts`。不确定归属时优先贴近现有同类文件。

## 4. 开发规范

### 4.1 通用

- 所有配置经 [config.py](backend/app/core/config.py) `Settings` 读取（.env），**密钥不落代码、不进日志、不提交**；新增配置项必须同步 `.env.example` 并给安全默认值。
- 提交前：后端 `ruff check` + `pyright` + `pytest` 全绿；前端 `npm run build`（含 vue-tsc）通过。
- Git 提交信息用中文短句概述变更（参考历史："完善后端项目配置"、"BUG"→ 应避免无信息量的消息）。
- 知识库数据（`文档切片/chunks.json`、`meta_data.json`、`处理后的md手册文档/`）**只读使用**，任何代码不得写入；管理端上传/转换/图片写入走独立目录 `uploads/`（`UPLOAD_ROOT`），不得与只读知识库目录混用。

### 4.2 后端（FastAPI / Python 3.13）

- **全异步**：路由与服务方法一律 `async def`；同步阻塞调用（pymilvus、sentence-transformers/BGE 编码、mammoth docx 转换）必须包 `asyncio.to_thread`。
- **路由保持薄**：参数校验（pydantic）+ 调 service + 返回，业务逻辑不写在 `api/`。
- 数据契约用 pydantic 模型表达（各模块 `models.py`），跨层传 dict 时须先 `model_dump()`；接口出入参字段用 snake_case。
- 日志用 `structlog` 的 `get_logger(__name__)`，事件式键值对（`logger.info("app_started", ...)` 风格），记录 session_id / 各阶段耗时 / 命中数；**禁止记录 API Key 与完整 Prompt 中的敏感信息**。
- 依赖版本在 `pyproject.toml` 中区间锁定（如 `fastapi>=0.115,<0.116`），新增依赖必须设上界。
- 外部依赖（MySQL/Redis/Milvus/LLM）一律优雅降级：不可用时功能降级或直通，启动日志标记可用性，不允许崩溃。
- 测试：pytest + pytest-asyncio，LLM 一律 mock；检索质量用 `tests/eval_questions.json` 评测集验证。

### 4.3 前端（Vue 3 + TS）

- 一律 `<script setup lang="ts">` 组合式 API；组件名 PascalCase，与文件名一致。
- **分层**：`views/` 组合 `components/` 并消费 `stores/`；请求只在 `api/`；状态（会话/消息流/澄清/生成中）集中在 Pinia，组件只做展示与交互。
- SSE 用 fetch + ReadableStream（[api/chat.ts](frontend/src/api/chat.ts) 的 `streamChat` 模式），支持 AbortController 中断；**不用 EventSource**（需 POST）。
- 类型：新字段先加 `types.ts` 再使用，保持与后端 pydantic/SSE 契约同步（改动契约必须两侧同改）。
- 样式：Tailwind 原子类优先，复杂组件局部 `<style scoped>`；主题色主 `#3B5BFD` / 背景 `#F5F7FB` / 正文 `#1F2329`，功能色绿 `#34A853` 红 `#F5222D` 橙 `#FA8C16`；动效 200~300ms ease，克制使用。
- 严谨 TS：`strict: true`，禁止 `any` 滥用（与后端契约对应的 `Record<string, any>` 除外）；提交前 `vue-tsc` 通过。

### 4.4 数据库（MySQL 8.0）

- **表结构变更三同步**（强约束，详见 [sql/migrations/README.md](sql/migrations/README.md)）：
  1. `sql/migrations/` 新增 `V{三位递增}__描述.sql`（幂等写法，历史脚本禁止修改）；
  2. 同步更新 `sql/init.sql` 基线；
  3. 同步更新 [backend/app/db/models.py](backend/app/db/models.py) ORM 模型。
- 命名：表/字段全小写下划线，表名复数（sessions/messages）；索引 `idx_表_用途`。
- 建表统一：`ENGINE=InnoDB`、`utf8mb4 / utf8mb4_unicode_ci`；每表每字段必写中文 `COMMENT`；时间用 `DATETIME DEFAULT CURRENT_TIMESTAMP`（更新时间加 `ON UPDATE`）。
- 主键策略：业务表（sessions/messages/users/source_documents）用 `VARCHAR(64)` 应用层生成的 uuid hex；日志/反馈表用 `INT AUTO_INCREMENT`；切片表 `chunks` 用 `VARCHAR(512)` 存业务语义 id（父=`{doc}_{path}`、子=`{父id}::c{child_index}`）。
- 关联用**逻辑外键**（session_id/message_id 字段 + 显式索引），不建物理外键；高频查询路径（按会话查消息/日志）必须建索引。
- 结构化数据（sources/steps/clarify_state/sub_question）存 JSON 列；JSON 列内的结构变更不算表结构变更，但 ORM 模型与前端类型须同步。
- 禁止 `SELECT *` 于正式查询、禁止在循环内逐条查询（N+1）；写操作走 `session_service` 聚合，不散落各处。

## 5. 关键契约（改动需评审）

1. **SSE 事件协议**（[utils/sse.py](backend/app/utils/sse.py) ↔ 前端 `types.ts`/`stores/chat.ts`）：
   事件类型 `meta / step / clarify / token / sources / done / error`，字段见 `sse.py`；新增事件类型须前后端同步并考虑历史会话回看兼容。
2. **意图识别输出** `IntentResult`：`input / simple_input / module / role / description / intent_type(irrelevant|precise|vague) / missing_fields / clarify_question / intent_reason`。纯 Prompt 驱动（不依赖 meta_data.json，不做切片匹配，收录与否由向量检索决定）；LLM 输出 JSON 先代码校验修复、失败重试一次、仍失败判 vague 友好提示。
3. **回答兜底话术**：检索不到必须回复"手册中未找到"；irrelevant 固定话术拒答——两者来自 `prompts/manual.py` 常量（经 `prompt_service.get` 读取，DB 可编辑），不硬编码在业务代码。
4. **图片路由** `/api/images/{doc}/{filename}` 必须做路径穿越校验；markdown 中 `./media/x.png` 改写为该路由 URL（管理端上传的图片优先取 `uploads/{doc}/media/`、回退只读 `media_root`）。
5. **澄清轮次上限** `MAX_CLARIFY_ROUNDS=3`，超限按最相关切片直接回答并提示。
6. **父子切片检索链路**（v2.0）：Milvus 只入子切片（collection `manual_rag_child_chunks`），父切片存 MySQL `chunks` 表；检索走「子切片命中 → `child_id→parent_id` 回溯 → 按父去重 → 按 doc+chunk_index 文档序排序」；`retrieve_top_k` 默认 8；来源卡片子 path 定位 + 父 content 展示。
7. **管理端 API**：统一前缀 `/admin/api/*`（与 SPA 页面路由 `/admin` 区分）；本期不做登录/权限。

## 6. 红线

- `vector_store/`、知识库数据目录：只读，禁止改动/写入。
- 不修改、不回滚已发布的 migration 脚本；`init.sql` 只增不改。
- 不在代码/日志/提交中泄露任何 Key；`.env` 不提交仓库。
- 不引入新的重型全局状态（全局可变单例须经 lifespan 管理生命周期）。
- 不破坏降级链路：意图小模型未配置→规则匹配；Redis 不可用→直通；MySQL 不可用→内存兜底并日志告警。
