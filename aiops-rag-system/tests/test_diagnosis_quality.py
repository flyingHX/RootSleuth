"""单次诊断质量指标（在线评估）单元测试：每次深度诊断展示质量指标的数据基础。"""
from src.utils.quality_eval import GATE_LINE, evaluate_diagnosis_quality

QUERY = {
    "template": "redis connection timeout",
    "error_type": "redis_timeout",
    "service_name": "order-service",
}


def test_grounded_answer_scores_high():
    """答案完全有据 + 引用覆盖全部上下文 → trust_index 达到 0.85 质量线。"""
    contexts = [
        {
            "case_id": "kb_case_1",
            "root_cause": "redis connection pool exhausted causing timeout in order-service",
            "solution": "扩容 redis 连接池并重启 order-service",
            "alert_template": "redis connection timeout",
        }
    ]
    answer = "redis 连接池耗尽导致 order-service 超时。\n扩容 redis 连接池并重启服务。"
    m = evaluate_diagnosis_quality(QUERY, answer, contexts)
    assert m["faithfulness"] >= 0.99
    assert m["context_coverage"] == 1.0
    assert m["hallucination_rate"] <= 0.01
    assert m["trust_index"] >= GATE_LINE
    assert m["quality_ok"] is True
    assert m["gate_line"] == GATE_LINE


def test_hallucinated_answer_detected():
    """答案与上下文/症状无词元交集 → faithfulness=0、幻觉率=1、不达标。"""
    contexts = [
        {
            "case_id": "kb_case_2",
            "root_cause": "磁盘空间不足导致写入失败",
            "solution": "清理过期日志并扩容磁盘",
            "alert_template": "",
        }
    ]
    answer = "根因是火星引力异常导致磁盘阵列磁悬浮失效。建议重启卫星天线。"
    m = evaluate_diagnosis_quality(QUERY, answer, contexts)
    assert m["faithfulness"] == 0.0
    assert m["hallucination_rate"] == 1.0
    assert m["quality_ok"] is False
    assert m["unsupported_claims"]


def test_no_context_low_score():
    """无上下文（no_result 降级）→ 覆盖为 0，不达标。"""
    m = evaluate_diagnosis_quality(QUERY, "未找到相似历史案例，请人工排查", [])
    assert m["context_coverage"] == 0.0
    assert m["quality_ok"] is False


def test_partial_grounded_multi_claim():
    """多断言部分有据：num_claims 计数与 faithfulness 比例正确。"""
    contexts = [
        {
            "case_id": "kb_case_3",
            "root_cause": "redis 连接池耗尽",
            "solution": "扩容连接池",
            "alert_template": "redis timeout",
        }
    ]
    answer = "redis 连接池耗尽。扩容连接池。"
    m = evaluate_diagnosis_quality(QUERY, answer, contexts)
    assert m["num_claims"] == 2
    assert m["faithfulness"] == 1.0
    assert m["context_coverage"] == 1.0
    assert m["answer_relevance"] > 0.0
    assert m["trust_index"] > 0.9


def test_symptom_restating_not_hallucination():
    """复述告警现象（查询症状）不算幻觉（RAGAS 同款约定）。"""
    contexts = [
        {
            "case_id": "kb_case_4",
            "root_cause": "连接池配置过小",
            "solution": "调大 max_connections",
            "alert_template": "",
        }
    ]
    answer = "告警现象：redis connection timeout（服务：order-service）。建议调大 max_connections。"
    m = evaluate_diagnosis_quality(QUERY, answer, contexts)
    assert m["faithfulness"] >= 0.5
