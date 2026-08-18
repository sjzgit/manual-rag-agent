-- ============================================================================
-- V003__add_llm_call_logs
-- 说明：新增 LLM 调用日志表，记录每次调用大模型的原始输入（system prompt + messages）
--       与输出（意图识别 JSON 原文 / 回答全文），供管理端会话详情对照展示。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> V002 -> V003）
-- ============================================================================

USE manual_rag;

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
