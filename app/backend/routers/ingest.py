"""流水线事件 → 控制台事件表同步入口。

接收上游系统（RAG 流水线 webhook 契约、UMPS、Prometheus Alertmanager 转发等）
推送的告警事件，标准化后写入控制台 events 表（status=pending，等待诊断）。

鉴权：配置中心 event_ingest_token 非空时，请求必须携带精确匹配的
X-Ingest-Token 头（不匹配返回 401）；留空时放行（与 RAG fail-open 口径一致）。
幂等：按 event_id 去重，重复推送返回 duplicated，不重复入库。
分类：载荷缺 error_type 时按关键词规则兜底分类（网关 502/OOM/超时等）。
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.Events import Events
from services.console_common import detect_sensitivity, get_config, mask_alert_text, write_audit
from services.llm_runtime import decrypt_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

# 批量同步上限：单次请求最多入库 200 条
BATCH_LIMIT = 200

# webhook 整数 severity → 事件表字符串 severity（事件表存 info/warning/critical）
SEVERITY_INT_MAP = {1: "info", 2: "warning", 3: "critical"}
# 常见文本 severity 归一化
SEVERITY_TEXT_MAP = {
    "info": "info",
    "notice": "info",
    "low": "info",
    "warning": "warning",
    "warn": "warning",
    "medium": "warning",
    "major": "warning",
    "error": "warning",
    "critical": "critical",
    "crit": "critical",
    "fatal": "critical",
    "high": "critical",
    "emergency": "critical",
    "alert": "critical",
}

# 关键词兜底分类规则（与种子规则库同源，按顺序命中即停；severity 为该类默认级别）
CLASSIFY_RULES: List[Tuple[str, Tuple[str, ...], str]] = [
    ("gateway_502", ("bad gateway", "upstream error", "502"), "critical"),
    ("oom_killed", ("oomkilled", "out of memory", "oom"), "critical"),
    ("connection_refused", ("connection refused",), "warning"),
    ("disk_full", ("no space left", "disk full", "disk usage"), "warning"),
    ("cpu_throttling", ("cpu throttling", "throttling"), "warning"),
    ("redis_pool_exhausted", ("redis pool exhausted", "max active connections"), "warning"),
    ("timeout", ("timeout", "timed out", "deadline exceeded"), "warning"),
]


def _normalize_severity(value: Any, default: str = "warning") -> str:
    """severity 归一化：整数 1/2/3 → info/warning/critical；常见文本别名就近映射。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return SEVERITY_INT_MAP.get(int(value), default)
    text = str(value).strip()
    if text.isdigit():
        return SEVERITY_INT_MAP.get(int(text), default)
    return SEVERITY_TEXT_MAP.get(text.lower(), default)


def _classify(haystack: str) -> Optional[Tuple[str, str]]:
    """关键词兜底分类：返回 (error_type, 默认 severity)，未命中返回 None。"""
    lowered = haystack.lower()
    for error_type, keywords, severity in CLASSIFY_RULES:
        for keyword in keywords:
            if keyword in lowered:
                return error_type, severity
    return None


def _extract_service_name(body: "IngestAlertBody") -> str:
    """服务名：显式字段优先，其次 labels（Prometheus/UMPS 常见键），兜底 unknown。"""
    if body.service_name:
        return body.service_name
    labels = body.labels if isinstance(body.labels, dict) else {}
    for key in ("service", "service_name", "app", "application", "job"):
        value = labels.get(key)
        if value:
            return str(value)
    return "unknown"


