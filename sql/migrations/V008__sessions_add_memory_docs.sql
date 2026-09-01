-- ============================================================================
-- V008__sessions_add_memory_docs
-- 说明：sessions 表新增 memory_docs 字段，记录会话关联的操作手册文档 id 列表（≤3）。
--       仅存 doc_id 不存内容，作答时按 id 实时取 md 内容，避免文档内容变更/删除后仍用旧内容。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> ... -> V008）
-- ============================================================================

USE manual_rag;

ALTER TABLE sessions
  ADD COLUMN memory_docs JSON NULL COMMENT '会话关联的操作手册文档id列表（上限3个，仅存id不存内容）' AFTER clarify_state;
