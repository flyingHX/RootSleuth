"""Agent 生成质量指标（治理草稿 / 值班报告）专项测试。

覆盖 services.console_agent 的纯函数质量评估链路：
- _evaluate_agent_quality：空答案/空查询静默返回 None
- _draft_quality_evaluate：起草案例质量结构与 gate_line
- _oncall_quality_evaluate：值班报告质量 grounding
- _aggregate_agent_quality：聚合均值、达标率与 None 样本剔除
"""

from services.console_agent import (
    _aggregate_agent_quality,
    _draft_quality_evaluate,
    _evaluate_agent_quality,
    _oncall_quality_evaluate,
)
from services.quality_scan import GATE_LINE

QUALITY_KEYS = {
    "faithfulness",
    "context_coverage",
    "answer_relevance",
    "hallucination_rate",
    "trust_index",
    "num_claims",
    "unsupported_claims",
    "quality_ok",
    "gate_line",
}


def test_evaluate_agent_quality_empty_returns_none():
    assert _evaluate_agent_quality({"template": "t"}, "   ", []) is None
    assert _evaluate_agent_quality(None, "有内容", []) is None


def test_draft_quality_structure():
    draft = {
        "alert_template": "OOMKilled",
        "error_type": "memory",
        "service_name": "orders",
        "root_cause": "容器内存超限被 cgroup 杀掉",
        "solution": "调大内存限制并重启 pod",
    }
    clusters = [
        {
            "template": "OOMKilled",
            "count": 3,
            "sample_raw_log": "OOMKilled pod orders Memory cgroup limit exceeded",
        }
    ]
    quality = _draft_quality_evaluate(draft, clusters)
    assert quality is not None
    assert QUALITY_KEYS <= set(quality)
    assert 0.0 <= quality["trust_index"] <= 1.0
    assert quality["gate_line"] == GATE_LINE == 0.85
    assert quality["num_claims"] >= 1


def test_oncall_quality_structure():
    report = {
        "impact_summary": "订单服务受影响，出现多条严重告警",
        "priority": "P1",
        "actions": ["按知识库处置清单执行", "通知系统负责人"],
        "owners_to_notify": ["zhang@example.com"],
        "chatops_text": "【值班告警汇总】时间窗: 24h",
    }
    stats = {
        "affected_systems": [
            {
                "system": "订单系统",
                "services": [{"service": "orders-api", "count": 3}],
                "owners": ["zhang@example.com"],
            }
        ]
    }
    quality = _oncall_quality_evaluate("24h", report, stats)
    assert quality is not None
    assert QUALITY_KEYS <= set(quality)
    assert 0.0 <= quality["hallucination_rate"] <= 1.0
    assert 0.0 <= quality["trust_index"] <= 1.0


def test_aggregate_quality_ignores_none_and_reports_rate():
    samples = [
        {
            "faithfulness": 0.8,
            "context_coverage": 0.6,
            "answer_relevance": 0.7,
            "hallucination_rate": 0.1,
            "trust_index": 0.8,
            "quality_ok": True,
        },
        None,
    ]
    agg = _aggregate_agent_quality(samples)
    assert agg is not None
    assert agg["sample_count"] == 1
    assert agg["faithfulness"] == 0.8
    assert agg["quality_ok_rate"] == 1.0
    assert agg["gate_line"] == GATE_LINE


def test_aggregate_quality_empty_returns_none():
    assert _aggregate_agent_quality([]) is None
    assert _aggregate_agent_quality([None, None]) is None
