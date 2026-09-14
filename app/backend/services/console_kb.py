"""知识库运营工作流服务。

覆盖：
- change_set 编辑（before/after/diff、字段校验）
- 审批状态机（OFF / SINGLE_REVIEW / MULTI_LEVEL，申请人不可自审）
- 发布（写 kb_version、失效语义缓存、审计）
- 指定版本回滚
- 相似案例扫描与合并提案
- 规则 YAML 校验 / 版本 / 热加载（审计模拟 reload）/ 回滚
- 未知模板晋升与废弃
- 反馈闭环（👍/👎 + 人工修正自动创建 change_set）
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import yaml
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.approval_requests import Approval_requests
from models.approval_steps import Approval_steps
from models.Events import Events
from models.kb_cases import Kb_cases
from models.kb_change_sets import Kb_change_sets
from models.kb_merge_proposals import Kb_merge_proposals
from models.kb_versions import Kb_versions
from models.rule_versions import Rule_versions
from models.unknown_templates import Unknown_templates
from schemas.auth import UserResponse
from services.console_common import (
    ROLE_LABELS,
    ROLE_LEVELS,
    build_diff,
    get_config,
    now_iso,
    require_role,
    role_at_least,
    snapshot_case,
    write_audit,
)

logger = logging.getLogger(__name__)

KB_CASE_FIELDS = [
    "error_type",
    "service_name",
    "cluster",
    "alert_template",
    "root_cause",
    "solution",
    "topology_snapshot",
]

VALID_SEVERITIES = {"critical", "warning", "info"}
PROMOTE_RULE_SCORE = 0.6
FEEDBACK_SCORE_MIN = -5.0
FEEDBACK_SCORE_MAX = 5.0
APPROVAL_SAMPLE_EVENTS = 5


# ------------------ 序列化 ------------------

def ser_case(case: Kb_cases) -> Dict[str, Any]:
    return {
        "id": case.id,
        "case_id": case.case_id,
        "error_type": case.error_type,
        "service_name": case.service_name,
        "cluster": case.cluster,
        "alert_template": case.alert_template,
        "root_cause": case.root_cause,
        "solution": case.solution,
        "topology_snapshot": case.topology_snapshot,
        "status": case.status,
        "version": case.version,
        "feedback_score": case.feedback_score,
        "created_at": str(case.created_at) if case.created_at else None,
        "updated_at": str(case.updated_at) if case.updated_at else None,
    }


def ser_change_set(cs: Kb_change_sets) -> Dict[str, Any]:
    def _loads(raw: Optional[str]) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    return {
        "id": cs.id,
        "case_id": cs.case_id,
        "change_type": cs.change_type,
        "before": _loads(cs.before_json),
        "after": _loads(cs.after_json),
        "diff": _loads(cs.diff_json),
        "reason": cs.reason,
        "status": cs.status,
        "version": cs.version,
        "approval_request_id": cs.approval_request_id,
        "created_by": cs.created_by,
        "created_at": str(cs.created_at) if cs.created_at else None,
    }


def ser_request(request: Approval_requests, steps: List[Approval_steps]) -> Dict[str, Any]:
    return {
        "id": request.id,
        "applicant": request.applicant,
        "applicant_role": request.applicant_role,
        "applicant_role_label": ROLE_LABELS.get(request.applicant_role or "", request.applicant_role),
        "biz_type": request.biz_type,
        "biz_id": request.biz_id,
        "title": request.title,
        "reason": request.reason,
        "risk_level": request.risk_level,
        "status": request.status,
        "current_step": request.current_step,
        "total_steps": request.total_steps,
        "published_at": request.published_at,
        "steps": [
            {
                "id": s.id,
                "step_no": s.step_no,
                "approver_role": s.approver_role,
                "approver_role_label": ROLE_LABELS.get(s.approver_role, s.approver_role),
                "approver": s.approver,
                "action": s.action,
                "comment": s.comment,
                "acted_at": s.acted_at,
            }
            for s in sorted(steps, key=lambda x: x.step_no)
        ],
        "created_at": str(request.created_at) if request.created_at else None,
    }


def ser_version(v: Kb_versions) -> Dict[str, Any]:
    return {
        "id": v.id,
        "case_id": v.case_id,
        "version": v.version,
        "snapshot": json.loads(v.snapshot_json) if v.snapshot_json else None,
        "approval_id": v.approval_id,
        "created_by": v.created_by,
        "created_at": str(v.created_at) if v.created_at else None,
    }


def ser_rule(v: Rule_versions) -> Dict[str, Any]:
    return {
        "id": v.id,
        "version": v.version,
        "content": v.content,
        "status": v.status,
        "change_note": v.change_note,
        "created_by": v.created_by,
        "created_at": str(v.created_at) if v.created_at else None,
    }


def ser_template(t: Unknown_templates) -> Dict[str, Any]:
    return {
        "id": t.id,
        "template": t.template,
        "suggested_error_type": t.suggested_error_type,
        "sample_count": t.sample_count,
        "last_seen_service": t.last_seen_service,
        "status": t.status,
        "created_at": str(t.created_at) if t.created_at else None,
    }


def ser_merge(p: Kb_merge_proposals) -> Dict[str, Any]:
    return {
        "id": p.id,
        "master_case_id": p.master_case_id,
        "merged_case_ids": json.loads(p.merged_case_ids) if p.merged_case_ids else [],
        "merge_strategy": json.loads(p.merge_strategy_json) if p.merge_strategy_json else None,
        "reason": p.reason,
        "status": p.status,
        "approval_request_id": p.approval_request_id,
        "created_by": p.created_by,
        "created_at": str(p.created_at) if p.created_at else None,
    }


# ------------------ 内部工具 ------------------

async def _get_case(db: AsyncSession, case_id: str) -> Kb_cases:
    result = await db.execute(select(Kb_cases).where(Kb_cases.case_id == case_id).limit(1))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail=f"知识案例 {case_id} 不存在")
    return case


def _validate_fields(fields: Dict[str, Any]) -> Dict[str, str]:
    """校验并过滤知识案例编辑字段。"""
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="fields 必须是对象")
    cleaned: Dict[str, str] = {}
    for key in KB_CASE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"字段 {key} 必须是字符串")
        value = value.strip()
        if not value:
            raise HTTPException(status_code=400, detail=f"字段 {key} 不能为空字符串")
        cleaned[key] = value
    if not cleaned:
        raise HTTPException(status_code=400, detail="至少提供一个待修改字段")
    if change_type_needs_identity(cleaned) and not cleaned.get("error_type"):
        raise HTTPException(status_code=400, detail="新建案例必须提供 error_type")
    return cleaned


def change_type_needs_identity(fields: Dict[str, Any]) -> bool:
    return False


async def _generate_case_id(db: AsyncSession) -> str:
    """新建案例自动生成 ID：KB-YYYYMMDD-当日三位序号。

    序号按「已入库案例 + 在途 create 变更集」去重后取最小空位，避免审批
    在途期间出现重复；当日满 999 个时退化为随机后缀兜底。
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"KB-{date_str}-"
    existing: set = set()
    result = await db.execute(select(Kb_cases.case_id).where(Kb_cases.case_id.like(f"{prefix}%")))
    existing.update(result.scalars().all())
    result_cs = await db.execute(
        select(Kb_change_sets.case_id).where(
            Kb_change_sets.case_id.like(f"{prefix}%"),
            Kb_change_sets.change_type == "create",
        )
    )
    existing.update(result_cs.scalars().all())
    for i in range(1, 1000):
        candidate = f"{prefix}{i:03d}"
        if candidate not in existing:
            return candidate
    return f"{prefix}{uuid4().hex[:6].upper()}"


