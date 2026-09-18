"""RootSleuth 运营控制台业务 API：RBAC、Dashboard、告警流、AI 诊断、知识库、审批、规则、审计、配置。"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.agent_sessions import Agent_sessions
from models.audit_logs import Audit_logs
from models.console_configs import Console_configs
from models.Events import Events
from models.kb_cases import Kb_cases
from schemas.auth import UserResponse
from services import console_kb
from services.console_ai import run_diagnosis
from services.notify_service import send_notification
from services.kb_health import build_health_report
from services.quality_scan import aggregate_quality_stats
from services.llm_runtime import (
    SECRET_CONFIG_KEYS,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
    test_llm_connectivity,
)
from services.console_common import (
    CONFIG_DEFAULTS,
    CONFIG_DESCRIPTIONS,
    ROLE_LEVELS,
    get_config,
    now_iso,
    permissions_for,
    require_role,
    resolve_role,
    set_config,
    validate_config_value,
    write_audit,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/console", tags=["console"])


# ------------------ 请求模型 ------------------

class ChangeSetBody(BaseModel):
    case_id: str
    change_type: str = "update"
    fields: Dict[str, Any] = {}
    reason: str = ""
    allow_override: bool = False
    override_reason: str = ""


class RollbackBody(BaseModel):
    version: int


class FeedbackBody(BaseModel):
    rating: str
    correction: Dict[str, str] = {}
    comment: str = ""


class DecideBody(BaseModel):
    action: str
    comment: str = ""


class MergeBody(BaseModel):
    master_case_id: str
    merged_case_ids: List[str]
    reason: str = ""
    strategy: Dict[str, Any] = {}


class RuleContentBody(BaseModel):
    content: str
    change_note: str = ""


class PromoteBody(BaseModel):
    error_type: str = ""


class ConfigUpdateBody(BaseModel):
    key: str
    value: str


class PatrolBody(BaseModel):
    dry_run: bool = True


# ------------------ 权限与 Dashboard ------------------

@router.get("/permissions")
async def get_permissions(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = await resolve_role(db, current_user)
    approval_mode = await get_config(db, "approval_mode", "SINGLE_REVIEW")
    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
        },
        **permissions_for(role),
        "approval_mode": approval_mode,
    }


@router.get("/dashboard")
async def get_dashboard(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    events_result = await db.execute(select(Events).order_by(Events.id.desc()).limit(2000))
    events = list(events_result.scalars().all())
    total = len(events)

    by_severity: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    fingerprints = set()
    unknown_count = 0
    rag_success = 0
    rag_ms_values: List[float] = []
    llm_ok = 0
    llm_fail = 0
    error_type_counter: Dict[str, int] = {}
    service_counter: Dict[str, int] = {}

    for event in events:
        by_severity[event.severity] = by_severity.get(event.severity, 0) + 1
        if event.fingerprint:
            fingerprints.add(event.fingerprint)
        if event.status == "unknown" or event.rag_status == "unknown":
            unknown_count += 1
        if event.rag_status == "success":
            rag_success += 1
            if event.rag_ms:
                rag_ms_values.append(event.rag_ms)
        if event.ai_output_json:
            llm_ok += 1
        if (event.degraded_reason or "").startswith("llm_") or (event.degraded_reason or "").startswith("invalid_json"):
            llm_fail += 1
        if event.error_type:
            error_type_counter[event.error_type] = error_type_counter.get(event.error_type, 0) + 1
        if event.service_name:
            service_counter[event.service_name] = service_counter.get(event.service_name, 0) + 1

    rag_ms_values.sort()
    p99 = rag_ms_values[min(len(rag_ms_values) - 1, int(0.99 * len(rag_ms_values)))] if rag_ms_values else None
    avg_rag_ms = round(sum(rag_ms_values) / len(rag_ms_values), 1) if rag_ms_values else None

    noise_reduction = round(1 - len(fingerprints) / total, 4) if total else 0.0
    unknown_rate = round(unknown_count / total, 4) if total else 0.0
    rag_success_rate = round(rag_success / total, 4) if total else 0.0

    kb_total_result = await db.execute(select(func.count()).select_from(Kb_cases))
    kb_total = kb_total_result.scalar() or 0
    kb_archived_result = await db.execute(select(func.count()).select_from(Kb_cases).where(Kb_cases.status == "archived"))
    kb_archived = kb_archived_result.scalar() or 0
    avg_feedback_result = await db.execute(select(func.avg(Kb_cases.feedback_score)))
    avg_feedback = round(float(avg_feedback_result.scalar() or 0), 2)

    pending_approval_result = await db.execute(
        select(func.count()).select_from(console_kb.Approval_requests).where(
            console_kb.Approval_requests.status == "pending"
        )
    )
    pending_approvals = pending_approval_result.scalar() or 0
    pending_unknown_result = await db.execute(
        select(func.count()).select_from(console_kb.Unknown_templates).where(
            console_kb.Unknown_templates.status == "pending"
        )
    )
    pending_unknowns = pending_unknown_result.scalar() or 0

    # 质量态势（总览）：单次诊断 Trust Index/质量指标聚合（事件 + Agent 会话双来源）
    sessions_result = await db.execute(
        select(Agent_sessions)
        .where(Agent_sessions.session_type == "diagnose")
        .order_by(Agent_sessions.id.desc())
        .limit(200)
    )
    agent_rows = list(sessions_result.scalars().all())
    quality_stats = aggregate_quality_stats(
        [e.ai_output_json for e in events if e.ai_output_json],
        [s.result_json for s in agent_rows],
    )
    # 知识健康（总览聚合）：红黄绿分级摘要（复用健康报表口径，Ops 页保留明细）
    kb_health_summary = (await build_health_report(db))["summary"]
    # 内容安全统计（总览）：content_guard_scan / content_guard_override 审计聚合
    scan_stats: Dict[str, Any] = {
        "scans": 0,
        "blocked": 0,
        "flagged": 0,
        "passed": 0,
        "overrides": 0,
        "last_rule_version": None,
    }
    audits_result = await db.execute(
        select(Audit_logs)
        .where(Audit_logs.action.in_(["content_guard_scan", "content_guard_override"]))
        .order_by(Audit_logs.id.desc())
        .limit(300)
    )
    for log in audits_result.scalars().all():
        try:
            after = json.loads(log.after_json) if log.after_json else {}
        except (TypeError, ValueError):
            after = {}
        if not isinstance(after, dict):
            continue
        if log.action == "content_guard_override":
            scan_stats["overrides"] += 1
            continue
        scan_stats["scans"] += 1
        risk_level = str(after.get("risk_level") or "none")
        if after.get("override"):
            # 误报放行（人工复核通过）计入 passed，不重复计 blocked
            scan_stats["passed"] += 1
        elif after.get("blocked") or risk_level == "high":
            scan_stats["blocked"] += 1
        elif risk_level == "medium":
            scan_stats["flagged"] += 1
        else:
            scan_stats["passed"] += 1
        if after.get("rule_version"):
            scan_stats["last_rule_version"] = after["rule_version"]

    def top(counter: Dict[str, int], n: int = 5) -> List[Dict[str, Any]]:
        return [
            {"name": k, "count": v}
            for k, v in sorted(counter.items(), key=lambda x: x[1], reverse=True)[:n]
        ]

    recent = [
        {
            "id": e.id,
            "event_id": e.event_id,
            "severity": e.severity,
            "service_name": e.service_name,
            "error_type": e.error_type,
            "status": e.status,
            "created_at": str(e.created_at) if e.created_at else None,
        }
        for e in events[:6]
    ]

    llm_calls = llm_ok + llm_fail
    return {
        "metrics": {
            "total_events": total,
            "noise_reduction": noise_reduction,
            "unknown_rate": unknown_rate,
            "rag_success_rate": rag_success_rate,
            "rag_p99_ms": p99,
            "avg_rag_ms": avg_rag_ms,
        },
        "by_severity": by_severity,
        "top_error_types": top(error_type_counter),
        "top_services": top(service_counter),
        "recent_events": recent,
        "health": {
            "milvus": {"status": "healthy", "detail": f"avg rag {avg_rag_ms or 0}ms"},
            "redis": {"status": "healthy", "detail": f"降噪率 {noise_reduction * 100:.1f}%"},
            "elasticsearch": {"status": "healthy", "detail": f"归档案例 {kb_archived}"},
            "llm": {
                "status": "degraded" if llm_calls and llm_fail > llm_ok else "healthy",
                "detail": f"诊断成功 {llm_ok}/{llm_calls}",
            },
        },
        "kb": {"total": kb_total, "archived": kb_archived, "avg_feedback": avg_feedback},
        "kb_health": kb_health_summary,
        "todo": {"pending_approvals": pending_approvals, "pending_unknowns": pending_unknowns},
        "quality": {
            **quality_stats,
            "generation": {
                "success": llm_ok,
                "fail": llm_fail,
                "success_rate": round(llm_ok / llm_calls, 4) if llm_calls else None,
            },
            "retrieval": {
                "success_rate": rag_success_rate,
                "p99_ms": p99,
                "avg_ms": avg_rag_ms,
            },
            "content_safety": scan_stats,
        },
    }


# ------------------ 知识库健康报表 ------------------

@router.get("/kb/health")
async def get_kb_health(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识库红黄绿健康报表（P2-1）：分级统计 + 同步闭环 + 内容安全 + 老化聚合。"""
    return await build_health_report(db)


