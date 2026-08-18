-- ============================================================================
-- V004__add_tickets
-- 说明：新增反馈工单表 tickets 与反馈-工单多对多关联表 feedback_tickets，
--       支撑管理端「用户反馈」页的反馈列表与工单归集（把零散反馈归为可跟踪处理的工单）。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> V002 -> V003 -> V004）
-- ============================================================================

USE manual_rag;

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
