"""Console 公共能力：配置中心读取、RBAC 角色解析、审计写入与通用工具。

角色层级（值越大权限越高）：
viewer(0) < operator(1) < sre(2) < approver(3) < kb_admin(4) < sys_admin(5)
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

# 三个业务 Agent 的独立配置作用域（模型/温度/超时，留空逐项继承全局 llm_* 配置）
AGENT_CONFIG_SCOPES = ("diagnose", "kb_governance", "oncall")
AGENT_TIMEOUT_KEYS = tuple(f"{scope}_llm_timeout_seconds" for scope in AGENT_CONFIG_SCOPES)
AGENT_TEMPERATURE_KEYS = ("kb_governance_temperature", "oncall_temperature")
# Agent 独立接入配置键（provider/base_url/api_key，留空逐项继承全局 llm_*）
AGENT_PROVIDER_KEYS = tuple(f"{scope}_llm_provider" for scope in AGENT_CONFIG_SCOPES)
AGENT_BASE_URL_KEYS = tuple(f"{scope}_llm_base_url" for scope in AGENT_CONFIG_SCOPES)
AGENT_ACCESS_API_KEY_KEYS = tuple(f"{scope}_llm_api_key" for scope in AGENT_CONFIG_SCOPES)

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
    "diagnose_temperature": "0",
    "diagnose_time_budget_seconds": "90",
    "diagnose_llm_model": "",
    "diagnose_llm_timeout_seconds": "",
    "kb_governance_llm_model": "",
    "kb_governance_temperature": "",
    "kb_governance_llm_timeout_seconds": "",
    "oncall_llm_model": "",
    "oncall_temperature": "",
    "oncall_llm_timeout_seconds": "",
    "diagnose_llm_provider": "",
    "diagnose_llm_base_url": "",
    "diagnose_llm_api_key": "",
    "kb_governance_llm_provider": "",
    "kb_governance_llm_base_url": "",
    "kb_governance_llm_api_key": "",
    "oncall_llm_provider": "",
    "oncall_llm_base_url": "",
    "oncall_llm_api_key": "",
    "embedding_base_url": "",
    "embedding_api_key": "",
    "embedding_model": "",
    "rag_base_url": "",
    "rag_api_key": "",
    "kb_expire_days": "90",
    "kb_expire_unconditional_days": "180",
    "rag_sync_retry_base_seconds": "30",
    "rag_sync_max_attempts": "5",
    "notify_webhook_url": "",
    "notify_webhook_token": "",
    "event_ingest_token": "",
    # 混合 LLM 分级路由（评审：本地 LLM + 远程 LLM 混合调用，敏感数据不出域）
    "llm_local_base_url": "",
    "llm_local_model": "",
    "llm_local_api_key": "",
    "llm_routing_policy": "auto",
    "llm_remote_approval_id": "",
    # 入站数据脱敏（评审：告警原文先脱敏再落库，原始内容不落库）
    "data_masking_enabled": "true",
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
    "llm_model": "LLM Chat 模型名称（全局默认，三个 Agent 可用 <agent>_llm_model 单独覆盖，如 deepseek-v4-flash）",
    "llm_temperature": "LLM 采样温度（0~2，默认 0.2）",
    "diagnose_temperature": "深度诊断 Agent 采样温度（0~2，默认 0：固定零温保证同一事件重复诊断输出稳定；非法值回退 0）",
    "diagnose_time_budget_seconds": "诊断墙钟时间预算（秒，30~600，默认 90）：深度诊断多轮推理+强制收尾+单轮诊断（含 Agent 降级）的总时长上限，超时返回结构化 502 并自动降级，防止 LLM 变慢时请求越过边缘代理（如 Cloudflare 100s）",
    "diagnose_llm_model": "深度诊断 Agent 独立模型名（留空继承全局 llm_model）",
    "diagnose_llm_timeout_seconds": "深度诊断 Agent 单次 LLM 超时（秒，10~300；留空继承全局 llm_timeout_seconds）",
    "kb_governance_llm_model": "知识治理 Agent 独立模型名（留空继承全局 llm_model）",
    "kb_governance_temperature": "知识治理 Agent 采样温度（0~2；留空或非法继承全局 llm_temperature）",
    "kb_governance_llm_timeout_seconds": "知识治理 Agent 单次 LLM 超时（秒，10~300；留空继承全局 llm_timeout_seconds）",
    "oncall_llm_model": "值班 Agent 独立模型名（留空继承全局 llm_model）",
    "oncall_temperature": "值班 Agent 采样温度（0~2；留空或非法继承全局 llm_temperature）",
    "oncall_llm_timeout_seconds": "值班 Agent 单次 LLM 超时（秒，10~300；留空继承全局 llm_timeout_seconds）",
    "diagnose_llm_provider": "深度诊断 Agent 独立 LLM 接入方式（atoms_hub / openai_compatible；留空继承全局 llm_provider）",
    "diagnose_llm_base_url": "深度诊断 Agent 独立 OpenAI 兼容 Base URL（留空继承全局 llm_base_url）",
    "diagnose_llm_api_key": "深度诊断 Agent 独立 API Key（加密存储、脱敏展示；留空继承全局 llm_api_key）",
    "kb_governance_llm_provider": "知识治理 Agent 独立 LLM 接入方式（atoms_hub / openai_compatible；留空继承全局 llm_provider）",
    "kb_governance_llm_base_url": "知识治理 Agent 独立 OpenAI 兼容 Base URL（留空继承全局 llm_base_url）",
    "kb_governance_llm_api_key": "知识治理 Agent 独立 API Key（加密存储、脱敏展示；留空继承全局 llm_api_key）",
    "oncall_llm_provider": "值班 Agent 独立 LLM 接入方式（atoms_hub / openai_compatible；留空继承全局 llm_provider）",
    "oncall_llm_base_url": "值班 Agent 独立 OpenAI 兼容 Base URL（留空继承全局 llm_base_url）",
    "oncall_llm_api_key": "值班 Agent 独立 API Key（加密存储、脱敏展示；留空继承全局 llm_api_key）",
    "embedding_base_url": "Embedding Base URL（缺省回退 llm_base_url）",
    "embedding_api_key": "Embedding API Key（加密存储、脱敏展示；缺省回退 llm_api_key）",
    "embedding_model": "Embedding 模型名称（配置后启用诊断 RAG 语义加分，如 bge-m3）",
    "rag_base_url": "RAG 检索服务 Base URL（如 http://rag:8080；留空表示未部署，知识索引同步自动跳过）",
    "rag_api_key": "RAG 服务间鉴权 API Key（RAG 启用 RAG_API_KEYS_JSON 时必填；服务端加密存储、脱敏展示；留空表示 RAG 侧鉴权关闭）",
    "notify_webhook_url": "诊断后通知推送 Webhook 地址（如 ITSM 工单系统；须以 http:// 或 https:// 开头；留空表示不推送）",
    "notify_webhook_token": "通知推送鉴权 Token（以 Authorization: Bearer 头随通知一并携带；加密存储、脱敏展示；留空表示不携带鉴权头）",
    "event_ingest_token": "事件同步入口鉴权 Token（外部系统 POST /api/v1/ingest/alerts 须携带 X-Ingest-Token 头精确匹配；加密存储、脱敏展示；留空表示不鉴权放行，与 RAG fail-open 口径一致）",
    "llm_local_base_url": "本地 LLM OpenAI 兼容 Base URL（Ollama 如 http://ollama:11434/v1、vLLM 如 http://vllm:8000/v1；留空表示未部署本地 LLM，敏感数据诊断降级为确定性结论）",
    "llm_local_model": "本地 LLM 模型名称（如 qwen2.5:14b、deepseek-r1:14b；与 llm_local_base_url 同时配置后本地路由可用）",
    "llm_local_api_key": "本地 LLM API Key（Ollama/vLLM 通常留空即可；加密存储、脱敏展示；留空表示匿名访问本地网关）",
    "llm_routing_policy": "LLM 混合路由策略：auto（按敏感度/服务等级/告警等级三维决策，默认保守走本地）/ local_only（全部走本地，未部署本地 LLM 时降级确定性结论）/ remote_only（全部走远程，敏感数据仍强制本地）",
    "llm_remote_approval_id": "远程 LLM 数据出域合规审批编号（留空时远程路由被拒绝，一律走本地或确定性降级；用于审计追溯的数据出域受理凭证）",
    "data_masking_enabled": "入站数据脱敏开关（true/false，默认 true）：开启后事件同步入口对银行卡号/手机号/身份证/内网 IP/密钥赋值/Bearer Token/邮箱/长令牌掩码后再落库，原始内容不落库；脱敏判定同时作为 LLM 路由敏感度依据",
    "kb_expire_days": "知识老化归档天数（生命周期巡检：老化且负反馈的活跃案例归档淘汰）",
    "kb_expire_unconditional_days": "知识无条件老化天数（健康报表红级阈值；0 或留空 = 2×kb_expire_days）",
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


# ------------------ 合规审计哈希链（评审：LLM 调用合规审计留痕） ------------------
# 仅对合规链动作（llm_route_decision / llm_invocation）建立 prev_hash/hash 链，
# 复用现有 audit_logs 表（链字段存于 after_json.chain），不改表结构。
# hash = sha256(prev_hash | actor | action | target_type | target_id | after 规范化 JSON)，
# 覆盖审计内容本身；篡改任何一条记录的 after_json 都会导致整条链校验失败。

COMPLIANCE_CHAIN_ACTIONS = ("llm_route_decision", "llm_invocation")


def compute_chain_hash(
    prev_hash: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: Any,
    after: Optional[Dict[str, Any]],
) -> str:
    """计算审计链哈希：after 为不含 chain 块的业务字段字典（规范化排序后参与哈希）。"""
    payload = json.dumps(after or {}, ensure_ascii=False, sort_keys=True, default=str)
    raw = "|".join(
        [prev_hash or "GENESIS", actor or "system", action, target_type, str(target_id), payload]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_chain_hash(after_json: Optional[str]) -> str:
    """从既有审计行的 after_json 中读取链哈希（解析失败返回空串）。"""
    if not after_json:
        return ""
    try:
        data = json.loads(after_json)
    except (TypeError, ValueError):
        return ""
    if isinstance(data, dict):
        return str((data.get("chain") or {}).get("hash") or "")
    return ""


async def write_audit_chained(
    db: AsyncSession,
    actor: str,
    action: str,
    target_type: str,
    target_id: Any,
    after: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入带哈希链的合规审计记录（独立提交，语义同 write_audit）。

    prev_hash 取合规链动作的最近一条记录哈希；返回本次链信息（prev_hash/hash）
    供调用方复核。after 中不建议自带 chain 键（写入时会被覆盖）。
    """
    result = await db.execute(
        select(Audit_logs)
        .where(Audit_logs.action.in_(COMPLIANCE_CHAIN_ACTIONS))
        .order_by(Audit_logs.id.desc())
        .limit(1)
    )
    prev_row = result.scalar_one_or_none()
    prev_hash = _read_chain_hash(prev_row.after_json) if prev_row is not None else ""
    chain_after: Dict[str, Any] = dict(after or {})
    chain_after.pop("chain", None)
    hash_value = compute_chain_hash(prev_hash, actor, action, target_type, target_id, chain_after)
    chain_after["chain"] = {"prev_hash": prev_hash or None, "hash": hash_value}
    entry = Audit_logs(
        actor=actor or "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        after_json=json.dumps(chain_after, ensure_ascii=False),
    )
    db.add(entry)
    await db.commit()
    return {"prev_hash": prev_hash or None, "hash": hash_value}