def _extract_cluster(body: "IngestAlertBody") -> Optional[str]:
    """集群：显式字段优先，其次 labels；namespace 作为最后兜底。"""
    if body.cluster:
        return body.cluster
    labels = body.labels if isinstance(body.labels, dict) else {}
    for key in ("cluster", "k8s_cluster"):
        value = labels.get(key)
        if value:
            return str(value)
    if body.namespace:
        return str(body.namespace)
    return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """兼容毫秒时间戳 / 秒时间戳 / ISO 字符串；无法解析返回 None（用入库默认时间）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fingerprint(source: str, service_name: str, error_type: Optional[str]) -> str:
    """指纹与 RAG webhook 同口径：md5(source|service|error_type)。"""
    raw = f"{source}|{service_name}|{error_type or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _ensure_ingest_authorized(db: AsyncSession, presented_token: Optional[str]) -> bool:
    """event_ingest_token 非空时要求 X-Ingest-Token 精确匹配；留空放行（fail-open）。

    返回 True 表示本次请求执行了 Token 校验（记入审计 token_checked）。
    """
    configured = decrypt_secret((await get_config(db, "event_ingest_token", "")) or "").strip()
    if not configured:
        return False
    if (presented_token or "").strip() != configured:
        logger.warning("事件同步请求被拒绝：X-Ingest-Token 缺失或不匹配")
        raise HTTPException(status_code=401, detail="X-Ingest-Token 缺失或不匹配")
    return True


class IngestAlertBody(BaseModel):
    """兼容 RAG webhook 契约：source/raw_message/labels/timestamp 为核心字段，
    event_id/error_type/template/severity/confidence/topology 等可选。"""

    source: Optional[str] = "webhook"
    raw_message: Optional[str] = None
    labels: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[Any] = None
    event_id: Optional[str] = None
    error_type: Optional[str] = None
    template: Optional[str] = None
    severity: Optional[Any] = None
    confidence: Optional[float] = None
    topology: Optional[str] = None
    service_name: Optional[str] = None
    cluster: Optional[str] = None
    namespace: Optional[str] = None


class IngestBatchBody(BaseModel):
    """批量同步载荷：events 数组单次最多 BATCH_LIMIT 条。"""

    events: List[IngestAlertBody] = Field(default_factory=list)


def _standardize(body: IngestAlertBody, source: str) -> Tuple[Dict[str, Any], str]:
    """标准化为事件表行参数：缺 error_type 时关键词兜底分类，生成指纹与 event_id。"""
    labels = body.labels if isinstance(body.labels, dict) else {}
    raw_message = (body.raw_message or "").strip()
    service_name = _extract_service_name(body)
    error_type = (body.error_type or "").strip() or None
    default_severity = "warning"
    if not error_type:
        haystack = " ".join(
            [raw_message, body.template or "", json.dumps(labels, ensure_ascii=False, default=str)]
        )
        classified = _classify(haystack)
        if classified:
            error_type, default_severity = classified
    severity = _normalize_severity(body.severity, default=default_severity)
    fingerprint = _fingerprint(source, service_name, error_type)
    event_id = (body.event_id or "").strip() or f"evt_{int(time.time() * 1000)}_{fingerprint[:8]}"
    template = (
        (body.template or "").strip()
        or (str(labels.get("alertname")) if labels.get("alertname") else "")
        or raw_message[:120]
    )
    kwargs: Dict[str, Any] = {
        "event_id": event_id,
        "service_name": service_name,
        "severity": severity,
        "status": "pending",
        "template": template or None,
        "raw_log": raw_message or None,
        "fingerprint": fingerprint,
        "topology": (body.topology or "").strip() or None,
        "cluster": _extract_cluster(body),
        "error_type": error_type,
        "confidence": body.confidence,
    }
    parsed_ts = _parse_timestamp(body.timestamp)
    if parsed_ts is not None:
        kwargs["created_at"] = parsed_ts
        kwargs["updated_at"] = parsed_ts
    return kwargs, event_id


async def _ingest_one(
    db: AsyncSession,
    body: IngestAlertBody,
    token_checked: bool = False,
    seen_event_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """标准化 → 幂等检查 → 入库（status=pending）→ 审计，返回单条结果。

    入站脱敏（评审采纳项）：data_masking_enabled=true（默认）时，原始告警文本
    在落库前经 mask_alert_text 掩码（证件/手机号/银行卡/内网 IP/密钥/令牌），
    原始内容不落库；掩码占位符保留可识别格式（138****5678、10.1.2.x），
    detect_sensitivity 仍可对落库文本二次判定敏感度并驱动 LLM 路由。
    """
    source = (body.source or "webhook").strip() or "webhook"
    masking_enabled = ((await get_config(db, "data_masking_enabled", "true")) or "true").strip().lower() != "false"
    if masking_enabled and (body.raw_message or "").strip():
        body.raw_message = mask_alert_text(body.raw_message)
    kwargs, event_id = _standardize(body, source)
    if seen_event_ids is not None and event_id in seen_event_ids:
        return {"event_id": event_id, "result": "duplicated"}
    existing = await db.execute(select(Events).where(Events.event_id == event_id).limit(1))
    if existing.scalar_one_or_none() is not None:
        if seen_event_ids is not None:
            seen_event_ids.add(event_id)
        await write_audit(
            db,
            actor=f"ingest:{source}",
            action="event_ingest",
            target_type="event",
            target_id=event_id,
            after={
                "result": "duplicated",
                "service_name": kwargs["service_name"],
                "error_type": kwargs.get("error_type"),
                "severity": kwargs["severity"],
                "token_checked": token_checked,
            },
        )
        return {"event_id": event_id, "result": "duplicated"}
    db.add(Events(**kwargs))
    await db.commit()
    if seen_event_ids is not None:
        seen_event_ids.add(event_id)
    await write_audit(
        db,
        actor=f"ingest:{source}",
        action="event_ingest",
        target_type="event",
        target_id=event_id,
        after={
            "result": "accepted",
            "service_name": kwargs["service_name"],
            "error_type": kwargs.get("error_type"),
            "severity": kwargs["severity"],
            "template": kwargs.get("template"),
            "token_checked": token_checked,
            "data_masking_enabled": masking_enabled,
            "masked": masking_enabled and bool(body.raw_message),
            "sensitivity_categories": detect_sensitivity(kwargs.get("raw_log") or "")[1],
        },
    )
    return {"event_id": event_id, "result": "accepted"}


@router.post("/alerts")
async def ingest_alert(
    body: IngestAlertBody,
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
    db: AsyncSession = Depends(get_db),
):
    """单条告警同步（兼容 RAG webhook 契约）。"""
    token_checked = await _ensure_ingest_authorized(db, x_ingest_token)
    result = await _ingest_one(db, body, token_checked=token_checked)
    accepted = 1 if result["result"] == "accepted" else 0
    return {
        "accepted": accepted,
        "duplicated": 1 - accepted,
        "event_id": result["event_id"],
        "total": 1,
    }


@router.post("/alerts/batch")
async def ingest_alerts_batch(
    body: IngestBatchBody,
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
    db: AsyncSession = Depends(get_db),
):
    """批量告警同步（单次最多 200 条；按 event_id 幂等去重，单条失败不阻塞整批）。"""
    token_checked = await _ensure_ingest_authorized(db, x_ingest_token)
    if not body.events:
        raise HTTPException(status_code=400, detail="events 不能为空")
    if len(body.events) > BATCH_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"批量同步单次最多 {BATCH_LIMIT} 条，当前 {len(body.events)} 条",
        )
    seen: Set[str] = set()
    results: List[Dict[str, Any]] = []
    for item in body.events:
        try:
            result = await _ingest_one(db, item, token_checked=token_checked, seen_event_ids=seen)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 单条失败不阻塞整批
            await db.rollback()
            logger.warning("批量同步单条失败: %s", exc)
            results.append({"event_id": None, "result": "failed", "error": str(exc)})
            continue
        results.append(result)
    accepted = sum(1 for r in results if r["result"] == "accepted")
    duplicated = sum(1 for r in results if r["result"] == "duplicated")
    return {
        "total": len(results),
        "accepted": accepted,
        "duplicated": duplicated,
        "failed": sum(1 for r in results if r["result"] == "failed"),
        "results": results,
    }
