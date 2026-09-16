"""评测指标纯函数（P0-3 检索质量 / P1-1 生成质量 / Trust Index）。

全部为确定性纯函数，无外部依赖，供脚本与 pytest 门禁复用。

指标口径说明：
- Precision@K 采用 AP@K（Average Precision at K，与 Ragas Context Precision 同款）：
  稀疏标注下普通 P@K 均值被 1/K 上界截断（单相关文档时 P@5 ≤ 0.2，门禁不可达），
  AP@K 对 Top-K 内每个相关命中位置取"前缀命中率"均值，归一化因子 min(|relevant|, K)。
  单相关文档排第 1 → 1.0，排第 2 → 0.5。
- Recall@K：|Top-K ∩ relevant| / |relevant|。
- MRR：首个相关结果倒数排名。
- Hit@K：Top-K 内至少一个相关案例。
- Context Recall：送入 LLM 上下文的 Top-K 对相关案例的覆盖（等价 recall@k）。
- Trust Index：T = 0.4F + 0.35C + 0.25(1-P)（F=Faithfulness, C=Citation Accuracy, P=Hallucination Rate）。
"""
from typing import Dict, List, Optional, Sequence, Set

# 检索质量门禁（docs/RAG_TRUST_EVALUATION.md P0-3 目标）
RETRIEVAL_GATES: Dict[str, float] = {
    "precision@1": 0.7,
    "precision@5": 0.5,
    "recall@10": 0.8,
    "recall@20": 0.9,
    "mrr": 0.6,
}

# 生成质量门禁（P1-1 目标：Faithfulness>0.85、Answer Relevance>0.8、
# Citation Accuracy>0.95、Hallucination Rate<5%、Trust Index≥0.85 告警线）
GENERATION_GATES: Dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevance": 0.8,
    "citation_accuracy": 0.95,
    "trust_index": 0.85,
}

GENERATION_MAX: Dict[str, float] = {
    "hallucination_rate": 0.05,
}


def precision_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    """Precision@K（AP@K 语义，见模块 docstring）。"""
    top = list(ranked)[: max(1, k)]
    hits = 0
    total = 0.0
    for idx, case_id in enumerate(top, start=1):
        if case_id in relevant:
            hits += 1
            total += hits / idx
    denom = min(len(relevant), max(1, k))
    return total / denom if denom else 0.0


def recall_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    """Top-K 召回率：|Top-K ∩ relevant| / |relevant|。"""
    if not relevant:
        return 0.0
    top = set(list(ranked)[: max(1, k)])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: Set[str]) -> float:
    """首个相关结果的倒数排名；无命中为 0。"""
    for idx, case_id in enumerate(ranked, start=1):
        if case_id in relevant:
            return 1.0 / idx
    return 0.0


def hit_rate(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    """Hit@K：Top-K 内至少一个相关案例（0/1）。"""
    top = set(list(ranked)[: max(1, k)])
    return 1.0 if top & relevant else 0.0


def context_recall(retrieved_case_ids: Sequence[str], relevant: Set[str], k: int = 20) -> float:
    """Context Recall：送入 LLM 上下文的 Top-K 是否覆盖全部相关案例（等价 recall@k 语义）。"""
    return recall_at_k(retrieved_case_ids, relevant, k)


def aggregate_retrieval(eval_items: List[dict]) -> Dict[str, float]:
    """聚合逐条评测结果 -> 库级指标。

    每个 eval_item: {"p@1", "p@5", "r@10", "r@20", "hit@5", "rr", "context_recall"}。
    """
    n = len(eval_items)
    if n == 0:
        return {}
    return {
        "precision@1": sum(i["p@1"] for i in eval_items) / n,
        "precision@5": sum(i["p@5"] for i in eval_items) / n,
        "recall@10": sum(i["r@10"] for i in eval_items) / n,
        "recall@20": sum(i["r@20"] for i in eval_items) / n,
        "hit_rate@5": sum(i["hit@5"] for i in eval_items) / n,
        "mrr": sum(i["rr"] for i in eval_items) / n,
        "context_recall": sum(i["context_recall"] for i in eval_items) / n,
    }


def check_gates(
    metrics: Dict[str, float],
    gates: Dict[str, float],
    max_limits: Optional[Dict[str, float]] = None,
) -> tuple:
    """质量门禁：gates 为下限（须严格大于），max_limits 为上限（须严格小于）。

    Returns: (ok: bool, failures: [str])
    """
    failures: List[str] = []
    for name, threshold in gates.items():
        value = metrics.get(name)
        if value is None or value <= threshold:
            failures.append(f"{name}={value} 未达标（目标 > {threshold}）")
    for name, limit in (max_limits or {}).items():
        value = metrics.get(name)
        if value is None or value >= limit:
            failures.append(f"{name}={value} 超限（上限 < {limit}）")
    return (not failures, failures)


def trust_index(faithfulness: float, citation_accuracy: float, hallucination_rate: float) -> float:
    """综合信任指数 T = 0.4F + 0.35C + 0.25(1-P)，输入截断到 [0,1]。"""

    def clamp(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    f, c = clamp(faithfulness), clamp(citation_accuracy)
    p = clamp(hallucination_rate)
    return 0.4 * f + 0.35 * c + 0.25 * (1.0 - p)
