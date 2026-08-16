"""全局配置：pydantic-settings 读取 .env，对应 Java 侧 application.yaml。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# changan_ai 项目根目录（config.py 在 app/ 下，上级即根）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 从 .env 读取模型配置
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 大模型（DeepSeek，OpenAI 兼容协议）----
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # ---- Embedding（DeepSeek 无 embedding 接口，走硅基流动；可切本地兜底）----
    # embedding_provider: siliconflow | local
    embedding_provider: str = "siliconflow"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    # 重排增强（可选，无网自动降级规则重排）
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- Java 后端（数据源，Python 不直连 MySQL）----
    java_base_url: str = "http://127.0.0.1:8081"
    java_timeout_seconds: float = 3.0

    # ---- RAG 参数 ----
    rag_recall_top_k: int = 8
    rag_rerank_top_k: int = 4
    rag_min_score: float = 0.35
    rag_max_context_chars: int = 6000

    # ---- 服务 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    # EMBEDDING_PROVIDER=local 时的模型名（ModelScope 下载 BAAI/bge-m3）
    local_embedding_model: str = "BAAI/bge-m3"


settings = Settings()
