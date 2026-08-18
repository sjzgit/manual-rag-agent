-- ============================================================================
-- 操作手册 RAG 数据库初始化脚本（系统基线，只增不改）
-- 用途：全新环境一键初始化。已上线环境的结构变更请使用 sql/migrations/ 下的版本化迁移脚本，勿直接修改本文件。
-- 应用启动时会自动 create_all（仅建表、不建库），此脚本负责建库 + 建表，供手动部署/审计使用。
-- 执行方式：mysql -u root -p < sql/init.sql
-- ============================================================================

CREATE DATABASE IF NOT EXISTS manual_rag
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE manual_rag;

-- ----------------------------------------------------------------------------
-- 会话表：一次问答会话的元数据
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id            VARCHAR(64)  PRIMARY KEY COMMENT '会话ID（时间戳+随机数生成）',
    title         VARCHAR(256) NOT NULL DEFAULT '新会话' COMMENT '会话标题（默认"新会话"，用户提问后自动取问题前50字）',
    doc_filter    VARCHAR(256) NULL COMMENT '文档过滤条件（限定检索的文档名，为空表示全部文档）',
    clarify_state JSON         NULL COMMENT '澄清追问状态（ClarifyState 的 JSON 序列化，包含已追问轮数与选项）',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间（会话列表按此倒序）'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会话表';

-- ----------------------------------------------------------------------------
-- 消息表：会话中的对话消息（用户提问与 AI 回答）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id         VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '消息ID（时间戳+随机数生成）',
    session_id VARCHAR(64) NOT NULL COMMENT '所属会话ID（逻辑外键，指向 sessions.id，删除会话时需一并删除）',
    role       VARCHAR(16) NOT NULL COMMENT '消息角色：user=用户提问，assistant=AI回答',
    content    TEXT        NOT NULL COMMENT '消息正文（assistant 消息为 Markdown，可含 /api/images/ 图片链接）',
    sources    JSON        NULL COMMENT 'AI回答的检索来源（SourceChunk JSON 数组：chunk_id/doc/path/score/content/images）',
    steps      JSON        NULL COMMENT '思考过程步骤（StepEvent JSON 数组：type/title/detail），历史会话回看时展示',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（历史消息按此正序）',
    INDEX idx_session (session_id) COMMENT '按会话查询消息的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='消息表';

