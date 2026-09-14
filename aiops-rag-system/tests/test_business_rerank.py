"""四层业务重排单元测试：L1 规则过滤 / L2 七特征 / L3 降级 / L4 校验 / 编排与融合。

全部用例离线可跑：L3 依赖缺失经 sys.modules 注入模拟，L4 以 llm_client=None 走降级路径。
"""
import sys

from src.models.event import StandardizedEvent
from src.rag_pipeline.documents import (
    document_to_case_dict,
    row_to_document,
    rows_to_documents,
)
from src.rag_pipeline.reranker_l1 import RuleFilter
from src.rag_pipeline.reranker_l2 import (
    BusinessReranker,
    feedback_score,
    freshness_score,
    hit_rate,
    template_overlap,
    time_decay,
    topo_similarity,
)
from src.rag_pipeline.reranker_l3 import BGEReranker
from src.rag_pipeline.reranker_l4 import LLMListwiseReranker
from src.rag_pipeline.rerank_pipeline import RerankPipeline, fuse_final_scores

NOW_MS = 1_700_000_000_000
_DAY_MS = 86_400_000


def make_row(
    case_id: str = "case_1",
    *,
    distance: float = 0.8,
    age_days: float = 1.0,
    upvotes: int = 0,
    downvotes: int = 0,
    feedback_score: int = 0,
    service: str = "order-service",
    solution: str = "扩容连接池",
    alert_template: str = "jedis connection timeout",
    topology: str = '{"downstream": ["redis-cluster"]}',
    hit_count: int = 0,
    recall_count: int = 0,
) -> dict:
    return {
        "case_id": case_id,
        "fingerprint": f"fp_{case_id}",
        "service_name": service,
        "cluster": "prod",
        "error_type": "redis_timeout",
        "severity": 3,
        "start_time": NOW_MS - int(age_days * _DAY_MS),
        "feedback_score": feedback_score,
        "upvotes": upvotes,
        "downvotes": downvotes,
        "hit_count": hit_count,
        "recall_count": recall_count,
        "root_cause": "连接池耗尽",
        "solution": solution,
        "alert_template": alert_template,
        "topology_snapshot": topology,
        "resolved_by": "auto",
        "created_at": NOW_MS,
        "distance": distance,
    }


def make_event(**overrides) -> StandardizedEvent:
    data = dict(
        event_id="evt_rerank_001",
        fingerprint="fp",
        service_name="order-service",
        cluster="prod",
        error_type="redis_timeout",
        severity=3,
        confidence=0.95,
        template="jedis connection timeout",
        raw_log="jedis timeout",
        topology={"downstream": ["redis-cluster"]},
        timestamp=NOW_MS,
    )
    data.update(overrides)
    return StandardizedEvent(**data)


class TestDocumentsBridge:
    def test_row_document_roundtrip(self):
        row = make_row("rt", distance=0.77)
        doc = row_to_document(row)
        case = document_to_case_dict(doc)
        assert case["case_id"] == "rt"
        assert case["distance"] == 0.77
        assert case["upvotes"] == 0 and case["recall_count"] == 0
        assert "alert_template" in doc.page_content

    def test_rows_to_documents_skips_none(self):
        docs = rows_to_documents([None, make_row("ok")])
        assert [d.metadata["case_id"] for d in docs] == ["ok"]


class TestL1RuleFilter:
    def test_keep_recent_healthy_cases(self):
        docs = rows_to_documents([make_row("a"), make_row("b", age_days=10)])
        out = RuleFilter({}).filter(docs, NOW_MS)
        assert [d.metadata["case_id"] for d in out] == ["a", "b"]

    def test_drop_expired_case(self):
        docs = rows_to_documents([make_row("old", age_days=400)])
        assert RuleFilter({"l1_max_age_days": 365}).filter(docs, NOW_MS) == []

    def test_drop_blacklisted_case_and_service(self):
        docs = rows_to_documents(
            [make_row("bad"), make_row("svc", service="legacy-svc")]
        )
        cfg = {
            "l1_blacklist_case_ids": ["bad"],
            "l1_blacklist_services": ["legacy-svc"],
        }
        assert RuleFilter(cfg).filter(docs, NOW_MS) == []

    def test_drop_low_feedback_case(self):
        docs = rows_to_documents(
            [make_row("bad", downvotes=6, feedback_score=-6)]
        )
        assert RuleFilter({"l1_min_feedback": -5}).filter(docs, NOW_MS) == []

    def test_drop_deprecated_solution(self):
        docs = rows_to_documents([make_row("x", solution="回滚到老版本网关")])
        cfg = {"l1_deprecated_keywords": ["老版本"]}
        assert RuleFilter(cfg).filter(docs, NOW_MS) == []


