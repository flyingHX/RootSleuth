"""运行时单例容器：由 main.py 启动时初始化，供 API 层共享访问。"""
import threading
from typing import Any, Optional

_lock = threading.Lock()
_config: Optional[dict] = None
_engine: Optional[Any] = None
_producer: Optional[Any] = None
_pipeline: Optional[Any] = None
_redis: Optional[Any] = None


def init_runtime(config: dict, engine, producer, pipeline, redis_client) -> None:
    """在应用启动时注入全局单例（标准库线程锁保证幂等）。"""
    global _config, _engine, _producer, _pipeline, _redis
    with _lock:
        _config = config
        _engine = engine
        _producer = producer
        _pipeline = pipeline
        _redis = redis_client


def get_config() -> dict:
    if _config is None:
        raise RuntimeError("Runtime not initialized")
    return _config


def get_engine():
    if _engine is None:
        raise RuntimeError("Runtime not initialized")
    return _engine


def get_producer():
    if _producer is None:
        raise RuntimeError("Runtime not initialized")
    return _producer


def get_pipeline():
    if _pipeline is None:
        raise RuntimeError("Runtime not initialized")
    return _pipeline


def get_redis():
    if _redis is None:
        raise RuntimeError("Runtime not initialized")
    return _redis