def _template_tokens(template: str) -> set:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{2,}", template or ""))


def _build_rule_entry(template: Unknown_templates) -> Dict[str, Any]:
    """由未知模板构造晋升规则条目（晋升执行与审批内容预览共用，保证预览即所得）。"""
    tokens = [t for t in sorted(_template_tokens(template.template)) if len(t) > 2][:6]
    return {
        "id": f"unknown_{template.id}",
        "error_type": template.suggested_error_type or "unknown",
        "keywords": tokens or [f"template_{template.id}"],
        "score": PROMOTE_RULE_SCORE,
        "severity": "warning",
    }


def _ser_event_samples(events: List[Events]) -> List[Dict[str, Any]]:
    """事件 → 审批内容日志实例样本（证据用途，不替代知识案例正文）。"""
    return [
        {
            "event_id": ev.event_id,
            "service_name": ev.service_name,
            "severity": ev.severity,
            "status": ev.status,
            "error_type": ev.error_type,
            "template": ev.template,
            "raw_log": ev.raw_log,
            "created_at": str(ev.created_at) if ev.created_at else None,
        }
        for ev in events
    ]


async def _fetch_related_events(
    db: AsyncSession, template: Optional[str], service: Optional[str]
) -> List[Events]:
    """关联日志实例：优先按标准化模板精确匹配，其次按服务名兜底，取最近 N 条。"""
    result_ev = await db.execute(
        select(Events)
        .where(Events.template == (template or ""))
        .order_by(Events.created_at.desc())
        .limit(APPROVAL_SAMPLE_EVENTS)
    )
    related = list(result_ev.scalars().all())
    if not related and service:
        result_ev = await db.execute(
            select(Events)
            .where(Events.service_name == service)
            .order_by(Events.created_at.desc())
            .limit(APPROVAL_SAMPLE_EVENTS)
        )
        related = list(result_ev.scalars().all())
    return related


async def get_case_related_events(db: AsyncSession, case_id: str) -> List[Dict[str, Any]]:
    """案例关联告警实例日志：告警模板精确匹配优先、服务名兜底（案例详情展示）。"""
    result = await db.execute(select(Kb_cases).where(Kb_cases.case_id == case_id).limit(1))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="知识案例不存在")
    return _ser_event_samples(await _fetch_related_events(db, case.alert_template, case.service_name))


async def preview_related_events(
    db: AsyncSession, template: Optional[str], service: Optional[str]
) -> List[Dict[str, Any]]:
    """新建/编辑案例时的实例日志预览（按告警模板与服务名即时查询，不入库）。"""
    return _ser_event_samples(await _fetch_related_events(db, template, service))


async def get_case_related_event_counts(db: AsyncSession, cases: List[Kb_cases]) -> Dict[str, int]:
    """批量统计案例关联告警实例数（模板精确匹配优先、服务名兜底），用于案例库卡片展示。"""
    counts: Dict[str, int] = {}
    if not cases:
        return counts
    templates = {c.alert_template for c in cases if c.alert_template}
    services = {c.service_name for c in cases if c.service_name}
    tpl_counts: Dict[str, int] = {}
    svc_counts: Dict[str, int] = {}
    if templates:
        result = await db.execute(
            select(Events.template, func.count())
            .where(Events.template.in_(templates))
            .group_by(Events.template)
        )
        tpl_counts = {tpl: int(n) for tpl, n in result.all() if tpl}
    if services:
        result = await db.execute(
            select(Events.service_name, func.count())
            .where(Events.service_name.in_(services))
            .group_by(Events.service_name)
        )
        svc_counts = {svc: int(n) for svc, n in result.all() if svc}
    for c in cases:
        counts[c.case_id] = tpl_counts.get(c.alert_template or "", 0) or svc_counts.get(c.service_name or "", 0)
    return counts


