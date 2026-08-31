-- ============================================================================
-- V005__retrieval_logs_add_hybrid_scores
-- 说明：混合检索（稠密+BM25 稀疏+加权 RRF 融合+rerank 重排）上线，
--       retrieval_logs 拆分落库各路分数与检索模式，供管理端对比展示与检索质量分析。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> ... -> V005）
-- ============================================================================

USE manual_rag;

-- ----------------------------------------------------------------------------
-- retrieval_logs 增列：全部可空/有默认，旧数据不受影响
-- ----------------------------------------------------------------------------
ALTER TABLE retrieval_logs
    ADD COLUMN dense_score  DOUBLE       NULL COMMENT '稠密路 COSINE 原始分（纯稀疏命中为 NULL）' AFTER score,
    ADD COLUMN sparse_score DOUBLE       NULL COMMENT '稀疏路 BM25 原始分（内积，纯稠密命中为 NULL）' AFTER dense_score,
    ADD COLUMN fused_score  DOUBLE       NULL COMMENT '加权 RRF 融合分（单路未融合时为 NULL）' AFTER sparse_score,
    ADD COLUMN rerank_score DOUBLE       NULL COMMENT '重排相关度分（0~1，未重排/降级时为 NULL）' AFTER fused_score,
    ADD COLUMN mode         VARCHAR(16)  NOT NULL DEFAULT 'dense' COMMENT '检索模式：dense=纯稠密，hybrid=混合，hybrid-rerank=混合+重排' AFTER rerank_score;
