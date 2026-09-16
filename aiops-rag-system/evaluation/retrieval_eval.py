"""检索质量评测运行器（P0-3）：Golden Set v1 → P@1/P@5/R@10/R@20/MRR/HitRate/Context Recall + 质量门禁。

两种模式：
- lexical（默认，无外部依赖）：确定性词元重叠排序器（模拟向量软召回语义：
  service_name 精确命中 +0.3、error_type 命中 +0.2、模板/关键词/根因/方案词元重叠为基底分），
  用于 CI 门禁与基线回归——不依赖 Milvus/Embedding/LLM；
- milvus：构造真实 RAGPipeline 走 retrieve_top_cases（需 Milvus + 语料已入库 + Embedding 可用），
  用于生产环境实测四层重排后的真实分布。

使用（aiops-rag-system 根目录）：
    python -m evaluation.retrieval_eval --mode lexical --fail-on-gate
    python -m evaluation.retrieval_eval --mode milvus --fail-on-gate
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

from .metrics import (
    RETRIEVAL_GATES,
    aggregate_retrieval,
    check_gates,
    hit_rate,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

EVAL_DIR = Path(__file__).resolve().parent

# 词元化：英文/数字（≥2 位）+ CJK 单字（支撑中文根因/方案的确定性重叠）；
# 英文停用词过滤避免虚词稀释重叠信号（对查询与语料对称生效，不影响排序判别力）
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "for", "with", "and", "or", "not", "no", "could", "would",
    "should", "can", "may", "might", "get", "got", "from", "by", "at", "as",
    "it", "its", "this", "that", "these", "those", "when", "while", "via",
    "use", "used", "using", "your", "you", "we",
}


def tokenize(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def load_jsonl(path: Path) -> List[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def lexical_rank(query: dict, corpus: List[dict]) -> List[str]:
    """确定性基线排序（模拟向量召回 + 业务过滤语义，无外部依赖）。"""
    q_tokens = tokenize(query.get("template", "")) | tokenize(query.get("error_type", ""))
    scored = []
    for case in corpus:
        c_tokens = (
            tokenize(case.get("template", ""))
            | tokenize(" ".join(case.get("keywords") or []))
            | tokenize(case.get("root_cause", ""))
            | tokenize(case.get("solution", ""))
        )
        overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
        score = overlap
        if query.get("service_name") == case.get("service_name"):
            score += 0.3
        if query.get("error_type") == case.get("error_type"):
            score += 0.2
        scored.append((score, case["case_id"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cid for _, cid in scored]


def _rank_milvus(query: dict, pipeline) -> List[str]:
    cases = pipeline.retrieve_top_cases(dict(query), top_k=20)
    return [c.get("case_id") for c in cases if c.get("case_id")]


def _set_gauges(metrics: Dict[str, float]) -> None:
    """回写最新评测值到 Prometheus（评测脚本运行时暴露趋势；导入失败静默跳过）。"""
    try:
        from src.utils.metrics import (
            rag_eval_hit_rate,
            rag_eval_mrr,
            rag_eval_precision_at1,
            rag_eval_precision_at5,
            rag_eval_recall_at10,
            rag_eval_recall_at20,
        )
    except Exception:  # noqa: BLE001 - 独立运行无 src 依赖时静默
        return
    rag_eval_precision_at1.set(metrics.get("precision@1", 0.0))
    rag_eval_precision_at5.set(metrics.get("precision@5", 0.0))
    rag_eval_recall_at10.set(metrics.get("recall@10", 0.0))
    rag_eval_recall_at20.set(metrics.get("recall@20", 0.0))
    rag_eval_mrr.set(metrics.get("mrr", 0.0))
    rag_eval_hit_rate.set(metrics.get("hit_rate@5", 0.0))


def run_eval(mode: str = "lexical", set_gauges: bool = True) -> dict:
    """跑 Golden Set 检索评测，返回 {mode, metrics, gate_ok, gate_failures, num_queries, items}。"""
    golden = load_jsonl(EVAL_DIR / "golden_set_v1.jsonl")
    corpus = load_jsonl(EVAL_DIR / "golden_corpus.jsonl")

    pipeline = None
    if mode == "milvus":
        from src.config import load_config
        from src.rag_pipeline.pipeline import RAGPipeline

        pipeline = RAGPipeline(load_config())

    items = []
    for g in golden:
        query = g["query"]
        relevant = set(g["relevant_case_ids"])
        ranked = _rank_milvus(query, pipeline) if pipeline is not None else lexical_rank(query, corpus)
        items.append(
            {
                "qid": g["qid"],
                "service_name": query.get("service_name"),
                "error_type": query.get("error_type"),
                "p@1": precision_at_k(ranked, relevant, 1),
                "p@5": precision_at_k(ranked, relevant, 5),
                "r@10": recall_at_k(ranked, relevant, 10),
                "r@20": recall_at_k(ranked, relevant, 20),
                "hit@5": hit_rate(ranked, relevant, 5),
                "rr": reciprocal_rank(ranked, relevant),
                "context_recall": recall_at_k(ranked, relevant, 20),
            }
        )

    metrics = aggregate_retrieval(items)
    gate_ok, gate_failures = check_gates(metrics, RETRIEVAL_GATES)
    if set_gauges:
        _set_gauges(metrics)
    return {
        "mode": mode,
        "metrics": metrics,
        "gate_ok": gate_ok,
        "gate_failures": gate_failures,
        "num_queries": len(golden),
        "items": items,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Golden Set 检索质量评测（P0-3）")
    parser.add_argument("--mode", choices=["lexical", "milvus"], default="lexical",
                        help="lexical=确定性基线（CI 默认）；milvus=真实检索链路实测")
    parser.add_argument("--fail-on-gate", action="store_true", help="门禁未达标时退出码 1（CI 质量门禁）")
    args = parser.parse_args(argv)
    report = run_eval(mode=args.mode)
    summary = {k: v for k, v in report.items() if k != "items"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_gate and not report["gate_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
