"""单次诊断质量在线评估（用户需求：每次深度诊断展示质量指标）。

与 evaluation/ 批量评测的分工：
- evaluation/generation_eval.py：离线批量（Golden Set），产出 Prometheus Gauge + CI 门禁；
- 本模块：在线单次诊断的即时质量评估（纯函数、无外部依赖），由 RAG /diagnostic 与
  控制台诊断链路在响应中返回，供前端对每一次诊断逐次展示。

口径（与批量评测语义一致）：
- Faithfulness：答案分句后逐句与「检索上下文 ∪ 告警症状」做词元重叠，
  重叠 ≥0.35 视为有据（复述告警现象不计为幻觉，RAGAS 同款约定）；
- Hallucination Rate = 1 - Faithfulness；
- Context Coverage（引用覆盖）：注入上下文的 Top-K 案例中被答案实质引用
  （案例词元与答案词元重叠 ≥0.15）的比例——批量评测的 Citation Accuracy 在单次
  诊断下的对应物（RAG 诊断答案不带 [kb_case_xxx] 标记，以实质引用语义代替）；
- Answer Relevance：答案对查询症状（service/error_type/template）词元的覆盖率；
- Trust Index：T = 0.4F + 0.35·Coverage + 0.25(1-P)；
- 质量线：trust_index ≥ 0.85 视为单次达标（与 docs/OPERATIONS_RUNBOOK.md 告警线一致）。
"""
import re
from typing import Dict, List

# 词元化：英文/数字（≥2 位）+ CJK 单字（与 evaluation/retrieval_eval.py 保持一致）
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "for", "with", "and", "or", "not", "no", "could", "would",
    "should", "can", "may", "might", "get", "got", "from", "by", "at", "as",
    "it", "its", "this", "that", "these", "those", "when", "while", "via",
    "use", "used", "using", "your", "you", "we",
}
_CLAIM_SPLIT_RE = re.compile(r"[。；;！!？?\n]+")
_SUPPORT_THRESHOLD = 0.35
_CITATION_THRESHOLD = 0.15

# 单次诊断质量线（与批量质量告警线一致）
GATE_LINE = 0.85


def tokenize(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def split_claims(answer: str) -> List[str]:
    """按中英文句读拆分答案为原子断言。"""
    return [c.strip() for c in _CLAIM_SPLIT_RE.split(answer or "") if c.strip()]


def claim_supported(claim: str, grounding_tokens: set) -> bool:
    """断言有据判定：词元重叠 ≥ SUPPORT_THRESHOLD。"""
    tokens = tokenize(claim)
    if not tokens:
        return True
    return len(tokens & grounding_tokens) / len(tokens) >= _SUPPORT_THRESHOLD


def evaluate_diagnosis_quality(
    query: Dict[str, str],
    answer: str,
    contexts: List[Dict],
    gate_line: float = GATE_LINE,
) -> Dict:
    """对单次诊断答案计算质量指标（纯函数，RAG /diagnostic 与控制台诊断共用）。

    Args:
        query: 告警症状 {"template", "error_type", "service_name"}；
        answer: 诊断答案（root_cause + solution 拼接）；
        contexts: 注入上下文的检索案例列表（root_cause/solution/alert_template）；
        gate_line: 单次质量线（默认 0.85，与运维告警线一致）。

    Returns:
        质量指标 dict（faithfulness / context_coverage / answer_relevance /
        hallucination_rate / trust_index / num_claims / unsupported_claims /
        quality_ok / gate_line）。
    """
    claims = split_claims(answer)
    q_tokens = (
        tokenize(query.get("template", ""))
        | tokenize(query.get("error_type", ""))
        | tokenize(query.get("service_name", ""))
    )

    context_tokens: set = set()
    case_token_sets: List[set] = []
    for ctx in contexts or []:
        toks = (
            tokenize(str(ctx.get("root_cause") or ""))
            | tokenize(str(ctx.get("solution") or ""))
            | tokenize(str(ctx.get("alert_template") or ctx.get("template") or ""))
        )
        case_token_sets.append(toks)
        context_tokens |= toks

    # grounding 基准 = 检索上下文 ∪ 查询症状：诊断答案复述告警现象不算幻觉
    grounding_tokens = context_tokens | q_tokens
    supported = [claim_supported(c, grounding_tokens) for c in claims]
    faithfulness = (sum(1 for s in supported if s) / len(claims)) if claims else 0.0
    hallucination_rate = 1.0 - faithfulness

    answer_tokens: set = set()
    for claim in claims:
        answer_tokens |= tokenize(claim)

    # 引用覆盖：被答案实质引用（重叠比例达标）的上下文案例占比
    cited = 0
    if case_token_sets and answer_tokens:
        for toks in case_token_sets:
            if toks and len(answer_tokens & toks) / len(answer_tokens) >= _CITATION_THRESHOLD:
                cited += 1
    coverage = cited / len(case_token_sets) if case_token_sets else 0.0

    relevance = (
        len(q_tokens & answer_tokens) / len(q_tokens) if q_tokens else 0.0
    )

    trust_index = 0.4 * faithfulness + 0.35 * coverage + 0.25 * (1.0 - hallucination_rate)

    return {
        "faithfulness": round(faithfulness, 4),
        "context_coverage": round(coverage, 4),
        "answer_relevance": round(relevance, 4),
        "hallucination_rate": round(hallucination_rate, 4),
        "trust_index": round(trust_index, 4),
        "num_claims": len(claims),
        "unsupported_claims": [c[:60] for c, s in zip(claims, supported) if not s],
        "quality_ok": bool(trust_index >= gate_line),
        "gate_line": gate_line,
    }
