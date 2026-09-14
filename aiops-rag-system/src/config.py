"""配置加载：.env 环境变量 -> 统一 config dict。"""
import os
from pathlib import Path

from dotenv import load_dotenv


def load_config(base_dir: str = ".") -> dict:
    env_path = Path(base_dir) / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    return {
        "llm": {
            "api_key": os.getenv("LLM_API_KEY", ""),
            "base_url": os.getenv("LLM_BASE_URL") or None,
            "model": os.getenv("LLM_MODEL", "deepseek-chat"),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.1")),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "5")),
            "max_retries": int(os.getenv("LLM_MAX_RETRIES", "0")),
        },
        "milvus": {
            "host": os.getenv("MILVUS_HOST", "localhost"),
            "port": int(os.getenv("MILVUS_PORT", "19530")),
            "collection": os.getenv("MILVUS_COLLECTION", "aiops_knowledge_base"),
            "nprobe": int(os.getenv("MILVUS_NPROBE", "32")),
            "search_timeout": float(os.getenv("MILVUS_SEARCH_TIMEOUT", "3")),
            "collection_cache_ttl": float(os.getenv("MILVUS_COLLECTION_CACHE_TTL", "5")),
        },
        "embedder": {
            "model_path": os.getenv("EMBEDDING_MODEL_PATH", "BAAI/bge-m3"),
            "api_url": os.getenv("EMBEDDING_API_URL", ""),
            "api_key": os.getenv("EMBEDDING_API_KEY", ""),
            "dim": int(os.getenv("EMBEDDING_DIM", "1024")),
            "timeout": int(os.getenv("EMBEDDING_TIMEOUT", "10")),
            "fail_threshold": int(os.getenv("EMBEDDING_FAIL_THRESHOLD", "3")),
            "circuit_seconds": float(os.getenv("EMBEDDING_CIRCUIT_SECONDS", "30")),
        },
        # RAG 检索/并发调优（P99 长尾治理）
        "top_k": int(os.getenv("RAG_TOP_K", "20")),
        "final_k": int(os.getenv("RAG_FINAL_K", "3")),
        "llm_timeout": float(os.getenv("RAG_LLM_TIMEOUT_SECONDS", "5")),
        "llm_max_workers": int(os.getenv("RAG_LLM_MAX_WORKERS", "8")),
        "recent_window_days": int(os.getenv("RAG_RECENT_WINDOW_DAYS", "30")),
        "kafka": {
            "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "topic_standardized": os.getenv("KAFKA_TOPIC_STANDARDIZED", "standardized-events"),
            "topic_raw": os.getenv("KAFKA_TOPIC_RAW", "raw-alerts"),
        },
        "redis": {"url": os.getenv("REDIS_URL", "redis://localhost:6379/0")},
        "es": {
            "host": os.getenv("ES_HOST", "http://localhost:9200"),
            "index": os.getenv("ES_INDEX", "aiops-events"),
        },
        "chatops": {"webhook": os.getenv("CHATOPS_WEBHOOK", "")},
        "service_port": int(os.getenv("SERVICE_PORT", "8080")),
        "rules_path": os.getenv("RULES_PATH", "config/rules.yaml"),
        "drain_path": os.getenv("DRAIN_PATH", "config/drain_patterns.yaml"),
    }
