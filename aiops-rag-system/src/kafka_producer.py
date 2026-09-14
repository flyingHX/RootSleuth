"""Kafka 生产者封装：标准化事件统一出口。"""
import json
from typing import Optional

from .utils.logger import get_logger

logger = get_logger(__name__)


class KafkaProducerWrapper:
    """kafka-python 生产者封装，连接失败时静默降级（事件仅返回不投递）。"""

    def __init__(self, bootstrap_servers: str, topic: str = "standardized-events"):
        self.topic = topic
        self._producer = None
        try:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                acks="all",
                retries=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka producer connect failed: %s", exc)

    @property
    def healthy(self) -> bool:
        """底层生产者客户端是否可用（供就绪探针使用）。"""
        return self._producer is not None

    def send(self, topic: str, event: dict) -> bool:
        target = topic or self.topic
        if self._producer is None:
            logger.warning("Producer unavailable, event %s not sent", event.get("event_id"))
            return False
        try:
            future = self._producer.send(target, event)
            future.get(timeout=5)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Kafka send failed: %s", exc)
            return False

    def close(self) -> None:
        if self._producer:
            self._producer.flush()
            self._producer.close()
