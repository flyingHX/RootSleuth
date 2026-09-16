"""Agent 生成质量指标（治理草稿 / 值班报告）+ 诊断确定性（波动治理）专项测试。

覆盖 services.console_agent 的纯函数质量评估链路：
- _evaluate_agent_quality：空答案/空查询静默返回 None
- _draft_quality_evaluate：起草案例质量结构与 gate_line
- _oncall_quality_evaluate：值班报告质量 grounding
- _aggregate_agent_quality：聚合均值、达标率与 None 样本剔除

诊断确定性（同一事件重复深度诊断指标稳定）：
- _parse_diagnose_temperature：默认/非法回退 0，合法值截断 [0, 2]
- _rank_local_cases：固定过滤 + (score desc, case_id asc) 稳定排序
- _build_eval_context：本地候选 ∪ RAG 召回去重合并，评估基准与工具轨迹解耦
- _context_fingerprint / _input_fingerprint：顺序无关指纹，输入变化可感知
- evaluate_diagnosis_quality：相同输入两次计算结果逐字段一致
"""

from models.Events import Events
from models.kb_cases import Kb_cases
from services.console_agent import (
    _aggregate_agent_quality,
    _build_eval_context,
    _context_fingerprint,
    _draft_quality_evaluate,
    _evaluate_agent_quality,
    _input_fingerprint,
    _oncall_quality_evaluate,
    _parse_diagnose_temperature,
    _parse_diagnose_time_budget,
    _rank_local_cases,
)
from services.quality_scan import GATE_LINE, evaluate_diagnosis_quality

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


def test_parse_diagnose_time_budget_defaults_and_clamp():
    """诊断墙钟预算解析：默认/空/非法/NaN 回退 90 秒，合法值截断到 [30, 600]。"""
    assert _parse_diagnose_time_budget(None) == 90.0
    assert _parse_diagnose_time_budget("") == 90.0
    assert _parse_diagnose_time_budget("abc") == 90.0
    assert _parse_diagnose_time_budget("nan") == 90.0
    assert _parse_diagnose_time_budget("120") == 120.0
    assert _parse_diagnose_time_budget(" 45 ") == 45.0
    assert _parse_diagnose_time_budget("5") == 30.0
    assert _parse_diagnose_time_budget("9999") == 600.0


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


# ------------------ 诊断确定性（波动治理） ------------------


def _mk_case(case_id: str, error_type: str, service_name: str) -> Kb_cases:
    return Kb_cases(
        case_id=case_id,
        error_type=error_type,
        service_name=service_name,
        cluster="c1",
        alert_template=f"tpl-{case_id}",
        root_cause=f"root {case_id}",
        solution=f"fix {case_id}",
        topology_snapshot="",
        status="active",
        version=1,
        feedback_score=0,
    )


def _mk_event(**kw) -> Events:
    fields = dict(
        event_id="EV-1",
        template="OOMKilled",
        error_type="memory",
        service_name="orders",
        cluster="c1",
        severity="critical",
        raw_log="OOMKilled pod orders",
        topology="",
    )
    fields.update(kw)
    return Events(**fields)


def test_parse_diagnose_temperature_defaults_to_zero():
    """默认/空/非法一律回退 0（确定性优先）；合法值截断到 [0, 2]。"""
    assert _parse_diagnose_temperature(None) == 0.0
    assert _parse_diagnose_temperature("") == 0.0
    assert _parse_diagnose_temperature("abc") == 0.0
    assert _parse_diagnose_temperature("0") == 0.0
    assert _parse_diagnose_temperature("0.2") == 0.2
    assert _parse_diagnose_temperature(" 1.5 ") == 1.5
    assert _parse_diagnose_temperature("-1") == 0.0
    assert _parse_diagnose_temperature("5") == 2.0


def test_rank_local_cases_stable_order():
    """同分候选乱序输入两次排序结果一致（case_id tie-break）。"""
    cases = [_mk_case(f"c{i}", "memory", "orders") for i in range(5)]
    event = _mk_event()
    run1 = _rank_local_cases(cases, event, "memory", "orders")
    run2 = _rank_local_cases(list(reversed(cases)), event, "memory", "orders")
    assert len(run1) == 5
    assert [c["case_id"] for c in run1] == [c["case_id"] for c in run2]


def test_rank_local_cases_filters_mismatch():
    """error_type / service_name 精确匹配过滤，非 archived 才参与。"""
    cases = [
        _mk_case("a1", "memory", "orders"),
        _mk_case("a2", "disk", "orders"),
        _mk_case("a3", "memory", "payments"),
        _mk_case("a4", "memory", "orders"),
    ]
    cases[2].status = "archived"
    ranked = _rank_local_cases(cases, _mk_event(), "memory", "orders")
    assert [c["case_id"] for c in ranked] == ["a1", "a4"]


def test_eval_context_dedup_and_order_independent_fingerprint():
    """本地 ∪ RAG 去重合并；打乱输入顺序指纹不变；无 RAG 时仍有固定候选。"""
    local = [
        {"case_id": "kb-2", "root_cause": "r2", "solution": "s2"},
        {"case_id": "kb-1", "root_cause": "r1", "solution": "s1"},
    ]
    rag = [
        {"case_id": "kb-1", "root_cause": "r1", "solution": "s1"},
        {"case_id": "kb-3", "root_cause": "r3", "solution": "s3"},
    ]
    ctx = _build_eval_context(local, rag)
    assert [c["case_id"] for c in ctx] == ["kb-1", "kb-2", "kb-3"]

    fp1 = _context_fingerprint(_build_eval_context(local, rag))
    fp2 = _context_fingerprint(_build_eval_context(list(reversed(local)), list(reversed(rag))))
    assert fp1 == fp2 and fp1 != ""

    no_rag = _build_eval_context(local, [])
    assert [c["case_id"] for c in no_rag] == ["kb-1", "kb-2"]
    assert _context_fingerprint([]) != ""


def test_input_fingerprint_stable_and_sensitive():
    """相同事件输入指纹一致；关键字段变化指纹可感知。"""
    assert _input_fingerprint(_mk_event()) == _input_fingerprint(_mk_event())
    assert _input_fingerprint(_mk_event()) != _input_fingerprint(_mk_event(template="DiskFull"))
    assert _input_fingerprint(_mk_event()) != _input_fingerprint(_mk_event(raw_log="changed log"))


def test_diagnose_quality_deterministic():
    """质量评估纯函数：相同 query/answer/contexts 两次计算逐字段一致。"""
    query = {"template": "OOMKilled", "error_type": "memory", "service_name": "orders"}
    answer = "容器内存超限被 cgroup 杀掉。建议调大内存限制并重启 pod。"
    contexts = [
        {"root_cause": "容器内存超限", "solution": "调大内存限制并重启 pod", "alert_template": "OOMKilled"}
    ]
    q1 = evaluate_diagnosis_quality(query, answer, contexts)
    q2 = evaluate_diagnosis_quality(query, answer, contexts)
    assert q1 == q2
