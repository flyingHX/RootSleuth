"""运营平台用户管理服务：档案创建、角色分配、启用/禁用与审计。

用户体系设计（复用 Atoms Auth，不引入第二套登录体系）：
- 用户档案持久化于 users 表，主键即登录身份标识（平台 sub，或预创建档案的邮箱）。
- 控制台角色统一由配置中心 role_bindings_json（email -> role）解析，
  用户管理在创建/变更角色时同步维护该绑定。
- status=disabled 的账号在登录与角色解析阶段被拒绝，实现快速封禁。
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.auth import User
from services.console_common import (
    ROLE_LABELS,
    ROLE_LEVELS,
    get_config,
    get_config_json,
    role_at_least,
    set_config,
    write_audit,
)

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
VALID_STATUS = (STATUS_ACTIVE, STATUS_DISABLED)


class UserAdminService:
    """运营平台用户档案与角色绑定的管理能力。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------------- 角色解析 ----------------

    async def _binding_role(self, user: User) -> str:
        """解析某档案当前的控制台角色：绑定优先，其次 admin 旧值映射，最后回退默认角色。"""
        bindings = await get_config_json(self.db, "role_bindings_json", {}) or {}
        role = bindings.get(user.email or "", "")
        if role in ROLE_LEVELS:
            return role
        if (user.role or "") == "admin":
            return "sys_admin"
        default_role = await get_config(self.db, "default_role", "viewer")
        # 防越权加固：default_role 不允许解析为 sys_admin，异常配置一律回退 viewer
        if default_role not in ROLE_LEVELS or default_role == "sys_admin":
            return "viewer"
        return default_role

    async def _upsert_binding(self, email: str, role: Optional[str]) -> Dict[str, str]:
        """同步 role_bindings_json 绑定（role 为 None 表示移除绑定）。"""
        bindings = await get_config_json(self.db, "role_bindings_json", {}) or {}
        bindings = {k: v for k, v in bindings.items() if isinstance(v, str)}
        if role is None:
            bindings.pop(email, None)
        else:
            bindings[email] = role
        await set_config(
            self.db,
            "role_bindings_json",
            json.dumps(bindings, ensure_ascii=False, sort_keys=True),
        )
        return bindings

    async def _count_active_sys_admins(self, exclude_id: Optional[str] = None) -> int:
        """统计启用状态的系统管理员数量（有效角色 = 绑定 > 旧 admin 字段 > 默认角色）。"""
        rows = (
            await self.db.execute(
                select(User).where(func.coalesce(User.status, STATUS_ACTIVE) != STATUS_DISABLED)
            )
        ).scalars().all()
        if not rows:
            return 0
        bindings = await get_config_json(self.db, "role_bindings_json", {}) or {}
        default_role = await get_config(self.db, "default_role", "viewer")
        # 防越权加固：default_role 不允许解析为 sys_admin，异常配置一律回退 viewer
        if default_role not in ROLE_LEVELS or default_role == "sys_admin":
            default_role = "viewer"
        count = 0
        for u in rows:
            if exclude_id and u.id == exclude_id:
                continue
            role = bindings.get(u.email or "", "")
            if role not in ROLE_LEVELS:
                role = "sys_admin" if (u.role or "") == "admin" else default_role
            if role == "sys_admin":
                count += 1
        return count

    # ---------------- 查询 ----------------

    async def list_users(
        self,
        q: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按关键词/状态过滤用户档案，返回（列表, 总数），角色为控制台角色。"""
        stmt = select(User)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(User.email.ilike(like), User.name.ilike(like), User.id.ilike(like)))
        if status_filter in VALID_STATUS:
            stmt = stmt.where(User.status == status_filter)

        total = (await self.db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (
            await self.db.execute(
                stmt.order_by(User.created_at.desc().nullslast(), User.id).offset(skip).limit(limit)
            )
        ).scalars().all()

        items: List[Dict[str, Any]] = []
        for u in rows:
            role = await self._binding_role(u)
            items.append(
                {
                    "id": u.id,
                    "email": u.email,
                    "name": u.name,
                    "role": role,
                    "role_label": ROLE_LABELS.get(role, role),
                    "status": u.status or STATUS_ACTIVE,
                    "created_at": u.created_at,
                    "last_login": u.last_login,
                }
            )
        return items, total

    # ---------------- 创建 ----------------

    async def create_user(
        self,
        email: str,
        name: Optional[str],
        role: str,
        status: str,
        actor: str,
    ) -> Tuple[User, str]:
        """创建用户档案并绑定控制台角色，写审计日志。

        档案以邮箱为主键预创建；用户首次通过 Atoms Auth（OIDC/平台令牌/演示登录）
        登录时按 email 自动关联到平台身份，无需单独的密码体系。
        """
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        if role not in ROLE_LEVELS:
            raise HTTPException(status_code=400, detail="角色必须是 " + " / ".join(ROLE_LEVELS) + " 之一")
        status = (status or STATUS_ACTIVE).strip()
        if status not in VALID_STATUS:
            raise HTTPException(status_code=400, detail="状态仅支持 active / disabled")
        name = (name or "").strip() or None

        dup = (
            await self.db.execute(select(User).where(or_(User.id == email, User.email == email)).limit(1))
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status_code=409, detail="该邮箱已存在用户档案")

        user = User(id=email, email=email, name=name, role="user", status=status)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)

        await self._upsert_binding(email, role)
        await write_audit(
            self.db,
            actor=actor or "system",
            action="user_create",
            target_type="user",
            target_id=email,
            after={"email": email, "name": name, "role": role, "status": status},
        )
        logger.info("[user_admin] user created: %s role=%s status=%s actor=%s", email, role, status, actor)
        return user, role

    # ---------------- 更新 ----------------

    async def update_user(
        self,
        user_id: str,
        name: Optional[str],
        role: Optional[str],
        status: Optional[str],
        actor: str,
        actor_email: Optional[str],
        actor_role: str,
    ) -> Tuple[User, str]:
        """更新姓名 / 控制台角色 / 启用禁用状态，写审计日志。

        防自锁保护：不允许当前管理员禁用自己或降低自己的角色。
        最后一道防线：系统至少保留一个启用状态的系统管理员，禁用或降级最后一个
        sys_admin 时被拒绝。
        """
        user = (await self.db.execute(select(User).where(User.id == user_id).limit(1))).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")

        current_role = await self._binding_role(user)
        before = {"name": user.name, "role": current_role, "status": user.status or STATUS_ACTIVE}
        after = dict(before)
        is_self = bool(actor_email) and (user.email or "").lower() == actor_email.lower()

        # 保留最后一个可用系统管理员：目标为 sys_admin 且本次变更将使其失去管理员能力时，
        # 必须仍存在其他启用中的 sys_admin（自锁场景已由下方 self 校验兜底）。
        if current_role == "sys_admin" and not is_self:
            role_will_drop = role is not None and role in ROLE_LEVELS and role != "sys_admin"
            status_will_disable = status is not None and status.strip() == STATUS_DISABLED
            if (role_will_drop or status_will_disable) and await self._count_active_sys_admins(exclude_id=user.id) < 1:
                raise HTTPException(status_code=400, detail="系统至少保留一个启用状态的系统管理员")

        if name is not None:
            name = name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="姓名不能为空")
            user.name = name
            after["name"] = name

        if role is not None:
            if role not in ROLE_LEVELS:
                raise HTTPException(status_code=400, detail="角色必须是 " + " / ".join(ROLE_LEVELS) + " 之一")
            if is_self and not role_at_least(role, actor_role):
                raise HTTPException(status_code=400, detail="不能降低自己的角色")
            await self._upsert_binding(user.email, role)
            after["role"] = role

        if status is not None:
            status = status.strip()
            if status not in VALID_STATUS:
                raise HTTPException(status_code=400, detail="状态仅支持 active / disabled")
            if is_self and status == STATUS_DISABLED:
                raise HTTPException(status_code=400, detail="不能禁用当前登录账号")
            user.status = status
            after["status"] = status

        await self.db.commit()
        await self.db.refresh(user)

        await write_audit(
            self.db,
            actor=actor or "system",
            action="user_update",
            target_type="user",
            target_id=user.email or user.id,
            before=before,
            after=after,
        )
        logger.info("[user_admin] user updated: %s before=%s after=%s actor=%s", user.email, before, after, actor)
        return user, after["role"]
