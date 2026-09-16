"""kb-search 检索接口测试（评审 P0-1）：控制台 search_kb 的 RAG 检索入口。"""
import pytest
from fastapi.testclient import TestClient

from src import runtime
from src.main import app
from src.rag_pipeline.pipeline import RAGPipeline
from src.rag_pipeline.rerank_pipeline import RerankResult, RerankStats


class FakeEngine:
    def classify(self, raw_message, labels=None):
        return {"error_type": "redis_timeout", "confidence": 0.95, "template": "t", "is_unknown": False}

    def shutdown(self):
        pass


class FakeProducer:
    healthy = True

    def send(self, topic, event):
        return True

    def close(self):
        pass


class FakeRedis:
    def ping(self):
        return True

    def save_event(self, event, ttl_seconds=None):
        return True

    def get_event(self, event_id):
        return None

    def get_topology(self, service_name):
        return None


class FakeMilvus:
    def is_connected(self):
        return True


class StubPipeline:
    """仅覆盖 kb-search 依赖面：milvus 探针 + retrieve_top_cases。"""

    def __init__(self):
        self.milvus = FakeMilvus()
        self.calls = []

    def retrieve_top_cases(self, event, top_k=5, tenant_id=None):
        self.calls.append((event.get("service_name"), event.get("error_type"), top_k, tenant_id))
        if event.get("service_name") == "order-service":
            return [
                {
                    "case_id": "case_001",
                    "service_name": "order-service",
                    "error_type": "redis_timeout",
                    "alert_template": "jedis connection timeout",
                    "root_cause": "Redis 连接池耗尽",
                    "solution": "扩容连接池至 200",
                    "score": 0.88,
                    "l2_score": 0.7,
                    "l3_score": None,
                    "l4_score": None,
                }
            ]
        return []


@pytest.fixture()
def client():
    runtime.init_runtime(
        config={"kafka": {"topic_standardized": "standardized-events"}},
        engine=FakeEngine(),
        producer=FakeProducer(),
        pipeline=StubPipeline(),
        redis_client=FakeRedis(),
    )
    # 不进入上下文管理器，避免触发真实 startup（与 test_api 约定一致）
    return TestClient(app)


def test_kb_search_returns_reranked_cases(client):
    resp = client.post(
        "/api/v1/kb-search",
        json={"service_name": "order-service", "error_type": "redis_timeout"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "milvus_rag"
    assert body["cases"][0]["case_id"] == "case_001"
    assert body["cases"][0]["root_cause"] == "Redis 连接池耗尽"


def test_kb_search_empty_result(client):
    resp = client.post("/api/v1/kb-search", json={"service_name": "unknown-service"})
    assert resp.status_code == 200
    assert resp.json()["cases"] == []


def test_retrieve_top_cases_rerank_only():
    pipeline = RAGPipeline.__new__(RAGPipeline)

    class FakeRetriever:
        def embed_query_text(self, text):
            return [0.1]

        def retrieve_event(self, event, query_vector, tenant_id=None):
            return [{"case_id": "case_1", "root_cause": "r", "solution": "s", "service_name": "svc"}]

    class FakeRerank:
        def rerank(self, rows, event, now_ms):
            # 缺省时间戳应回退为当前毫秒，避免 L1 过期过滤在 now_ms=0 下全量误杀
            assert now_ms > 0
            return RerankResult(cases=[{**rows[0], "_final_score": 0.9}], stats=RerankStats())

    pipeline.retriever = FakeRetriever()
    pipeline.rerank_pipeline = FakeRerank()
    cases = pipeline.retrieve_top_cases({"service_name": "svc"}, top_k=3)
    assert cases and cases[0]["case_id"] == "case_1" and cases[0]["score"] == 0.9


def test_retrieve_top_cases_fail_open():
    pipeline = RAGPipeline.__new__(RAGPipeline)

    class Boom:
        def embed_query_text(self, text):
            raise RuntimeError("milvus down")

    pipeline.retriever = Boom()
    assert pipeline.retrieve_top_cases({"service_name": "svc"}) == []
