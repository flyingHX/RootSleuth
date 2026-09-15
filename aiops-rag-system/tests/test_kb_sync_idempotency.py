"""知识同步幂等与语义缓存失效回归（补偿闭环 P0-1/P0-3 增强）。

覆盖：
- kb-sync/upsert 内容等值幂等短路（同内容不重新 Embedding，旧集合兼容）；
- 内容变更触发重新索引，且保留检索侧反馈/召回累计计数；
- /feedback idempotency_key 防重复加分（补偿重试复用同键）；
- kb-cache/invalidate 本地缓存清空 + Redis Pub/Sub 广播；
- kb-cache/status 缓存观测（epoch、本地条数、Redis 连通性）；
- kb_version 版本守卫：旧版本覆盖 409 拒绝、同内容旧版本幂等短路、同版本更新放行、无版本兼容。
"""
import pytest
from fastapi.testclient import TestClient

from src import runtime
from src.main import app


class _StubEngine:
    def shutdown(self):
        pass


class _StubProducer:
    healthy = True

    def close(self):
        pass


class FakeMilvus:
    def __init__(self):
        self.rows = {}
        self.deleted = []
        self.feedback_calls = []

    def is_connected(self):
        return True

    def query_by_case_id(self, case_id):
        return self.rows.get(case_id, [])

    def delete_cases(self, case_ids):
        self.deleted.extend(case_ids)
        for case_id in case_ids:
            self.rows.pop(case_id, None)
        return True

    def update_feedback(self, case_id, delta):
        self.feedback_calls.append((case_id, delta))
        rows = self.rows.setdefault(case_id, [{"case_id": case_id, "feedback_score": 0}])
        rows[0]["feedback_score"] = int(rows[0].get("feedback_score", 0) or 0) + delta
        return rows[0]["feedback_score"]


class FakeEmbedder:
    def __init__(self):
        self.cache = {"seed": [0.1]}
        self.invalidations = []

    def invalidate(self, case_id=""):
        self.invalidations.append(case_id)
        cleared = len(self.cache)
        self.cache.clear()
        return cleared


class FakePipeline:
    def __init__(self):
        self.milvus = FakeMilvus()
        self.embedder = FakeEmbedder()
        self.upserts = []

    def invalidate_case_cache(self, case_id=""):
        return self.embedder.invalidate(case_id)

    def upsert_console_case(self, fields):
        self.upserts.append(dict(fields))
        case_id = fields["case_id"]
        self.milvus.rows[case_id] = [
            {
                "case_id": case_id,
                "root_cause": (fields.get("root_cause") or "")[:2048],
                "solution": (fields.get("solution") or "")[:2048],
                "alert_template": (fields.get("alert_template") or "")[:1024],
                "upvotes": 3,
                "downvotes": 0,
                "hit_count": 1,
                "recall_count": 5,
                "kb_version": int(fields.get("kb_version") or 0),
                "feedback_score": 2,
            }
        ]
        return case_id


class FakeRedis:
    def __init__(self):
        self.seen = set()
        self.published = []

    def ping(self):
        return True

    def mark_feedback_once(self, key, ttl_seconds=None):
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def publish_cache_invalidate(self, case_id, reason="manual"):
        self.published.append((case_id, reason))
        return True

    def get_cache_epoch(self):
        return 0


BASE_UPSERT = {
    "case_id": "KB-IDEM-001",
    "service_name": "order-service",
    "error_type": "redis_timeout",
    "alert_template": "jedis timeout",
    "root_cause": "连接池耗尽",
    "solution": "扩容连接池",
}


@pytest.fixture()
def client():
    runtime.init_runtime(
        config={"kafka": {"topic_standardized": "standardized-events"}},
        engine=_StubEngine(),
        producer=_StubProducer(),
        pipeline=FakePipeline(),
        redis_client=FakeRedis(),
    )
    return TestClient(app)


def test_upsert_idempotent_short_circuit(client):
    """同内容重复 upsert：第二次短路返回 idempotent=True，不重新 Embedding。"""
    first = client.post("/api/v1/kb-sync/upsert", json=BASE_UPSERT)
    assert first.status_code == 200
    assert first.json()["idempotent"] is False

    second = client.post("/api/v1/kb-sync/upsert", json=BASE_UPSERT)
    assert second.status_code == 200
    body = second.json()
    assert body["idempotent"] is True
    assert body["exists"] is True
    assert len(runtime.get_pipeline().upserts) == 1  # 第二次未触发重新索引


def test_upsert_content_change_reindexes_and_preserves_counts(client):
    """内容变更触发重新索引，且保留检索侧反馈/召回累计计数（不清零）。"""
    client.post("/api/v1/kb-sync/upsert", json=BASE_UPSERT)
    changed = {**BASE_UPSERT, "solution": "改用长连接池并调大 maxTotal"}
    resp = client.post("/api/v1/kb-sync/upsert", json=changed)
    assert resp.status_code == 200
    assert resp.json()["idempotent"] is False
    assert len(runtime.get_pipeline().upserts) == 2
    # 读改写保留 Milvus 侧累计计数（upvotes=3 / recall_count=5）
    assert runtime.get_pipeline().upserts[1]["upvotes"] == 3
    assert runtime.get_pipeline().upserts[1]["recall_count"] == 5


