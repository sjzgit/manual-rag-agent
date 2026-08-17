# 操作手册 RAG 智能体

基于 RAG（检索增强生成）的企业操作手册智能问答系统。支持意图识别、多轮澄清、稠密检索、过程可视化、来源卡片展示。

## 技术栈

| 层级   | 技术                                                              |
|--------|-------------------------------------------------------------------|
| 前端   | Vue 3 + TypeScript + Element Plus + Pinia + Vite                 |
| 后端   | FastAPI (Python 3.13) + SSE 流式回答                              |
| 向量库 | Milvus + BAAI/bge-base-zh-v1.5 (sentence-transformers)           |
| LLM    | DeepSeek-V3（OpenAI 兼容协议，可切换）                             |
| 数据库 | MySQL 8.0 (会话/反馈/审计) + Redis (限流/缓存)                    |
| 部署   | Docker Compose + Nginx 反向代理                                   |

## 架构

```
用户 → Nginx (port 80)
        ├─ /api/*   → FastAPI 后端 (port 8000)
        └─ /        → Vue 前端静态文件

FastAPI 流水线：
  意图识别 → 澄清对话(≤3轮) → Milvus 检索(top_k=2) → LLM 流式回答
                ↕                              ↕
           MySQL / Redis                   Agentic 模式(AgentScope)
```

## 目录结构

```
code/
├── backend/             # 后端
│   ├── app/             # FastAPI 应用
│   │   ├── api/         #   路由 (chat, images, feedback, admin)
│   │   ├── core/        #   配置 / 认证 / 日志
│   │   ├── db/          #   SQLAlchemy 模型与连接
│   │   ├── intent/      #   意图识别模块
│   │   ├── prompts/     #   Prompt 模板
│   │   ├── rag/         #   检索器
│   │   └── services/    #   聊天 / Agent / 会话 / 缓存服务
│   ├── tests/           # pytest 测试
│   ├── .env.example     # 环境变量模板
│   ├── requirements.txt
│   └── pytest.ini
├── frontend/            # 前端
│   ├── src/
│   │   ├── api/         #   SSE 客户端
│   │   ├── components/  #   消息 / 来源卡片 / 澄清对话框 等
│   │   ├── stores/      #   Pinia 状态 (chat)
│   │   └── views/       #   ChatView 主页面
│   ├── nginx.conf       # Nginx 配置（生产构建用）
│   └── vite.config.ts
├── docker/              # Docker 脚本
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   └── docker-compose.yml
├── sql/
│   └── init.sql         # 建表脚本（应用也会自动 create_all）
├── vector_store/        # Milvus 入库脚本（只读）
├── chat/                # 需求文档
├── docs/                # 开发规划文档
└── README.md
```

## 快速启动

### 1. 准备知识库数据

确保以下目录在 `操作手册RAG/` 下存在：
- `文档切片/` 含 `chunks.json`
- `处理后的md手册文档/` 含手册 Markdown 和图片

### 2. 配置环境变量

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 LLM_API_KEY 等配置
```

### 3. 本地开发

**后端** (Python 3.13)：
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

**前端** (Node 20)：
```bash
cd frontend
npm ci
npm run dev          # Vite 开发服务器 + API 代理
```

### 4. Docker Compose 部署

```bash
cd docker
docker compose up -d --build
```

服务端口：`http://localhost:80`（Nginx 代理前端 + 后端）

### 5. 运行测试

```bash
cd backend
pytest -v
```

## 核心功能

- **意图识别**：独立小模型基于 Prompt 直接抽取 module/role/description 并三分类（精确/模糊/不相关）；输出 JSON 先代码校验修复，失败重试一次，仍失败转澄清；不依赖关键词索引，是否收录由向量检索决定
- **多轮澄清**：模糊意图自动反问，最多 3 轮，全部精确后才检索
- **过程可视化**：前端实时展示意图分析、检索步骤、工具调用
- **来源卡片**：回答附带原文切片链接 + 图片展示
- **Agentic 模式**：可选启用 AgentScope ReAct 代理，自行规划检索步骤
- **管理端点**：`/admin/*` 反馈统计、审计日志（需 ADMIN_TOKEN）

## 配置项速查

| 变量                  | 说明               | 必填 |
|----------------------|--------------------|------|
| `LLM_API_KEY`        | 主 LLM Key         | 是   |
| `INTENT_LLM_*`       | 意图识别 LLM 配置  | 否*  |
| `MYSQL_PASSWORD`     | MySQL 密码         | 是   |
| `REDIS_URL`          | Redis 连接串       | 否   |
| `ADMIN_TOKEN`        | 管理端点认证 Token | 否   |

> \* 不配置时意图识别降级为规则匹配