def _template_similarity(a: str, b: str) -> float:
    ta, tb = _template_tokens(a), _template_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _create_approval_request(
    db: AsyncSession,
    user: UserResponse,
    role: str,
    biz_type: str,
    biz_id: str,
    title: str,
    reason: str,
    risk_level: str,
) -> Approval_requests:
    """按审批模式创建审批单与步骤。"""
    mode = await get_config(db, "approval_mode", "SINGLE_REVIEW")
    if mode == "SINGLE_REVIEW":
        step_roles = ["approver"]
    else:
        step_roles = ["approver", "kb_admin"]
    request = Approval_requests(
        applicant=user.email or user.id,
        applicant_role=role,
        biz_type=biz_type,
        biz_id=biz_id,
        title=title,
        reason=reason or None,
        risk_level=risk_level,
        status="pending",
        current_step=1,
        total_steps=len(step_roles),
    )
    db.add(request)
    await db.flush()
    for idx, step_role in enumerate(step_roles, start=1):
        db.add(
            Approval_steps(
                request_id=request.id,
                step_no=idx,
                approver_role=step_role,
                action="pending",
            )
        )
    return request


async def _load_steps(db: AsyncSession, request_ids: List[int]) -> Dict[int, List[Approval_steps]]:
    if not request_ids:
        return {}
    result = await db.execute(
        select(Approval_steps).where(Approval_steps.request_id.in_(request_ids))
    )
    steps: Dict[int, List[Approval_steps]] = {}
    for step in result.scalars().all():
        steps.setdefault(step.request_id, []).append(step)
    return steps


async def _publish_change_set(
    db: AsyncSession,
    actor: str,
    change_set: Kb_change_sets,
    approval_request_id: Optional[int],
) -> Dict[str, Any]:
    """将 change_set 应用到 kb_cases：更新或创建案例、写版本快照、失效语义缓存、审计。"""
    after = json.loads(change_set.after_json or "{}")
    result = await db.execute(select(Kb_cases).where(Kb_cases.case_id == change_set.case_id).limit(1))
    case = result.scalar_one_or_none()
    if case is None:
        case = Kb_cases(
            case_id=change_set.case_id,
            error_type=after.get("error_type", "unknown"),
            service_name=after.get("service_name", "unknown"),
            status="active",
            version=1,
            feedback_score=0,
        )
        db.add(case)
    else:
        case.version = (case.version or 1) + 1
    for key in KB_CASE_FIELDS:
        if key in after and after[key]:
            setattr(case, key, after[key])
    case.status = "active"
    await db.flush()

    db.add(
        Kb_versions(
            case_id=case.case_id,
            version=case.version or 1,
            snapshot_json=json.dumps(snapshot_case(case), ensure_ascii=False),
            approval_id=approval_request_id,
            created_by=actor,
        )
    )
    change_set.status = "published"
    if approval_request_id:
        result_req = await db.execute(
            select(Approval_requests).where(Approval_requests.id == approval_request_id)
        )
        request = result_req.scalar_one_or_none()
        if request is not None:
            request.published_at = now_iso()
    await db.commit()

    # 语义缓存失效：以审计事件形式固化失效记录（流水线侧下次检索将重建缓存）
    await write_audit(
        db,
        actor=actor,
        action="semantic_cache_invalidate",
        target_type="kb_cache",
        target_id=f"kb:{case.case_id}",
        after={"reason": "kb_publish", "version": case.version},
    )
    await write_audit(
        db,
        actor=actor,
        action="kb_publish",
        target_type="kb_case",
        target_id=case.case_id,
        after={"version": case.version, "change_set_id": change_set.id, "approval_id": approval_request_id},
    )
    return {"case_id": case.case_id, "version": case.version}


async def _apply_merge(db: AsyncSession, proposal: Kb_merge_proposals, actor: str) -> Dict[str, Any]:
    """执行合并：冗余案例归档，主案例补充合并来源说明。"""
    merged_ids = json.loads(proposal.merged_case_ids or "[]")
    master = await _get_case(db, proposal.master_case_id)
    archived: List[str] = []
    for merged_id in merged_ids:
        if merged_id == master.case_id:
            continue
        redundant = await _get_case(db, merged_id)
        if redundant.status != "archived":
            redundant.status = "archived"
            archived.append(redundant.case_id)
    if not master.root_cause:
        first = await _get_case(db, merged_ids[0]) if merged_ids else None
        if first is not None and first.root_cause:
            master.root_cause = first.root_cause
    proposal.status = "merged"
    await db.commit()
    await write_audit(
        db,
        actor=actor,
        action="kb_merge",
        target_type="kb_case",
        target_id=master.case_id,
        after={"archived": archived, "proposal_id": proposal.id},
    )
    return {"master": master.case_id, "archived": archived}