async def verify_compliance_chain(db: AsyncSession) -> Dict[str, Any]:
    """校验合规审计哈希链完整性：逐条重算哈希并比对 prev_hash 连接。

    返回 {"ok": bool, "total": int, "head_hash": str, "broken_id": Optional[int]}。
    """
    result = await db.execute(
        select(Audit_logs)
        .where(Audit_logs.action.in_(COMPLIANCE_CHAIN_ACTIONS))
        .order_by(Audit_logs.id.asc())
    )
    rows = list(result.scalars().all())
    prev_hash = ""
    for row in rows:
        try:
            data = json.loads(row.after_json or "{}")
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        chain = data.get("chain") or {}
        recomputed = compute_chain_hash(
            prev_hash,
            row.actor,
            row.action,
            row.target_type,
            row.target_id,
            {k: v for k, v in data.items() if k != "chain"},
        )
        if chain.get("hash") != recomputed or (chain.get("prev_hash") or "") != prev_hash:
            return {"ok": False, "total": len(rows), "head_hash": prev_hash, "broken_id": row.id}
        prev_hash = str(chain.get("hash") or "")
    return {"ok": True, "total": len(rows), "head_hash": prev_hash, "broken_id": None}


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


# ------------------ 入站数据脱敏管道（评审：数据分级与脱敏） ------------------
# 覆盖类别：身份证 / 手机号 / 银行卡（15~19 位连续数字）/ 内网 IP / 密钥赋值 /
# Bearer Token / 邮箱 / 长令牌。掩码占位符本身保留可识别格式（如 138****5678、
# 10.1.2.x），便于 detect_sensitivity 对已落库文本二次判定敏感度并驱动 LLM 路由。

