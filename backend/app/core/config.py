"""应用配置：全部通过 .env 读取，密钥不落代码。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 本文件位于 code/backend/app/core/config.py
BACKEND_ROOT = Path(__file__).resolve().parents[2]   # code/backend/
CODE_ROOT = BACKEND_ROOT.parent                       # code/
PROJECT_ROOT = CODE_ROOT.parent                       # 操作手册RAG/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 服务 ----
    app_name: str = "manual-rag-agent"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_origins: str = "*"

    # ---- Milvus ----
    milvus_host: str = "192.168.0.215"
    milvus_port: str = "19530"
    milvus_collection: str = "manual_rag_chunks"
    milvus_child_collection: str = "manual_rag_child_chunks_v2"

    # ---- Embedding ----
    embed_model_name: str = "BAAI/bge-base-zh-v1.5"
    # 本地模型缓存离线加载（不联网校验 etag）；换新模型需下载时临时设 false
    hf_offline: bool = True

    # ---- 检索 ----
    retrieve_top_k: int = 8
    score_threshold: float = 0.4
    rag_mode: str = "generic"  # generic | agentic

    # ---- 关键词检索（BM25 稀疏向量，混合检索） ----
    enable_keyword_search: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    recall_top_k: int = 20  # 每路召回条数（融合候选池）
    fusion_dense_weight: float = 0.7  # RRF 语义路权重
    fusion_sparse_weight: float = 0.3  # RRF 关键词路权重
    rrf_k: int = 60
    keyword_pass_rank: int = 3  # 稀疏路免语义阈值的排名上限

    # ---- 重排（SiliconFlow rerank） ----
    rerank_top_n: int = 5
    rerank_api_key: str = ""  # 留空则不重排，降级融合序
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_model: str = "Qwen/Qwen3-Reranker-8B"
    rerank_timeout: float = 10.0

    # ---- 数据文件（只读） ----
    chunks_path: str = str(PROJECT_ROOT / "文档切片" / "chunks.json")
    media_root: str = str(PROJECT_ROOT / "处理后的md手册文档")

    # ---- 上传目录（管理端写入，区别于只读的知识库目录） ----
    upload_root: str = str(PROJECT_ROOT / "uploads")

    # ---- 主 LLM（DeepSeek，OpenAI 兼容可切换） ----
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3
    # reasoning 模型（deepseek-v4-flash 等）思考过程计入 max_tokens，需留足预算给正式回答；
    # 模型单次输出上限实测为 393216，取最大值彻底放开
    llm_max_tokens: int = 393216

    # ---- 意图识别小模型（OpenAI 兼容，独立配置） ----
    intent_llm_api_key: str = ""
    intent_llm_base_url: str = ""
    intent_llm_model: str = ""
    intent_llm_temperature: float = 0.1
    intent_llm_max_tokens: int = 1024

    # ---- 澄清 ----
    max_clarify_rounds: int = 3

    # ---- MySQL ----
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "manual_rag"

    # ---- Redis（第5步） ----
    redis_url: str = ""
    cache_ttl_seconds: int = 3600
    rate_limit_per_minute: int = 30

    # ---- 管理端 ----
    admin_token: str = ""

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def intent_llm_configured(self) -> bool:
        return bool(self.intent_llm_api_key and self.intent_llm_base_url and self.intent_llm_model)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def rerank_configured(self) -> bool:
        return bool(self.rerank_api_key and self.rerank_base_url and self.rerank_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