def _extract_rule_docs(content: str) -> List[Dict[str, Any]]:
    """解析并校验规则 YAML，返回 rules 列表；失败抛 400。"""
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败：{exc}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("rules"), list) or not parsed["rules"]:
        raise HTTPException(status_code=400, detail="YAML 必须包含非空 rules 列表")
    normalized: List[Dict[str, Any]] = []
    ids = set()
    for idx, rule in enumerate(parsed["rules"], start=1):
        if not isinstance(rule, dict):
            raise HTTPException(status_code=400, detail=f"第 {idx} 条规则必须是对象")
        rule_id = rule.get("id")
        error_type = rule.get("error_type")
        score = rule.get("score")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise HTTPException(status_code=400, detail=f"第 {idx} 条规则缺少有效 id")
        if rule_id in ids:
            raise HTTPException(status_code=400, detail=f"规则 id 重复：{rule_id}")
        ids.add(rule_id)
        if not isinstance(error_type, str) or not error_type.strip():
            raise HTTPException(status_code=400, detail=f"规则 {rule_id} 缺少 error_type")
        if not isinstance(score, (int, float)) or not (0 < float(score) <= 1):
            raise HTTPException(status_code=400, detail=f"规则 {rule_id} 的 score 必须在 (0, 1]")
        keywords = rule.get("keywords")
        pattern = rule.get("pattern")
        if not keywords and not pattern:
            raise HTTPException(status_code=400, detail=f"规则 {rule_id} 需要 keywords 或 pattern 之一")
        if keywords is not None and not isinstance(keywords, list):
            raise HTTPException(status_code=400, detail=f"规则 {rule_id} 的 keywords 必须是列表")
        severity = rule.get("severity", "warning")
        if severity not in VALID_SEVERITIES:
            raise HTTPException(status_code=400, detail=f"规则 {rule_id} 的 severity 无效：{severity}")
        normalized.append({**rule, "score": float(score)})
    return normalized


async def _publish_rule_content(
    db: AsyncSession,
    actor: str,
    content: str,
    change_note: str,
) -> Rule_versions:
    docs = _extract_rule_docs(content)
    result = await db.execute(select(Rule_versions).order_by(Rule_versions.version.desc()).limit(1))
    latest = result.scalar_one_or_none()
    next_version = (latest.version + 1) if latest else 1
    result_active = await db.execute(
        select(Rule_versions).where(Rule_versions.status == "active").limit(1)
    )
    active = result_active.scalar_one_or_none()
    if active is not None:
        active.status = "superseded"
    rule_version = Rule_versions(
        version=next_version,
        content=yaml.safe_dump({"rules": docs}, allow_unicode=True, sort_keys=False),
        status="active",
        change_note=change_note or None,
        created_by=actor,
    )
    db.add(rule_version)
    await db.commit()
    await write_audit(
        db,
        actor=actor,
        action="rule_reload",
        target_type="rule_version",
        target_id=str(rule_version.id),
        after={"version": next_version, "rule_count": len(docs), "note": change_note or None},
    )
    return rule_version


async def _apply_rule_promote(db: AsyncSession, request: Approval_requests, actor: str) -> Dict[str, Any]:
    """未知模板晋升：向激活规则追加条目并发布新版本。"""
    template_id = int(request.biz_id)
    result = await db.execute(select(Unknown_templates).where(Unknown_templates.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="未知模板不存在")
    error_type = template.suggested_error_type or "unknown"
    result_active = await db.execute(
        select(Rule_versions).where(Rule_versions.status == "active").order_by(Rule_versions.version.desc()).limit(1)
    )
    active = result_active.scalar_one_or_none()
    parsed: Dict[str, Any] = {}
    if active is not None:
        try:
            parsed = yaml.safe_load(active.content) or {}
        except yaml.YAMLError:
            parsed = {}
    rules = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(rules, list):
        rules = []
    entry = _build_rule_entry(template)
    rules = [r for r in rules if isinstance(r, dict) and r.get("id") != entry["id"]]
    rules.append(entry)
    rule_version = await _publish_rule_content(
        db,
        actor,
        yaml.safe_dump({"rules": rules}, allow_unicode=True, sort_keys=False),
        f"未知模板 #{template.id} 晋升为规则（{error_type}）",
    )
    template.status = "promoted"
    await db.commit()
    await write_audit(
        db,
        actor=actor,
        action="unknown_promote",
        target_type="unknown_template",
        target_id=str(template.id),
        after={"error_type": error_type, "rule_version_id": rule_version.id},
    )
    return {"rule_version": rule_version.version, "error_type": error_type}


# ------------------ 对外业务入口 ------------------

async def create_change_set(
    db: AsyncSession, user: UserResponse, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """创建知识库变更（update/create），按审批模式走审批或直接发布。"""
    role = await require_role(db, user, "sre")
    case_id = (payload.get("case_id") or "").strip()
    change_type = payload.get("change_type", "update")
    reason = (payload.get("reason") or "").strip()
    if change_type not in ("update", "create"):
        raise HTTPException(status_code=400, detail="change_type 仅支持 update / create")
    if change_type == "create" and not case_id:
        case_id = await _generate_case_id(db)
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id 不能为空")
    fields = _validate_fields(payload.get("fields") or {})

    result = await db.execute(select(Kb_cases).where(Kb_cases.case_id == case_id).limit(1))
    existing = result.scalar_one_or_none()
    if change_type == "update" and existing is None:
        raise HTTPException(status_code=404, detail=f"知识案例 {case_id} 不存在")
    if change_type == "create" and existing is not None:
        raise HTTPException(status_code=400, detail=f"知识案例 {case_id} 已存在，请使用 update")

    if existing is not None:
        before = snapshot_case(existing)
        after = {**before, **fields}
        next_version = (existing.version or 1) + 1
        risk_level = "medium" if any(k in fields for k in ("root_cause", "solution")) else "low"
    else:
        before = None
        after = {
            "case_id": case_id,
            "status": "active",
            "version": 1,
            **fields,
        }
        next_version = 1
        risk_level = "low"
    after["version"] = next_version

    diff = build_diff(before, after) if before is not None else {k: {"before": None, "after": v} for k, v in fields.items()}
    change_set = Kb_change_sets(
        case_id=case_id,
        change_type=change_type,
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False),
        diff_json=json.dumps(diff, ensure_ascii=False),
        reason=reason or None,
        status="pending",
        version=next_version,
        created_by=user.email or user.id,
    )
    db.add(change_set)
    await db.flush()

    mode = await get_config(db, "approval_mode", "SINGLE_REVIEW")
    if mode == "OFF":
        published = await _publish_change_set(db, user.email or user.id, change_set, None)
        return {
            "change_set": ser_change_set(change_set),
            "approval_mode": mode,
            "auto_published": True,
            "published": published,
        }

    request = await _create_approval_request(
        db,
        user,
        role,
        biz_type="kb_edit",
        biz_id=str(change_set.id),
        title=f"知识库{'新建' if change_type == 'create' else '变更'}：{case_id}",
        reason=reason,
        risk_level=risk_level,
    )
    change_set.approval_request_id = request.id
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="change_set_create",
        target_type="kb_change_set",
        target_id=str(change_set.id),
        after={"case_id": case_id, "change_type": change_type, "approval_id": request.id, "mode": mode},
    )
    return {
        "change_set": ser_change_set(change_set),
        "approval_mode": mode,
        "auto_published": False,
        "approval_request_id": request.id,
    }


