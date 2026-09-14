"""API 接口测试：Webhook 标准化、人工诊断、反馈评分与告警闭环。"""
import pytest
from fastapi.testclient import TestClient

from src import runtime
from src.main import app


class FakeEngine:
    def classify(self, raw_message, labels=None):
        return {
            "error_type": "redis_timeout",
            "confidence": 0.95,
            "template": "jedis connection timeout",
            "is_unknown": False,
        }

    def shutdown(self):
        pass


class FakeProducer:
    healthy = True

    def __init__(self):
        self.sent = []

    def send(self, topic, event):
        self.sent.append((topic, event))
        return True

    def close(self):
        pass


class FakeRedis:
    def __init__(self):
        self.events = {}

    def ping(self):
        return True

    def get_topology(self, service_name):
        return None

    def save_event(self, event, ttl_seconds=None):
        self.events[event["event_id"]] = event

    def get_event(self, event_id):
        return self.events.get(event_id)


class FakeMilvus:
    def is_connected(self):
        return True

    def update_feedback(self, case_id, delta):
        return delta


class FakePipeline:
    def __init__(self):
        self.milvus = FakeMilvus()
        self.written = []

    def search(self, event):
        return {
            "event_id": event.event_id,
            "root_cause": "Redis 连接池耗尽",
            "solution": "扩容连接池至 200",
            "confidence": 0.9,
            "suggest_actions": ["扩容连接池至 200"],
            "is_fallback": False,
            "reason": None,
            "latency_ms": 12,
        }

    def write_case(self, event, root_cause, solution, resolved_by="human"):
        self.written.append(event.event_id)
        return "case_test_001"


WEBHOOK_PAYLOAD = {
    "source": "apm",
    "raw_message": "redis.clients.jedis.exceptions.JedisConnectionException: connection timeout",
    "labels": {"service": "order-service", "cluster": "prod", "severity": "3"},
    "timestamp": 1700000000000,
}


@pytest.fixture()
def client():
    runtime.init_runtime(
        config={"kafka": {"topic_standardized": "standardized-events"}},
        engine=FakeEngine(),
        producer=FakeProducer(),
        pipeline=FakePipeline(),
        redis_client=FakeRedis(),
    )
    # 不进入上下文管理器，避免触发真实 startup（其会尝试连接 Kafka/Milvus）
    return TestClient(app)


def test_webhook_success(client):
    resp = client.post("/api/v1/webhook", json=WEBHOOK_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["error_type"] == "redis_timeout"
    assert body["event_id"].startswith("evt_")
    # 事件已投递 Kafka 并暂存 Redis（供人工诊断/闭环查询）
    assert len(runtime.get_producer().sent) == 1
    assert runtime.get_redis().get_event(body["event_id"]) is not None


def test_manual_diagnostic(client):
    event_id = "evt_manual_001"
    runtime.get_redis().save_event(
        {
            "event_id": event_id,
            "fingerprint": "fp",
            "service_name": "order-service",
            "cluster": "prod",
            "error_type": "redis_timeout",
            "severity": 3,
            "confidence": 0.9,
            "template": "jedis timeout",
            "raw_log": "x",
            "topology": {},
            "timestamp": 1700000000000,
        }
    )
    resp = client.post("/api/v1/diagnostic", json={"event_id": event_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_cause"] == "Redis 连接池耗尽"
    assert body["is_fallback"] is False

    assert client.post("/api/v1/diagnostic", json={"event_id": "missing"}).status_code == 404


def test_feedback_validation(client):
    resp = client.post("/api/v1/feedback", json={"case_id": "case_001", "score": 1})
    assert resp.status_code == 200
    assert resp.json()["feedback_score"] == 1

    # 非法评分（仅允许 +1/-1）
    assert client.post("/api/v1/feedback", json={"case_id": "case_001", "score": 5}).status_code == 422


def test_close_case_writes_knowledge(client):
    event_id = "evt_close_001"
    runtime.get_redis().save_event(
        {
            "event_id": event_id,
            "fingerprint": "fp2",
            "service_name": "cart-service",
            "cluster": "prod",
            "error_type": "mysql_deadlock",
            "severity": 3,
            "confidence": 0.9,
            "template": "deadlock",
            "raw_log": "x",
            "topology": {},
            "timestamp": 1700000000000,
        }
    )
    resp = client.post(
        "/api/v1/cases/close",
        json={
            "event_id": event_id,
            "root_cause": "库存服务长事务",
            "solution": "拆分事务",
            "resolved_by": "human",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["case_id"] == "case_test_001"
    assert runtime.get_pipeline().written == [event_id]


def test_healthz_and_readyz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["checks"] == {"kafka": True, "redis": True, "milvus": True}
