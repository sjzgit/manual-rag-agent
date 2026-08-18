-- ============================================================================
-- V002__add_admin_tables
-- 说明：新增后台管理端三张表：源文档表、切片表（父子切片）、提示词模板表。
--       支撑管理端知识库管理（docx 上传→md 转换→父子切片→向量化入库→状态回写）、
--       提示词模板管理（硬编码 prompt 迁入数据库可编辑）、会话管理（复用既有表）。
-- 应用范围：已上线环境增量执行（顺序执行：V001 -> V002 -> ...）
-- ============================================================================

USE manual_rag;

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
