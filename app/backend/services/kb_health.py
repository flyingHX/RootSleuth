"""知识库红黄绿健康度报表（P2-1）：知识健康度 API 聚合。

口径（对应 docs/OPERATIONS_RUNBOOK.md 知识健康目标）：
- 绿（健康）：active 案例、同步已 verify、反馈分 >= 0、新鲜度与内容安全均无风险；
- 黄（关注）：同步未 verify / 超过 kb_expire_days 未更新 / 反馈分为负 /
  内容扫描 medium（PII 标记放行）；
- 红（风险）：同步任务进入死信（索引缺知识）/ 反馈分 <= -2 /
  内容安全 high 命中（含误报放行留痕）/ 超过 2×kb_expire_days（无条件老化分级阈值）。

聚合输出：红黄绿统计、同步闭环状态、内容安全统计、老化口径与风险案例清单
（红优先、黄次之，供 OpsPage 健康报表 Tab 与总览聚合统计展示）。
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.audit_logs import Audit_logs
from models.kb_cases import Kb_cases
from models.rag_sync_tasks import Rag_sync_tasks
from services.console_common import get_config

CASE_FIELDS_LABEL = {
    "case_id": "案例 ID",
    "error_type": "错误类型",
    "service_name": "服务名",
}


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_days(case_updated_at: Any, now: datetime) -> Optional[int]:
    updated = _parse_dt(case_updated_at)
    if updated is None:
        return None
    return max(0, int((now - updated).total_seconds() // 86400))


def _latest_scan_risk(audit_rows: List[Audit_logs]) -> Tuple[Optional[str], Optional[str]]:
    """取案例最近一次内容安全扫描的 risk_level 与 rule_version（含误报放行标记）。"""
    for log in audit_rows:
        try:
            after = json.loads(log.after_json) if log.after_json else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(after, dict):
            continue
        risk = str(after.get("risk_level") or "none")
        if after.get("override") and risk == "high":
            risk = "high_overridden"
        return risk, (after.get("rule_version") or None)
    return None, None


def classify_case(
    *,
    status: Optional[str],
    feedback_score: Optional[float],
    verified: bool,
    dead_letters: int,
    content_risk: Optional[str],
    age: Optional[int],
    expire_days: int,
    unconditional_days: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """单案例红黄绿分级：红 > 黄 > 绿，返回（健康度, 命中原因列表）。"""
    reasons: List[str] = []
    # P2-2：无条件老化天数支持独立配置（kb_expire_unconditional_days）；
    # 未配置或非法值（None/0/负数）回退 2×kb_expire_days 兼容旧口径。
    effective_unconditional = (
        unconditional_days if unconditional_days is not None and unconditional_days > 0 else expire_days * 2
    )

    if dead_letters > 0:
        reasons.append(f"同步任务死信 {dead_letters} 条（索引可能缺失该知识）")
    if content_risk == "high":
        reasons.append("内容安全扫描命中高风险（已拦截）")
    elif content_risk == "high_overridden":
        reasons.append("内容安全高风险经人工误报放行（需复核）")
    if feedback_score is not None and feedback_score <= -2:
        reasons.append(f"反馈分过低（{feedback_score:g}）")
    if age is not None and age > effective_unconditional:
        reasons.append(f"超过无条件老化阈值（{age} 天未更新 > {effective_unconditional} 天）")

    if reasons:
        return "red", reasons

    if status == "archived":
        reasons.append("案例已归档（生命周期结束）")
    if not verified:
        reasons.append("发布后同步未完成 verify 确认")
    if content_risk == "medium":
        reasons.append("内容扫描发现 PII（标记放行，人工关注）")
    if feedback_score is not None and feedback_score < 0:
        reasons.append(f"存在负反馈（{feedback_score:g}）")
    if age is not None and age > expire_days:
        reasons.append(f"超过老化阈值（{age} 天未更新 > {expire_days} 天，待老化巡检归档）")

    if reasons:
        return "yellow", reasons
    return "green", []


async def build_health_report(db: AsyncSession) -> Dict[str, Any]:
    """构建知识库健康报表：红黄绿分级 + 同步闭环 + 内容安全 + 老化聚合。"""
    now = datetime.now(timezone.utc)
    expire_days = int(await get_config(db, "kb_expire_days", "90") or 90)
    # P2-2：无条件老化阈值独立配置（kb_expire_unconditional_days）；0/留空/非法值回退 2×kb_expire_days 旧口径
    try:
        unconditional_days = int(await get_config(db, "kb_expire_unconditional_days", "180"))
    except (TypeError, ValueError):
        unconditional_days = 0
    effective_unconditional = unconditional_days if unconditional_days > 0 else expire_days * 2

    cases = list((await db.execute(select(Kb_cases).order_by(Kb_cases.id.desc()))).scalars().all())

    # 同步闭环：按 case_id 汇总 verify 成功 / 死信 / 待重试
    sync_rows = (
        (await db.execute(select(Rag_sync_tasks).where(Rag_sync_tasks.case_id.isnot(None))))
        .scalars()
        .all()
    )
    verified_map: Dict[str, int] = {}
    dead_map: Dict[str, int] = {}
    pending_map: Dict[str, int] = {}
    sync_dead_total = 0
    sync_pending_total = 0
    for task in sync_rows:
        case_id = str(task.case_id or "")
        if not case_id:
            continue
        if task.status == "dead":
            dead_map[case_id] = dead_map.get(case_id, 0) + 1
            sync_dead_total += 1
        elif task.status == "pending":
            pending_map[case_id] = pending_map.get(case_id, 0) + 1
            sync_pending_total += 1
        elif task.status == "done" and task.verified:
            verified_map[case_id] = verified_map.get(case_id, 0) + 1

    # 内容安全：每案例最近一次扫描结果（action=content_guard_scan，按 target_id=case_id）
    scan_rows = list(
        (
            await db.execute(
                select(Audit_logs)
                .where(Audit_logs.action == "content_guard_scan")
                .order_by(Audit_logs.id.desc())
                .limit(500)
            )
        ).scalars().all()
    )
    scan_by_case: Dict[str, List[Audit_logs]] = {}
    for log in scan_rows:
        scan_by_case.setdefault(str(log.target_id), []).append(log)

    # 内容安全统计（与 Dashboard 同口径：审计聚合）
    safety_stats: Dict[str, Any] = {
        "scans": 0,
        "blocked": 0,
        "flagged": 0,
        "passed": 0,
        "overrides": 0,
        "last_rule_version": None,
    }
    for log in scan_rows:
        try:
            after = json.loads(log.after_json) if log.after_json else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(after, dict):
            continue
        safety_stats["scans"] += 1
        risk_level = str(after.get("risk_level") or "none")
        if after.get("override"):
            safety_stats["passed"] += 1
        elif after.get("blocked") or risk_level == "high":
            safety_stats["blocked"] += 1
        elif risk_level == "medium":
            safety_stats["flagged"] += 1
        else:
            safety_stats["passed"] += 1
        if after.get("rule_version"):
            safety_stats["last_rule_version"] = after["rule_version"]
    # 误报放行次数：override 审计单独统计
    override_rows = (
        (await db.execute(select(Audit_logs).where(Audit_logs.action == "content_guard_override").limit(500)))
        .scalars()
        .all()
    )
    safety_stats["overrides"] = len(list(override_rows))

    # 逐案例分级
    items: List[Dict[str, Any]] = []
    green = yellow = red = 0
    stale_candidates = 0
    ages: List[int] = []
    oldest: Optional[Dict[str, Any]] = None
    for case in cases:
        age = _age_days(case.updated_at, now)
        if age is not None:
            ages.append(age)
        verified = verified_map.get(case.case_id, 0) > 0
        dead_letters = dead_map.get(case.case_id, 0)
        pending = pending_map.get(case.case_id, 0)
        content_risk, rule_version = _latest_scan_risk(scan_by_case.get(case.case_id, []))
        health, reasons = classify_case(
            status=case.status,
            feedback_score=case.feedback_score,
            verified=verified,
            dead_letters=dead_letters,
            content_risk=content_risk,
            age=age,
            expire_days=expire_days,
            unconditional_days=unconditional_days,
        )
        if health == "red":
            red += 1
        elif health == "yellow":
            yellow += 1
        else:
            green += 1
        if case.status == "active" and age is not None and age > expire_days:
            stale_candidates += 1
        if oldest is None or (age is not None and (oldest.get("age_days") is None or age > oldest["age_days"])):
            oldest = {"case_id": case.case_id, "age_days": age}

        items.append(
            {
                "case_id": case.case_id,
                "error_type": case.error_type,
                "service_name": case.service_name,
                "status": case.status or "active",
                "version": case.version,
                "feedback_score": case.feedback_score,
                "age_days": age,
                "updated_at": str(case.updated_at) if case.updated_at else None,
                "health": health,
                "reasons": reasons,
                "sync_verified": verified,
                "sync_dead": dead_letters,
                "sync_pending": pending,
                "content_risk": content_risk,
                "rule_version": rule_version,
            }
        )

    active_total = sum(1 for c in cases if (c.status or "active") == "active")
    risk_items = [i for i in items if i["health"] != "green"]

    return {
        "generated_at": now.isoformat(),
        "expire_days": expire_days,
        "unconditional_expire_days": effective_unconditional,
        "summary": {
            "total": len(cases),
            "active": active_total,
            "archived": len(cases) - active_total,
            "green": green,
            "yellow": yellow,
            "red": red,
            # 健康率 = 绿 / 全部（归档视为生命周期终态，计入分母避免掩盖健康面）
            "health_rate": round(green / len(cases), 4) if cases else None,
        },
        "sync": {
            "verified_cases": sum(1 for i in items if i["sync_verified"]),
            "unverified_cases": sum(1 for i in items if not i["sync_verified"]),
            "pending_tasks": sync_pending_total,
            "dead_tasks": sync_dead_total,
        },
        "content_safety": safety_stats,
        "aging": {
            "expire_days": expire_days,
            "stale_candidates": stale_candidates,
            "avg_age_days": round(sum(ages) / len(ages), 1) if ages else None,
            "oldest": oldest,
        },
        # 风险案例清单：红优先、黄次之；红黄案例全量返回（供运维治理），绿不返回明细
        "risk_cases": risk_items,
    }
