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
    def __init__(self):
        self.deleted = []
        self.rows = {}

    def is_connected(self):
        return True

    def update_feedback(self, case_id, delta):
        return delta

    def query_by_case_id(self, case_id):
        return self.rows.get(case_id, [])

    def delete_cases(self, case_ids, tenant_id=None):
        self.deleted.extend(case_ids)
        for case_id in case_ids:
            self.rows.pop(case_id, None)
        return True


class FakePipeline:
    def __init__(self):
        self.milvus = FakeMilvus()
        self.written = []
        self.upserts = []

    def search(self, event, tenant_id=None):
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

    def write_case(self, event, root_cause, solution, resolved_by="human", tenant_id=None):
        self.written.append((event.event_id, tenant_id))
        return "case_test_001"

    def upsert_console_case(self, fields):
        self.upserts.append(fields)
        case_id = fields["case_id"]
        self.milvus.rows[case_id] = [{"case_id": case_id}]
        return case_id


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
    assert [w[0] for w in runtime.get_pipeline().written] == [event_id]


def test_healthz_and_readyz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["checks"] == {"kafka": True, "redis": True, "milvus": True}


def test_kb_sync_upsert_verify_and_delete(client):
    """知识索引同步闭环（评审 P0-1）：控制台发布 → upsert → 回读验证 → 归档删除。"""
    resp = client.post(
        "/api/v1/kb-sync/upsert",
        json={
            "case_id": "KB-TEST-001",
            "service_name": "order-service",
            "error_type": "redis_timeout",
            "alert_template": "jedis timeout",
            "root_cause": "连接池耗尽",
            "solution": "扩容连接池",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["exists"] is True

    verify = client.get("/api/v1/kb-sync/verify", params={"case_id": "KB-TEST-001"}).json()
    assert verify["exists"] is True
    assert client.get("/api/v1/kb-sync/verify", params={"case_id": "missing"}).json()["exists"] is False

    resp = client.post("/api/v1/kb-sync/delete", json={"case_ids": ["KB-TEST-001"]})
    assert resp.status_code == 200
    assert runtime.get_pipeline().milvus.deleted == ["KB-TEST-001"]
    # 空列表防护
    assert client.post("/api/v1/kb-sync/delete", json={"case_ids": []}).status_code == 422
