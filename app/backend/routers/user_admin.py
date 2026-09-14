"""运营平台用户管理 API（仅 sys_admin 可用）：创建用户、角色分配、启用/禁用。

认证复用 Atoms Auth（OIDC / 平台令牌 / 演示登录），本路由只维护
用户档案与控制台角色绑定，不引入独立密码体系；全部操作写审计日志。
"""
import logging
from datetime import datetime
from typing import List, Optional

from core.database import get_db
from dependencies.auth import get_current_user
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from schemas.auth import UserResponse
from services.console_common import ROLE_LABELS, require_role
from services.user_admin import STATUS_ACTIVE, UserAdminService
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/users", tags=["user-admin"])


class CreateUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    role: str = "viewer"
    status: str = STATUS_ACTIVE


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class UserAdminItem(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str
    role_label: str
    status: str
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class UserAdminListResponse(BaseModel):
    items: List[UserAdminItem]
    total: int
    skip: int
    limit: int


def _to_item(user, role: str) -> UserAdminItem:
    return UserAdminItem(
        id=user.id,
        email=user.email,
        name=user.name,
        role=role,
        role_label=ROLE_LABELS.get(role, role),
        status=user.status or STATUS_ACTIVE,
        created_at=user.created_at,
        last_login=user.last_login,
    )


@router.get("", response_model=UserAdminListResponse)
async def list_users(
    q: Optional[str] = Query(None, max_length=100),
    user_status: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """用户列表（支持按邮箱/姓名搜索与状态过滤），仅系统管理员可用。"""
    await require_role(db, current_user, "sys_admin")
    items, total = await UserAdminService(db).list_users(
        q=q, status_filter=user_status, skip=skip, limit=limit
    )
    return UserAdminListResponse(
        items=[_to_item_user_dict(item) for item in items], total=total, skip=skip, limit=limit
    )


def _to_item_user_dict(item: dict) -> UserAdminItem:
    return UserAdminItem(
        id=item["id"],
        email=item["email"],
        name=item.get("name"),
        role=item["role"],
        role_label=item["role_label"],
        status=item["status"],
        created_at=item.get("created_at"),
        last_login=item.get("last_login"),
    )


@router.post("", response_model=UserAdminItem)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """创建运营平台用户档案并绑定控制台角色（首次登录按邮箱自动关联）。"""
    await require_role(db, current_user, "sys_admin")
    user, role = await UserAdminService(db).create_user(
        email=body.email,
        name=body.name,
        role=body.role,
        status=body.status,
        actor=current_user.email or current_user.id,
    )
    return _to_item(user, role)


@router.put("/{user_id}", response_model=UserAdminItem)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """更新用户姓名 / 控制台角色 / 启用禁用状态（含防自锁保护）。"""
    actor_role = await require_role(db, current_user, "sys_admin")
    user, role = await UserAdminService(db).update_user(
        user_id=user_id,
        name=body.name,
        role=body.role,
        status=body.status,
        actor=current_user.email or current_user.id,
        actor_email=current_user.email,
        actor_role=actor_role,
    )
    return _to_item(user, role)
