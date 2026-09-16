"""控制台侧单次操作质量与内容安全指标（用户需求：诊断/治理操作逐次展示 + 总览整体统计）。

与 RAG 服务侧 src/utils/quality_eval.py 保持同一套口径（词元化、阈值、Trust Index 权重）：
- 诊断质量（每次诊断/Agent 深度诊断返回并在前端展示）：
  Faithfulness / Context Coverage（引用覆盖）/ Answer Relevance /
  Hallucination Rate / Trust Index = 0.4F + 0.35C + 0.25(1-P)，单次达标线 0.85；
- 内容安全扫描（知识新建/编辑/审批预检）：PII / 密钥 / JWT / 危险命令 /
  提示词注入 / 恶意脚本，规则版本 console-content-guard-v1；
  - high（密钥/JWT/危险命令/注入/恶意脚本）→ 拦截发布，可凭 override_reason 误报放行（审计留痕）；
  - medium（PII）→ 标记放行（flagged），前端提示人工关注；
  - RAG 侧 kb-sync 入库卡点（content_guard）仍为最终硬闸，本扫描为发布前的人审前置；
- 总览聚合：供 /api/v1/console/dashboard 输出整体 Trust Index、检索、生成与内容安全统计
  （数据源：事件 ai_output_json.quality + Agent 会话 result_json.quality + 审计 content_guard_scan/override）。
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ------------------ 单次诊断质量指标（与 RAG quality_eval 口径一致） ------------------

GATE_LINE = 0.85  # 单次质量线（与运维告警线一致）

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


def _tokenize(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def _split_claims(answer: str) -> List[str]:
    return [c.strip() for c in _CLAIM_SPLIT_RE.split(answer or "") if c.strip()]


def _claim_supported(claim: str, grounding_tokens: set) -> bool:
    tokens = _tokenize(claim)
    if not tokens:
        return True
    return len(tokens & grounding_tokens) / len(tokens) >= _SUPPORT_THRESHOLD


def evaluate_diagnosis_quality(
    query: Dict[str, str],
    answer: str,
    contexts: List[Dict],
    gate_line: float = GATE_LINE,
) -> Dict[str, Any]:
    """对单次诊断答案计算质量指标（纯函数，单轮诊断与 Agent 深度诊断共用）。

    Args:
        query: 告警症状 {"template", "error_type", "service_name"}；
        answer: 诊断答案（root_cause + solution）；
        contexts: 注入上下文的候选/召回案例列表（root_cause/solution/alert_template）；
        gate_line: 单次质量线（默认 0.85）。
    """
    claims = _split_claims(answer)
    q_tokens = (
        _tokenize(query.get("template", ""))
        | _tokenize(query.get("error_type", ""))
        | _tokenize(query.get("service_name", ""))
    )

    context_tokens: set = set()
    case_token_sets: List[set] = []
    for ctx in contexts or []:
        toks = (
            _tokenize(str(ctx.get("root_cause") or ""))
            | _tokenize(str(ctx.get("solution") or ""))
            | _tokenize(str(ctx.get("alert_template") or ctx.get("template") or ""))
        )
        case_token_sets.append(toks)
        context_tokens |= toks

    # grounding 基准 = 检索上下文 ∪ 查询症状：复述告警现象不计为幻觉（RAGAS 同款约定）
    grounding_tokens = context_tokens | q_tokens
    supported = [_claim_supported(c, grounding_tokens) for c in claims]
    faithfulness = (sum(1 for s in supported if s) / len(claims)) if claims else 0.0
    hallucination_rate = 1.0 - faithfulness

    answer_tokens: set = set()
    for claim in claims:
        answer_tokens |= _tokenize(claim)

    cited = 0
    if case_token_sets and answer_tokens:
        for toks in case_token_sets:
            if toks and len(answer_tokens & toks) / len(answer_tokens) >= _CITATION_THRESHOLD:
                cited += 1
    coverage = cited / len(case_token_sets) if case_token_sets else 0.0

    relevance = len(q_tokens & answer_tokens) / len(q_tokens) if q_tokens else 0.0
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


# ------------------ 内容安全扫描（发布/审批前置，规则版本化管理） ------------------

RULE_VERSION = "console-content-guard-v1"

# (类别, 风险级别, 编译后的正则)；全部使用非捕获组，findall 返回完整匹配
_RULES: Tuple[Tuple[str, str, "re.Pattern[str]"], ...] = (
    # --- PII（medium：标记放行，人工关注）---
    ("pii_email", "medium", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("pii_phone", "medium", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("pii_id_card", "medium", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
    # --- 密钥/凭证（high：拦截）---
    ("secret_aws_key", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret_github", "high", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("secret_slack", "high", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "secret_assignment",
        "high",
        re.compile(
            r"(?:api[_-]?key|access[_-]?token|secret|passwd|password|pwd)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
            re.IGNORECASE,
        ),
    ),
    ("token_jwt", "high", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    ("private_key", "high", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # --- 危险命令（high：仅命中灾难性目标，避免误杀常规清理命令）---
    (
        "dangerous_command",
        "high",
        re.compile(
            r"(?:rm\s+-rf\s+(?:/(?:\s|$)|~(?:\s|$)|/\*(?:\s|$)|~/\*(?:\s|$)|\$HOME(?:\s|$))"
            r"|mkfs\.\w+"
            r"|dd\s+if=/dev/(?:zero|random)\s+of=/dev/"
            r"|:\(\)\s*\{\s*:\|:&\s*\}\s*;"
            r"|chmod\s+-R\s+777\s+/(?:\s|$)"
            r"|\bflushall\b"
            r"|\bdrop\s+(?:table|database|schema)\b"
            r"|curl\s+[^|\n]*\|\s*(?:ba)?sh\b"
            r"|wget\s+[^|\n]*\|\s*(?:ba)?sh\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    # --- 提示词注入（high：拦截）---
    (
        "prompt_injection",
        "high",
        re.compile(
            r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
            r"|disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
            r"|忽略(?:之前|以上|上述)(?:的)?(?:所有)?(?:指令|提示|要求)"
            r"|无视(?:之前|以上|上述)(?:的)?(?:所有)?(?:指令|提示|要求)"
            r"|reveal\s+your\s+(?:system\s+)?prompt"
            r"|\bjailbreak\b"
            r"|developer\s+mode"
            r")",
            re.IGNORECASE,
        ),
    ),
    # --- 恶意脚本（high：拦截）---
    (
        "malicious_script",
        "high",
        re.compile(r"(?:<script\b|\bonerror\s*=|powershell\s+-enc)"),
    ),
)

_RISK_WEIGHT = {"high": 0.6, "medium": 0.2}


def _mask_sample(sample: str) -> str:
    """命中片段脱敏展示：保留首尾少量字符，避免敏感值明文回流到前端/审计。"""
    text = str(sample).strip()
    if len(text) <= 6:
        return text[:2] + "***"
    return text[:4] + "***" + text[-2:]


def _scan_text(text: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for category, risk, pattern in _RULES:
        found = pattern.findall(text or "")
        if found:
            sample = found[0] if isinstance(found[0], str) else str(found[0])
            hits.append({"category": category, "risk": risk, "count": len(found), "sample": _mask_sample(sample)})
    return hits


def scan_case_content(fields: Dict[str, str]) -> Dict[str, Any]:
    """知识内容安全扫描：返回结构化扫描结果（前端/审批展示 + 审计留痕 + 总览聚合）。

    Returns:
        {"rule_version", "risk_level"(none/medium/high), "blocked", "can_override",
         "categories", "hits", "quality_score", "scanned_fields", "override"(可选)}
    """
    scanned = {key: str(value or "") for key, value in fields.items() if str(value or "").strip()}
    hits: List[Dict[str, Any]] = []
    for key, text in scanned.items():
        for hit in _scan_text(text):
            hits.append({"field": key, **hit})
    categories = sorted({h["category"] for h in hits})
    has_high = any(h["risk"] == "high" for h in hits)
    has_medium = any(h["risk"] == "medium" for h in hits)
    risk_level = "high" if has_high else ("medium" if has_medium else "none")
    penalty = sum(_RISK_WEIGHT[h["risk"]] * min(h["count"], 3) for h in hits)
    return {
        "rule_version": RULE_VERSION,
        "risk_level": risk_level,
        "blocked": has_high,
        "can_override": has_high,
        "categories": categories,
        "hits": hits,
        "quality_score": round(max(0.0, 1.0 - penalty), 4),
        "scanned_fields": sorted(scanned),
    }


# ------------------ 总览聚合（Dashboard 整体质量态势） ------------------

def _loads(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_quality_payload(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """从持久化 JSON（事件 ai_output_json / Agent 会话 result_json）提取质量指标。"""
    data = _loads(raw)
    if not data:
        return None
    quality = data.get("quality")
    if isinstance(quality, dict) and "trust_index" in quality:
        return quality
    return None


def aggregate_quality_stats(event_outputs: List[Optional[str]], session_outputs: List[Optional[str]]) -> Dict[str, Any]:
    """聚合单次诊断质量指标为总览整体统计（Trust Index / Faithfulness / 幻觉率等均值）。"""
    samples: List[Dict[str, Any]] = []
    for raw in list(event_outputs) + list(session_outputs):
        quality = parse_quality_payload(raw)
        if quality:
            samples.append(quality)
    if not samples:
        return {
            "sample_count": 0,
            "trust_index_avg": None,
            "faithfulness_avg": None,
            "context_coverage_avg": None,
            "answer_relevance_avg": None,
            "hallucination_rate_avg": None,
            "quality_ok_rate": None,
        }

    def avg(key: str) -> float:
        return round(sum(float(s.get(key) or 0) for s in samples) / len(samples), 4)

    ok_count = sum(1 for s in samples if s.get("quality_ok"))
    return {
        "sample_count": len(samples),
        "trust_index_avg": avg("trust_index"),
        "faithfulness_avg": avg("faithfulness"),
        "context_coverage_avg": avg("context_coverage"),
        "answer_relevance_avg": avg("answer_relevance"),
        "hallucination_rate_avg": avg("hallucination_rate"),
        "quality_ok_rate": round(ok_count / len(samples), 4),
    }
