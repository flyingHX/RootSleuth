"""Webhook 接口：接收原始告警 -> 标准化 -> 上下文丰富 -> 写入 Kafka。"""
import hashlib
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..models.event import RawAlert, StandardizedEvent
from ..models.response import WebhookResponse
from ..runtime import get_config, get_engine, get_producer, get_redis
from ..utils.logger import get_logger
from ..utils.metrics import standardization_latency, standardization_total, unknown_rate

logger = get_logger(__name__)
router = APIRouter()


def _to_int(value: Optional[str], default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@router.post("/webhook", response_model=WebhookResponse)
async def receive_alert(alert: RawAlert):
    """接收原始告警，标准化后投递到 Kafka standardized-events 主题。"""
    start = time.time()
    try:
        # 1. 标准化分类（Drain 模板 + 规则打分，目标 P99 < 10ms）
        result = get_engine().classify(raw_message=alert.raw_message, labels=alert.labels)
        standardization_latency.observe(time.time() - start)
        unknown_rate.set(1.0 if result["is_unknown"] else 0.0)
        standardization_total.labels(
            source=alert.source, error_type=result["error_type"]
        ).inc()

        service_name = alert.labels.get("service", "unknown")
        fingerprint = hashlib.md5(
            f"{alert.source}|{service_name}|{result['error_type']}".encode()
        ).hexdigest()

        # 2. CMDB 上下文丰富：优先读 Redis 拓扑缓存（TTL 1 小时），不可用则降级为空
        topology = get_redis().get_topology(service_name) or {}

        event = StandardizedEvent(
            event_id=f"evt_{alert.timestamp}_{fingerprint[:8]}",
            fingerprint=fingerprint,
            service_name=service_name,
            cluster=alert.labels.get("cluster", "default"),
            namespace=alert.labels.get("namespace"),
            error_type=result["error_type"],
            severity=_to_int(alert.labels.get("severity"), default=2),
            confidence=result["confidence"],
            template=result["template"],
            raw_log=alert.raw_message,
            topology=topology,
            timestamp=alert.timestamp,
        )

        # 3. 事件暂存 Redis（TTL 7 天），供人工诊断 / 告警闭环接口查询
        get_redis().save_event(event.model_dump(), ttl_seconds=7 * 24 * 3600)

        # 4. 投递 Kafka（三消费者并行消费）
        ok = get_producer().send(
            get_config()["kafka"]["topic_standardized"], event.model_dump()
        )
        if not ok:
            logger.warning("Event %s saved but Kafka send failed", event.event_id)

        return WebhookResponse(
            status="success",
            event_id=event.event_id,
            error_type=event.error_type,
            confidence=event.confidence,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