async def decide_approval(
    db: AsyncSession, user: UserResponse, request_id: int, action: str, comment: str
) -> Dict[str, Any]:
    """审批决定：approve / reject / withdraw（申请人不可审批自己的审批单）。"""
    role = await require_role(db, user, "viewer")
    actor = user.email or user.id
    result = await db.execute(select(Approval_requests).where(Approval_requests.id == request_id))
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="审批单不存在")

    if action == "withdraw":
        if request.applicant != actor:
            raise HTTPException(status_code=403, detail="只有申请人可以撤回审批单")
        if request.status != "pending":
            raise HTTPException(status_code=400, detail="仅待审批状态可撤回")
        request.status = "withdrawn"
        await _sync_biz_status(db, request, "withdrawn", actor)
        await db.commit()
        await write_audit(
            db, actor=actor, action="approval_withdraw", target_type="approval_request",
            target_id=str(request.id), after={"status": "withdrawn"},
        )
        return {"status": "withdrawn"}

    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 仅支持 approve / reject / withdraw")

    if not role_at_least(role, "approver"):
        raise HTTPException(status_code=403, detail="权限不足：需要审批人及以上角色")
    if request.status != "pending":
        raise HTTPException(status_code=400, detail=f"审批单当前状态为 {request.status}，不可审批")
    if request.applicant == actor:
        raise HTTPException(status_code=403, detail="申请人不能审批自己的审批单")

    result_step = await db.execute(
        select(Approval_steps).where(
            Approval_steps.request_id == request.id,
            Approval_steps.step_no == request.current_step,
        )
    )
    step = result_step.scalar_one_or_none()
    if step is None or step.action != "pending":
        raise HTTPException(status_code=400, detail="当前审批步骤不存在或已处理")
    if not role_at_least(role, step.approver_role):
        raise HTTPException(status_code=403, detail=f"当前步骤需要 {ROLE_LABELS.get(step.approver_role, step.approver_role)} 审批")

    step.action = action
    step.approver = actor
    step.comment = (comment or "").strip() or None
    step.acted_at = now_iso()

    if action == "reject":
        request.status = "rejected"
        await _sync_biz_status(db, request, "rejected", actor)
        await db.commit()
        await write_audit(
            db, actor=actor, action="approval_reject", target_type="approval_request",
            target_id=str(request.id), before={"status": "pending"}, after={"status": "rejected", "comment": comment},
        )
        return {"status": "rejected"}

    if request.current_step < request.total_steps:
        request.current_step += 1
        await db.commit()
        await write_audit(
            db, actor=actor, action="approval_approve", target_type="approval_request",
            target_id=str(request.id), after={"status": "pending", "current_step": request.current_step},
        )
        return {"status": "pending", "current_step": request.current_step}

    request.status = "approved"
    outcome = await _complete_request(db, request, actor)
    await db.commit()
    await write_audit(
        db, actor=actor, action="approval_approve", target_type="approval_request",
        target_id=str(request.id), after={"status": "approved", "outcome": outcome},
    )
    return {"status": "approved", "outcome": outcome}


