"""消费者 1：去重聚合器（group=alert-dedup-group）。
每条消息立即处理：Redis 指纹去重 + 时间窗口聚合 -> 收敛后推送 ChatOps。延迟要求 < 100ms。
"""
from typing import Optional

import json

from ..rag_pipeline.pipeline import RAGPipeline
from ..storage.redis_client import RedisClient
from ..utils.logger import get_logger
from ..utils.metrics import dedup_reduction_rate

logger = get_logger(__name__)

_WINDOW_SECONDS = 300          # 5 分钟聚合窗口
_ESCALATE_THRESHOLD = 20       # 窗口内重复超过阈值升级为 critical


class DedupConsumer:
    """指纹去重 + 时间窗口聚合，收敛后推送 ChatOps。"""

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str,
                 redis_url: str, pipeline: RAGPipeline, chatops_webhook: Optional[str] = None):
        self.pipeline = pipeline
        self.redis = RedisClient(redis_url)
        self.chatops_webhook = chatops_webhook
        self._consumer = None
        try:
            from kafka import KafkaConsumer

            self._consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="latest",
                enable_auto_commit=False,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dedup consumer connect failed: %s", exc)

    def run(self) -> None:
        if self._consumer is None:
            logger.warning("Dedup consumer not available, exit")
            return
        logger.info("DedupConsumer started (group=alert-dedup-group)")
        for record in self._consumer:
            event = record.value
            try:
                self._process(event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Dedup process failed for %s: %s", event.get("event_id"), exc)
            finally:
                self._consumer.commit()

    def _process(self, event: dict) -> None:
        fingerprint = event.get("fingerprint", "")
        is_first = self.redis.first_seen(fingerprint)
        self.redis.window_add(fingerprint, {"event_id": event.get("event_id")}, _WINDOW_SECONDS)
        window_count = self.redis.window_count(fingerprint, _WINDOW_SECONDS)

        if not is_first:
            # 重复告警：静默聚合，不推送
            logger.debug("Duplicated alert %s (window count=%d)", event.get("event_id"), window_count)
            return

        # 首次出现：立即推送；若窗口内刷屏则升级严重级别
        severity = int(event.get("severity", 2))
        if window_count > _ESCALATE_THRESHOLD:
            severity = max(severity, 3)
            event = {**event, "severity": severity}

        dedup_reduction_rate.set(1.0 - 1.0 / max(1, window_count))
        self._push_chatops(event, window_count)

    def _push_chatops(self, event: dict, window_count: int) -> None:
        """推送收敛后的告警卡片到 ChatOps（企业微信/钉钉/Slack Webhook）。"""
        card = {
            "title": f"[{event.get('service_name')}] {event.get('error_type')}",
            "severity": event.get("severity"),
            "window_count": window_count,
            "template": event.get("template"),
            "event_id": event.get("event_id"),
        }
        logger.info("ChatOps card pushed: %s", json.dumps(card, ensure_ascii=False))
        if not self.chatops_webhook:
            return
        try:
            import httpx

            httpx.post(
                self.chatops_webhook,
                json={"msgtype": "markdown", "markdown": {"content": json.dumps(card, ensure_ascii=False)}},
                timeout=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("ChatOps push failed: %s", exc)

    def close(self) -> None:
        if self._consumer:
            self._consumer.close()
