-- ============================================================================
-- V006__retrieval_logs_add_stage_rank
-- 说明：混合检索全阶段落库——同一子问题的稠密路/稀疏路/RRF 融合/rerank 最终
--       结果分阶段各记一行（stage + hit_rank），会话详情可分别查看四种结果列表与得分。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> ... -> V006）
-- ============================================================================

USE manual_rag;

-- ----------------------------------------------------------------------------
-- retrieval_logs 增列：stage=命中所属阶段，hit_rank=该阶段内排名；旧行默认 final
-- ----------------------------------------------------------------------------
ALTER TABLE retrieval_logs
    ADD COLUMN stage    VARCHAR(16) NOT NULL DEFAULT 'final' COMMENT '命中阶段：dense=语义检索，sparse=关键词检索，fused=RRF 融合（父代表），final=rerank 最终结果' AFTER mode,
    ADD COLUMN hit_rank INT         NOT NULL DEFAULT 1 COMMENT '该阶段内排名（从 1 起）' AFTER stage,
    ADD INDEX idx_session_stage (session_id, stage) COMMENT '按会话+阶段查询检索日志的索引';
