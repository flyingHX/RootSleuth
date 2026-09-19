"""混合 LLM 分级路由层（评审采纳项）：敏感度 / 告警等级 / 服务等级三维决策。

红线语义（不可降级）：
- 敏感数据（身份证 / 手机号 / 银行卡 / 内网 IP / 密钥 / 令牌等，由
  detect_sensitivity 判定，含脱敏占位符二次识别）一律强制本地 LLM，
  绝不送远程；
- 本地 LLM 不可用且数据敏感时，调用方必须降级为确定性结论（知识库候选
  直接构造），不得回退远程；
- llm_remote_approval_id 留空时拒绝远程路由（远程处理的合规审批编号缺失
  视为未授权，双保险兜底回退本地）。

三维决策优先级（policy=auto）：
1. 数据敏感度：敏感 → local；
2. 告警等级：severity=critical → local（保守处理）；
3. 服务等级：CMDB environment=prod/production → local；
4. 其余 → remote，但需 llm_remote_approval_id 非空，否则回退 local。

路由范围仅覆盖诊断链路（单轮 run_diagnosis 与深度 Agent run_diagnose_agent
及其降级路径）；知识治理 / 值班 Agent 输入不含原始告警敏感文本，不经过本层。

每次路由决策经 write_audit_chained 写入合规审计哈希链
（action=llm_route_decision），与 LLM 调用审计（llm_invocation）共同构成
可追溯、防篡改的合规证据链；审计 after 仅记录敏感类别标签，不含原始文本。
"""
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.Events import Events
from models.cmdb_assets import Cmdb_assets
from services.console_common import (
    detect_sensitivity,
    get_config,
    write_audit_chained,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from models.Events import Events as EventsModel

logger = logging.getLogger(__name__)

# 生产环境标识（服务等级保守判定：命中任一即视为生产高等级）
_PROD_ENVIRONMENTS = ("prod", "production")
# 默认路由策略（与 CONFIG_DEFAULTS.llm_routing_policy 保持一致）
DEFAULT_ROUTING_POLICY = "auto"
# 合法策略集合（与 validate_config_value 保持一致）
ROUTING_POLICIES = ("auto", "local_only", "remote_only")


async def _resolve_service_tier(db: AsyncSession, service_name: Optional[str]) -> Optional[str]:
    """服务等级解析：按 service_name 关联 CMDB 资产的 environment 字段。

    同名服务多条资产时任一为生产即视为 prod；无 CMDB 记录返回 None
    （路由决策按"未知等级"处理，仅敏感/告警等级维度生效）。
    """
    if not (service_name or "").strip():
        return None
    try:
        result = await db.execute(
            select(Cmdb_assets.environment).where(Cmdb_assets.service_name == service_name)
        )
        rows = list(result.all())
    except Exception as exc:  # noqa: BLE001 - CMDB 不可用时路由决策不得失败
        logger.warning("CMDB 服务等级查询失败（按未知等级处理）: %s", exc)
        return None
    environments = [str(row[0] or "").strip().lower() for row in rows if row and row[0]]
    if not environments:
        return None
    if any(env in _PROD_ENVIRONMENTS for env in environments):
        return "prod"
    return environments[0]


async def decide_llm_route(db: AsyncSession, event: "EventsModel") -> Dict[str, Any]:
    """对单条事件执行混合 LLM 路由决策，返回决策结果并写入合规审计哈希链。

    返回结构：
    {
        "route": "local" | "remote",
        "policy": "auto" | "local_only" | "remote_only",
        "reasons": [决策依据（中文，按命中顺序）],
        "sensitive": bool,
        "sensitivity_categories": [敏感类别标签],
        "severity": 事件告警等级,
        "service_name": 服务名,
        "service_tier": CMDB 环境等级（prod/.../None）,
        "approval_present": bool（远程审批编号是否已配置）,
        "local_configured": bool（本地 LLM 是否已配置）,
    }

    调用方约定：route="local" 时 llm_chat 传 route="local"；远程调用前必须
    校验 approval_present 与 local_configured 之外的红线由本函数保证——敏感
    数据在本函数内已强制 local，调用方不得覆盖路由结果。
    """
    policy = ((await get_config(db, "llm_routing_policy", DEFAULT_ROUTING_POLICY)) or "").strip().lower()
    if policy not in ROUTING_POLICIES:
        policy = DEFAULT_ROUTING_POLICY
    approval_id = ((await get_config(db, "llm_remote_approval_id", "")) or "").strip()
    local_base = ((await get_config(db, "llm_local_base_url", "")) or "").strip()
    local_model = ((await get_config(db, "llm_local_model", "")) or "").strip()
    local_configured = bool(local_base and local_model)

    sensitive, categories = detect_sensitivity(f"{event.raw_log or ''}\n{event.template or ''}")
    severity = (event.severity or "").strip().lower()
    service_tier = await _resolve_service_tier(db, event.service_name)

    reasons: List[str] = []
    route = "remote"
    if policy == "local_only":
        route = "local"
        reasons.append("policy=local_only：策略强制本地处理")
    elif policy == "remote_only":
        if sensitive:
            route = "local"
            reasons.append("policy=remote_only，但敏感数据红线优先：强制本地，绝不送远程")
        else:
            route = "remote"
            reasons.append("policy=remote_only：策略允许远程处理")
    else:  # auto 三维决策
        if sensitive:
            route = "local"
            reasons.append("auto：数据敏感（" + "、".join(categories) + "）→ 强制本地，绝不送远程")
        elif severity == "critical":
            route = "local"
            reasons.append("auto：告警等级 critical → 本地保守处理")
        elif service_tier in _PROD_ENVIRONMENTS:
            route = "local"
            reasons.append("auto：CMDB 服务等级为生产环境 → 本地保守处理")
        elif not approval_id:
            route = "local"
            reasons.append("auto：llm_remote_approval_id 留空，远程处理未获审批授权 → 回退本地")
        else:
            route = "remote"
            reasons.append("auto：非敏感 / 非 critical / 非生产环境，且远程审批编号已配置 → 远程")

    # 双保险：任何策略下，远程路由都必须持有审批编号（红线兜底）
    if route == "remote" and not approval_id:
        route = "local"
        reasons.append("红线兜底：远程审批编号缺失，远程路由回退本地")

    if route == "local" and not local_configured and not sensitive:
        # 本地未配置但决策为本地：仍返回 local（调用方 llm_chat 抛 RuntimeError
        # 后走既有降级链路）；敏感场景由调用方降级确定性结论，不回退远程。
        reasons.append("提示：本地 LLM 未配置（llm_local_base_url / llm_local_model），调用将失败并走降级")

    decision: Dict[str, Any] = {
        "route": route,
        "policy": policy,
        "reasons": reasons,
        "sensitive": sensitive,
        "sensitivity_categories": categories,
        "severity": event.severity,
        "service_name": event.service_name,
        "service_tier": service_tier,
        "approval_present": bool(approval_id),
        "local_configured": local_configured,
    }

    try:
        await write_audit_chained(
            db,
            actor="llm_routing",
            action="llm_route_decision",
            target_type="event",
            target_id=event.id,
            after=decision,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不阻断诊断主链路
        logger.warning("LLM 路由决策审计写入失败: %s", exc)
    return decision


def build_deterministic_diagnosis(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """敏感数据本地 LLM 不可用时的确定性结论：直接复用知识库候选构造。

    绝不回退远程（红线）；confidence 取候选匹配分（无 LLM 把关，不再放大），
    供调用方持久化并标记 degraded_reason=sensitive_local_unavailable_deterministic。
    候选为空时返回 None（调用方走 unknown / 既有降级）。
    """
    if not candidates:
        return None
    top = candidates[0]
    root_cause = str(top.get("root_cause") or "").strip()
    solution = str(top.get("solution") or "").strip()
    if not root_cause and not solution:
        return None
    case_id = str(top.get("case_id") or "").strip()
    prefix = f"（敏感数据不出域·确定性结论，匹配知识案例 {case_id}）" if case_id else "（敏感数据不出域·确定性结论）"
    return {
        "root_cause": f"{prefix} {root_cause}".strip(),
        "solution": solution or "请人工根据匹配案例处置，敏感告警未送远程 LLM 分析。",
        "confidence": float(top.get("score") or 0.0),
        "command": "",
    }
