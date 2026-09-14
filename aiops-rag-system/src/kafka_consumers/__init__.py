"""Kafka 三消费者并行启动入口（各自独立 group.id、独立线程）。"""
import threading

from ..utils.logger import get_logger
from .dedup_consumer import DedupConsumer
from .rag_consumer import RAGConsumer
from .storage_consumer import StorageConsumer

logger = get_logger(__name__)


def start_consumers(config: dict, pipeline) -> None:
    """拉起三个消费者组并行消费 standardized-events 主题：
    ① 去重聚合（alert-dedup-group） ② AI 检索（alert-rag-group） ③ 冷存储（alert-storage-group）
    """
    bootstrap = config["kafka"]["bootstrap_servers"]
    topic = config["kafka"]["topic_standardized"]

    consumers = [
        DedupConsumer(
            bootstrap_servers=bootstrap,
            topic=topic,
            group_id="alert-dedup-group",
            redis_url=config["redis"]["url"],
            pipeline=pipeline,
            chatops_webhook=config.get("chatops", {}).get("webhook") or None,
        ),
        RAGConsumer(
            bootstrap_servers=bootstrap,
            topic=topic,
            group_id="alert-rag-group",
            pipeline=pipeline,
        ),
        StorageConsumer(
            bootstrap_servers=bootstrap,
            topic=topic,
            group_id="alert-storage-group",
            es_host=config["es"]["host"],
            es_index=config["es"]["index"],
        ),
    ]

    for consumer in consumers:
        threading.Thread(
            target=consumer.run, daemon=True, name=type(consumer).__name__
        ).start()
        logger.info("%s thread started", type(consumer).__name__)