class TestL2Features:
    def test_topo_similarity_layers(self):
        cur = {"downstream": ["redis-cluster"]}
        # 三层全等：downstream 1.0*0.5 + upstream/middleware 双缺中性 0.5*0.3/0.2
        assert topo_similarity(cur, cur) == 0.75
        # 单边缺失该层计 0，双缺层仍给中性分
        assert topo_similarity(cur, {}) == 0.25

    def test_time_decay_and_freshness(self):
        assert 0 < time_decay(NOW_MS, NOW_MS - _DAY_MS) < 1
        assert time_decay(NOW_MS, 0) == 0.0
        assert freshness_score(NOW_MS, NOW_MS - 3 * _DAY_MS) == 1.0
        assert freshness_score(NOW_MS, NOW_MS - 60 * _DAY_MS) == 0.4
        assert freshness_score(NOW_MS, NOW_MS - 200 * _DAY_MS) == 0.2

    def test_feedback_smoothing_and_hit_rate(self):
        assert feedback_score(0, 0) == 0.5
        assert feedback_score(10, 0) == 0.75  # (10+5)/(10+0+10)
        assert hit_rate(3, 2) == 0.5  # 召回样本不足给中性分
        assert hit_rate(8, 10) == 0.8

    def test_template_overlap(self):
        assert template_overlap("a b c", "a b c") == 1.0
        assert template_overlap("a b", "c d") == 0.0
        assert template_overlap("cost <NUM> ms", "cost <NUM> ms") == 1.0  # 占位符剔除


class TestL2BusinessReranker:
    def test_rank_and_top_k(self):
        rows = [make_row(f"c{i}", distance=0.5 + i * 0.01) for i in range(12)]
        docs = BusinessReranker(top_k=5).rerank_documents(
            rows_to_documents(rows),
            {"downstream": ["redis-cluster"]},
            NOW_MS,
            "jedis connection timeout",
        )
        assert len(docs) == 5
        scores = [d.metadata["_l2_score"] for d in docs]
        assert scores == sorted(scores, reverse=True)
        assert docs[0].metadata["case_id"] == "c11"  # 语义距离最高者胜出

    def test_template_feature_breaks_tie(self):
        rows = [
            make_row("match", distance=0.80, alert_template="jedis connection timeout"),
            make_row("other", distance=0.80, alert_template="mysql deadlock found"),
        ]
        docs = BusinessReranker(top_k=0).rerank_documents(
            rows_to_documents(rows), {}, NOW_MS, "jedis connection timeout"
        )
        assert docs[0].metadata["case_id"] == "match"
        assert docs[0].metadata["_f5_template"] > docs[1].metadata["_f5_template"]

    def test_seven_features_recorded(self):
        docs = BusinessReranker(top_k=0).rerank_documents(
            rows_to_documents([make_row("f")]),
            {"downstream": ["redis-cluster"]},
            NOW_MS,
            "jedis connection timeout",
        )
        md = docs[0].metadata
        for key in (
            "_f1_semantic", "_f2_topo", "_f3_time", "_f4_feedback",
            "_f5_template", "_f6_freshness", "_f7_hitrate", "_l2_score",
        ):
            assert key in md
            assert 0.0 <= md[key] <= 1.0


class TestL3CrossEncoder:
    def test_disabled_passthrough(self):
        docs = rows_to_documents([make_row("a"), make_row("b")])
        out = BGEReranker(enabled=False).compress_documents(docs, query="q")
        assert [d.metadata["case_id"] for d in out] == ["a", "b"]
        assert all(d.metadata["_l3_available"] is False for d in out)

    def test_missing_dependency_degrades(self, monkeypatch):
        # 强制 FlagEmbedding 导入失败，模拟无 torch/GPU 的离线环境
        monkeypatch.setitem(sys.modules, "FlagEmbedding", None)
        docs = rows_to_documents([make_row("a", distance=0.9)])
        reranker = BGEReranker(enabled=True, model_name="fake-reranker-model")
        out = reranker.compress_documents(docs, query="q")
        assert out[0].metadata["case_id"] == "a"
        assert out[0].metadata["_l3_available"] is False
        assert reranker.is_available() is False