_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{15,19}(?!\d)")
_PRIVATE_IP_RE = re.compile(
    r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)
_MASKED_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){2}\.x\b", re.IGNORECASE)


def mask_alert_text(text: Optional[str]) -> Optional[str]:
    """入站告警文本脱敏：先个人证件/号码/内网 IP，再凭据类（复用 mask_sensitive 规则）。

    顺序敏感：身份证（18 位含校验位）先于银行卡（15~19 位）匹配，避免纯数字
    身份证被当作卡号掩码；手机号 11 位与卡号区间天然不重叠。Bearer 令牌先于
    密钥赋值规则掩码：避免 "Authorization: Bearer <token>" 的令牌值被键值对
    规则吞掉键名后以明文残留。
    """
    if not text:
        return text
    masked = _ID_CARD_RE.sub(lambda m: f"{m.group(0)[:3]}****{m.group(0)[-4:]}", text)
    masked = _PHONE_RE.sub(lambda m: f"{m.group(0)[:3]}****{m.group(0)[-4:]}", masked)
    masked = _BANK_CARD_RE.sub(lambda m: f"{m.group(0)[:4]}****{m.group(0)[-4:]}", masked)
    masked = _PRIVATE_IP_RE.sub(lambda m: ".".join(m.group(0).split(".")[:3]) + ".x", masked)
    masked = _BEARER_RE.sub("Bearer ***", masked)
    return mask_sensitive(masked) or masked


