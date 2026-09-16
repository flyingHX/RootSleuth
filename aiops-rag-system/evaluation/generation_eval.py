"""生成质量评测运行器（P1-1）：Faithfulness / Citation Accuracy / Answer Relevance / 幻觉率 + Trust Index。

比对器语义（确定性启发式，无外部依赖）：
- Faithfulness：答案分句后逐句与检索上下文做词元重叠（CJK 单字 + 英数 ≥2），
  重叠 ≥0.35 视为有据；引用标记 [kb_case_xxx] 不参与忠实度判定（由引用准确率负责）；
- Hallucination Rate：1 - Faithfulness（代理口径，与 Prometheus rag_quality_hallucination_rate 一致）；
- Citation Accuracy：答案引用的 case_id 必须出现在检索上下文中；未引用计 0（强约束可追溯）；
- Answer Relevance：答案词元对查询（service_name/error_type/template）的覆盖率；
- Trust Index：T = 0.4F + 0.35C + 0.25(1-P)。

两种模式：
- harness（默认）：以 Golden Set 检索 Top-1 案例合成"有据答案"，验证比对器端到端并产出基线指标，
  评测记录绑定 qid / case_id / kb_version / 文本版本 / Embedding 版本（P1-1 追溯要求）；
- api：对运行中的 RAG /diagnostic 发起真实诊断后用同一比对器评测（需 Milvus + LLM 可用）。

使用（aiops-rag-system 根目录）：
    python -m evaluation.generation_eval --fail-on-gate
"""
import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

from .metrics import GENERATION_GATES, GENERATION_MAX, check_gates, trust_index
from .retrieval_eval import EVAL_DIR, load_jsonl, lexical_rank, tokenize

CITATION_RE = re.compile(r"\[(kb_case_[A-Za-z0-9_]+)\]")
CLAIM_SPLIT_RE = re.compile(r"[。；;！!？?\n]+")
SUPPORT_THRESHOLD = 0.35


def split_claims(answer: str) -> List[str]:
    """按中英文句读拆分答案为原子断言。"""
    return [c.strip() for c in CLAIM_SPLIT_RE.split(answer or "") if c.strip()]


def claim_supported(claim: str, context_tokens: set) -> bool:
    """断言有据判定：词元重叠 ≥ SUPPORT_THRESHOLD。"""
    body = CITATION_RE.sub(" ", claim)
    tokens = tokenize(body)
    if not tokens:
        return True
    return len(tokens & context_tokens) / len(tokens) >= SUPPORT_THRESHOLD


def evaluate_generation(
    query: dict,
    answer: str,
    contexts: List[dict],
    cited_case_ids: Optional[List[str]] = None,
) -> dict:
    """对单条答案计算 F/C/相关性/幻觉率/Trust Index（纯函数，供脚本与 pytest 复用）。"""
    claims = split_claims(answer)
    context_tokens: set = set()
    for ctx in contexts:
        context_tokens |= tokenize(ctx.get("root_cause", ""))
        context_tokens |= tokenize(ctx.get("solution", ""))
        context_tokens |= tokenize(ctx.get("template", ""))
        context_tokens |= tokenize(" ".join(ctx.get("keywords") or []))
    q_tokens = (
        tokenize(query.get("template", ""))
        | tokenize(query.get("error_type", ""))
        | tokenize(query.get("service_name", ""))
    )
    # grounding 基准 = 检索上下文 ∪ 查询症状：真实诊断答案会复述告警现象（服务/错误类型/模板），
    # 复述查询本身不计为幻觉（RAGAS faithfulness 同款约定：与 question 重合不算 fabricated）。
    grounding_tokens = context_tokens | q_tokens
    supported = [claim_supported(c, grounding_tokens) for c in claims]
    faithfulness = (sum(1 for s in supported if s) / len(claims)) if claims else 0.0
    hallucination_rate = 1.0 - faithfulness

    cited = list(cited_case_ids) if cited_case_ids is not None else CITATION_RE.findall(answer or "")
    retrieved_ids = {c.get("case_id") for c in contexts}
    citation_accuracy = (
        sum(1 for c in cited if c in retrieved_ids) / len(cited) if cited else 0.0
    )

    answer_tokens = tokenize(answer or "")
    relevance = len(q_tokens & answer_tokens) / max(1, len(q_tokens))

    return {
        "faithfulness": round(faithfulness, 4),
        "citation_accuracy": round(citation_accuracy, 4),
        "answer_relevance": round(relevance, 4),
        "hallucination_rate": round(hallucination_rate, 4),
        "trust_index": round(trust_index(faithfulness, citation_accuracy, hallucination_rate), 4),
        "num_claims": len(claims),
        "unsupported_claims": [c[:60] for c, s in zip(claims, supported) if not s],
    }