class TestL4Listwise:
    def test_no_llm_degrades(self):
        docs = rows_to_documents([make_row("a"), make_row("b")])
        out = LLMListwiseReranker({}, None).rerank(docs, "query")
        assert [d.metadata["case_id"] for d in out] == ["a", "b"]
        assert all(d.metadata["_l4_available"] is False for d in out)

    def test_hallucinated_ids_filtered_and_missing_appended(self):
        docs = rows_to_documents([make_row("a"), make_row("b"), make_row("c")])
        ranking = [
            {"case_id": "ghost", "reason": "幻觉案例"},
            {"case_id": "b", "reason": "根因最贴近"},
            {"case_id": "a", "reason": "次优"},
        ]
        ordered = LLMListwiseReranker._validate_and_score(docs, ranking)
        # 幻觉 ID 被剔除；遗漏的 c 按原顺序补在末尾
        assert [d.metadata["case_id"] for d in ordered] == ["b", "a", "c"]
        assert ordered[0].metadata["_l4_reason"] == "根因最贴近"
        scores = [d.metadata["_l4_score"] for d in ordered]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 1.0
        assert all(d.metadata["_l4_available"] for d in ordered)

    def test_empty_ranking_keeps_original_order(self):
        docs = rows_to_documents([make_row("a"), make_row("b")])
        ordered = LLMListwiseReranker._validate_and_score(docs, [])
        assert [d.metadata["case_id"] for d in ordered] == ["a", "b"]


class TestScoreFusion:
    def test_weight_renormalization_per_availability(self):
        docs = rows_to_documents([make_row("a"), make_row("b"), make_row("c")])
        md0, md1, md2 = (d.metadata for d in docs)
        md0["_l2_score"] = 0.8
        md1["_l2_score"] = 0.8
        md1["_l3_score"] = 0.6
        md1["_l3_available"] = True
        md2["_l2_score"] = 0.8
        md2["_l3_score"] = 0.6
        md2["_l3_available"] = True
        md2["_l4_score"] = 0.9
        md2["_l4_available"] = True
        fuse_final_scores(docs)
        # 仅 L2 可用：权重重归一化后等于 L2 分
        assert md0["_final_score"] == 0.8
        assert md1["_final_score"] == round((0.2 * 0.8 + 0.4 * 0.6) / 0.6, 4)
        assert md2["_final_score"] == round(0.2 * 0.8 + 0.4 * 0.6 + 0.4 * 0.9, 4)


class TestRerankPipelineE2E:
    CFG = {
        "rerank": {
            "final_k": 3,
            "l2_top_k": 10,
            "l3_enabled": False,
            "l4_timeout": 1,
        }
    }

    def test_funnel_and_top3(self):
        rows = [
            make_row(f"case_{i}", distance=0.9 - i * 0.02, age_days=1 + i)
            for i in range(8)
        ]
        pipeline = RerankPipeline(self.CFG, llm_client=None)
        result = pipeline.rerank(rows, make_event(), NOW_MS)
        assert len(result.cases) == 3
        stats = result.stats
        assert stats.input_count == 8
        assert stats.l1_out == 8
        assert stats.l2_out == 8
        assert stats.l3_available is False
        assert stats.l4_status == "degraded"  # llm_client=None -> L4 降级
        assert stats.final_out == 3
        # 输出按融合分降序，Top-1 为语义距离最高且最新的案例
        assert result.cases[0]["case_id"] == "case_0"
        assert "_final_score" in result.cases[0]

    def test_accepts_dict_event(self):
        rows = [make_row("only"), make_row("only2", distance=0.6)]
        pipeline = RerankPipeline(self.CFG, llm_client=None)
        result = pipeline.rerank(rows, make_event().model_dump(), NOW_MS)
        assert result.stats.final_out == 2

    def test_l1_drops_expired_before_scoring(self):
        rows = [make_row("fresh"), make_row("stale", age_days=400)]
        pipeline = RerankPipeline(self.CFG, llm_client=None)
        result = pipeline.rerank(rows, make_event(), NOW_MS)
        assert result.stats.input_count == 2
        assert result.stats.l1_out == 1
        assert [c["case_id"] for c in result.cases] == ["fresh"]

    def test_final_k_truncates_l2_output(self):
        rows = [make_row(f"k{i}", distance=0.9 - i * 0.01) for i in range(12)]
        pipeline = RerankPipeline(self.CFG, llm_client=None)
        result = pipeline.rerank(rows, make_event(), NOW_MS)
        assert result.stats.l2_out == 10  # L2 top_k 截断
        assert result.stats.final_out == 3
