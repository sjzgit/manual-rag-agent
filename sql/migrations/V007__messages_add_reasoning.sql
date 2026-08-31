-- ============================================================================
-- V007__messages_add_reasoning
-- 说明：messages 表新增 reasoning 字段，持久化 LLM 推理思维链（reasoning_content）。
--       reasoning 模型（deepseek-v4-flash 等）在正式回答前输出思考过程，此前仅
--       实时推送、未入库，导致历史会话回看时丢失。区别于 steps（流水线过程步骤），
--       reasoning 是模型自身的思维链。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> ... -> V007）
-- ============================================================================

USE manual_rag;

ALTER TABLE messages
  ADD COLUMN reasoning TEXT NULL COMMENT 'LLM 推理思维链（reasoning_content），历史会话回看时展示' AFTER steps;
