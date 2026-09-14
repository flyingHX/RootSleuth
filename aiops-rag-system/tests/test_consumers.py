"""Kafka 去重消费者测试：首次推送、重复抑制与突发严重级别升级。"""
import pytest

from src.kafka_consumers.dedup_consumer import DedupConsumer


class FakeDedupRedis:
    """可控的指纹去重 / 窗口计数桩。"""

    def __init__(self, seen=False, window_count=1):
        self._seen = seen
        self._window_count = window_count

    def first_seen(self, fingerprint, ttl_seconds=3600):
        if self._seen:
            return False
        self._seen = True
        return True

    def window_add(self, fingerprint, payload, window_seconds=300):
        pass

    def window_count(self, fingerprint, window_seconds=300):
        return self._window_count


@pytest.fixture()
def consumer(monkeypatch):
    dedup = DedupConsumer(
        bootstrap_servers="localhost:9092",
        topic="standardized-events",
        group_id="test-group",
        redis_url="redis://localhost:6379/0",
        pipeline=None,
        chatops_webhook=None,
    )
    monkeypatch.setattr(dedup, "redis", FakeDedupRedis())
    return dedup


EVENT = {
    "event_id": "evt_001",
    "fingerprint": "fp_001",
    "service_name": "order-service",
    "error_type": "redis_timeout",
    "severity": 2,
    "template": "jedis timeout",
}


def test_first_alert_pushed(consumer, monkeypatch):
    pushed = []
    monkeypatch.setattr(
        consumer, "_push_chatops", lambda event, wc: pushed.append((event["severity"], wc))
    )
    consumer._process(EVENT)
    assert pushed == [(2, 1)]


def test_duplicate_alert_suppressed(monkeypatch):
    dedup = DedupConsumer(
        bootstrap_servers="localhost:9092",
        topic="standardized-events",
        group_id="test-group",
        redis_url="redis://localhost:6379/0",
        pipeline=None,
        chatops_webhook=None,
    )
    monkeypatch.setattr(dedup, "redis", FakeDedupRedis(seen=True, window_count=5))
    pushed = []
    monkeypatch.setattr(dedup, "_push_chatops", lambda event, wc: pushed.append(event))
    dedup._process(EVENT)
    assert pushed == []  # 重复告警被静默聚合


def test_burst_escalates_severity(consumer, monkeypatch):
    monkeypatch.setattr(
        consumer.redis, "window_count", lambda fp, window_seconds=300: 50
    )
    pushed = []
    monkeypatch.setattr(
        consumer, "_push_chatops", lambda event, wc: pushed.append((event["severity"], wc))
    )
    consumer._process(EVENT)
    assert pushed == [(3, 50)]  # 5 分钟窗口 50 次 -> severity 升级为 critical