def detect_sensitivity(text: Optional[str]) -> Tuple[bool, List[str]]:
    """检测文本敏感度：命中返回 (True, 类别列表)。

    两层判定：
    - 原始敏感模式（密钥赋值 / Bearer / 内网 IP / 手机号 / 身份证 / 15~19 位数字 /
      邮箱 / 长令牌）：用于脱敏开关关闭时落库原文的场景兜底；
    - 掩码占位模式（****、a.b.c.x、***@***）：用于脱敏后落库文本的二次判定。
    任一命中即视为敏感，作为混合 LLM 路由「敏感数据强制本地」的依据。
    """
    if not text:
        return False, []
    hits: List[str] = []
    if _SENSITIVE_KEY_RE.search(text):
        hits.append("credential_assignment")
    if _BEARER_RE.search(text):
        hits.append("bearer_token")
    if _PRIVATE_IP_RE.search(text) or _MASKED_IP_RE.search(text):
        hits.append("internal_ip")
    if _PHONE_RE.search(text):
        hits.append("phone_number")
    if _ID_CARD_RE.search(text):
        hits.append("id_card")
    if _BANK_CARD_RE.search(text):
        hits.append("bank_card_or_long_digits")
    if _EMAIL_RE.search(text):
        hits.append("email")
    if _LONG_TOKEN_RE.search(text):
        hits.append("long_token")
    if "****" in text or "***@***" in text:
        hits.append("masked_placeholder")
    return (bool(hits), sorted(set(hits)))


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
    if key == "diagnose_time_budget_seconds":
        try:
            num = float(value)
        except ValueError:
            return False, "diagnose_time_budget_seconds 必须是数字"
        if not (30 <= num <= 600):
            return False, "diagnose_time_budget_seconds 必须在 30~600 之间"
        return True, "ok"
    if key.endswith("_llm_model") and key[: -len("_llm_model")] in AGENT_CONFIG_SCOPES:
        # 留空表示继承全局 llm_model；含脱敏占位符说明是回显值被误提交
        if "****" in value:
            return False, f"{key} 配置值无效（请输入完整模型名或留空继承全局）"
        return True, "ok"
    if key in AGENT_TEMPERATURE_KEYS:
        if not value.strip():
            return True, "ok"  # 留空继承全局 llm_temperature
        try:
            num = float(value)
        except ValueError:
            return False, f"{key} 必须是数字"
        if not (0 <= num <= 2):
            return False, f"{key} 必须在 0~2 之间"
        return True, "ok"
    if key in AGENT_TIMEOUT_KEYS:
        if not value.strip():
            return True, "ok"  # 留空继承全局 llm_timeout_seconds
        try:
            num = int(value)
        except ValueError:
            return False, f"{key} 必须是整数"
        if not (10 <= num <= 300):
            return False, f"{key} 必须在 10~300 之间"
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
    if key in AGENT_PROVIDER_KEYS:
        if value and value not in ("atoms_hub", "openai_compatible"):
            return False, f"{key} 仅支持 atoms_hub / openai_compatible（或留空继承全局）"
        return True, "ok"
    if key in AGENT_BASE_URL_KEYS:
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            return False, f"{key} 必须以 http:// 或 https:// 开头（或留空继承全局）"
        return True, "ok"
    if key in AGENT_ACCESS_API_KEY_KEYS:
        if "****" in value:
            return False, f"{key} 展示为脱敏格式，请输入完整 API Key（或留空继承全局）"
        return True, "ok"
    if key in ("llm_api_key", "embedding_api_key", "rag_api_key"):
        if "****" in value:
            return False, f"{key} 展示为脱敏格式，请输入完整 API Key（或留空清除）"
        return True, "ok"
    if key == "notify_webhook_url":
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            return False, "notify_webhook_url 必须以 http:// 或 https:// 开头（或留空表示不推送）"
        return True, "ok"
    if key in ("notify_webhook_token", "event_ingest_token"):
        if "****" in value:
            return False, f"{key} 展示为脱敏格式，请输入完整 Token（或留空清除）"
        return True, "ok"
    if key == "llm_routing_policy":
        if value not in ("auto", "local_only", "remote_only"):
            return False, "llm_routing_policy 仅支持 auto / local_only / remote_only"
        return True, "ok"
    if key == "llm_local_base_url":
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            return False, "llm_local_base_url 必须以 http:// 或 https:// 开头（或留空）"
        return True, "ok"
    if key in ("llm_local_model", "llm_remote_approval_id"):
        if "****" in value:
            return False, f"{key} 配置值无效（请输入完整内容或留空）"
        return True, "ok"
    if key == "llm_local_api_key":
        if "****" in value:
            return False, "llm_local_api_key 展示为脱敏格式，请输入完整 API Key（或留空清除）"
        return True, "ok"
    if key == "data_masking_enabled":
        if value not in ("true", "false"):
            return False, "data_masking_enabled 仅支持 true / false"
        return True, "ok"
    if key == "llm_model":
        if not value.strip() or "****" in value:
            return False, "llm_model 必须是有效的模型名称"
        return True, "ok"
    if key in ("llm_temperature", "diagnose_temperature"):
        try:
            num = float(value)
        except ValueError:
            return False, f"{key} 必须是数字"
        if not (0 <= num <= 2):
            return False, f"{key} 必须在 0~2 之间"
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