def test_feedback_idempotency_dedup(client):
    """补偿重试复用同一幂等键：第二次提交不重复加分。"""
    runtime.get_pipeline().milvus.rows["KB-FB-1"] = [
        {"case_id": "KB-FB-1", "feedback_score": 0}
    ]
    payload = {"case_id": "KB-FB-1", "score": 1, "idempotency_key": "feedback:KB-FB-1:abc123"}
    first = client.post("/api/v1/feedback", json=payload)
    assert first.status_code == 200
    assert first.json()["feedback_score"] == 1

    second = client.post("/api/v1/feedback", json=payload)
    assert second.status_code == 200
    assert second.json()["feedback_score"] == 1  # 未重复加分
    assert len(runtime.get_pipeline().milvus.feedback_calls) == 1


def test_feedback_without_key_backward_compatible(client):
    """不携带幂等键的旧调用方：行为不变（直接加分）。"""
    resp = client.post("/api/v1/feedback", json={"case_id": "KB-FB-2", "score": -1})
    assert resp.status_code == 200
    assert resp.json()["feedback_score"] == -1


def test_cache_invalidate_endpoint(client):
    """缓存失效：清本地 Embedding 缓存并经 Redis Pub/Sub 广播。"""
    resp = client.post(
        "/api/v1/kb-cache/invalidate", json={"case_id": "KB-IDEM-001", "reason": "upsert"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["cleared_entries"] == 1
    assert body["broadcast"] is True
    assert runtime.get_pipeline().embedder.invalidations == ["KB-IDEM-001"]
    assert runtime.get_redis().published == [("KB-IDEM-001", "upsert")]


def test_cache_status_endpoint(client):
    """缓存观测：epoch 对账、本地缓存条数与 Redis 连通性。"""
    body = client.get("/api/v1/kb-cache/status").json()
    assert body["redis_available"] is True
    assert body["redis_epoch"] == 0
    assert body["local_cache_entries"] == 0


def test_delete_invalidates_cache(client):
    """索引删除同步：删除行并失效语义缓存。"""
    client.post("/api/v1/kb-sync/upsert", json=BASE_UPSERT)
    runtime.get_pipeline().embedder.cache["b"] = [0.2]
    resp = client.post("/api/v1/kb-sync/delete", json={"case_ids": ["KB-IDEM-001"]})
    assert resp.status_code == 200
    assert "KB-IDEM-001" in runtime.get_pipeline().milvus.deleted
    assert "KB-IDEM-001" in runtime.get_pipeline().embedder.invalidations


# ---------------- 版本守卫（kb_version 幂等 / 旧版本覆盖拒绝） ----------------

def _seed_versioned_row(case_id: str, kb_version: int) -> None:
    runtime.get_pipeline().milvus.rows[case_id] = [{
        "case_id": case_id,
        "root_cause": "连接池耗尽",
        "solution": "扩容连接池",
        "alert_template": "jedis timeout",
        "kb_version": kb_version,
        "upvotes": 2,
        "recall_count": 4,
    }]


def test_upsert_stale_version_rejected_409(client):
    """版本守卫：内容变更且 kb_version 低于索引版本 → 409 拒绝，不触发重新索引。"""
    _seed_versioned_row("KB-VER-1", kb_version=5)
    stale = {**BASE_UPSERT, "case_id": "KB-VER-1", "solution": "旧版本方案：重启", "kb_version": 4}
    resp = client.post("/api/v1/kb-sync/upsert", json=stale)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "stale_version_rejected"
    assert detail["index_version"] == 5
    assert detail["request_version"] == 4
    assert len(runtime.get_pipeline().upserts) == 0  # 未触发重新索引


def test_upsert_same_content_older_version_idempotent(client):
    """乱序重放安全：同内容 + 旧版本 → 幂等短路成功（先于版本守卫判定）。"""
    _seed_versioned_row("KB-VER-2", kb_version=5)
    replay = {**BASE_UPSERT, "case_id": "KB-VER-2", "kb_version": 4}
    resp = client.post("/api/v1/kb-sync/upsert", json=replay)
    assert resp.status_code == 200
    body = resp.json()
    assert body["idempotent"] is True
    assert body["exists"] is True
    assert len(runtime.get_pipeline().upserts) == 0


def test_upsert_same_version_content_change_allowed(client):
    """同版本内容变更 → 正常重新索引（守卫只拒绝更低版本）。"""
    _seed_versioned_row("KB-VER-3", kb_version=3)
    changed = {**BASE_UPSERT, "case_id": "KB-VER-3", "solution": "v3 新方案：限流降级", "kb_version": 3}
    resp = client.post("/api/v1/kb-sync/upsert", json=changed)
    assert resp.status_code == 200
    assert resp.json()["idempotent"] is False
    assert len(runtime.get_pipeline().upserts) == 1
    # 读改写保留检索侧累计计数（upvotes=2）
    assert runtime.get_pipeline().upserts[0]["upvotes"] == 2
    assert runtime.get_pipeline().upserts[0]["kb_version"] == 3


def test_upsert_missing_version_backward_compatible(client):
    """旧调用方不携带 kb_version → 守卫跳过，行为与历史契约一致。"""
    _seed_versioned_row("KB-VER-4", kb_version=5)
    changed = {**BASE_UPSERT, "case_id": "KB-VER-4", "solution": "无版本号新方案"}
    resp = client.post("/api/v1/kb-sync/upsert", json=changed)
    assert resp.status_code == 200
    assert resp.json()["idempotent"] is False
    assert len(runtime.get_pipeline().upserts) == 1
