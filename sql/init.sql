-- 操作手册 RAG 数据库初始化脚本
-- 应用启动时会自动 create_all，此脚本供手动部署/审计使用

CREATE DATABASE IF NOT EXISTS manual_rag
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE manual_rag;

-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    id            VARCHAR(64)  PRIMARY KEY,
    title         VARCHAR(256) NOT NULL DEFAULT '新会话',
    doc_filter    VARCHAR(256),
    clarify_state JSON,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 消息表
CREATE TABLE IF NOT EXISTS messages (
    id         VARCHAR(64) NOT NULL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    role       VARCHAR(16) NOT NULL COMMENT 'user|assistant',
    content    TEXT        NOT NULL,
    sources    JSON,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 反馈表
CREATE TABLE IF NOT EXISTS feedbacks (
    id         INT         NOT NULL AUTO_INCREMENT PRIMARY KEY,
    message_id VARCHAR(64) NOT NULL,
    score      INT         NOT NULL COMMENT '1=赞, -1=踩',
    comment    TEXT        NOT NULL DEFAULT '',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 意图识别日志
CREATE TABLE IF NOT EXISTS intent_logs (
    id             INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id     VARCHAR(64)  NOT NULL,
    message_id     VARCHAR(64)  NOT NULL DEFAULT '',
    sub_question   JSON         NOT NULL,
    intent_type    VARCHAR(16)  NOT NULL,
    intent_reason  VARCHAR(512) NOT NULL DEFAULT '',
    used_llm       TINYINT(1)   NOT NULL DEFAULT 0,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_intent (session_id),
    INDEX idx_intent_type (intent_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 检索日志
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id           INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id   VARCHAR(64)  NOT NULL,
    message_id   VARCHAR(64)  NOT NULL DEFAULT '',
    sub_question VARCHAR(1024) NOT NULL,
    chunk_id     VARCHAR(512) NOT NULL,
    doc          VARCHAR(256) NOT NULL,
    path         VARCHAR(1024) NOT NULL,
    score        DOUBLE       NOT NULL,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_retrieval (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INT         NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    VARCHAR(64) NOT NULL DEFAULT '',
    action     VARCHAR(64) NOT NULL,
    detail     JSON,
    ip         VARCHAR(64) NOT NULL DEFAULT '',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_audit (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            VARCHAR(64)  NOT NULL PRIMARY KEY,
    username      VARCHAR(128) NOT NULL UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    role          VARCHAR(32)  NOT NULL DEFAULT 'user' COMMENT 'user|admin',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