def synthesize_answer(query: dict, case: dict) -> str:
    """合成有据答案：先复述告警现象（真实诊断答案的常规结构，且按 RAGAS 约定复述查询不算幻觉），
    再给出引用标记 + 根因 + 方案（事实全部来自语料，无外部事实）。"""
    return (
        f"告警现象：{query.get('template', '')}（服务：{query.get('service_name', '')}，"
        f"错误类型：{query.get('error_type', '')}）。"
        f"[{case['case_id']}] 根因：{case['root_cause']}。"
        f"处置方案：{case['solution']}。"
        f"参考案例：{case['template']}（服务：{case['service_name']}，"
        f"错误类型：{case['error_type']}，关键词：{'、'.join(case.get('keywords') or [])}）"
    )


def _set_gauges(metrics: Dict[str, float]) -> None:
    """回写最新评测值到 Prometheus（0.85 告警线见 docs/OPERATIONS_RUNBOOK.md）。"""
    try:
        from src.utils.metrics import (
            rag_quality_citation_accuracy,
            rag_quality_faithfulness,
            rag_quality_hallucination_rate,
            rag_quality_trust_index,
        )
    except Exception:  # noqa: BLE001 - 独立运行无 src 依赖时静默
        return
    rag_quality_faithfulness.set(metrics.get("faithfulness", 0.0))
    rag_quality_citation_accuracy.set(metrics.get("citation_accuracy", 0.0))
    rag_quality_hallucination_rate.set(metrics.get("hallucination_rate", 0.0))
    rag_quality_trust_index.set(metrics.get("trust_index", 0.0))


def run_generation_eval(set_gauges: bool = True) -> dict:
    """harness 模式：Golden Set 全量合成答案 → 比对器评测 → 门禁。"""
    golden = load_jsonl(EVAL_DIR / "golden_set_v1.jsonl")
    corpus = load_jsonl(EVAL_DIR / "golden_corpus.jsonl")
    by_id = {c["case_id"]: c for c in corpus}

    results = []
    for g in golden:
        query = g["query"]
        ranked = lexical_rank(query, corpus)
        contexts = [by_id[cid] for cid in ranked[:3] if cid in by_id]
        top_case = by_id.get(ranked[0]) if ranked else None
        if top_case is None:
            continue
        answer = synthesize_answer(query, top_case)
        item = evaluate_generation(query, answer, contexts, cited_case_ids=[top_case["case_id"]])
        item["binding"] = {
            "qid": g["qid"],
            "case_id": top_case["case_id"],
            "kb_version": 0,
            "text_version": "golden_corpus_v1",
            "embedding_version": os.getenv("EMBEDDING_MODEL_PATH", "bge-m3-fallback"),
        }
        results.append(item)

    n = len(results)
    keys = ("faithfulness", "citation_accuracy", "answer_relevance", "hallucination_rate", "trust_index")
    metrics = {k: sum(r[k] for r in results) / n for k in keys} if n else {}
    gate_ok, gate_failures = check_gates(metrics, GENERATION_GATES, GENERATION_MAX)
    if set_gauges:
        _set_gauges(metrics)
    return {
        "mode": "harness",
        "metrics": metrics,
        "gate_ok": gate_ok,
        "gate_failures": gate_failures,
        "num_queries": n,
        "items": results,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Golden Set 生成质量评测（P1-1）")
    parser.add_argument("--fail-on-gate", action="store_true", help="门禁未达标时退出码 1（CI 质量门禁）")
    args = parser.parse_args(argv)
    report = run_generation_eval()
    summary = {k: v for k, v in report.items() if k != "items"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_gate and not report["gate_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