-- ----------------------------------------------------------------------------
-- 反馈表：用户对 AI 回答的赞/踩
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedbacks (
    id         INT         NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '反馈记录自增ID',
    message_id VARCHAR(64) NOT NULL COMMENT '被反馈的消息ID（逻辑外键，指向 messages.id）',
    score      INT         NOT NULL COMMENT '反馈分值：1=赞，-1=踩',
    comment    TEXT        NOT NULL COMMENT '踩时的补充说明（无输入时为空串，默认值由应用层保证）',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '反馈时间',
    INDEX idx_message (message_id) COMMENT '按消息查询反馈的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='反馈表';

-- ----------------------------------------------------------------------------
-- 反馈工单表：将用户反馈归集为工单，支持状态/优先级/处理结果管理
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tickets (
    id           INT          NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '工单自增ID',
    title        VARCHAR(256) NOT NULL COMMENT '工单标题',
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT '工单状态：pending=待处理，processing=处理中，resolved=已处理，closed=已关闭',
    priority     VARCHAR(16)  NOT NULL DEFAULT 'medium' COMMENT '工单优先级：high=高，medium=中，low=低',
    result       TEXT         NULL COMMENT '处理结果（可空，处理完成后填写）',
    processed_at DATETIME     NULL COMMENT '处理时间（状态进入已处理/已关闭时记录，回到待处理/处理中时清空）',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    INDEX idx_status (status) COMMENT '按状态筛选工单的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='反馈工单表';

-- ----------------------------------------------------------------------------
-- 反馈-工单多对多关联表：一条反馈可挂多个工单，一个工单可含多条反馈
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback_tickets (
    id          INT      NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '关联记录自增ID',
    feedback_id INT      NOT NULL COMMENT '反馈ID（逻辑外键，指向 feedbacks.id）',
    ticket_id   INT      NOT NULL COMMENT '工单ID（逻辑外键，指向 tickets.id）',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '关联时间',
    UNIQUE KEY uk_feedback_ticket (feedback_id, ticket_id) COMMENT '防重复关联（同一反馈同一工单仅一条）',
    INDEX idx_ticket (ticket_id) COMMENT '按工单查关联反馈的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='反馈-工单多对多关联表';

-- ----------------------------------------------------------------------------
-- 意图识别日志表：记录每轮提问拆解出的子问题与意图
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intent_logs (
    id             INT          NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '日志自增ID',
    session_id     VARCHAR(64)  NOT NULL COMMENT '所属会话ID',
    message_id     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '触发本轮意图识别的用户消息ID',
    sub_question   JSON         NOT NULL COMMENT '子问题信息（IntentResult JSON：子问题文本/回答/对应意图）',
    intent_type    VARCHAR(16)  NOT NULL COMMENT '意图类型（single=单意图，multi=多意图，clarify=需澄清等）',
    intent_reason  VARCHAR(512) NOT NULL DEFAULT '' COMMENT '意图判断理由',
    used_llm       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否调用了 LLM 做意图识别（0=未用，1=用了）',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    INDEX idx_session_intent (session_id) COMMENT '按会话查询意图日志的索引',
    INDEX idx_intent_type (intent_type) COMMENT '按意图类型统计的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='意图识别日志表';

-- ----------------------------------------------------------------------------
-- 检索日志表：记录每次向量检索命中的切片
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id           INT          NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '日志自增ID',
    session_id   VARCHAR(64)  NOT NULL COMMENT '所属会话ID',
    message_id   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '触发本次检索的用户消息ID',
    sub_question VARCHAR(1024) NOT NULL COMMENT '本次检索的子问题文本',
    chunk_id     VARCHAR(512) NOT NULL COMMENT '命中的向量切片ID（对应 chunks.json 中的 chunk id）',
    doc          VARCHAR(256) NOT NULL COMMENT '切片所属文档名',
    path         VARCHAR(1024) NOT NULL COMMENT '切片在文档中的路径（章节层级）',
    score        DOUBLE       NOT NULL COMMENT '检索相似度得分',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    INDEX idx_session_retrieval (session_id) COMMENT '按会话查询检索日志的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='检索日志表';

-- ----------------------------------------------------------------------------
-- 审计日志表：管理端操作审计（预留）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INT         NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '日志自增ID',
    user_id    VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作人ID（当前版本无登录体系，默认为空）',
    action     VARCHAR(64) NOT NULL COMMENT '操作类型（如 user_login/create_user/reingest 等）',
    detail     JSON        NULL COMMENT '操作详情（JSON，如变更前后值、请求参数）',
    ip         VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作来源IP',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    INDEX idx_user_audit (user_id) COMMENT '按操作人查询审计日志的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审计日志表';

-- ----------------------------------------------------------------------------
-- 用户表：管理端账号
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '用户ID（时间戳+随机数生成）',
    username      VARCHAR(128) NOT NULL UNIQUE COMMENT '登录用户名（唯一）',
    password_hash VARCHAR(256) NOT NULL COMMENT '密码哈希（SHA256(salt+password)，不存明文）',
    role          VARCHAR(32)  NOT NULL DEFAULT 'user' COMMENT '角色：user=普通用户，admin=管理员',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- ----------------------------------------------------------------------------
-- 源文档表：记录上传的原始 docx 及其转换产物与处理状态
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_documents (
    id                 VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '文档ID（uuid4.hex 生成）',
    doc_name           VARCHAR(256) NOT NULL UNIQUE COMMENT '手册名（唯一，重复上传按此覆盖）',
    original_file_path VARCHAR(512) NOT NULL COMMENT '原始 docx 上传路径（相对 upload_root）',
    md_file_path       VARCHAR(512) NULL COMMENT '转换清洗后的 md 文件路径（相对 upload_root）',
    preview_file_path  VARCHAR(512) NULL COMMENT 'word 预览 HTML 文件路径（相对 upload_root）',
    file_size          INT          NOT NULL DEFAULT 0 COMMENT '原始文件大小（字节）',
    status             VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT '处理状态：pending=待处理，processing=处理中，success=成功，failure=失败',
    error_message      TEXT         NULL COMMENT '处理失败原因',
    chunk_count        INT          NOT NULL DEFAULT 0 COMMENT '切片总数（含父切片与子切片）',
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    INDEX idx_doc_name (doc_name) COMMENT '按手册名查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='源文档表';

-- ----------------------------------------------------------------------------
-- 切片表：父子切片（v2.0）。父切片=H3 完整功能模块（不向量化），子切片=H4/语义段（向量化）。
-- 父 id = {doc}_{path}；子 id = {父id}::c{child_index}
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id           VARCHAR(512) NOT NULL PRIMARY KEY COMMENT '切片ID（父={doc}_{path}，子={父id}::c{child_index}）',
    doc_id       VARCHAR(64)  NOT NULL COMMENT '所属源文档ID（逻辑外键，指向 source_documents.id）',
    doc          VARCHAR(256) NOT NULL COMMENT '手册名（冗余，供检索过滤与展示）',
    chunk_type   VARCHAR(16)  NOT NULL COMMENT '切片类型：parent=父切片，child=子切片',
    parent_id    VARCHAR(512) NULL COMMENT '父切片ID（子切片指向父切片，父切片为空）',
    child_index  INT          NOT NULL DEFAULT 0 COMMENT '子切片在父切片内的序号（从 0 开始，父切片为 0）',
    path         VARCHAR(1024) NOT NULL COMMENT '面包屑路径（H2 > H3，子切片含 H4，> 分隔）',
    level        INT          NOT NULL DEFAULT 2 COMMENT '路径层级深度（2=H2>H3，3=H2>H3>H4）',
    chunk_index  INT          NOT NULL DEFAULT 0 COMMENT '文档内切片序号（父切片文档序，用于检索结果排序）',
    content      TEXT         NOT NULL COMMENT '切片正文（含面包屑标题+操作步骤文本+图片引用，父切片为完整 H3 内容）',
    char_count   INT          NOT NULL DEFAULT 0 COMMENT '纯文本字符数（不含图片链接）',
    has_images   TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否包含图片引用（0=否，1=是）',
    image_count  INT          NOT NULL DEFAULT 0 COMMENT '包含的图片数量',
    vector_state VARCHAR(16)  NULL COMMENT '向量化状态：pending=待向量化，success=成功，failure=失败（仅子切片有值）',
    vector_id    VARCHAR(512) NULL COMMENT '对应向量数据库中的向量数据ID（仅子切片有值）',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    INDEX idx_doc (doc) COMMENT '按手册名查询切片的索引',
    INDEX idx_doc_id (doc_id) COMMENT '按源文档ID查询切片的索引',
    INDEX idx_parent (parent_id) COMMENT '按父切片ID查询子切片的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='切片表（父子切片）';

-- ----------------------------------------------------------------------------
-- 提示词模板表：存储 RAG 各环节硬编码 prompt，支持在线查看与编辑
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_templates (
    id         INT          NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '模板自增ID',
    `key`      VARCHAR(64)  NOT NULL UNIQUE COMMENT '模板标识（与架构绑定，如 intent_system/answer_system 等）',
    name       VARCHAR(128) NOT NULL COMMENT '模板显示名（中文描述用途）',
    content    TEXT         NOT NULL COMMENT '模板正文（prompt 内容）',
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    INDEX idx_key (`key`) COMMENT '按模板标识查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提示词模板表';

-- ----------------------------------------------------------------------------
-- LLM 调用日志表：记录每次调用大模型的原始输入输出
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_call_logs (
    id            INT          NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '日志自增ID',
    session_id    VARCHAR(64)  NOT NULL COMMENT '所属会话ID（逻辑外键，指向 sessions.id）',
    message_id    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '关联消息ID（指向 messages.id，意图识别与回答归属同一轮）',
    call_type     VARCHAR(32)  NOT NULL COMMENT '调用类型：intent=意图识别，answer=回答生成',
    system_prompt TEXT         NOT NULL COMMENT '系统提示词（输入）',
    messages      JSON         NOT NULL COMMENT '用户消息列表（输入，含历史轮次与当前问题）',
    output        TEXT         NOT NULL COMMENT 'LLM 输出（意图识别 JSON 原文 / 回答全文）',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    INDEX idx_llm_session (session_id) COMMENT '按会话查询LLM调用日志的索引',
    INDEX idx_llm_message (message_id) COMMENT '按消息查询LLM调用日志的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='LLM 调用日志表';
