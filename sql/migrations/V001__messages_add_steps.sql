-- ============================================================================
-- V001__messages_add_steps
-- 说明：messages 表新增 steps 字段，持久化思考过程步骤（StepEvent JSON 数组）。
--       此前思考过程仅在 SSE 流式时实时推送、未入库，导致历史会话回看时丢失，
--       只剩结果与来源。新增本列后，assistant 消息随回答一起保存步骤。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> V002 -> ...）
-- ============================================================================

USE manual_rag;

ALTER TABLE messages
  ADD COLUMN steps JSON NULL COMMENT '思考过程步骤（StepEvent JSON 数组：type/title/detail），历史会话回看时展示' AFTER sources;
