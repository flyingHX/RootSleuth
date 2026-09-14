"""RAG 流水线测试：JSON 解析、业务重排、Prompt 构建与降级路径。"""
from src.models.event import StandardizedEvent
from src.rag_pipeline.llm_client import LLMClient
from src.rag_pipeline.pipeline import RAGPipeline
from src.rag_pipeline.prompt_builder import build_prompt
from src.rag_pipeline.reranker import Reranker

# 本地无 Milvus / LLM 服务时的降级配置（各客户端均静默降级）
PIPELINE_CONFIG = {
    "milvus": {"host": "localhost", "port": 19530, "collection": "aiops_knowledge_base"},
    "embedder": {"dim": 128, "timeout": 1},
    "llm": {"api_key": "test-key", "model": "deepseek-chat", "timeout": 1},
    "top_k": 20,
    "final_k": 3,
    "llm_timeout": 1,
    # L4 独立超时调短：离线测试环境中 LLM 不可达时快速降级，避免拖慢用例
    "rerank": {"final_k": 3, "l2_top_k": 10, "l3_enabled": False, "l4_timeout": 0.5},
}


def make_event(**overrides) -> StandardizedEvent:
    data = dict(
        event_id="evt_test_001",
        fingerprint="fp001",
        service_name="order-service",
        cluster="prod",
        error_type="redis_timeout",
        severity=3,
        confidence=0.95,
        template="jedis connection timeout",
        raw_log="jedis connection timeout after <NUM>ms",
        topology={"downstream": ["redis-cluster", "mysql"]},
        timestamp=1700000000000,
    )
    data.update(overrides)
    return StandardizedEvent(**data)


class TestLLMParseJson:
    def test_valid_json(self):
        parsed = LLMClient.parse_json('{"root_cause_service": "redis"}')
        assert parsed == {"root_cause_service": "redis"}

    def test_markdown_wrapped_json(self):
        text = '```json\n{"root_cause_service": "mysql", "confidence": 0.9}\n```'
        assert LLMClient.parse_json(text)["root_cause_service"] == "mysql"

    def test_invalid_output(self):
        assert LLMClient.parse_json("sorry, I cannot answer") is None
        assert LLMClient.parse_json("") is None


class TestReranker:
    NOW_MS = 1700000000000

    def test_recent_and_feedback_rank_higher(self):
        candidates = [
            {
                "case_id": "old",
                "distance": 0.90,
                "start_time": self.NOW_MS - 90 * 86400 * 1000,
                "feedback_score": 0,
                "topology_snapshot": '{"downstream": ["redis-cluster"]}',
            },
            {
                "case_id": "recent",
                "distance": 0.88,
                "start_time": self.NOW_MS - 86400 * 1000,
                "feedback_score": 5,
                "topology_snapshot": '{"downstream": ["redis-cluster"]}',
            },
        ]
        result = Reranker().rerank(candidates, {"downstream": ["redis-cluster"]}, self.NOW_MS)
        assert result[0]["case_id"] == "recent"
        assert all("_final_score" in case for case in result)

    def test_topology_similarity(self):
        reranker = Reranker()
        assert reranker._calc_topo_similarity(["a", "b"], ["a"]) == 0.5
        assert reranker._calc_topo_similarity([], ["a"]) == 0.5
        assert reranker._calc_topo_similarity(["a", "b"], ["b", "a"]) == 1.0


class TestPromptBuilder:
    def test_prompt_contains_key_sections(self):
        event = make_event()
        cases = [
            {
                "case_id": "case_1",
                "distance": 0.92,
                "_final_score": 0.9,
                "start_time": event.timestamp - 3600 * 1000,
                "alert_template": "jedis timeout",
                "root_cause": "连接池耗尽",
                "solution": "扩容连接池",
            }
        ]
        prompt = build_prompt(event, cases)
        assert "order-service" in prompt
        assert "redis_timeout" in prompt
        assert "连接池耗尽" in prompt
        assert "扩容连接池" in prompt
        assert "root_cause_service" in prompt  # 强制 JSON 输出格式


class TestPipelineFallback:
    def test_search_fallback_when_no_result(self):
        pipeline = RAGPipeline(PIPELINE_CONFIG)
        result = pipeline.search(make_event())
        assert result["is_fallback"] is True
        assert result["confidence"] == 0.0
        assert result["event_id"] == "evt_test_001"

    def test_write_case_returns_id(self):
        pipeline = RAGPipeline(PIPELINE_CONFIG)
        case_id = pipeline.write_case(
            make_event(), root_cause="测试根因", solution="测试方案"
        )
        assert case_id.startswith("case_")