async def get_approval_content(db: AsyncSession, request_id: int) -> Dict[str, Any]:
    """审批内容详情：按 biz_type 返回关联业务对象（知识库变更含修改前后 diff）。"""
    result = await db.execute(select(Approval_requests).where(Approval_requests.id == request_id))
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="审批单不存在")

    content: Optional[Dict[str, Any]] = None
    if request.biz_type == "kb_edit":
        result_cs = await db.execute(
            select(Kb_change_sets).where(Kb_change_sets.id == int(request.biz_id))
        )
        cs = result_cs.scalar_one_or_none()
        if cs is not None:
            data = ser_change_set(cs)
            diff = data.get("diff")
            if isinstance(diff, dict):
                # 过滤元数据字段与无实际变化的条目，仅展示有意义的字段级 diff
                data["diff"] = {
                    k: v
                    for k, v in diff.items()
                    if k not in ("status", "version", "feedback_score")
                    and isinstance(v, dict)
                    and v.get("before") != v.get("after")
                }
            if cs.change_type == "create":
                # 新建案例：按案例库全部字段构造完整视图，未填写字段显式标注，便于审批人评估
                after = data.get("after") if isinstance(data.get("after"), dict) else {}
                data["full_case"] = {
                    "case_id": cs.case_id,
                    "error_type": str(after.get("error_type") or ""),
                    "service_name": str(after.get("service_name") or ""),
                    "cluster": after.get("cluster"),
                    "alert_template": after.get("alert_template"),
                    "root_cause": after.get("root_cause"),
                    "solution": after.get("solution"),
                    "topology_snapshot": after.get("topology_snapshot"),
                    "status": after.get("status") or "active",
                    "version": after.get("version") or cs.version,
                    "feedback_score": None,
                    "missing_fields": [f for f in KB_CASE_FIELDS if not str(after.get(f) or "").strip()],
                }
                service = str(after.get("service_name") or "").strip()
                if service:
                    data["related_events"] = _ser_event_samples(
                        await _fetch_related_events(db, after.get("alert_template"), service)
                    )
            content = data
    elif request.biz_type == "merge":
        result_p = await db.execute(
            select(Kb_merge_proposals).where(Kb_merge_proposals.id == int(request.biz_id))
        )
        p = result_p.scalar_one_or_none()
        if p is not None:
            content = ser_merge(p)
            # 完整展示合并双方：主案例与全部被合并案例的完整业务字段（含已归档案例）
            result_master = await db.execute(
                select(Kb_cases).where(Kb_cases.case_id == p.master_case_id).limit(1)
            )
            master_case = result_master.scalar_one_or_none()
            content["master_case"] = ser_case(master_case) if master_case is not None else None
            merged_cases: List[Dict[str, Any]] = []
            for merged_id in content["merged_case_ids"]:
                result_m = await db.execute(
                    select(Kb_cases).where(Kb_cases.case_id == merged_id).limit(1)
                )
                merged_case = result_m.scalar_one_or_none()
                if merged_case is not None:
                    merged_cases.append(ser_case(merged_case))
                else:
                    merged_cases.append({"case_id": merged_id, "missing": True})
            content["merged_cases"] = merged_cases
    elif request.biz_type == "rule_promote":
        result_t = await db.execute(
            select(Unknown_templates).where(Unknown_templates.id == int(request.biz_id))
        )
        t = result_t.scalar_one_or_none()
        if t is not None:
            content = ser_template(t)
            # 晋升后写入的规则条目预览（与晋升执行共用同一构造逻辑，预览即所得）
            content["proposed_rule_entry"] = _build_rule_entry(t)
            content["related_events"] = _ser_event_samples(
                await _fetch_related_events(db, t.template, t.last_seen_service)
            )

    return {
        "biz_type": request.biz_type,
        "biz_id": request.biz_id,
        "title": request.title,
        "content": content,
    }


async def _complete_request(db: AsyncSession, request: Approval_requests, actor: str) -> Dict[str, Any]:
    """审批终审通过后按 biz_type 执行业务动作。"""
    if request.biz_type == "kb_edit":
        result = await db.execute(select(Kb_change_sets).where(Kb_change_sets.id == int(request.biz_id)))
        change_set = result.scalar_one_or_none()
        if change_set is None:
            raise HTTPException(status_code=404, detail="关联的变更集不存在")
        published = await _publish_change_set(db, actor, change_set, request.id)
        return {"type": "kb_publish", **published}
    if request.biz_type == "merge":
        result = await db.execute(select(Kb_merge_proposals).where(Kb_merge_proposals.id == int(request.biz_id)))
        proposal = result.scalar_one_or_none()
        if proposal is None:
            raise HTTPException(status_code=404, detail="关联的合并提案不存在")
        merged = await _apply_merge(db, proposal, actor)
        return {"type": "kb_merge", **merged}
    if request.biz_type == "rule_promote":
        promoted = await _apply_rule_promote(db, request, actor)
        return {"type": "rule_promote", **promoted}
    return {"type": "noop"}


async def _sync_biz_status(db: AsyncSession, request: Approval_requests, status: str, actor: str) -> None:
    """将审批终态同步回业务对象。"""
    if request.biz_type == "kb_edit":
        result = await db.execute(select(Kb_change_sets).where(Kb_change_sets.id == int(request.biz_id)))
        change_set = result.scalar_one_or_none()
        if change_set is not None:
            change_set.status = status
    elif request.biz_type == "merge":
        result = await db.execute(select(Kb_merge_proposals).where(Kb_merge_proposals.id == int(request.biz_id)))
        proposal = result.scalar_one_or_none()
        if proposal is not None:
            proposal.status = status


