"""RAG 同步补偿队列运维 API：任务列表 / 统计 / 手动重放 / 到期重试。

权限：sys_admin（can_manage_config）或 kb_admin（can_edit_kb）。
路由由 main.py 自动发现（routers 包内 APIRouter 变量）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from schemas.auth import UserResponse
from dependencies.auth import get_current_user
from services import rag_sync
from services.console_common import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/console/rag-sync", tags=["rag-sync"])


@router.get("/tasks")
async def list_sync_tasks(
    status: Optional[str] = Query(None, description="任务状态过滤：pending/done/dead"),
    task_type: Optional[str] = Query(None, description="任务类型过滤：upsert/delete/feedback/cache_invalidate"),
    case_id: Optional[str] = Query(None, description="按 case_id 过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """补偿任务列表查询（支持状态/类型/case_id 过滤与分页）。"""
    await require_role(db, current_user, "sre")
    return await rag_sync.list_tasks(
        db, status=status, task_type=task_type, case_id=case_id, limit=limit, offset=offset
    )


@router.get("/stats")
async def sync_task_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """补偿任务状态统计（pending/done/dead/total）。"""
    await require_role(db, current_user, "sre")
    return await rag_sync.task_stats(db)


@router.post("/retry-due")
async def retry_due_tasks(
    limit: int = Query(20, ge=1, le=100),
    dry_run: bool = Query(False, description="试运行：仅统计不执行"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """手动触发到期补偿任务重试（也可由外部调度定时调用）。"""
    role = await require_role(db, current_user, "kb_admin")
    result = await rag_sync.retry_due_tasks(db, limit=limit, dry_run=dry_run)
    logger.info("retry_due_tasks triggered by %s (role=%s): %s", current_user.email, role, result)
    return result


@router.post("/tasks/{task_id}/replay")
async def replay_single_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """人工重放单个补偿任务（立即执行一次，忽略退避窗口）。"""
    role = await require_role(db, current_user, "kb_admin")
    result = await rag_sync.replay_task(db, actor=current_user.email or current_user.id, task_id=task_id)
    if not result.get("ok") and "not found" in (result.get("error") or ""):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/replay-dead")
async def replay_dead_tasks(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """批量重放死信任务：重置计数后重新排队（等待下次 retry_due_tasks 执行）。"""
    role = await require_role(db, current_user, "kb_admin")
    return await rag_sync.replay_dead_tasks(
        db, actor=current_user.email or current_user.id, limit=limit
    )