# ------------------ 事件与诊断 ------------------

@router.get("/events")
async def list_events(
    severity: str = Query(None),
    service: str = Query(None),
    cluster: str = Query(None),
    error_type: str = Query(None),
    status: str = Query(None),
    time_range: str = Query("7d"),
    q: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if severity:
        conditions.append(Events.severity == severity)
    if service:
        conditions.append(Events.service_name == service)
    if cluster:
        conditions.append(Events.cluster == cluster)
    if error_type:
        conditions.append(Events.error_type == error_type)
    if status:
        conditions.append(Events.status == status)
    if time_range and time_range != "all":
        deltas = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
        delta = deltas.get(time_range)
        if delta:
            conditions.append(Events.created_at >= datetime.now(timezone.utc) - delta)
    if q:
        like = f"%{q}%"
        conditions.append((Events.event_id.ilike(like)) | (Events.template.ilike(like)) | (Events.raw_log.ilike(like)))

    stmt = select(Events)
    count_stmt = select(func.count()).select_from(Events)
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.order_by(Events.id.desc()).offset(skip).limit(limit))
    items = [
        {
            "id": e.id,
            "event_id": e.event_id,
            "severity": e.severity,
            "service_name": e.service_name,
            "cluster": e.cluster,
            "error_type": e.error_type,
            "template": e.template,
            "status": e.status,
            "rag_status": e.rag_status,
            "rag_score": e.rag_score,
            "confidence": e.confidence,
            "degraded_reason": e.degraded_reason,
            "created_at": str(e.created_at) if e.created_at else None,
        }
        for e in result.scalars().all()
    ]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/events/{event_id}")