async def rollback_case(
    db: AsyncSession, user: UserResponse, case_id: str, version: int
) -> Dict[str, Any]:
    """回滚知识案例到指定历史版本（生成新版本快照，不删除历史）。"""
    await require_role(db, user, "kb_admin")
    case = await _get_case(db, case_id)
    result = await db.execute(
        select(Kb_versions).where(Kb_versions.case_id == case_id, Kb_versions.version == version).limit(1)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"案例 {case_id} 不存在版本 {version}")
    before = snapshot_case(case)
    snapshot = json.loads(target.snapshot_json)
    for key in KB_CASE_FIELDS:
        if snapshot.get(key):
            setattr(case, key, snapshot[key])
    case.status = "active"
    case.version = (case.version or 1) + 1
    db.add(
        Kb_versions(
            case_id=case_id,
            version=case.version,
            snapshot_json=json.dumps(snapshot_case(case), ensure_ascii=False),
            created_by=user.email or user.id,
        )
    )
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="kb_rollback",
        target_type="kb_case",
        target_id=case_id,
        before={**before, "restored_from_version": version},
        after={"version": case.version},
    )
    return ser_case(case)


async def list_case_versions(db: AsyncSession, case_id: str) -> List[Dict[str, Any]]:
    await _get_case(db, case_id)
    result = await db.execute(
        select(Kb_versions).where(Kb_versions.case_id == case_id).order_by(Kb_versions.version.desc())
    )
    return [ser_version(v) for v in result.scalars().all()]


async def scan_duplicates(db: AsyncSession) -> List[Dict[str, Any]]:
    """扫描相似活跃案例：同 error_type + service 且模板相似或同集群。"""
    result = await db.execute(select(Kb_cases).where(Kb_cases.status == "active"))
    cases = list(result.scalars().all())
    groups: List[Dict[str, Any]] = []
    used: set = set()
    for i, a in enumerate(cases):
        if a.case_id in used:
            continue
        group = [a]
        for b in cases[i + 1 :]:
            if b.case_id in used:
                continue
            same_sig = a.error_type == b.error_type and a.service_name == b.service_name
            if not same_sig:
                continue
            similar = _template_similarity(a.alert_template or "", b.alert_template or "") >= 0.5
            same_cluster = a.cluster and a.cluster == b.cluster
            if similar or same_cluster:
                group.append(b)
        if len(group) >= 2:
            used.update(c.case_id for c in group)
            groups.append(
                {
                    "error_type": a.error_type,
                    "service_name": a.service_name,
                    "case_ids": [c.case_id for c in group],
                    "cases": [ser_case(c) for c in group],
                    "suggested_master": max(group, key=lambda c: c.feedback_score or 0).case_id,
                }
            )
    return groups


async def create_merge_proposal(
    db: AsyncSession, user: UserResponse, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """创建合并提案并按模式走审批（OFF 模式直接合并）。"""
    role = await require_role(db, user, "sre")
    master_id = (payload.get("master_case_id") or "").strip()
    merged_ids = payload.get("merged_case_ids") or []
    reason = (payload.get("reason") or "").strip()
    strategy = payload.get("strategy") or {"keep_fields": "master", "archive_redundant": True}
    if not master_id:
        raise HTTPException(status_code=400, detail="master_case_id 不能为空")
    if not isinstance(merged_ids, list) or not merged_ids:
        raise HTTPException(status_code=400, detail="merged_case_ids 不能为空")
    if master_id in merged_ids:
        raise HTTPException(status_code=400, detail="主案例不能同时出现在合并列表中")
    await _get_case(db, master_id)
    for mid in merged_ids:
        await _get_case(db, mid)

    proposal = Kb_merge_proposals(
        master_case_id=master_id,
        merged_case_ids=json.dumps(merged_ids, ensure_ascii=False),
        merge_strategy_json=json.dumps(strategy, ensure_ascii=False),
        reason=reason or None,
        status="pending",
        created_by=user.email or user.id,
    )
    db.add(proposal)
    await db.flush()

    mode = await get_config(db, "approval_mode", "SINGLE_REVIEW")
    if mode == "OFF":
        merged = await _apply_merge(db, proposal, user.email or user.id)
        return {"proposal": ser_merge(proposal), "auto_merged": True, "outcome": merged}

    request = await _create_approval_request(
        db,
        user,
        role,
        biz_type="merge",
        biz_id=str(proposal.id),
        title=f"知识库去重合并：{master_id}",
        reason=reason,
        risk_level="medium",
    )
    proposal.approval_request_id = request.id
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="merge_proposal_create",
        target_type="kb_merge_proposal",
        target_id=str(proposal.id),
        after={"master": master_id, "merged": merged_ids, "approval_id": request.id},
    )
    return {"proposal": ser_merge(proposal), "auto_merged": False, "approval_request_id": request.id}


async def list_merge_proposals(db: AsyncSession) -> List[Dict[str, Any]]:
    result = await db.execute(select(Kb_merge_proposals).order_by(Kb_merge_proposals.id.desc()).limit(100))
    return [ser_merge(p) for p in result.scalars().all()]


# ------------------ 规则管理 ------------------

async def list_rules(db: AsyncSession) -> Dict[str, Any]:
    result = await db.execute(select(Rule_versions).order_by(Rule_versions.version.desc()).limit(50))
    versions = [ser_rule(v) for v in result.scalars().all()]
    active = next((v for v in versions if v["status"] == "active"), None)
    rule_count = 0
    if active:
        try:
            rule_count = len(_extract_rule_docs(active["content"]))
        except HTTPException:
            rule_count = 0
    return {"active": active, "versions": versions, "rule_count": rule_count}


async def validate_rules(db: AsyncSession, user: UserResponse, content: str) -> Dict[str, Any]:
    await require_role(db, user, "sre")
    docs = _extract_rule_docs(content)
    return {"valid": True, "rule_count": len(docs), "rule_ids": [d.get("id") for d in docs]}


