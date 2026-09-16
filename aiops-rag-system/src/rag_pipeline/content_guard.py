"""知识内容安全预检（P1 投毒检测）：密钥/PII/危险命令/提示词注入规则扫描。

卡点位置（双保险）：
- 控制台：发布/变更创建前扫描（app/backend/services/content_guard.py）-> 422 拦截；
- RAG：kb-sync/upsert 与 cases/close -> pipeline 写入（write_case / upsert_console_case）
  入 Milvus 前最终扫描 -> 422 poisoned_content_rejected；
  补偿任务重放遇 422 为永久失败 -> 进入死信，人工复核（误报放行 = 修订文案后重新发布）。

指标：kb_poison_scan_total{result} / kb_poison_blocked_total{category}。
规则版本：GUARD_RULE_VERSION（规则集演进时递增，供审计与误报回溯）。
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..utils.logger import get_logger
from ..utils.metrics import kb_poison_blocked_total, kb_poison_scan_total

logger = get_logger(__name__)

GUARD_RULE_VERSION = "v1"


@dataclass
class GuardFinding:
    """单条命中：类别 / 规则 ID / 严重级别 / 脱敏摘录。"""

    category: str
    pattern_id: str
    severity: str
    excerpt: str

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "pattern_id": self.pattern_id,
            "severity": self.severity,
            "excerpt": self.excerpt,
        }


@dataclass
class GuardResult:
    """扫描结果：clean=True 无命中；findings 供 422 详情与审计。"""

    clean: bool
    findings: List[GuardFinding] = field(default_factory=list)
    rule_version: str = GUARD_RULE_VERSION

    def to_detail(self) -> dict:
        return {
            "clean": self.clean,
            "findings": [f.to_dict() for f in self.findings],
            "rule_version": self.rule_version,
        }


def _p(pattern: str, flags: int = 0):
    return re.compile(pattern, flags)


# (pattern_id, category, severity, compiled_regex)
_PATTERNS = [
    # ---- 密钥 / 凭证泄露 ----
    ("secret_aws_access_key", "secret", "critical", _p(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret_api_key_prefix", "secret", "critical", _p(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("secret_github_token", "secret", "critical", _p(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("secret_private_key_block", "secret", "critical", _p(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "secret_password_literal",
        "secret",
        "high",
        _p(
            r"\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"']?[^\s\"']{4,}",
            re.IGNORECASE,
        ),
    ),
    # ---- PII ----
    ("pii_mobile_cn", "pii", "high", _p(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("pii_id_card_cn", "pii", "high", _p(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    # ---- 危险命令 ----
    ("cmd_rm_rf_root", "dangerous_command", "critical", _p(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(?:\s|$)")),
    ("cmd_disk_wipe", "dangerous_command", "critical", _p(r"\bmkfs(?:\.\w+)?\s|\bdd\s+if=/dev/(?:zero|random)\s+of=/dev/")),
    ("cmd_fork_bomb", "dangerous_command", "critical", _p(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("cmd_power_control", "dangerous_command", "high", _p(r"\b(?:shutdown|reboot|halt|poweroff)\b[^;\n]{0,40}\b(?:now|-h|-r)\b", re.IGNORECASE)),
    ("cmd_chmod_777_root", "dangerous_command", "high", _p(r"\bchmod\s+(?:-R\s+)?777\s+/(?:\s|$)")),
    ("cmd_sql_drop", "dangerous_command", "high", _p(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE)),
    ("cmd_pipe_to_shell", "dangerous_command", "critical", _p(r"\b(?:curl|wget)\b[^;\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b", re.IGNORECASE)),
    ("cmd_base64_exec", "dangerous_command", "critical", _p(r"\bbase64\s+(?:--decode|-d)\b[^;\n|]*\|\s*(?:ba|z)?sh\b", re.IGNORECASE)),
    ("cmd_dynamic_exec", "dangerous_command", "medium", _p(r"\b(?:eval|exec)\s*\(")),
    # ---- 提示词注入 / 恶意指令 ----
    ("inject_ignore_instructions", "prompt_injection", "critical", _p(r"ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|messages?)", re.IGNORECASE)),
    ("inject_disregard", "prompt_injection", "critical", _p(r"(?:disregard|forget)\s+(?:all\s+|your\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?|rules?)", re.IGNORECASE)),
    ("inject_role_hijack", "prompt_injection", "high", _p(r"you\s+are\s+now\s+(?:a|an|the)\b", re.IGNORECASE)),
    ("inject_marker", "prompt_injection", "critical", _p(r"<\|(?:im_start|im_end|system)\|>|###\s*(?:system|assistant)\s*###", re.IGNORECASE)),
    ("inject_zh", "prompt_injection", "critical", _p(r"忽略(?:以上|之前|上述|前面)(?:的)?(?:所有)?(?:指令|提示|规则)|(?:系统提示词|system\s*prompt)\s*[:：]", re.IGNORECASE)),
]


def _mask(match_text: str) -> str:
    """摘录脱敏：仅保留首尾少量字符，避免拦截详情二次泄露敏感内容。"""
    text = match_text.strip()
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def scan_text(text: str, findings: Optional[List[GuardFinding]] = None) -> List[GuardFinding]:
    """对单段文本扫描全部规则，命中追加到 findings（可复用列表便于多字段聚合）。"""
    result = findings if findings is not None else []
    if not text:
        return result
    for pattern_id, category, severity, regex in _PATTERNS:
        for match in regex.finditer(text):
            result.append(
                GuardFinding(
                    category=category,
                    pattern_id=pattern_id,
                    severity=severity,
                    excerpt=_mask(match.group()),
                )
            )
    return result


def scan_case_content(root_cause: str, solution: str, alert_template: str) -> GuardResult:
    """知识案例三文本字段聚合扫描（去重后返回）。"""
    findings: List[GuardFinding] = []
    for text in (root_cause or "", solution or "", alert_template or ""):
        scan_text(text, findings)
    deduped: List[GuardFinding] = []
    seen = set()
    for f in findings:
        key = (f.category, f.pattern_id, f.excerpt)
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return GuardResult(clean=not deduped, findings=deduped)


class PoisonedContentError(ValueError):
    """内容安全预检未通过；携带 GuardResult 供 API 层构造 422 详情。"""

    def __init__(self, result: GuardResult):
        self.result = result
        super().__init__("poisoned_content_rejected")


def assert_case_content_safe(
    root_cause: str, solution: str, alert_template: str, source: str = "kb_sync"
) -> GuardResult:
    """入库卡点：扫描 -> 指标 -> 命中即抛 PoisonedContentError（调用方转 422）。"""
    result = scan_case_content(root_cause, solution, alert_template)
    kb_poison_scan_total.labels(result="clean" if result.clean else "blocked").inc()
    for finding in result.findings:
        kb_poison_blocked_total.labels(category=finding.category).inc()
    if not result.clean:
        logger.warning(
            "Poisoned content rejected (source=%s, findings=%s)",
            source,
            result.to_detail()["findings"],
        )
        raise PoisonedContentError(result)
    return result