async def get_event_detail(
    event_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Events).where(Events.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")

    def _loads(raw: Optional[str]) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    return {
        "id": event.id,
        "event_id": event.event_id,
        "severity": event.severity,
        "service_name": event.service_name,
        "cluster": event.cluster,
        "error_type": event.error_type,
        "template": event.template,
        "raw_log": event.raw_log,
        "fingerprint": event.fingerprint,
        "topology": event.topology,
        "status": event.status,
        "rag_status": event.rag_status,
        "rag_score": event.rag_score,
        "rag_ms": event.rag_ms,
        "std_ms": event.std_ms,
        "confidence": event.confidence,
        "degraded_reason": event.degraded_reason,
        "candidates": _loads(event.candidates_json),
        "ai_root_cause": event.ai_root_cause,
        "ai_solution": event.ai_solution,
        "ai_command": event.ai_command,
        "ai_output": _loads(event.ai_output_json),
        "created_at": str(event.created_at) if event.created_at else None,
        "updated_at": str(event.updated_at) if event.updated_at else None,
    }


@router.post("/events/{event_id}/diagnose")
async def diagnose_event(
    event_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "operator")
    return await run_diagnosis(db, event_id, current_user.email or current_user.id)


@router.post("/events/{event_id}/feedback")
async def feedback_event(
    event_id: int,
    body: FeedbackBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.apply_feedback(
        db, current_user, event_id, body.model_dump()
    )


# ------------------ 知识库 ------------------

@router.get("/kb/cases")
async def list_kb_cases(
    q: str = Query(None),
    error_type: str = Query(None),
    service: str = Query(None),
    status: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if error_type:
        conditions.append(Kb_cases.error_type == error_type)
    if service:
        conditions.append(Kb_cases.service_name == service)
    if status:
        conditions.append(Kb_cases.status == status)
    if q:
        like = f"%{q}%"
        conditions.append(
            (Kb_cases.case_id.ilike(like))
            | (Kb_cases.alert_template.ilike(like))
            | (Kb_cases.root_cause.ilike(like))
        )
    stmt = select(Kb_cases)
    count_stmt = select(func.count()).select_from(Kb_cases)
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.order_by(Kb_cases.id.desc()).offset(skip).limit(limit))
    rows = list(result.scalars().all())
    counts = await console_kb.get_case_related_event_counts(db, rows)
    items = []
    for c in rows:
        item = console_kb.ser_case(c)
        item["related_event_count"] = counts.get(c.case_id, 0)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/kb/cases/{case_id}")
async def get_kb_case(
    case_id: str,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Kb_cases).where(Kb_cases.case_id == case_id).limit(1))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="知识案例不存在")
    versions = await console_kb.list_case_versions(db, case_id)
    cs_result = await db.execute(
        select(console_kb.Kb_change_sets)
        .where(console_kb.Kb_change_sets.case_id == case_id)
        .order_by(console_kb.Kb_change_sets.id.desc())
        .limit(20)
    )
    return {
        "case": console_kb.ser_case(case),
        "versions": versions,
        "change_sets": [console_kb.ser_change_set(cs) for cs in cs_result.scalars().all()],
        "related_events": await console_kb.get_case_related_events(db, case_id),
    }


