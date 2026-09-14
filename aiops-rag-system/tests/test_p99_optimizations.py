"""P99 长尾治理优化回归：dict 事件兼容、LLM 快速降级、分阶段指标与 Embedding 熔断。"""
import pytest
from prometheus_client import REGISTRY

from src.rag_pipeline.embedder import BGEEmbedder
from src.rag_pipeline.llm_client import LLMClient
from src.rag_pipeline.pipeline import RAGPipeline
from src.utils.metrics import rag_stage_latency

from .test_rag_pipeline import PIPELINE_CONFIG, make_event

FAKE_CASE = {
    "case_id": "case_fake_001",
    "distance": 0.92,
    "start_time": 1700000000000 - 3600 * 1000,
    "feedback_score": 5,
    "topology_snapshot": '{"downstream": ["redis-cluster"]}',
    "root_cause": "Redis 连接池耗尽",
    "solution": "扩容连接池至 200",
    "alert_template": "jedis connection timeout",
}


@pytest.fixture()
def pipeline():
    return RAGPipeline(PIPELINE_CONFIG)


def test_search_accepts_dict_event(pipeline):
    """Kafka 消费路径直接传 dict 事件不再报错（此前 AttributeError 静默转 no_result）。"""
    result = pipeline.search(make_event().model_dump())
    assert result["event_id"] == "evt_test_001"


def test_result_always_carries_latency_and_cases(pipeline):
    """端到端耗时与相似案例列表恒存在（诊断接口契约字段）。"""
    result = pipeline.search(make_event())
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0
    assert isinstance(result["similar_cases"], list)


def test_llm_error_falls_back_to_top1(pipeline, monkeypatch):
    """LLM 调用异常（网络/限流/鉴权）时快速降级 top1，不丢弃已就绪检索结果。"""
    monkeypatch.setattr(pipeline.milvus, "search", lambda **kwargs: [dict(FAKE_CASE)])
    monkeypatch.setattr(pipeline.llm, "invoke", lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")))

    result = pipeline.search(make_event())
    assert result["is_fallback"] is True
    assert result["reason"].startswith("LLM error")
    assert result["root_cause"] == FAKE_CASE["root_cause"]
    assert result["solution"] == FAKE_CASE["solution"]
    assert len(result["similar_cases"]) == 1
    assert result["similar_cases"][0]["case_id"] == FAKE_CASE["case_id"]


def test_stage_metrics_recorded(pipeline, monkeypatch):
    """检索后 embedding / milvus_search / rerank 分阶段指标已采样。"""
    monkeypatch.setattr(pipeline.milvus, "search", lambda **kwargs: [dict(FAKE_CASE)])
    pipeline.search(make_event())

    for stage in ("embedding", "milvus_search", "rerank"):
        value = REGISTRY.get_sample_value(
            "rag_stage_latency_seconds_sum", {"stage": stage}
        )
        assert value is not None and value > 0, f"stage metric missing: {stage}"


def test_embedding_circuit_breaker_opens_and_falls_back():
    """Embedding API 连续失败达到阈值后熔断：窗口内不再发起 HTTP 请求，直接降级。"""
    embedder = BGEEmbedder(
        {
            "api_url": "http://127.0.0.1:9",  # discard 端口，连接立即被拒
            "dim": 32,
            "timeout": 0.2,
            "fail_threshold": 1,
            "circuit_seconds": 60,
        }
    )
    vec1 = embedder.embed("first query")
    assert len(vec1) == 32  # 首次失败 -> 确定性降级向量
    assert embedder._circuit_is_open() is True

    # 熔断窗口内：不再走 HTTP（即使 HTTP 恢复也等窗口半开），直接降级
    vec2 = embedder.embed("second query")
    assert len(vec2) == 32


def test_embedding_success_resets_circuit():
    """Embedding 成功后失败计数清零、熔断复位。"""
    embedder = BGEEmbedder({"dim": 16, "fail_threshold": 2, "circuit_seconds": 60})
    embedder._record_api_failure()
    embedder._record_api_success()
    assert embedder._circuit_is_open() is False
    embedder._record_api_failure()
    assert embedder._circuit_is_open() is False  # 阈值 2，单次失败不熔断


def test_llm_default_retries_zero():
    """LLM 底层重试默认关闭，端到端预算由上层 timeout 统一熔断。"""
    assert LLMClient({}).max_retries == 0
