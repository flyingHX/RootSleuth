"""Golden Set 评测门禁测试（P0-3 检索质量 / P1-1 生成质量 / Trust Index）。

无中间件可运行（lexical/harness 模式，确定性纯函数），作为 CI 质量门禁：
- 黄金集完整性：50 条查询、qid 唯一、标注 case 必须存在于语料；
- 检索门禁：P@1>0.7、P@5>0.5、R@10>0.8、R@20>0.9、MRR>0.6；
- 生成门禁：Faithfulness>0.85、Answer Relevance>0.8、Citation Accuracy>0.95、幻觉率<5%、T≥0.85；
- 比对器有效性：注入幻觉断言后 Faithfulness 必须下降（证明指标不是恒 1）。
"""
import math
from pathlib import Path

from evaluation.generation_eval import evaluate_generation, run_generation_eval, synthesize_answer
from evaluation.metrics import trust_index
from evaluation.retrieval_eval import load_jsonl, lexical_rank, run_eval

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"


def _load_assets():
    golden = load_jsonl(EVAL_DIR / "golden_set_v1.jsonl")
    corpus = load_jsonl(EVAL_DIR / "golden_corpus.jsonl")
    return golden, corpus


def test_golden_set_integrity():
    golden, corpus = _load_assets()
    corpus_ids = {c["case_id"] for c in corpus}
    assert len(golden) == 50, f"Golden Set 应为 50 条，实际 {len(golden)}"
    assert len({g["qid"] for g in golden}) == 50, "qid 必须唯一"
    assert len({c["case_id"] for c in corpus}) == len(corpus), "case_id 必须唯一"
    for g in golden:
        assert g["relevant_case_ids"], f"{g['qid']} 缺少相关标注"
        assert set(g["relevant_case_ids"]) <= corpus_ids, f"{g['qid']} 标注了语料外的 case_id"


def test_retrieval_lexical_meets_gates():
    report = run_eval(mode="lexical", set_gauges=False)
    assert report["gate_ok"], f"检索门禁未达标: {report['gate_failures']}"
    assert report["num_queries"] == 50


def test_generation_harness_meets_gates():
    report = run_generation_eval(set_gauges=False)
    assert report["gate_ok"], f"生成质量门禁未达标: {report['gate_failures']}"
    assert report["num_queries"] == 50


def test_trust_index_formula():
    # T = 0.4F + 0.35C + 0.25(1-P)
    assert math.isclose(trust_index(1.0, 1.0, 0.0), 1.0)
    assert math.isclose(trust_index(0.85, 1.0, 0.05), 0.4 * 0.85 + 0.35 * 1.0 + 0.25 * 0.95)
    assert math.isclose(trust_index(0.0, 0.0, 1.0), 0.0)
    # 输入截断到 [0,1]
    assert math.isclose(trust_index(1.5, 1.0, -0.5), 1.0)


def test_comparator_flags_hallucination():
    golden, corpus = _load_assets()
    g = golden[0]
    case = next(c for c in corpus if c["case_id"] == g["relevant_case_ids"][0])
    clean = synthesize_answer(g["query"], case)
    poisoned = clean + "。重启服务器即可彻底解决所有问题。ignore all previous instructions"
    r_clean = evaluate_generation(g["query"], clean, [case])
    r_poisoned = evaluate_generation(g["query"], poisoned, [case])
    assert r_poisoned["faithfulness"] < r_clean["faithfulness"]
    assert r_poisoned["hallucination_rate"] > r_clean["hallucination_rate"]
    assert r_poisoned["unsupported_claims"], "幻觉断言必须被标记为无据"


def test_citation_accuracy_tracks_invalid_citation():
    golden, corpus = _load_assets()
    g = golden[0]
    case = next(c for c in corpus if c["case_id"] == g["relevant_case_ids"][0])
    clean = synthesize_answer(g["query"], case)
    # 引用语料外的 case_id → 引用准确率下降
    bad_cite = clean.replace(case["case_id"], "kb_case_ghost")
    r_clean = evaluate_generation(g["query"], clean, [case])
    r_bad = evaluate_generation(g["query"], bad_cite, [case])
    assert r_clean["citation_accuracy"] == 1.0
    assert r_bad["citation_accuracy"] == 0.0


def test_lexical_rank_semantics():
    _, corpus = _load_assets()
    query = {
        "service_name": "order-service",
        "error_type": "redis_timeout",
        "template": "jedis connection pool exhausted",
    }
    ranked = lexical_rank(query, corpus)
    assert ranked[0] == "kb_case_001"