async def publish_rules(db: AsyncSession, user: UserResponse, content: str, change_note: str) -> Dict[str, Any]:
    await require_role(db, user, "sys_admin")
    rule_version = await _publish_rule_content(db, user.email or user.id, content, change_note)
    await write_audit(
        db,
        actor=user.email or user.id,
        action="rule_publish",
        target_type="rule_version",
        target_id=str(rule_version.id),
        after={"version": rule_version.version},
    )
    return ser_rule(rule_version)


async def rollback_rules(db: AsyncSession, user: UserResponse, version_id: int) -> Dict[str, Any]:
    """回滚到指定历史规则版本：目标版本重新激活，当前激活版本被替代。"""
    await require_role(db, user, "sys_admin")
    result = await db.execute(select(Rule_versions).where(Rule_versions.id == version_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    if target.status == "active":
        raise HTTPException(status_code=400, detail="目标版本已经是激活版本")
    result_active = await db.execute(select(Rule_versions).where(Rule_versions.status == "active").limit(1))
    active = result_active.scalar_one_or_none()
    before_version = active.version if active else None
    if active is not None:
        active.status = "superseded"
    target.status = "active"
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="rule_rollback",
        target_type="rule_version",
        target_id=str(target.id),
        before={"active_version": before_version},
        after={"active_version": target.version},
    )
    return ser_rule(target)


# ------------------ 未知模板 ------------------

async def list_unknown_templates(db: AsyncSession, status: Optional[str]) -> List[Dict[str, Any]]:
    stmt = select(Unknown_templates).order_by(Unknown_templates.sample_count.desc().nullslast(), Unknown_templates.id.desc())
    if status:
        stmt = stmt.where(Unknown_templates.status == status)
    result = await db.execute(stmt.limit(200))
    return [ser_template(t) for t in result.scalars().all()]


async def promote_unknown_template(
    db: AsyncSession, user: UserResponse, template_id: int, error_type: str
) -> Dict[str, Any]:
    """将未知模板提交晋升审批（生成新规则条目）。"""
    role = await require_role(db, user, "sre")
    result = await db.execute(select(Unknown_templates).where(Unknown_templates.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="未知模板不存在")
    if template.status != "pending":
        raise HTTPException(status_code=400, detail=f"模板当前状态为 {template.status}，不可晋升")
    error_type = (error_type or template.suggested_error_type or "").strip()
    if not error_type:
        raise HTTPException(status_code=400, detail="必须提供目标 error_type")
    template.suggested_error_type = error_type
    request = await _create_approval_request(
        db,
        user,
        role,
        biz_type="rule_promote",
        biz_id=str(template.id),
        title=f"未知告警晋升规则：#{template.id}",
        reason=f"将未知模板晋升为 {error_type} 分类规则",
        risk_level="medium",
    )
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="unknown_promote_request",
        target_type="unknown_template",
        target_id=str(template.id),
        after={"error_type": error_type, "approval_id": request.id},
    )
    return {"template": ser_template(template), "approval_request_id": request.id}


async def discard_unknown_template(db: AsyncSession, user: UserResponse, template_id: int) -> Dict[str, Any]:
    await require_role(db, user, "sre")
    result = await db.execute(select(Unknown_templates).where(Unknown_templates.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="未知模板不存在")
    before_status = template.status
    template.status = "discarded"
    await db.commit()
    await write_audit(
        db, actor=user.email or user.id, action="unknown_discard",
        target_type="unknown_template", target_id=str(template.id),
        before={"status": before_status}, after={"status": "discarded"},
    )
    return ser_template(template)


# ------------------ 反馈闭环 ------------------

async def apply_feedback(
    db: AsyncSession, user: UserResponse, event_id: int, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """👍/👎 更新案例 feedback_score；人工修正自动创建 change_set。"""
    rating = payload.get("rating")
    if rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 仅支持 up / down")
    correction = payload.get("correction") or {}
    comment = (payload.get("comment") or "").strip()
    if correction:
        await require_role(db, user, "sre")
    else:
        await require_role(db, user, "operator")

    result = await db.execute(select(Events).where(Events.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    try:
        candidates = json.loads(event.candidates_json or "[]")
    except (TypeError, ValueError):
        candidates = []
    if not candidates:
        raise HTTPException(status_code=400, detail="该事件没有 RAG 召回案例，无法反馈")
    case_id = candidates[0].get("case_id")
    case = await _get_case(db, case_id)

    before_score = case.feedback_score or 0
    delta = 1.0 if rating == "up" else -1.0
    case.feedback_score = max(FEEDBACK_SCORE_MIN, min(FEEDBACK_SCORE_MAX, before_score + delta))
    await db.commit()
    await write_audit(
        db,
        actor=user.email or user.id,
        action="feedback",
        target_type="kb_case",
        target_id=case.case_id,
        before={"feedback_score": before_score, "event_id": event.id},
        after={"feedback_score": case.feedback_score, "rating": rating},
    )

    change_set_result: Optional[Dict[str, Any]] = None
    fields = {k: v for k, v in correction.items() if k in ("root_cause", "solution") and v}
    if fields:
        change_set_result = await create_change_set(
            db,
            user,
            {
                "case_id": case.case_id,
                "change_type": "update",
                "fields": fields,
                "reason": f"人工修正反馈（事件 {event.event_id}）：{comment}".strip("： "),
            },
        )
    return {
        "case_id": case.case_id,
        "feedback_score": case.feedback_score,
        "rating": rating,
        "correction_change_set": change_set_result,
    }