@router.post("/kb/change-sets")
async def create_change_set(
    body: ChangeSetBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.create_change_set(db, current_user, body.model_dump())


@router.post("/kb/cases/{case_id}/rollback")
async def rollback_kb_case(
    case_id: str,
    body: RollbackBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.rollback_case(db, current_user, case_id, body.version)


@router.post("/kb/cases/{case_id}/archive")
async def archive_kb_case(
    case_id: str,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """归档知识案例：业务库归档 + 审计 + RAG 检索侧索引删除（生命周期闭环）。"""
    return await console_kb.archive_case(db, current_user, case_id)


@router.post("/kb/lifecycle-patrol")
async def lifecycle_patrol(
    body: PatrolBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识生命周期巡检：负反馈/老化活跃案例归档淘汰（dry_run=true 仅返回候选预览）。"""
    return await console_kb.run_lifecycle_patrol(db, current_user, body.dry_run)


@router.get("/kb/cases/{case_id}/versions")
async def list_versions(
    case_id: str,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await console_kb.list_case_versions(db, case_id)}


@router.get("/kb/duplicates")
async def scan_duplicates(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "viewer")
    return {"groups": await console_kb.scan_duplicates(db)}


@router.get("/kb/related-events")
async def preview_kb_related_events(
    template: str = Query(None),
    service: str = Query(None),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """新建/编辑案例时的关联实例日志预览（证据参考，不入库）。"""
    await require_role(db, current_user, "viewer")
    return {"items": await console_kb.preview_related_events(db, template, service)}


@router.get("/kb/merge-proposals")
async def list_merge_proposals(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await console_kb.list_merge_proposals(db)}


@router.post("/kb/merge-proposals")
async def create_merge_proposal(
    body: MergeBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.create_merge_proposal(db, current_user, body.model_dump())


@router.get("/change-sets")
async def list_change_sets(
    status: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(console_kb.Kb_change_sets)
    count_stmt = select(func.count()).select_from(console_kb.Kb_change_sets)
    if status:
        stmt = stmt.where(console_kb.Kb_change_sets.status == status)
        count_stmt = count_stmt.where(console_kb.Kb_change_sets.status == status)
    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.order_by(console_kb.Kb_change_sets.id.desc()).offset(skip).limit(limit))
    return {
        "items": [console_kb.ser_change_set(cs) for cs in result.scalars().all()],
        "total": total,
    }


# ------------------ 审批中心 ------------------

@router.get("/approvals")
async def list_approvals(
    box: str = Query("pending"),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = await resolve_role(db, current_user)
    email = current_user.email or current_user.id
    if box == "pending":
        affordable_roles = [k for k, v in ROLE_LEVELS.items() if v <= ROLE_LEVELS.get(role, -1)]
        stmt = (
            select(console_kb.Approval_requests, console_kb.Approval_steps)
            .join(console_kb.Approval_steps, console_kb.Approval_steps.request_id == console_kb.Approval_requests.id)
            .where(
                console_kb.Approval_requests.status == "pending",
                console_kb.Approval_steps.step_no == console_kb.Approval_requests.current_step,
                console_kb.Approval_steps.action == "pending",
                console_kb.Approval_requests.applicant != email,
                console_kb.Approval_steps.approver_role.in_(affordable_roles),
            )
            .order_by(console_kb.Approval_requests.id.desc())
            .limit(100)
        )
        rows = (await db.execute(stmt)).all()
        requests = [r[0] for r in rows]
    elif box == "created":
        result = await db.execute(
            select(console_kb.Approval_requests)
            .where(console_kb.Approval_requests.applicant == email)
            .order_by(console_kb.Approval_requests.id.desc())
            .limit(100)
        )
        requests = list(result.scalars().all())
    elif box == "done":
        stmt = (
            select(console_kb.Approval_requests)
            .join(console_kb.Approval_steps, console_kb.Approval_steps.request_id == console_kb.Approval_requests.id)
            .where(
                console_kb.Approval_steps.approver == email,
                console_kb.Approval_steps.action != "pending",
            )
            .order_by(console_kb.Approval_requests.id.desc())
            .limit(100)
        )
        rows = (await db.execute(stmt)).all()
        requests = list({r[0].id: r[0] for r in rows}.values())
    else:
        raise HTTPException(status_code=400, detail="box 仅支持 pending / created / done")

    steps_map = await console_kb._load_steps(db, [r.id for r in requests])
    return {
        "items": [console_kb.ser_request(r, steps_map.get(r.id, [])) for r in requests],
        "role": role,
    }


@router.post("/approvals/{request_id}/decide")
async def decide_approval(
    request_id: int,
    body: DecideBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.decide_approval(db, current_user, request_id, body.action, body.comment)


@router.get("/approvals/{request_id}/content")
async def get_approval_content(
    request_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.get_approval_content(db, request_id)


# ------------------ 规则管理与未知模板 ------------------

@router.get("/rules")
async def get_rules(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.list_rules(db)


@router.post("/rules/validate")
async def validate_rules(
    body: RuleContentBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.validate_rules(db, current_user, body.content)


@router.post("/rules/publish")
async def publish_rules(
    body: RuleContentBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.publish_rules(db, current_user, body.content, body.change_note)


@router.post("/rules/{version_id}/rollback")
async def rollback_rules(
    version_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.rollback_rules(db, current_user, version_id)


@router.get("/unknown-templates")
async def list_unknown_templates(
    status: str = Query(None),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await console_kb.list_unknown_templates(db, status)}


@router.post("/unknown-templates/{template_id}/promote")
async def promote_template(
    template_id: int,
    body: PromoteBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.promote_unknown_template(db, current_user, template_id, body.error_type)


@router.post("/unknown-templates/{template_id}/discard")
async def discard_template(
    template_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await console_kb.discard_unknown_template(db, current_user, template_id)


# ------------------ 审计与配置 ------------------

@router.get("/audit-logs")
async def list_audit_logs(
    action: str = Query(None),
    actor: str = Query(None),
    target_type: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 审计日志仅系统管理员可查看（与配置中心一致）
    await require_role(db, current_user, "sys_admin")
    conditions = []
    if action:
        conditions.append(Audit_logs.action == action)
    if actor:
        conditions.append(Audit_logs.actor.ilike(f"%{actor}%"))
    if target_type:
        conditions.append(Audit_logs.target_type == target_type)
    stmt = select(Audit_logs)
    count_stmt = select(func.count()).select_from(Audit_logs)
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.order_by(Audit_logs.id.desc()).offset(skip).limit(limit))
    items = []
    for log in result.scalars().all():
        def _loads(raw: Optional[str]) -> Any:
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return raw
        items.append(
            {
                "id": log.id,
                "actor": log.actor,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "before": _loads(log.before_json),
                "after": _loads(log.after_json),
                "created_at": str(log.created_at) if log.created_at else None,
            }
        )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/configs")
async def list_configs(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "sys_admin")
    rows_result = await db.execute(select(Console_configs).order_by(Console_configs.config_key))
    stored = {row.config_key: row.config_value for row in rows_result.scalars().all()}
    secret_keys = set(SECRET_CONFIG_KEYS)
    items = []
    for key in sorted(set(CONFIG_DEFAULTS) | set(stored)):
        raw_value = stored.get(key, CONFIG_DEFAULTS.get(key, ""))
        if key in secret_keys:
            # API Key 永不明文回显：统一脱敏展示（如 sk-a****wxyz）
            raw_value = mask_secret(decrypt_secret(raw_value))
        items.append(
            {
                "key": key,
                "value": raw_value,
                "description": CONFIG_DESCRIPTIONS.get(key, ""),
                "is_default": key not in stored,
                "is_secret": key in secret_keys,
            }
        )
    return {"items": items}


@router.put("/configs")
async def update_config(
    body: ConfigUpdateBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "sys_admin")
    valid, message = validate_config_value(body.key, body.value)
    if not valid:
        raise HTTPException(status_code=400, detail=message)
    result = await db.execute(
        select(Console_configs).where(Console_configs.config_key == body.key).limit(1)
    )
    row = result.scalar_one_or_none()
    before_value = row.config_value if row else None
    value_to_store = body.value
    if body.key in SECRET_CONFIG_KEYS:
        # API Key 加密持久化（Fernet）；空串表示清除
        value_to_store = encrypt_secret(body.value) if body.value.strip() else ""
    saved = await set_config(db, body.key, value_to_store)
    if body.key in SECRET_CONFIG_KEYS:
        # 审计快照同样脱敏，避免明文密钥落入审计日志
        before_display = mask_secret(decrypt_secret(before_value or "")) or "(已清除)"
        after_display = mask_secret(decrypt_secret(saved.config_value or "")) or "(已清除)"
    else:
        before_display = before_value
        after_display = saved.config_value
    await write_audit(
        db,
        actor=current_user.email or current_user.id,
        action="config_update",
        target_type="console_config",
        target_id=body.key,
        before={"value": before_display},
        after={"value": after_display},
    )
    display_value = (
        mask_secret(decrypt_secret(saved.config_value or ""))
        if body.key in SECRET_CONFIG_KEYS
        else saved.config_value
    )
    return {"key": body.key, "value": display_value}


@router.post("/configs/llm-test")
async def test_llm_config(
    agent: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员连通性自检：按当前配置真实调用一次 Chat（及已启用的 Embedding）。

    agent 可选传三个业务 Agent 作用域（diagnose / kb_governance / oncall），
    按 <agent>_* 独立配置测试（留空项回退全局 llm_* 配置）。
    """
    await require_role(db, current_user, "sys_admin")
    result = await test_llm_connectivity(db, agent=agent)
    await write_audit(
        db,
        actor=current_user.email or current_user.id,
        action="llm_config_test",
        target_type="console_config",
        target_id=f"llm_runtime:{agent or 'global'}",
        after={
            "agent": agent or "global",
            "chat_ok": bool(result["chat"].get("ok")),
            "embedding_ok": bool(result["embedding"].get("ok", False)),
        },
    )
    return result


@router.post("/notify/test")
async def test_notify_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员连通性自检：向 notify_webhook_url 同步发送一条样例诊断通知并回显结果。

    未配置通知地址返回 400；推送结果（状态码/耗时/错误）同步返回供前端展示，
    且写入审计（脱敏：仅记录结果摘要，不含样例载荷与 Token）。
    """
    await require_role(db, current_user, "sys_admin")
    url = ((await get_config(db, "notify_webhook_url", "")) or "").strip()
    token = decrypt_secret((await get_config(db, "notify_webhook_token", "")) or "").strip()
    if not url:
        raise HTTPException(
            status_code=400,
            detail="notify_webhook_url 未配置，请先在配置中心「集成与通知」分组填写通知推送地址",
        )
    sample_payload = {
        "source": "rootsleuth",
        "event_type": "notify_test",
        "event_id": "evt_notify_test",
        "service_name": "demo-service",
        "cluster": None,
        "topology": None,
        "error_type": None,
        "severity": "info",
        "template": "RootSleuth 通知连通性测试",
        "status": "pending",
        "degraded_reason": None,
        "root_cause": "这是一条连通性测试消息：收到即代表通知链路配置正确",
        "solution": "无需处理；如需真实告警通知请保持 notify_webhook_url 配置",
        "command": None,
        "confidence": None,
        "trust_index": None,
        "model": None,
        "diagnosis_source": "test",
        "diagnosed_at": now_iso(),
    }
    summary = await send_notification(url, token, sample_payload)
    await write_audit(
        db,
        actor=current_user.email or current_user.id,
        action="notify_config_test",
        target_type="console_config",
        target_id="notify_webhook",
        after={
            "ok": bool(summary.get("ok")),
            "status_code": summary.get("status_code"),
            "error": summary.get("error"),
        },
    )
    return {
        "ok": bool(summary.get("ok")),
        "status_code": summary.get("status_code"),
        "latency_ms": summary.get("latency_ms"),
        "error": summary.get("error"),
        "url": summary.get("url"),
        "sample_payload": sample_payload,
    }
