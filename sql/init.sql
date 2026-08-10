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
