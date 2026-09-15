"""Console 公共能力：配置中心读取、RBAC 角色解析、审计写入与通用工具。

角色层级（值越大权限越高）：
viewer(0) < operator(1) < sre(2) < approver(3) < kb_admin(4) < sys_admin(5)
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.audit_logs import Audit_logs
from models.console_configs import Console_configs
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

ROLE_LEVELS: Dict[str, int] = {
    "viewer": 0,
    "operator": 1,
    "sre": 2,
    "approver": 3,
    "kb_admin": 4,
    "sys_admin": 5,
}

ROLE_LABELS: Dict[str, str] = {
    "viewer": "只读审计",
    "operator": "值班运维",
    "sre": "SRE",
    "approver": "审批人 / SRE Lead",
    "kb_admin": "知识库管理员",
    "sys_admin": "系统管理员",
}

APPROVAL_MODES = ("OFF", "SINGLE_REVIEW", "MULTI_LEVEL")

CONFIG_DEFAULTS: Dict[str, str] = {
    "approval_mode": "SINGLE_REVIEW",
    "confidence_threshold": "0.75",
    "rerank_weight_json": '{"cosine":0.5,"topology":0.2,"time_decay":0.1,"feedback":0.2}',
    "llm_timeout_seconds": "45",
    "llm_provider": "atoms_hub",
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "deepseek-v4-flash",
    "llm_temperature": "0.2",
    "embedding_base_url": "",
    "embedding_api_key": "",
    "embedding_model": "",
    "rag_base_url": "",
    "kb_expire_days": "90",
    "rag_sync_retry_base_seconds": "30",
    "rag_sync_max_attempts": "5",
    "feature_flags_json": '{"auto_diagnose":true,"dedup_scan":true}',
    # 未绑定角色用户的默认角色设为 sre：保证真实账号登录后可见三类 Agent 操作按钮（viewer 只读会全部隐藏）；
    # 安全红线不变：default_role 校验禁止设为 sys_admin
    "default_role": "sre",
    "role_bindings_json": '{"demo-operator@atoms.dev":"operator","demo-sre@atoms.dev":"sre","demo-lead@atoms.dev":"approver","demo-admin@atoms.dev":"sys_admin"}',
}

# 允许通过配置中心修改的键及其中文说明
CONFIG_DESCRIPTIONS: Dict[str, str] = {
    "approval_mode": "审批模式：OFF / SINGLE_REVIEW / MULTI_LEVEL",
    "confidence_threshold": "诊断置信度阈值（0~1），低于阈值标记为低置信",
    "rerank_weight_json": "重排权重 JSON（cosine/topology/time_decay/feedback）",
    "llm_timeout_seconds": "LLM 诊断超时时间（秒，10~300）",
    "llm_provider": "LLM 接入方式：atoms_hub（平台内置 AIHub）/ openai_compatible（自建 OpenAI 兼容接口）",
    "llm_base_url": "LLM OpenAI 兼容 Base URL（openai_compatible 时必填，如 https://api.deepseek.com/v1）",
    "llm_api_key": "LLM API Key（加密存储、脱敏展示；留空清除）",
    "llm_model": "LLM Chat 模型名称（诊断与三类 Agent 共用，如 deepseek-v4-flash）",
    "llm_temperature": "LLM 采样温度（0~2，默认 0.2）",
    "embedding_base_url": "Embedding Base URL（缺省回退 llm_base_url）",
    "embedding_api_key": "Embedding API Key（加密存储、脱敏展示；缺省回退 llm_api_key）",
    "embedding_model": "Embedding 模型名称（配置后启用诊断 RAG 语义加分，如 bge-m3）",
    "rag_base_url": "RAG 检索服务 Base URL（如 http://rag:8080；留空表示未部署，知识索引同步自动跳过）",
    "kb_expire_days": "知识老化归档天数（生命周期巡检：老化且负反馈的活跃案例归档淘汰）",
    "feature_flags_json": "功能开关 JSON（auto_diagnose/dedup_scan 等）",
    "default_role": "未绑定角色用户的默认角色",
    "role_bindings_json": "角色绑定 JSON（email -> role）",
    "rag_sync_retry_base_seconds": "RAG 同步补偿重试基础间隔（秒，指数退避基数，1~3600）",
    "rag_sync_max_attempts": "RAG 同步补偿最大尝试次数（超过进入死信，1~20）",
}


async def get_config(db: AsyncSession, key: str, default: Optional[str] = None) -> str:
    """读取单个配置项，缺失时回退到调用方默认值或内置默认值。"""
    result = await db.execute(
        select(Console_configs).where(Console_configs.config_key == key).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is not None and row.config_value is not None:
        return row.config_value
    if default is not None:
        return default
    return CONFIG_DEFAULTS.get(key, "")


async def get_config_json(db: AsyncSession, key: str, default: Any = None) -> Any:
    """读取 JSON 配置项，解析失败时返回默认值。"""
    raw = await get_config(db, key, None)
    if raw:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Config %s is not valid JSON: %s", key, raw)
    return default


async def set_config(db: AsyncSession, key: str, value: str, description: Optional[str] = None) -> Console_configs:
    """创建或更新配置项（config_key 无唯一约束，按首行覆盖）。"""
    result = await db.execute(
        select(Console_configs).where(Console_configs.config_key == key).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = Console_configs(
            config_key=key,
            config_value=value,
            description=description or CONFIG_DESCRIPTIONS.get(key),
        )
        db.add(row)
    else:
        row.config_value = value
        if description:
            row.description = description
    await db.commit()
    await db.refresh(row)
    return row


async def resolve_role(db: AsyncSession, user: UserResponse) -> str:
    """解析用户控制台角色：先查 role_bindings 绑定，再回退 default_role。

    账号被管理员禁用时直接 403，保证禁用即时生效而不必等待 JWT 过期。
    """
    from models.auth import User as UserModel

    if user.email or user.id:
        from sqlalchemy import or_

        row = await db.execute(
            select(UserModel).where(or_(UserModel.id == user.id, UserModel.email == user.email)).limit(1)
        )
        existing = row.scalar_one_or_none()
        if existing is not None and (existing.status or "active") == "disabled":
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员开通")

    bindings = await get_config_json(db, "role_bindings_json", {}) or {}
    role = bindings.get(user.email or "", "")
    if role in ROLE_LEVELS:
        return role
    default_role = await get_config(db, "default_role", "viewer")
    # 防越权加固：default_role 不允许解析为 sys_admin，异常配置一律回退 viewer
    if default_role not in ROLE_LEVELS or default_role == "sys_admin":
        return "viewer"
    return default_role


def role_at_least(role: str, min_role: str) -> bool:
    return ROLE_LEVELS.get(role, -1) >= ROLE_LEVELS.get(min_role, 0)


async def require_role(db: AsyncSession, user: UserResponse, min_role: str) -> str:
    """校验当前用户角色层级，不满足时抛 403，返回实际角色。"""
    role = await resolve_role(db, user)
    if not role_at_least(role, min_role):
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：该操作需要 {ROLE_LABELS.get(min_role, min_role)} 及以上角色",
        )
    return role


def permissions_for(role: str) -> Dict[str, Any]:
    """返回角色对应的权限清单，供前端渲染操作入口。"""
    level = ROLE_LEVELS.get(role, -1)
    return {
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "level": level,
        "can_diagnose": level >= 1,
        "can_feedback": level >= 1,
        "can_edit_kb": level >= 2,
        "can_approve": level >= 3,
        "can_publish": level >= 4,
        "can_manage_rules": level >= 5,
        "can_manage_config": level >= 5,
        "can_manage_users": level >= 5,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def write_audit(
    db: AsyncSession,
    actor: str,
    action: str,
    target_type: str,
    target_id: Any,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> None:
    """写入审计日志并独立提交，保证审计链路不被业务回滚牵连。"""
    entry = Audit_logs(
        actor=actor or "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False) if after is not None else None,
    )
    db.add(entry)
    await db.commit()


def snapshot_case(case: Any) -> Dict[str, Any]:
    """提取知识案例的业务字段快照（不含主键与时间戳）。"""
    return {
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
    }


def build_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """构建字段级 diff：仅保留发生变化的字段。"""
    diff: Dict[str, Any] = {}
    for key, new_value in (after or {}).items():
        old_value = (before or {}).get(key)
        if old_value != new_value:
            diff[key] = {"before": old_value, "after": new_value}
    return diff


_SENSITIVE_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|authorization|credential)\b\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/=_.-]{32,}\b")


def mask_sensitive(text: Optional[str]) -> Optional[str]:
    """脱敏原始日志中的敏感信息（评审 P0-6）：密钥赋值 / Bearer / 邮箱 / 长令牌。

    用于进入 LLM Prompt、Agent 会话 result_json 与审计展示的样本日志；
    IP 与主机名保留（诊断与 CMDB 关联需要），仅过滤凭据类与个人敏感信息。
    """
    if not text:
        return text
    masked = _SENSITIVE_KEY_RE.sub(lambda m: f"{m.group(1)}=***", text)
    masked = _BEARER_RE.sub("Bearer ***", masked)
    masked = _EMAIL_RE.sub("***@***", masked)
    masked = _LONG_TOKEN_RE.sub("***", masked)
    return masked


def validate_config_value(key: str, value: str) -> Tuple[bool, str]:
    """配置中心写入前的值校验。"""
    if key == "approval_mode":
        if value not in APPROVAL_MODES:
            return False, "approval_mode 仅支持 OFF / SINGLE_REVIEW / MULTI_LEVEL"
        return True, "ok"
    if key == "confidence_threshold":
        try:
            num = float(value)
        except ValueError:
            return False, "confidence_threshold 必须是数字"
        if not (0 < num <= 1):
            return False, "confidence_threshold 必须在 (0, 1] 区间"
        return True, "ok"
    if key == "llm_timeout_seconds":
        try:
            num = int(value)
        except ValueError:
            return False, "llm_timeout_seconds 必须是整数"
        if not (10 <= num <= 300):
            return False, "llm_timeout_seconds 必须在 10~300 之间"
        return True, "ok"
    if key in ("rerank_weight_json", "feature_flags_json", "role_bindings_json"):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return False, f"{key} 必须是合法 JSON"
        if not isinstance(parsed, dict):
            return False, f"{key} 必须是 JSON 对象"
        return True, "ok"
    if key == "llm_provider":
        if value not in ("atoms_hub", "openai_compatible"):
            return False, "llm_provider 仅支持 atoms_hub / openai_compatible"
        return True, "ok"
    if key in ("llm_base_url", "embedding_base_url"):
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            return False, f"{key} 必须以 http:// 或 https:// 开头（或留空）"
        return True, "ok"
    if key in ("llm_api_key", "embedding_api_key"):
        if "****" in value:
            return False, f"{key} 展示为脱敏格式，请输入完整 API Key（或留空清除）"
        return True, "ok"
    if key == "llm_model":
        if not value.strip() or "****" in value:
            return False, "llm_model 必须是有效的模型名称"
        return True, "ok"
    if key == "llm_temperature":
        try:
            num = float(value)
        except ValueError:
            return False, "llm_temperature 必须是数字"
        if not (0 <= num <= 2):
            return False, "llm_temperature 必须在 0~2 之间"
        return True, "ok"
    if key == "embedding_model":
        if "****" in value:
            return False, "embedding_model 配置值无效"
        return True, "ok"
    if key == "rag_sync_retry_base_seconds":
        try:
            num = int(value)
        except ValueError:
            return False, "rag_sync_retry_base_seconds 必须是整数"
        if not (1 <= num <= 3600):
            return False, "rag_sync_retry_base_seconds 必须在 1~3600 之间"
        return True, "ok"
    if key == "rag_sync_max_attempts":
        try:
            num = int(value)
        except ValueError:
            return False, "rag_sync_max_attempts 必须是整数"
        if not (1 <= num <= 20):
            return False, "rag_sync_max_attempts 必须在 1~20 之间"
        return True, "ok"
    if key == "default_role":
        # 安全红线：默认角色不得设为 sys_admin，防止未绑定用户越权获得管理员能力
        if value == "sys_admin":
            return False, "default_role 不能设置为 sys_admin（防止未绑定用户越权）"
        if value not in ROLE_LEVELS:
            return False, "default_role 必须是有效角色名"
        return True, "ok"
    return True, "ok"
