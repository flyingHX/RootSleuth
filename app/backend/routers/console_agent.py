"""Agent API：诊断 Agent / 知识治理 Agent / 值班 Agent / 会话轨迹 / CMDB / 报告历史。"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from services import console_agent
from services.console_common import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/console/agent", tags=["console-agent"])


class AgentDiagnoseBody(BaseModel):
    event_id: int


class GovernanceBody(BaseModel):
    time_window: str = "24h"


class OncallBody(BaseModel):
    time_window: str = "24h"


@router.post("/diagnose")
async def agent_diagnose(
    body: AgentDiagnoseBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """诊断 Agent：多轮工具调用推理给出根因结论（AI 失败自动降级单轮诊断）。"""
    await require_role(db, current_user, "operator")
    return await console_agent.run_diagnose_agent(db, current_user, body.event_id)


@router.post("/kb-governance")
async def agent_kb_governance(
    body: GovernanceBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识治理 Agent：按时间窗聚类告警簇 → 起草案例（走审批）→ 合并提案。"""
    await require_role(db, current_user, "sre")
    return await console_agent.run_kb_governance_agent(db, current_user, body.time_window)


@router.post("/oncall-report")
async def agent_oncall_report(
    body: OncallBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """值班 Agent：影响面汇总 + ChatOps 处置建议（AI 失败降级确定性报告）。"""
    await require_role(db, current_user, "operator")
    return await console_agent.run_oncall_agent(db, current_user, body.time_window)


@router.get("/sessions")
async def list_agent_sessions(
    session_type: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "viewer")
    return {"items": await console_agent.list_sessions(db, limit, session_type)}


@router.get("/cmdb")
async def list_cmdb(
    q: str = Query(None),
    limit: int = Query(100, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "viewer")
    return {"items": await console_agent.list_cmdb_assets(db, q, limit)}


@router.get("/oncall-reports")
async def list_oncall_reports(
    limit: int = Query(10, ge=1, le=50),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_role(db, current_user, "viewer")
    return {"items": await console_agent.list_oncall_reports(db, limit)}
