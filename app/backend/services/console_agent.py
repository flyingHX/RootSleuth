"""Agent 引擎与三个业务 Agent（诊断 / 知识治理 / 值班）。

通用 ReAct 循环（诊断 Agent 使用）：
- 每轮要求模型输出严格 JSON：{"thought", "action": {tool, args}} 或 {"thought", "finish", "result"}
- 工具观察以 OBSERVATION 消息回填，最多 MAX_ITERATIONS 轮，超限触发强制收尾
- 全部工具调用轨迹与最终结论持久化到 agent_sessions，动作写审计

业务 Agent：
- run_diagnose_agent：告警 → 主机/日志路径 → CMDB（系统/服务/负责人）→ 日志/规则/知识库 → 根因结论
- run_kb_governance_agent：告警簇聚类 → AI 起草知识案例（create 变更集走审批）→ 合并提案
- run_oncall_agent：时间窗影响面汇总（CMDB 关联）→ AI 生成 ChatOps 处置报告 → oncall_reports

降级策略（可用性优先）：
- diagnose：Agent 失败回退既有单轮诊断 run_diagnosis
- kb_governance：AI 起草失败仅保留聚类统计与合并提案
- oncall：AI 失败生成确定性统计报告
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.Events import Events
from models.agent_sessions import Agent_sessions
from models.cmdb_assets import Cmdb_assets
from models.kb_cases import Kb_cases
from models.kb_change_sets import Kb_change_sets
from models.kb_merge_proposals import Kb_merge_proposals
from models.oncall_reports import Oncall_reports
from models.rule_versions import Rule_versions
from schemas.aihub import ChatMessage
from schemas.auth import UserResponse
from services import console_kb, llm_runtime
from services.console_ai import (
    extract_json_payload,
    run_diagnosis,
    score_case,
)
from services.console_common import get_config, mask_sensitive, now_iso, write_audit
from services.quality_scan import GATE_LINE, evaluate_diagnosis_quality

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
FORMAT_RETRY_BUDGET = 2  # 非法 JSON 纠正独立预算（不计入推理轮次，评审 P1-5）
KB_SEARCH_SCAN_LIMIT = 200  # search_kb 单次扫描上限（评审 P0-1：避免全量加载）
EVIDENCE_TOKEN_OVERLAP = 0.6  # 证据交叉校验词元重合阈值（评审 P0-2）
SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}
WINDOW_DELTAS_HOURS = {"1h": 1, "24h": 24, "7d": 168}
ONCALL_PRIORITIES = {"P0", "P1", "P2", "P3"}

DIAGNOSE_AGENT_SYSTEM_PROMPT = """你是 AIOps 诊断 Agent。你只能通过工具获取信息，必须多轮取证后再下结论。

可用工具：
1. get_alert_detail - args: {}。返回告警详情：主机线索（extracted_ips / extracted_host_tokens）、服务、集群、模板、原始日志、拓扑，以及既有单轮诊断结论（若有）。
2. query_cmdb - args: {"ip"?: "...", "hostname"?: "...", "service_name"?: "..."}。返回 CMDB 资产：所属系统、服务、集群、负责人、依赖组件、日志路径。
3. read_recent_alert_samples - args: {"service_name"?: "...", "limit"?: 10}。返回该服务最近的告警日志样本（数据源为告警事件库，非完整日志文件）。
4. query_rules - args: {"keywords"?: ["..."]}。返回当前激活分类规则（可按关键词过滤）。
5. search_kb - args: {"error_type"?: "...", "service_name"?: "..."}。返回知识库相似案例（RAG 向量召回 + 四层重排，不可用时降级本地检索；优先带 error_type / service_name 收窄范围）。
6. 并行取证：相互独立的工具可在同一轮一次性声明（最多 3 个）：{"thought": "...", "action": {"tools": [{"tool": "query_cmdb", "args": {...}}, {"tool": "query_rules", "args": {}}]}}。

输出要求（每轮只输出一个 JSON 对象，禁止 Markdown 或多余文本）：
{"thought": "本轮推理与下一步计划", "action": {"tool": "工具名", "args": {...}}}
信息足够后输出最终结论：
{"thought": "结论推理摘要", "finish": true, "result": {"root_cause": "根因结论（中文）", "confidence": 0.0~1.0, "evidence_chain": ["证据1", "证据2"], "solution": "处置建议（中文，分步骤）", "command": "可直接执行的处置命令，无则为空字符串"}}

推理纪律：
- 第一轮先调用 get_alert_detail；
- 需要主机归属（系统/服务/负责人/日志路径）时调用 query_cmdb：优先用告警中的 IP/主机名，否则用 service_name；
- 结论必须给出至少 2 条证据链（来自工具观察）；
- 不要编造工具未返回的事实。"""

KB_DRAFT_SYSTEM_PROMPT = """你是知识治理 Agent。基于给定告警簇统计，起草新的知识案例草稿。

输出严格 JSON（禁止 Markdown）：
{"analysis": "对告警簇的整体分析（中文，1~3 句）", "drafts": [{"error_type": "分类（snake_case）", "service_name": "服务名（使用簇内真实服务）", "cluster": "集群（可选）", "alert_template": "告警模板（用簇模板原文）", "root_cause": "归纳根因（中文）", "solution": "处置建议（中文，分步骤）", "topology_snapshot": "拓扑（可选）", "reason": "起草理由（中文）"}]}

要求：
- 最多 3 条草稿，只对信息足以归纳根因的簇起草；不足以归纳的簇不要输出；
- error_type 优先参考簇内已知分类；服务名必须来自簇内真实服务；
- root_cause / solution 必须结合模板、服务、日志样本给出，不得空洞，且每条控制在 120 字以内；
- 只输出一个完整闭合的 JSON 对象，禁止输出被截断的内容。"""

ONCALL_SYSTEM_PROMPT = """你是值班 Agent。基于给定时间窗内的告警统计与 CMDB 影响面，生成值班 ChatOps 报告。

输出严格 JSON（禁止 Markdown）：
{"impact_summary": "影响面摘要（中文，2~4 句）", "priority": "P0/P1/P2/P3", "actions": ["分步处置动作（按优先级排序）"], "owners_to_notify": ["需要通知的负责人"], "chatops_text": "可直接粘贴到 ChatOps 群的消息文本（多行，含影响摘要、优先级、处置步骤、@负责人）"}

要求：
- 优先级判断：涉及 critical 且影响多个系统 → P0/P1；仅 warning → P2；仅 info → P3；
- 处置动作结合各系统负责人与已知知识库方案，必须具体可执行；
- chatops_text 使用纯文本，第一行必须以【值班告警汇总】开头，用换行与序号组织，@负责人 用邮箱前缀，全文控制在 600 字以内；
- 只输出一个完整闭合的 JSON 对象。"""


# ------------------ 序列化 ------------------

def _loads(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def ser_asset(asset: Cmdb_assets) -> Dict[str, Any]:
    return {
        "id": asset.id,
        "hostname": asset.hostname,
        "ip": asset.ip,
        "system_name": asset.system_name,
        "service_name": asset.service_name,
        "cluster": asset.cluster,
        "environment": asset.environment,
        "owner": asset.owner,
        "owner_email": asset.owner_email,
        "dependencies": _loads(asset.dependencies) or [],
        "log_path": asset.log_path,
        "status": asset.status,
        "description": asset.description,
    }


def ser_session(row: Agent_sessions) -> Dict[str, Any]:
    return {
        "id": row.id,
        "session_type": row.session_type,
        "event_id": row.event_id,
        "status": row.status,
        "model": row.model,
        "iterations": row.iterations,
        "duration_ms": row.duration_ms,
        "tool_trace": _loads(row.tool_trace),
        "result": _loads(row.result_json),
        "error_message": row.error_message,
        "actor": row.actor,
        "summary": row.summary,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def ser_oncall_report(row: Oncall_reports) -> Dict[str, Any]:
    return {
        "id": row.id,
        "time_window": row.time_window,
        "event_count": row.event_count,
        "critical_count": row.critical_count,
        "warning_count": row.warning_count,
        "info_count": row.info_count,
        "affected_systems": _loads(row.affected_systems),
        "report": _loads(row.report_json),
        "chatops_text": row.chatops_text,
        "session_id": row.session_id,
        "actor": row.actor,
        "created_at": str(row.created_at) if row.created_at else None,
    }


# ------------------ 基础设施 ------------------

async def _flush(db: AsyncSession) -> None:
    """调用慢速 AI 前结束当前数据库事务（会话边界规范）。"""
    await db.commit()


async def _llm_chat(
    db: AsyncSession,
    messages: List[ChatMessage],
    max_tokens: int = 1600,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
):
    """LLM Chat：模型/温度/接入方式由控制台配置中心驱动（llm_runtime）。

    temperature 显式传入时覆盖配置中心采样温度：诊断 Agent 固定零温采样，
    保证同一事件重复深度诊断的输出与质量指标稳定（波动治理）。
    timeout 显式传入时覆盖配置中心 llm_timeout_seconds：强制收尾阶段按剩余
    墙钟预算截断单次调用超时，避免收尾调用越过整体预算。
    """
    effective_timeout = timeout or int(await get_config(db, "llm_timeout_seconds", "45") or 45)
    return await llm_runtime.llm_chat(
        db, messages, max_tokens=max_tokens, timeout=effective_timeout, temperature=temperature
    )


async def _save_session(
    db: AsyncSession,
    *,
    session_type: str,
    status: str,
    model: str,
    event_id: Optional[int],
    result: Dict[str, Any],
    trace: List[Dict[str, Any]],
    iterations: int,
    elapsed_ms: float,
    actor: str,
    error_message: Optional[str] = None,
    summary: Optional[str] = None,
) -> Agent_sessions:
    row = Agent_sessions(
        session_type=session_type,
        event_id=event_id,
        status=status,
        model=model,
        iterations=iterations,
        duration_ms=round(elapsed_ms, 2),
        tool_trace=json.dumps(trace, ensure_ascii=False)[:20000],
        result_json=json.dumps(result, ensure_ascii=False)[:20000],
        error_message=(error_message or None)[:500] if error_message else None,
        actor=actor,
        summary=(summary or None)[:300] if summary else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ------------------ CMDB 查询 ------------------

def _cmdb_index(assets: List[Cmdb_assets]) -> Dict[str, Cmdb_assets]:
    index: Dict[str, Cmdb_assets] = {}
    for asset in assets:
        for key in (asset.ip, asset.hostname, asset.service_name):
            if key:
                index.setdefault(key, asset)
    return index


async def _load_cmdb(db: AsyncSession) -> List[Cmdb_assets]:
    result = await db.execute(select(Cmdb_assets))
    return list(result.scalars().all())


async def _search_cmdb(db: AsyncSession, key: str) -> List[Cmdb_assets]:
    result = await db.execute(
        select(Cmdb_assets).where(
            (Cmdb_assets.ip == key)
            | (Cmdb_assets.hostname == key)
            | (Cmdb_assets.service_name == key)
        )
    )
    exact = list(result.scalars().all())
    if exact:
        return exact
    like = f"%{key}%"
    result = await db.execute(
        select(Cmdb_assets).where(
            (Cmdb_assets.hostname.ilike(like))
            | (Cmdb_assets.service_name.ilike(like))
            | (Cmdb_assets.system_name.ilike(like))
        )
    )
    return list(result.scalars().all())


# ------------------ ReAct 循环 ------------------

ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


def _normalize_finish_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容两种 finish 形态：result 嵌套对象，或结论字段直接放在顶层。"""
    result = payload.get("result")
    if isinstance(result, dict) and str(result.get("root_cause") or "").strip():
        return result
    merged: Dict[str, Any] = dict(result) if isinstance(result, dict) else {}
    for key in ("root_cause", "solution", "confidence", "evidence_chain", "command"):
        if payload.get(key) is not None:
            merged.setdefault(key, payload.get(key))
    return merged


def _record_usage(
    trace: List[Dict[str, Any]],
    usage: Optional[Dict[str, Any]],
    acc: Dict[str, int],
) -> None:
    """记录单次 LLM 调用的 token 用量：追加轨迹明细并累加会话级汇总（评审 P2）。"""
    if not usage:
        return
    try:
        prompt_t = int(usage.get("prompt_tokens") or 0)
        completion_t = int(usage.get("completion_tokens") or 0)
        total_t = int(usage.get("total_tokens") or (prompt_t + completion_t))
    except (TypeError, ValueError):
        return
    if prompt_t <= 0 and completion_t <= 0 and total_t <= 0:
        return
    acc["prompt_tokens"] = acc.get("prompt_tokens", 0) + prompt_t
    acc["completion_tokens"] = acc.get("completion_tokens", 0) + completion_t
    acc["total_tokens"] = acc.get("total_tokens", 0) + total_t
    trace.append({"usage": {"prompt_tokens": prompt_t, "completion_tokens": completion_t, "total_tokens": total_t}})


def _summarize_usage(acc: Dict[str, int]) -> Optional[Dict[str, int]]:
    """会话级 token 汇总；全程无用量上报时返回 None。"""
    if not acc or not any(acc.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return None
    return {
        "prompt_tokens": acc.get("prompt_tokens", 0),
        "completion_tokens": acc.get("completion_tokens", 0),
        "total_tokens": acc.get("total_tokens", 0),
    }


async def _run_tool_call(tools: Dict[str, ToolHandler], call: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """执行单个工具调用；失败不抛出，返回 (tool_name, observation)。"""
    tool_name = str(call.get("tool") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    handler = tools.get(tool_name)
    if handler is None:
        return tool_name, {"error": f"未知工具 {tool_name}", "available": sorted(tools)}
    try:
        return tool_name, await handler(args)
    except Exception as exc:  # noqa: BLE001 - 单个工具失败不终止推理
        logger.warning("Agent tool %s failed: %s", tool_name, exc)
        return tool_name, {"error": f"工具执行失败: {type(exc).__name__}: {exc}"}


def _append_observation(messages: List[ChatMessage], tool_name: str, observation: Dict[str, Any]) -> None:
    """将工具观察回填为对话消息（超长截断，保持与旧版一致的 4000 字符上限）。"""
    observation_text = json.dumps(observation, ensure_ascii=False)[:4000]
    messages.append(
        ChatMessage(
            role="user",
            content=(
                f"OBSERVATION（工具 {tool_name} 返回）：\n{observation_text}\n\n"
                "请继续：若信息足够请输出 finish JSON，否则输出下一轮 action。"
            ),
        )
    )


def _tokens_overlap(text: str, observation_text: str) -> float:
    """轻量词元重合度：证据句与工具观察文本的词元交集占比（0~1）。"""
    stop = {"的", "了", "在", "是", "和", "与", "或", "及", "为", "有", "a", "an", "the", "of", "to", "in", "on", "and", "or"}

    def to_tokens(value: str) -> set:
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", str(value or "").lower())
        return {t for t in normalized.split() if len(t) > 1 and t not in stop}

    ev = to_tokens(text)
    if not ev:
        return 1.0
    return len(ev & to_tokens(observation_text)) / len(ev)


def _filter_evidence_by_trace(
    evidence: List[str],
    trace: List[Dict[str, Any]],
) -> Tuple[List[str], int]:
    """证据交叉校验（评审 P0-2）：仅保留能在工具轨迹观察中找到来源的证据。

    校验策略：证据句与全部工具观察拼接文本的词元重合度 ≥ EVIDENCE_TOKEN_OVERLAP 即视为有出处；
    全部证据都被过滤时放行原始证据（避免误杀真实结论），仅返回过滤计数供审计与前端提示。
    """
    observations: List[str] = []
    for step in trace:
        obs = step.get("observation")
        if isinstance(obs, dict):
            observations.append(json.dumps(obs, ensure_ascii=False))
        elif obs:
            observations.append(str(obs))
    if not observations:
        return evidence, 0
    blob = "\n".join(observations)
    kept = [e for e in evidence if _tokens_overlap(e, blob) >= EVIDENCE_TOKEN_OVERLAP]
    filtered = len(evidence) - len(kept)
    if not kept:
        return evidence, filtered
    return kept, filtered


async def _run_react(
    db: AsyncSession,
    system_prompt: str,
    task_prompt: str,
    tools: Dict[str, ToolHandler],
    temperature: Optional[float] = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """ReAct 多轮循环：模型输出动作 → 执行工具 → 回填观察，直至 finish 或超限。

    temperature 透传给全部 LLM 调用（含超限强制收尾），诊断 Agent 传 0 保证确定性。
    deadline 为墙钟时间预算的绝对时刻（perf_counter）：余量不足时停止继续推理转入
    强制收尾（trace 记录 budget_exceeded），整体耗时不再随 LLM 变慢无限叠加。
    """
    trace: List[Dict[str, Any]] = []
    messages: List[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=task_prompt),
    ]
    started = time.perf_counter()
    iterations = 0
    usage_acc: Dict[str, int] = {}

    format_retries_left = FORMAT_RETRY_BUDGET
    while iterations < MAX_ITERATIONS:
        # 墙钟时间预算（波动治理）：单次 LLM 调用有 timeout 上限，但多轮推理 × 强制收尾
        # 仍可能叠加到数百秒并触发边缘代理超时（如 Cloudflare 默认 100s）。余量不足预留
        # 时间即停止继续推理，转入强制收尾保证整体耗时有界。
        if deadline is not None and time.perf_counter() + DIAGNOSE_TIME_RESERVE_SECONDS > deadline:
            trace.append(
                {
                    "iteration": iterations,
                    "budget_exceeded": True,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
                }
            )
            break
        iterations += 1
        await _flush(db)
        response = await _llm_chat(db, messages, temperature=temperature)
        _record_usage(trace, getattr(response, "usage", None), usage_acc)
        payload = extract_json_payload(response.content)
        if payload is None:
            trace.append({"iteration": iterations, "error": "invalid_json", "raw": (response.content or "")[:400]})
            # 格式纠错走独立重试预算（评审 P1）：预算内不消耗推理轮次，避免轮次被格式错误耗尽
            if format_retries_left > 0:
                format_retries_left -= 1
                iterations -= 1
            messages = messages + [
                ChatMessage(role="user", content="上一轮输出不是合法 JSON，请严格按约定只输出一个 JSON 对象。"),
            ]
            continue

        if payload.get("finish"):
            return {
                "result": _normalize_finish_result(payload),
                "iterations": iterations,
                "trace": trace,
                "usage": usage_acc,
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }

        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        calls: List[Dict[str, Any]] = []
        single_tool = str(action.get("tool") or "")
        if single_tool:
            calls.append(
                {"tool": single_tool, "args": action.get("args") if isinstance(action.get("args"), dict) else {}}
            )
        for item in (action.get("tools") or []):  # 并行取证（评审 P1）：单轮最多 3 个独立工具
            if isinstance(item, dict) and str(item.get("tool") or ""):
                calls.append(
                    {
                        "tool": str(item["tool"]),
                        "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                    }
                )
        calls = calls[:3]

        step_trace: Dict[str, Any] = {"iteration": iterations, "thought": payload.get("thought")}
        if not calls:
            observation = {"error": "action 缺少 tool / tools 字段", "available": sorted(tools)}
            step_trace.update({"tool": "", "args": {}, "observation": observation})
        elif len(calls) == 1:
            tool_name, observation = await _run_tool_call(tools, calls[0])
            step_trace.update({"tool": tool_name, "args": calls[0]["args"], "observation": observation})
        else:
            tool_name = " + ".join(call["tool"] for call in calls)
            observations = list(await asyncio.gather(*(_run_tool_call(tools, call) for call in calls)))
            observation = {"parallel": [{"tool": name, "observation": obs} for name, obs in observations]}
            step_trace.update(
                {
                    "tool": tool_name,
                    "args": {call["tool"]: call["args"] for call in calls},
                    "observation": observation,
                }
            )
        trace.append(step_trace)
        messages = messages + [ChatMessage(role="assistant", content=json.dumps(payload, ensure_ascii=False))]
        _append_observation(messages, tool_name, observation)

    # 超限强制收尾：独立收尾函数（最多重试 2 次），避免单次输出抖动导致整体失败
    return await _conclude_react(
        db, messages, trace, iterations, started, usage_acc, temperature=temperature, deadline=deadline
    )


async def _conclude_react(
    db: AsyncSession,
    messages: List[ChatMessage],
    trace: List[Dict[str, Any]],
    iterations: int,
    started: float,
    usage_acc: Dict[str, int],
    temperature: Optional[float] = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """超限强制收尾：要求模型基于已有观察立即输出 finish JSON（最多重试 2 次）。

    deadline 传入时收尾阶段同样受墙钟预算约束：余量不足（< DIAGNOSE_MIN_CONCLUDE_SECONDS）
    抛出 agent_time_budget_exhausted 交由上层降级决策；每次收尾调用的 LLM 超时截断为
    剩余预算与配置超时的较小值，避免单次调用越过整体预算。
    """
    await _flush(db)
    try:
        cfg_timeout = int(await get_config(db, "llm_timeout_seconds", "45") or 45)
    except (TypeError, ValueError):
        cfg_timeout = 45
    final_messages = messages + [
        ChatMessage(
            role="user",
            content=(
                f"已达最大推理轮数（{MAX_ITERATIONS}）。请基于已获得的观察立即输出 finish JSON，给出当前最优结论。"
            ),
        )
    ]
    for _attempt in range(2):
        llm_timeout: Optional[int] = None
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining < DIAGNOSE_MIN_CONCLUDE_SECONDS:
                raise ValueError("agent_time_budget_exhausted")
            llm_timeout = max(DIAGNOSE_MIN_CONCLUDE_SECONDS, min(cfg_timeout, int(remaining)))
        response = await _llm_chat(db, final_messages, temperature=temperature, timeout=llm_timeout)
        _record_usage(trace, getattr(response, "usage", None), usage_acc)
        payload = extract_json_payload(response.content)
        if payload is not None and payload.get("finish"):
            return {
                "result": _normalize_finish_result(payload),
                "iterations": iterations + 1,
                "trace": trace,
                "usage": usage_acc,
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }
        final_messages = final_messages + [
            ChatMessage(role="assistant", content=(response.content or "")[:2000]),
            ChatMessage(
                role="user",
                content=(
                    "输出仍不符合要求。请只输出一个 JSON 对象，形如 "
                    '{"thought": "...", "finish": true, "result": {"root_cause": "...", "solution": "...", '
                    '"confidence": 0.0~1.0, "evidence_chain": ["..."], "command": ""}}'
                ),
            ),
        ]
    raise ValueError("agent_failed_to_conclude")


def _validate_diagnose_result(
    result: Dict[str, Any],
    trace: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """结论校验：字段完整性 + confidence 归一化（0~1 原生，兼容 0~100 / 0~10）+ 证据交叉校验。"""
    root_cause = str(result.get("root_cause") or "").strip()
    solution = str(result.get("solution") or "").strip()
    if not root_cause or not solution:
        return None
    try:
        confidence = float(result.get("confidence"))
    except (TypeError, ValueError):
        return None
    if confidence > 1.0 and confidence <= 100.0:
        # 评审 P2：百分制（>10）除以 100；10 分制（1~10] 除以 10，避免 9 分被误判 0.09
        confidence = confidence / (100.0 if confidence > 10.0 else 10.0)
    if not (0.0 <= confidence <= 1.0):
        return None
    evidence_raw = result.get("evidence_chain")
    evidence = [str(e).strip() for e in evidence_raw if str(e).strip()] if isinstance(evidence_raw, list) else []
    evidence_filtered = 0
    if evidence and trace:
        evidence, evidence_filtered = _filter_evidence_by_trace(evidence, trace)
    validated = {
        "root_cause": root_cause,
        "solution": solution,
        "confidence": round(confidence, 4),
        "evidence_chain": evidence[:10],
        "command": str(result.get("command") or "").strip(),
    }
    if evidence_filtered:
        validated["evidence_filtered_count"] = evidence_filtered
    return validated


async def _rag_kb_search(
    db: AsyncSession,
    error_type: str,
    service_name: str,
    template: str,
    top_k: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """调用 RAG 服务检索相似知识案例（评审 P0-1）。

    返回 None 表示 RAG 未配置或不可用，调用方应降级为本地 PostgreSQL 召回；
    正常返回案例列表（可能为空）。遵循全系统 fail-open 语义，异常不向上抛。
    """
    base = str(await get_config(db, "rag_base_url", "") or "").strip().rstrip("/")
    if not base:
        return None
    # P0-1 服务间鉴权：配置 rag_api_key 后以 X-API-Key 透传（RAG 侧鉴权关闭时忽略）
    api_key = str(await get_config(db, "rag_api_key", "") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base}/api/v1/kb-search",
                headers={"X-API-Key": api_key} if api_key else None,
                json={
                    "service_name": service_name,
                    "error_type": error_type,
                    "template": (template or "")[:500],
                    "top_k": top_k,
                },
            )
            resp.raise_for_status()
            cases = resp.json().get("cases")
        return cases if isinstance(cases, list) else None
    except Exception as exc:  # noqa: BLE001 - RAG 不可用时降级本地召回
        logger.warning("RAG kb-search failed, fallback to postgres: %s", exc)
        return None


# ------------------ 诊断 Agent（需求 a） ------------------

def _build_diagnose_tools(db: AsyncSession, event: Events) -> Dict[str, ToolHandler]:
    async def get_alert_detail(args: Dict[str, Any]) -> Dict[str, Any]:
        raw = event.raw_log or ""
        ips = sorted(set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)))
        host_tokens = sorted(
            {
                token
                for token in re.findall(r"[A-Za-z][A-Za-z0-9\-_]{2,}", f"{event.template or ''} {raw}")
                if re.search(r"(db|svc|gw|cache|redis|gateway|service|collector|proxy|node)", token, re.I)
            }
        )[:8]
        return {
            "event_id": event.event_id,
            "severity": event.severity,
            "service_name": event.service_name,
            "cluster": event.cluster,
            "error_type": event.error_type,
            "template": event.template,
            "raw_log": raw,
            "topology": event.topology,
            "status": event.status,
            "extracted_ips": ips,
            "extracted_host_tokens": host_tokens,
            "prior_single_round_diagnosis": _loads(event.ai_output_json),
            "hint": "如 extracted_ips 为空，请用 service_name 或主机线索调用 query_cmdb 获取所属系统、负责人与日志路径。",
        }

    async def query_cmdb(args: Dict[str, Any]) -> Dict[str, Any]:
        key = str(args.get("ip") or args.get("hostname") or args.get("service_name") or "").strip()
        if not key:
            return {"error": "query_cmdb 需要 ip / hostname / service_name 之一"}
        assets = await _search_cmdb(db, key)
        if not assets:
            return {"matches": [], "note": f"CMDB 未登记 {key}，请尝试 service_name 或其他主机线索"}
        return {"matches": [ser_asset(a) for a in assets[:5]]}

    async def read_recent_alert_samples(args: Dict[str, Any]) -> Dict[str, Any]:
        service = str(args.get("service_name") or "").strip()
        if not service:
            key = str(args.get("ip") or args.get("hostname") or "").strip()
            if key:
                assets = await _search_cmdb(db, key)
                service = assets[0].service_name if assets else ""
        if not service:
            return {"error": "read_recent_alert_samples 需要 service_name 或可解析到服务的主机线索"}
        limit = min(int(args.get("limit") or 10), 20)
        result = await db.execute(
            select(Events)
            .where(Events.service_name == service)
            .order_by(Events.id.desc())
            .limit(max(limit, 1) * 3)
        )
        lines = []
        seen = set()
        for row in result.scalars().all():
            if row.raw_log and row.raw_log not in seen:
                seen.add(row.raw_log)
                lines.append(
                    {
                        "event_id": row.event_id,
                        "severity": row.severity,
                        "log": mask_sensitive(row.raw_log),
                        "created_at": str(row.created_at) if row.created_at else None,
                    }
                )
            if len(lines) >= limit:
                break
        return {
            "service_name": service,
            "lines": lines,
            "note": "日志样本来自告警事件库最近记录；完整日志可按 CMDB log_path 上机核查。",
        }

    async def query_rules(args: Dict[str, Any]) -> Dict[str, Any]:
        result = await db.execute(
            select(Rule_versions).where(Rule_versions.status == "active").order_by(Rule_versions.version.desc()).limit(1)
        )
        active = result.scalar_one_or_none()
        if active is None:
            return {"rules": [], "note": "当前无激活规则版本"}
        docs = console_kb._extract_rule_docs(active.content)
        keywords = [str(k).lower() for k in (args.get("keywords") or []) if str(k).strip()]
        entries = []
        for doc in docs:
            text = json.dumps(doc, ensure_ascii=False).lower()
            if keywords and not any(k in text for k in keywords):
                continue
            entries.append(
                {
                    "id": doc.get("id"),
                    "error_type": doc.get("error_type"),
                    "score": doc.get("score"),
                    "severity": doc.get("severity"),
                    "keywords": doc.get("keywords"),
                }
            )
        return {"active_version": active.version, "rules": entries[:20]}

    async def search_kb(args: Dict[str, Any]) -> Dict[str, Any]:
        """知识检索（评审 P0-1）：优先 RAG 服务向量召回 + 四层重排，失败降级本地数据库限界扫描。"""
        error_type = str(args.get("error_type") or "").strip()
        service = str(args.get("service_name") or "").strip()
        rag_cases = await _rag_kb_search(db, error_type, service, event.template or "")
        if rag_cases is not None:
            return {"source": "rag", "cases": rag_cases[:5]}
        # 降级路径：PostgreSQL 限界召回（确定性排序，与固定评估上下文共用同一实现）
        return {
            "source": "postgres_fallback",
            "cases": await _local_kb_candidates(db, event, error_type, service),
        }

    return {
        "get_alert_detail": get_alert_detail,
        "query_cmdb": query_cmdb,
        "read_recent_alert_samples": read_recent_alert_samples,
        "query_rules": query_rules,
        "search_kb": search_kb,
    }


async def _diagnose_fallback(
    db: AsyncSession,
    event_id: int,
    actor: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """降级路径：复用既有单轮诊断（可能返回 unknown 结果或抛错）。"""
    try:
        fallback = await run_diagnosis(db, event_id, actor)
        return fallback, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _extract_rag_cases_from_trace(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从工具轨迹中收集 search_kb 观察到的召回案例（单次诊断质量评估的上下文来源）。"""
    cases: List[Dict[str, Any]] = []
    seen: set = set()

    def _collect(observation: Any) -> None:
        if not isinstance(observation, dict):
            return
        obs_cases = observation.get("cases")
        if isinstance(obs_cases, list):
            for case in obs_cases:
                if isinstance(case, dict) and case.get("case_id") and case["case_id"] not in seen:
                    seen.add(case["case_id"])
                    cases.append(case)
        parallel = observation.get("parallel")
        if isinstance(parallel, list):
            for item in parallel:
                if isinstance(item, dict):
                    _collect(item.get("observation"))

    for step in trace or []:
        if isinstance(step, dict):
            _collect(step.get("observation"))
    return cases


# ------------------ 诊断确定性（波动治理）：固定评估上下文 / 指纹 / 稳定排序 ------------------

DIAGNOSE_TOP_K = 5  # 本地确定性召回候选上限（与 RAG kb-search top_k 对齐）


def _parse_diagnose_temperature(raw: Optional[str]) -> float:
    """解析诊断采样温度：默认/空/非法回退 0（确定性优先），合法值截断到 [0, 2]。"""
    try:
        value = float(str(raw or "").strip())
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN 防御
        return 0.0
    return min(max(value, 0.0), 2.0)


async def _resolve_diagnose_temperature(db: AsyncSession) -> float:
    """读取配置中心 diagnose_temperature（默认 0）：诊断 Agent 全链路固定采样温度。"""
    raw = await get_config(db, "diagnose_temperature", "0")
    return _parse_diagnose_temperature(raw)


# 诊断墙钟时间预算（波动治理 P0）：单次 LLM 调用有 timeout 上限，但多轮 ReAct +
# 强制收尾仍可能叠加到数百秒并触发边缘代理（如 Cloudflare 默认 100s）502。预算约束
# 整体耗时：余量不足停止推理转入收尾，预算耗尽跳过 LLM 降级直接结构化报错。
DIAGNOSE_TIME_BUDGET_DEFAULT = 90.0
DIAGNOSE_TIME_BUDGET_MIN = 30.0
DIAGNOSE_TIME_BUDGET_MAX = 600.0
DIAGNOSE_TIME_RESERVE_SECONDS = 12.0
DIAGNOSE_MIN_CONCLUDE_SECONDS = 5.0
DIAGNOSE_MIN_FALLBACK_SECONDS = 15.0


def _parse_diagnose_time_budget(raw: Optional[str]) -> float:
    """解析诊断墙钟预算：默认/空/非法/NaN 回退 90 秒，合法值截断到 [30, 600]。"""
    try:
        value = float(str(raw or "").strip())
    except (TypeError, ValueError):
        return DIAGNOSE_TIME_BUDGET_DEFAULT
    if value != value:  # NaN 防御
        return DIAGNOSE_TIME_BUDGET_DEFAULT
    return min(max(value, DIAGNOSE_TIME_BUDGET_MIN), DIAGNOSE_TIME_BUDGET_MAX)


async def _resolve_diagnose_time_budget(db: AsyncSession) -> float:
    """读取配置中心 diagnose_time_budget_seconds（默认 90）：诊断整体墙钟预算。"""
    raw = await get_config(db, "diagnose_time_budget_seconds", str(int(DIAGNOSE_TIME_BUDGET_DEFAULT)))
    return _parse_diagnose_time_budget(raw)


def _rank_local_cases(
    cases: List[Kb_cases],
    event: Events,
    error_type: str,
    service_name: str,
) -> List[Dict[str, Any]]:
    """本地候选确定性召回与排序（search_kb 降级路径与固定评估上下文共用）。

    过滤条件固定（非 archived + error_type/service_name 精确匹配 + score>0），
    排序键 (score desc, case_id asc) 保证同分候选顺序跨次运行稳定。
    """
    scored: List[Tuple[float, Kb_cases]] = []
    for case in cases:
        if case.status == "archived":
            continue
        if error_type and case.error_type != error_type:
            continue
        if service_name and case.service_name != service_name:
            continue
        score = score_case(case, event)
        if score <= 0:
            continue
        scored.append((score, case))
    scored.sort(key=lambda item: (-item[0], str(item[1].case_id or "")))
    return [
        {
            "case_id": case.case_id,
            "error_type": case.error_type,
            "service_name": case.service_name,
            "alert_template": case.alert_template,
            "root_cause": case.root_cause,
            "solution": case.solution,
            "score": round(min(0.99, score / 6.0), 4),
        }
        for score, case in scored[:DIAGNOSE_TOP_K]
    ]


async def _local_kb_candidates(
    db: AsyncSession,
    event: Events,
    error_type: str,
    service_name: str,
) -> List[Dict[str, Any]]:
    """PostgreSQL 限界召回（确定性）：固定评估上下文与 search_kb 降级共用同一实现。"""
    result = await db.execute(
        select(Kb_cases)
        .where(Kb_cases.status != "archived")
        .order_by(Kb_cases.id.desc())
        .limit(KB_SEARCH_SCAN_LIMIT)
    )
    return _rank_local_cases(list(result.scalars().all()), event, error_type, service_name)


def _build_eval_context(
    local_candidates: List[Dict[str, Any]],
    rag_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """构建固定质量评估上下文：本地确定性候选 ∪ 本次 RAG 召回，按 case_id 去重后稳定排序。

    评估基准不再依赖"该次运行是否恰好调用 search_kb"：未调用 KB 工具时
    context_coverage 仍以固定本地候选为分母，保证同一事件重复诊断指标可比。
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for case in list(local_candidates) + list(rag_cases or []):
        if not isinstance(case, dict):
            continue
        key = str(case.get("case_id") or "")
        if not key or key in merged:
            continue
        merged[key] = case
    return [merged[key] for key in sorted(merged)]


def _context_fingerprint(cases: List[Dict[str, Any]]) -> str:
    """评估上下文指纹：case_id + 主要内容字段的顺序无关 SHA256（前 16 位）。"""
    material = "|".join(
        sorted(
            str(case.get("case_id") or "")
            + ":"
            + str(case.get("root_cause") or "")
            + ":"
            + str(case.get("solution") or "")
            for case in cases or []
            if isinstance(case, dict)
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _input_fingerprint(event: Events) -> str:
    """诊断输入指纹：事件关键字段 SHA256（前 16 位），比对重复诊断的输入是否一致。"""
    material = "|".join(
        str(field or "")
        for field in (
            event.event_id,
            event.template,
            event.error_type,
            event.service_name,
            event.cluster,
            event.severity,
            event.raw_log,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


async def _build_diagnose_snapshot(
    db: AsyncSession,
    event: Events,
    model_name: str,
    temperature: float,
    local_candidates: List[Dict[str, Any]],
    eval_context: List[Dict[str, Any]],
    time_budget: float,
) -> Dict[str, Any]:
    """诊断上下文快照（波动治理）：事件输入、知识候选、激活规则版本与模型参数落库。

    重复诊断时逐块比对 snapshot 即可定位波动来源：输入变化（event）/ 知识候选变化
    （kb_candidates）/ 规则或模型参数变化（rule_version、model_params）。
    敏感原文（raw_log 等）不入快照，仅记录 SHA256 指纹；候选只存 case_id 序列，控制体量。
    """
    rule_result = await db.execute(
        select(Rule_versions)
        .where(Rule_versions.status == "active")
        .order_by(Rule_versions.version.desc())
        .limit(1)
    )
    active_rule = rule_result.scalar_one_or_none()
    try:
        llm_timeout = int(await get_config(db, "llm_timeout_seconds", "45") or 45)
    except (TypeError, ValueError):
        llm_timeout = 45
    return {
        "event": {
            "event_id": event.event_id,
            "template": event.template,
            "error_type": event.error_type,
            "service_name": event.service_name,
            "cluster": event.cluster,
            "severity": event.severity,
            "raw_log_sha": hashlib.sha256(str(event.raw_log or "").encode("utf-8")).hexdigest()[:16],
        },
        "kb_candidates": {
            "local_ids": [str(c.get("case_id") or "") for c in local_candidates],
            "merged_ids": [str(c.get("case_id") or "") for c in eval_context],
        },
        "rule_version": active_rule.version if active_rule is not None else None,
        "model_params": {
            "model": model_name,
            "temperature": temperature,
            "timeout_seconds": llm_timeout,
            "time_budget_seconds": time_budget,
        },
    }


async def run_diagnose_agent(db: AsyncSession, user: UserResponse, event_id: int) -> Dict[str, Any]:
    """诊断 Agent：多轮工具调用推理给出根因结论，失败自动降级为单轮诊断。"""
    actor = user.email or user.id
    result_load = await db.execute(select(Events).where(Events.id == event_id))
    event = result_load.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")

    tools = _build_diagnose_tools(db, event)
    model_name = await llm_runtime.get_llm_model_name(db)
    # 波动治理：诊断 Agent 固定采样温度（diagnose_temperature，默认 0），重复诊断输出稳定
    diagnose_temperature = await _resolve_diagnose_temperature(db)
    task_prompt = (
        f"请诊断告警：event_id={event.event_id}（数据库主键 {event.id}）。\n"
        "建议流程：get_alert_detail →（按需）query_cmdb 确认主机所属系统/服务/负责人与日志路径 → "
        "read_recent_alert_samples / query_rules / search_kb 交叉验证 → finish 输出根因结论。\n"
        "证据足够时尽快输出 finish，不要为用满轮数而继续调用工具；结论 root_cause 与 solution 各控制在 150 字以内。"
    )
    started = time.perf_counter()
    loop_result: Optional[Dict[str, Any]] = None
    loop_error: Optional[str] = None
    # 墙钟时间预算（波动治理 P0）：多轮推理 + 强制收尾的总时长上限，超时自动降级
    time_budget = await _resolve_diagnose_time_budget(db)
    deadline = started + time_budget
    try:
        loop_result = await _run_react(
            db,
            DIAGNOSE_AGENT_SYSTEM_PROMPT,
            task_prompt,
            tools,
            temperature=diagnose_temperature,
            deadline=deadline,
        )
        conclusion = _validate_diagnose_result(loop_result["result"], loop_result["trace"])
        if conclusion is None:
            loop_error = "invalid_agent_conclusion"
            loop_result = None
    except Exception as exc:  # noqa: BLE001
        logger.error("Diagnose agent failed: %s", exc)
        loop_error = f"{type(exc).__name__}: {exc}"
        loop_result = None

    if loop_result is not None and loop_error is None:
        # conclusion 已在 try 块中经 _validate_diagnose_result(result, trace) 校验通过
        elapsed = loop_result["elapsed_ms"]
        threshold = float(await get_config(db, "confidence_threshold", "0.75") or 0.75)
        usage_summary = _summarize_usage(loop_result.get("usage") or {})
        # 重跑保留旧结论（评审 P0-3）：覆盖前把上一版结论落库为 superseded 会话
        previous_conclusion = {
            "root_cause": event.ai_root_cause,
            "solution": event.ai_solution,
            "confidence": event.confidence,
            "ai_output": _loads(event.ai_output_json),
        }
        if str(previous_conclusion.get("root_cause") or "").strip():
            await _save_session(
                db,
                session_type="diagnose",
                status="superseded",
                model=model_name,
                event_id=event.id,
                result={"previous_conclusion": previous_conclusion},
                trace=[],
                iterations=0,
                elapsed_ms=elapsed,
                actor=actor,
                summary="重跑诊断前保留的旧结论",
            )
        rag_cases = _extract_rag_cases_from_trace(loop_result["trace"])
        # 固定评估上下文（波动治理）：评估基准与"本次是否恰好调用 search_kb"解耦，
        # 本地确定性候选恒在分母中，同一事件重复诊断的质量指标才可比
        local_candidates = await _local_kb_candidates(
            db, event, event.error_type or "", event.service_name or ""
        )
        eval_context = _build_eval_context(local_candidates, rag_cases)
        snapshot = await _build_diagnose_snapshot(
            db, event, model_name, diagnose_temperature, local_candidates, eval_context, time_budget
        )
        stability = {
            "temperature": diagnose_temperature,
            "input_fingerprint": _input_fingerprint(event),
            "context_fingerprint": _context_fingerprint(eval_context),
            "eval_context": {
                "local_count": len(local_candidates),
                "rag_count": len(rag_cases),
                "merged_count": len(eval_context),
            },
            "snapshot": snapshot,
        }
        quality = evaluate_diagnosis_quality(
            {
                "template": event.template or "",
                "error_type": event.error_type or "",
                "service_name": event.service_name or "",
            },
            f"{conclusion['root_cause']}\n{conclusion['solution']}",
            eval_context,
        )
        event.ai_root_cause = conclusion["root_cause"]
        event.ai_solution = conclusion["solution"]
        event.ai_command = conclusion["command"] or None
        event.ai_output_json = json.dumps(
            {**conclusion, "agent": True, "quality": quality, "stability": stability}, ensure_ascii=False
        )
        event.confidence = conclusion["confidence"]
        event.status = "diagnosed"
        event.degraded_reason = None if conclusion["confidence"] >= threshold else f"low_confidence(<{threshold})"
        await db.commit()

        row = await _save_session(
            db,
            session_type="diagnose",
            status="succeeded",
            model=model_name,
            event_id=event.id,
            result={
                "conclusion": conclusion,
                "threshold": threshold,
                "usage": usage_summary,
                "quality": quality,
                "stability": stability,
                "rag": {"case_count": len(rag_cases), "kb_search_used": bool(rag_cases)},
            },
            trace=loop_result["trace"],
            iterations=loop_result["iterations"],
            elapsed_ms=elapsed,
            actor=actor,
            summary=conclusion["root_cause"][:300],
        )
        await write_audit(
            db,
            actor=actor,
            action="agent_diagnose",
            target_type="event",
            target_id=event.id,
            after={
                "session_id": row.id,
                "model": model_name,
                "iterations": loop_result["iterations"],
                "confidence": conclusion["confidence"],
                "usage": usage_summary,
                "low_confidence": conclusion["confidence"] < threshold,
                "trust_index": quality["trust_index"],
                "quality_ok": quality["quality_ok"],
                "temperature": diagnose_temperature,
                "time_budget_seconds": time_budget,
                "context_fingerprint": stability["context_fingerprint"],
                "rule_version": snapshot["rule_version"],
            },
        )
        return {
            "status": "success",
            "session_id": row.id,
            "event_id": event.id,
            "message": "Agent 深度诊断完成",
            "agent": {
                "model": model_name,
                "iterations": loop_result["iterations"],
                "duration_ms": round(elapsed, 2),
                "usage": usage_summary,
                "quality": quality,
                "stability": stability,
                "rag": {"case_count": len(rag_cases), "kb_search_used": bool(rag_cases)},
                "tool_trace": loop_result["trace"],
                "conclusion": {
                    **conclusion,
                    "low_confidence": conclusion["confidence"] < threshold,
                    "threshold": threshold,
                },
            },
        }

    # 降级：回退既有单轮诊断。墙钟预算余量不足时跳过 LLM 降级（避免再叠加一次
    # LLM 调用将整体耗时推向边缘代理超时），直接返回结构化 502 供前端提示重试。
    elapsed = (time.perf_counter() - started) * 1000.0
    if time_budget - elapsed / 1000.0 < DIAGNOSE_MIN_FALLBACK_SECONDS:
        fallback, fallback_error = None, "diagnose_time_budget_exhausted"
    else:
        fallback, fallback_error = await _diagnose_fallback(db, event_id, actor)
    status = "degraded" if fallback is not None else "failed"
    row = await _save_session(
        db,
        session_type="diagnose",
        status=status,
        model=model_name,
        event_id=event.id,
        result={"fallback": "single_round_diagnosis", "diagnosis": fallback, "fallback_error": fallback_error},
        trace=[],
        iterations=0,
        elapsed_ms=elapsed,
        actor=actor,
        error_message=loop_error,
        summary="Agent 多轮推理失败，已降级为单轮诊断" if fallback is not None else "Agent 与降级诊断均失败",
    )
    await write_audit(
        db,
        actor=actor,
        action="agent_diagnose",
        target_type="event",
        target_id=event.id,
        after={"session_id": row.id, "status": status, "error": loop_error},
    )
    if fallback is not None:
        return {
            "status": "degraded",
            "session_id": row.id,
            "event_id": event.id,
            "message": "Agent 多轮推理失败，已降级为单轮诊断",
            "agent": None,
            "fallback": fallback,
        }
    raise HTTPException(status_code=502, detail=f"Agent 诊断失败且降级诊断不可用：{fallback_error or loop_error}")


# ------------------ 知识治理 Agent（需求 b） ------------------

KB_DRAFT_REQUIRED_FIELDS = ("error_type", "service_name", "alert_template", "root_cause", "solution")


WINDOW_LABELS = {"1h": "近 1 小时", "24h": "近 24 小时", "7d": "近 7 天"}


# ------------------ Agent 生成质量指标（与诊断质量同一口径） ------------------

def _evaluate_agent_quality(
    query: Dict[str, str], answer: str, contexts: List[Dict[str, str]]
) -> Optional[Dict[str, Any]]:
    """包装 evaluate_diagnosis_quality：空答案返回 None，评估异常静默降级，不阻塞 Agent 主流程。"""
    text = (answer or "").strip()
    if not text:
        return None
    try:
        return evaluate_diagnosis_quality(query, text, contexts)
    except Exception:  # noqa: BLE001 - 质量评估失败仅影响指标展示
        return None


def _draft_quality_evaluate(draft: Dict[str, Any], clusters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """评估 AI 起草案例质量：grounding = 命中告警簇的模板与样本日志（已脱敏）。"""
    template = str(draft.get("alert_template") or "")
    matched = next((c for c in clusters if c.get("template") == template), None)
    evidence_template = str((matched or {}).get("template") or template)
    sample_log = str((matched or {}).get("sample_raw_log") or "")
    query = {
        "template": evidence_template,
        "error_type": str(draft.get("error_type") or ""),
        "service_name": str(draft.get("service_name") or ""),
    }
    answer = f"{draft.get('root_cause') or ''}。{draft.get('solution') or ''}"
    contexts = [{"alert_template": evidence_template, "root_cause": "", "solution": sample_log}]
    return _evaluate_agent_quality(query, answer, contexts)


def _oncall_quality_evaluate(
    window: str, report: Dict[str, Any], stats: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """评估值班报告质量：grounding = CMDB 影响面（系统/服务/负责人）与告警窗口。"""
    answer = "；".join(
        [str(report.get("impact_summary") or ""), *[str(a) for a in report.get("actions") or []]]
    )
    contexts = [
        {
            "alert_template": str(item.get("system") or ""),
            "root_cause": " ".join(str(svc.get("service") or "") for svc in item.get("services") or []),
            "solution": " ".join(str(o) for o in item.get("owners") or []),
        }
        for item in stats.get("affected_systems") or []
    ]
    query = {"template": f"值班报告 {window} 影响面与处置建议", "error_type": "", "service_name": ""}
    return _evaluate_agent_quality(query, answer, contexts)


def _aggregate_agent_quality(qualities: List[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """聚合多次生成质量样本：均值指标 + 达标率；无样本返回 None（前端不渲染质量卡）。"""
    samples = [q for q in qualities if isinstance(q, dict) and "trust_index" in q]
    if not samples:
        return None
    keys = ("faithfulness", "context_coverage", "answer_relevance", "hallucination_rate", "trust_index")
    agg: Dict[str, Any] = {
        key: round(sum(float(s.get(key) or 0) for s in samples) / len(samples), 4) for key in keys
    }
    agg["sample_count"] = len(samples)
    ok_count = sum(1 for s in samples if s.get("quality_ok"))
    agg["quality_ok_rate"] = round(ok_count / len(samples), 4)
    agg["quality_ok"] = bool(agg["trust_index"] >= GATE_LINE)
    agg["gate_line"] = GATE_LINE
    return agg


async def _cluster_recent_events(db: AsyncSession, time_window: str = "24h") -> List[Dict[str, Any]]:
    """按模板聚类指定时间窗（1h/24h/7d，默认 24h）内告警，返回规模最大的簇（最多 3 个）。"""
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_DELTAS_HOURS.get(time_window, 24))
    result = await db.execute(
        select(Events).where(Events.created_at >= since).order_by(Events.id.desc()).limit(1000)
    )
    events = list(result.scalars().all())
    groups: Dict[str, Dict[str, Any]] = {}
    for event in events:
        key = (event.template or event.raw_log or event.event_id or "").strip()
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                "template": key,
                "count": 0,
                "services": set(),
                "clusters": set(),
                "severity_dist": {"critical": 0, "warning": 0, "info": 0},
                "error_types": set(),
                "last_seen": None,
                "sample_raw_log": None,
            },
        )
        group["count"] += 1
        group["services"].add(event.service_name)
        if event.cluster:
            group["clusters"].add(event.cluster)
        group["severity_dist"][event.severity] = group["severity_dist"].get(event.severity, 0) + 1
        if event.error_type:
            group["error_types"].add(event.error_type)
        created = str(event.created_at) if event.created_at else None
        if created and (group["last_seen"] is None or created > group["last_seen"]):
            group["last_seen"] = created
        if group["sample_raw_log"] is None and event.raw_log:
            # 脱敏（评审 P0-6）：样本日志进入 LLM Prompt 与会话 result_json 前过滤敏感信息
            group["sample_raw_log"] = mask_sensitive(event.raw_log)

    clusters = []
    for group in groups.values():
        if group["count"] < 2:
            continue
        max_severity = max(group["severity_dist"], key=lambda s: (group["severity_dist"][s], SEVERITY_RANK.get(s, 0)))
        if group["severity_dist"][max_severity] == 0:
            max_severity = "info"
        clusters.append(
            {
                "template": group["template"],
                "count": group["count"],
                "services": sorted(group["services"]),
                "clusters": sorted(group["clusters"]),
                "severity_dist": group["severity_dist"],
                "max_severity": max_severity,
                "known_error_types": sorted(group["error_types"]),
                "last_seen": group["last_seen"],
                "sample_raw_log": group["sample_raw_log"],
            }
        )
    clusters.sort(key=lambda c: (SEVERITY_RANK.get(c["max_severity"], 0), c["count"]), reverse=True)
    return clusters[:3]


async def _draft_kb_cases(
    db: AsyncSession, clusters: List[Dict[str, Any]], window_label: str = "近 24 小时"
) -> Tuple[List[Dict[str, Any]], str]:
    """调用 AI 起草知识案例草稿；返回校验后的草稿列表与分析文本。"""
    if not clusters:
        return [], f"{window_label}内没有可聚类的重复告警簇"
    await _flush(db)
    base_messages = [
        ChatMessage(role="system", content=KB_DRAFT_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"告警簇统计（{window_label}）：\n"
                + json.dumps(clusters, ensure_ascii=False, indent=2)
                + "\n\n请输出 JSON 草稿（仅输出一个 JSON 对象）。"
                "注意：每条 root_cause 与 solution 控制在 120 字以内，确保 JSON 完整闭合。"
            ),
        ),
    ]
    payload = None
    messages = base_messages
    for _attempt in range(2):
        await _flush(db)
        response = await _llm_chat(db, messages, max_tokens=3000)
        payload = extract_json_payload(response.content)
        if payload is not None:
            break
        messages = base_messages + [
            ChatMessage(role="assistant", content=(response.content or "")[:2000]),
            ChatMessage(
                role="user",
                content="上一条输出不是合法 JSON（可能被截断）。请压缩内容，重新只输出一个完整闭合的 JSON 对象。",
            ),
        ]
    if payload is None:
        raise ValueError("模型输出不是合法 JSON")
    drafts_raw = payload.get("drafts")
    drafts: List[Dict[str, Any]] = []
    if isinstance(drafts_raw, list):
        for draft in drafts_raw[:3]:
            if not isinstance(draft, dict):
                continue
            cleaned = {
                key: str(draft.get(key) or "").strip()
                for key in (
                    "error_type",
                    "service_name",
                    "cluster",
                    "alert_template",
                    "root_cause",
                    "solution",
                    "topology_snapshot",
                    "reason",
                )
            }
            if all(cleaned.get(key) for key in KB_DRAFT_REQUIRED_FIELDS):
                drafts.append(cleaned)
    return drafts, str(payload.get("analysis") or "").strip()


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符（评审 P0-5）：防止模板中的 %/_ 被解释为通配符导致误判。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _template_case_exists(db: AsyncSession, template: str) -> bool:
    """同模板已有活跃案例或在途 create 变更集时返回 True（幂等保护）。"""
    if not template:
        return False
    result = await db.execute(
        select(Kb_cases.id).where(Kb_cases.alert_template == template, Kb_cases.status != "archived").limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True
    result = await db.execute(
        select(Kb_change_sets.id).where(
            Kb_change_sets.change_type == "create",
            Kb_change_sets.status == "pending",
            Kb_change_sets.after_json.like(f"%{_escape_like(template)}%", escape="\\"),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _auto_merge_proposal(db: AsyncSession, user: UserResponse) -> Dict[str, Any]:
    """基于相似案例扫描自动提交合并提案（同主案例在途提案时跳过）。"""
    groups = await console_kb.scan_duplicates(db)
    if not groups:
        return {"skipped": "当前没有满足条件的相似案例组"}
    group = groups[0]
    master = group["suggested_master"]
    merged_ids = [cid for cid in group["case_ids"] if cid != master]
    if not merged_ids:
        return {"skipped": f"相似案例组 {master} 没有可合并的其余案例"}
    result = await db.execute(
        select(Kb_merge_proposals).where(
            Kb_merge_proposals.status == "pending",
            Kb_merge_proposals.master_case_id == master,
        ).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return {"skipped": f"主案例 {master} 已存在在途合并提案"}
    outcome = await console_kb.create_merge_proposal(
        db,
        user,
        {
            "master_case_id": master,
            "merged_case_ids": merged_ids,
            "reason": "知识治理 Agent：基于模板相似度自动生成的去重合并建议",
            "strategy": {"keep_fields": "master", "archive_redundant": True},
        },
    )
    return {
        "proposal_id": outcome.get("proposal", {}).get("id"),
        "master_case_id": master,
        "merged_case_ids": merged_ids,
        "approval_request_id": outcome.get("approval_request_id"),
        "auto_merged": outcome.get("auto_merged", False),
    }


async def run_kb_governance_agent(
    db: AsyncSession, user: UserResponse, time_window: str = "24h"
) -> Dict[str, Any]:
    """知识治理 Agent 外层兜底：任何未捕获异常落 status=failed 会话后返回 500。

    评审 P0-4：此前数据库异常发生在会话落库之前，该次运行无 failed 记录、排障只能依赖后端日志；
    现统一在会话边界兜底（先 rollback 复位事务，再落 failed 会话），保证每次运行可追溯。
    """
    if time_window not in WINDOW_DELTAS_HOURS:
        raise HTTPException(status_code=400, detail="time_window 仅支持 1h / 24h / 7d")
    actor = user.email or user.id
    started = time.perf_counter()
    try:
        return await _run_kb_governance_agent_inner(db, user, time_window)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("KB governance agent failed: %s", exc)
        error_message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            await db.rollback()  # 复位可能处于失败状态的事务，确保 failed 会话可写入
            await _save_session(
                db,
                session_type="kb_governance",
                status="failed",
                model="unknown",
                event_id=None,
                result={"time_window": time_window, "error": error_message},
                trace=[],
                iterations=0,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                actor=actor,
                error_message=error_message,
                summary=f"治理运行失败：{error_message}"[:300],
            )
        except Exception as save_exc:  # noqa: BLE001 - 兜底落库自身失败时仅记日志
            logger.error("Failed to persist failed session: %s", save_exc)
        raise HTTPException(status_code=500, detail=f"知识治理 Agent 运行失败：{error_message}")


async def _run_kb_governance_agent_inner(
    db: AsyncSession, user: UserResponse, time_window: str = "24h"
) -> Dict[str, Any]:
    """知识治理 Agent 主体：按时间窗聚类告警 → AI 起草案例（走审批）→ 合并提案。"""
    if time_window not in WINDOW_DELTAS_HOURS:
        raise HTTPException(status_code=400, detail="time_window 仅支持 1h / 24h / 7d")
    actor = user.email or user.id
    started = time.perf_counter()
    model_name = await llm_runtime.get_llm_model_name(db)

    clusters = await _cluster_recent_events(db, time_window)
    status = "succeeded"
    error_message: Optional[str] = None
    analysis = ""
    drafts: List[Dict[str, Any]] = []
    try:
        drafts, analysis = await _draft_kb_cases(db, clusters, WINDOW_LABELS.get(time_window, "近 24 小时"))
    except Exception as exc:  # noqa: BLE001
        logger.error("KB governance AI draft failed: %s", exc)
        status = "degraded"
        error_message = f"ai_draft_failed: {type(exc).__name__}: {exc}"
        drafts = []

    drafts_submitted: List[Dict[str, Any]] = []
    drafts_skipped: List[Dict[str, Any]] = []
    draft_qualities: List[Optional[Dict[str, Any]]] = []
    for draft in drafts:
        draft_quality = _draft_quality_evaluate(draft, clusters)
        draft_qualities.append(draft_quality)
        fields = {key: draft[key] for key in ("error_type", "service_name", "cluster", "alert_template", "root_cause", "solution", "topology_snapshot") if draft.get(key)}
        if await _template_case_exists(db, fields.get("alert_template", "")):
            drafts_skipped.append({"alert_template": fields.get("alert_template"), "quality": draft_quality, "reason": "已存在同模板案例或在途新建变更集"})
            continue
        try:
            outcome = await console_kb.create_change_set(
                db,
                user,
                {
                    "case_id": "",
                    "change_type": "create",
                    "fields": fields,
                    "reason": f"知识治理 Agent 自动起草：{draft.get('reason') or '基于告警簇分析'}",
                },
            )
            drafts_submitted.append(
                {
                    "case_id": outcome["change_set"]["case_id"],
                    "approval_request_id": outcome.get("approval_request_id"),
                    "auto_published": outcome.get("auto_published", False),
                    "alert_template": fields.get("alert_template"),
                    "quality": draft_quality,
                }
            )
        except HTTPException as exc:
            drafts_skipped.append({"alert_template": fields.get("alert_template"), "quality": draft_quality, "reason": f"提交失败: {exc.detail}"})

    draft_quality_agg = _aggregate_agent_quality(draft_qualities)
    merge_result: Dict[str, Any]
    try:
        merge_result = await _auto_merge_proposal(db, user)
    except HTTPException as exc:
        merge_result = {"error": str(exc.detail)}

    elapsed = (time.perf_counter() - started) * 1000.0
    window_label = WINDOW_LABELS.get(time_window, time_window)
    summary = f"[{window_label}] 聚类 {len(clusters)} 簇，起草 {len(drafts_submitted)} 条案例，合并提案 {'已提交' if merge_result.get('proposal_id') else '未生成'}"
    row = await _save_session(
        db,
        session_type="kb_governance",
        status=status,
        model=model_name,
        event_id=None,
        result={
            "time_window": time_window,
            "analysis": analysis,
            "clusters": clusters,
            "drafts_submitted": drafts_submitted,
            "drafts_skipped": drafts_skipped,
            "merge_result": merge_result,
            "quality": draft_quality_agg,
        },
        trace=[
            {"step": "cluster_events", "clusters": len(clusters)},
            {"step": "ai_draft", "drafts": len(drafts), "status": "ok" if not error_message else error_message},
            {"step": "submit_change_sets", "submitted": len(drafts_submitted), "skipped": len(drafts_skipped)},
            {"step": "draft_quality", "trust_index": (draft_quality_agg or {}).get("trust_index"), "samples": (draft_quality_agg or {}).get("sample_count", 0)},
            {"step": "merge_proposal", "result": merge_result},
        ],
        iterations=len(drafts) + 1,
        elapsed_ms=elapsed,
        actor=actor,
        error_message=error_message,
        summary=summary,
    )
    await write_audit(
        db,
        actor=actor,
        action="agent_kb_governance",
        target_type="agent_session",
        target_id=row.id,
        after={
            "clusters": len(clusters),
            "drafts_submitted": [d["case_id"] for d in drafts_submitted],
            "merge_proposal_id": merge_result.get("proposal_id"),
            "trust_index": (draft_quality_agg or {}).get("trust_index"),
            "status": status,
        },
    )
    return {
        "status": status,
        "session_id": row.id,
        "message": summary if status == "succeeded" else "AI 起草失败已降级：仅输出聚类统计与合并提案",
        "governance": {
            "time_window": time_window,
            "analysis": analysis,
            "clusters": clusters,
            "drafts_submitted": drafts_submitted,
            "drafts_skipped": drafts_skipped,
            "merge_result": merge_result,
            "quality": draft_quality_agg,
            "model": model_name,
            "duration_ms": round(elapsed, 2),
        },
    }


# ------------------ 值班 Agent（需求 c） ------------------

def _aggregate_oncall(events: List[Events], index: Dict[str, Cmdb_assets]) -> Dict[str, Any]:
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    systems: Dict[str, Dict[str, Any]] = {}
    unmapped_services: Dict[str, int] = {}
    for event in events:
        by_severity[event.severity] = by_severity.get(event.severity, 0) + 1
        asset = index.get(event.service_name)
        system_name = asset.system_name if asset else "未登记（CMDB 缺失）"
        bucket = systems.setdefault(
            system_name,
            {"system": system_name, "event_count": 0, "services": {}, "owners": set(), "max_severity": "info", "clusters": set()},
        )
        bucket["event_count"] += 1
        service_bucket = bucket["services"].setdefault(event.service_name, {"count": 0, "max_severity": "info"})
        service_bucket["count"] += 1
        if SEVERITY_RANK.get(event.severity, 0) > SEVERITY_RANK.get(service_bucket["max_severity"], 0):
            service_bucket["max_severity"] = event.severity
        if asset:
            if asset.owner:
                bucket["owners"].add(asset.owner)
            if asset.owner_email:
                bucket["owners"].add(asset.owner_email)
        else:
            unmapped_services[event.service_name] = unmapped_services.get(event.service_name, 0) + 1
        if SEVERITY_RANK.get(event.severity, 0) > SEVERITY_RANK.get(bucket["max_severity"], 0):
            bucket["max_severity"] = event.severity
        if event.cluster:
            bucket["clusters"].add(event.cluster)

    affected = []
    for bucket in systems.values():
        affected.append(
            {
                "system": bucket["system"],
                "event_count": bucket["event_count"],
                "max_severity": bucket["max_severity"],
                "owners": sorted(bucket["owners"]),
                "clusters": sorted(bucket["clusters"]),
                "services": [
                    {"service": name, "count": svc["count"], "max_severity": svc["max_severity"]}
                    for name, svc in sorted(bucket["services"].items(), key=lambda item: -item[1]["count"])
                ],
            }
        )
    affected.sort(key=lambda item: (SEVERITY_RANK.get(item["max_severity"], 0), item["event_count"]), reverse=True)
    return {
        "by_severity": by_severity,
        "affected_systems": affected,
        "unmapped_services": dict(sorted(unmapped_services.items(), key=lambda item: -item[1])),
    }


def _deterministic_oncall_report(stats: Dict[str, Any], window: str) -> Dict[str, Any]:
    severity = stats["by_severity"]
    total = sum(severity.values())
    critical = severity.get("critical", 0)
    if critical >= 3:
        priority = "P0"
    elif critical > 0:
        priority = "P1"
    elif severity.get("warning", 0) > 0:
        priority = "P2"
    else:
        priority = "P3"
    affected = stats["affected_systems"]
    owners = sorted({owner for item in affected for owner in item["owners"]})
    lines = [
        f"【值班告警汇总】时间窗: {window}，共 {total} 条告警（critical {critical} / warning {severity.get('warning', 0)} / info {severity.get('info', 0)}）",
        "受影响系统：",
    ]
    for item in affected:
        services_text = "、".join(f"{svc['service']}({svc['count']})" for svc in item["services"])
        owner_text = " @".join(item["owners"]) if item["owners"] else "未登记负责人"
        lines.append(f"- {item['system']}：{item['event_count']} 条，最高 {item['max_severity']}；服务 {services_text}；负责人 @{owner_text}")
    if stats["unmapped_services"]:
        lines.append("CMDB 未登记服务：" + "、".join(stats["unmapped_services"]))
    lines.append("处置建议：优先处理 critical 告警，按知识库处置清单执行；处置完成后回写复盘。")
    if owners:
        lines.append("通知：" + " ".join(f"@{owner}" for owner in owners))
    return {
        "impact_summary": f"最近 {window} 共 {total} 条告警，涉及 {len(affected)} 个系统；最高严重级别为 "
        + (max((item["max_severity"] for item in affected), key=lambda s: SEVERITY_RANK.get(s, 0), default="info")),
        "priority": priority,
        "actions": [
            "确认 critical 告警对应服务的健康状态并按知识库方案处置",
            "通知相关系统负责人跟进 warning 告警",
            "处置完成后在控制台回写诊断反馈",
        ],
        "owners_to_notify": owners,
        "chatops_text": "\n".join(lines),
    }


def _validate_oncall_report(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    impact_summary = str(payload.get("impact_summary") or "").strip()
    chatops_text = str(payload.get("chatops_text") or "").strip()
    if not impact_summary or not chatops_text:
        return None
    # ChatOps 文本格式统一：确保以【值班告警汇总】标记开头，保证可直接复制到群
    if not chatops_text.startswith("【值班告警汇总】"):
        chatops_text = f"【值班告警汇总】\n{chatops_text}"
    priority = str(payload.get("priority") or "P1").strip().upper()
    if priority not in ONCALL_PRIORITIES:
        priority = "P1"
    actions_raw = payload.get("actions")
    actions = [str(a).strip() for a in actions_raw if str(a).strip()] if isinstance(actions_raw, list) else []
    owners_raw = payload.get("owners_to_notify")
    owners = [str(o).strip() for o in owners_raw if str(o).strip()] if isinstance(owners_raw, list) else []
    return {
        "impact_summary": impact_summary,
        "priority": priority,
        "actions": actions[:10],
        "owners_to_notify": owners[:10],
        "chatops_text": chatops_text,
    }


async def run_oncall_agent(db: AsyncSession, user: UserResponse, time_window: str) -> Dict[str, Any]:
    """值班 Agent：时间窗影响面汇总 + ChatOps 处置建议，持久化到 oncall_reports。"""
    actor = user.email or user.id
    model_name = await llm_runtime.get_llm_model_name(db)
    window = time_window if time_window in WINDOW_DELTAS_HOURS else "24h"
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_DELTAS_HOURS[window])

    result_load = await db.execute(
        select(Events).where(Events.created_at >= since).order_by(Events.id.desc()).limit(2000)
    )
    events = list(result_load.scalars().all())
    assets = await _load_cmdb(db)
    index = _cmdb_index(assets)
    stats = _aggregate_oncall(events, index)
    severity = stats["by_severity"]

    report: Optional[Dict[str, Any]] = None
    status = "succeeded"
    error_message: Optional[str] = None
    if events:
        try:
            base_messages = [
                ChatMessage(role="system", content=ONCALL_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=(
                        f"时间窗: {window}\n告警统计与 CMDB 影响面：\n"
                        + json.dumps(stats, ensure_ascii=False, indent=2)
                        + "\n\n请输出值班报告 JSON（仅输出一个 JSON 对象，chatops_text 控制在 600 字以内）。"
                    ),
                ),
            ]
            messages = base_messages
            report = None
            for _attempt in range(2):
                await _flush(db)
                response = await _llm_chat(db, messages, max_tokens=2000)
                payload = extract_json_payload(response.content)
                report = _validate_oncall_report(payload) if payload else None
                if report is not None:
                    break
                messages = base_messages + [
                    ChatMessage(role="assistant", content=(response.content or "")[:2000]),
                    ChatMessage(
                        role="user",
                        content="上一条输出校验失败（可能被截断或缺少必填字段）。请压缩内容，重新只输出一个完整闭合且字段齐全的 JSON 对象。",
                    ),
                ]
            if report is None:
                raise ValueError("模型输出校验失败")
        except Exception as exc:  # noqa: BLE001
            logger.error("Oncall agent AI report failed: %s", exc)
            status = "degraded"
            error_message = f"ai_report_failed: {type(exc).__name__}: {exc}"
            report = _deterministic_oncall_report(stats, window)
    else:
        status = "succeeded"
        report = {
            "impact_summary": f"最近 {window} 窗口内无告警，一切正常。",
            "priority": "P3",
            "actions": [],
            "owners_to_notify": [],
            "chatops_text": f"【值班告警汇总】时间窗: {window}，窗口内无告警。",
        }

    # 生成质量指标（与诊断/知识治理同一口径）：grounding = CMDB 影响面；无告警窗口不计质量
    report_quality = _oncall_quality_evaluate(window, report, stats) if events else None
    if report_quality is not None:
        report["quality"] = report_quality

    report_row = Oncall_reports(
        time_window=window,
        event_count=len(events),
        critical_count=severity.get("critical", 0),
        warning_count=severity.get("warning", 0),
        info_count=severity.get("info", 0),
        affected_systems=json.dumps(stats["affected_systems"], ensure_ascii=False),
        report_json=json.dumps(report, ensure_ascii=False),
        chatops_text=report["chatops_text"],
        actor=actor,
    )
    db.add(report_row)
    await db.flush()

    started = time.perf_counter()
    row = await _save_session(
        db,
        session_type="oncall",
        status=status,
        model=model_name,
        event_id=None,
        result={"report_id": report_row.id, "priority": report["priority"], "impact_summary": report["impact_summary"], "quality": report_quality},
        trace=[
            {"step": "aggregate_window", "window": window, "events": len(events)},
            {"step": "cmdb_mapping", "systems": len(stats["affected_systems"]), "unmapped": list(stats["unmapped_services"])},
            {"step": "ai_report", "status": "ok" if not error_message else error_message},
            {"step": "report_quality", "trust_index": (report_quality or {}).get("trust_index"), "sample_count": (report_quality or {}).get("sample_count", 1 if report_quality else 0)},
        ],
        iterations=3,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        actor=actor,
        error_message=error_message,
        summary=f"{report['priority']}：{report['impact_summary'][:200]}",
    )
    report_row.session_id = row.id
    await db.commit()
    await write_audit(
        db,
        actor=actor,
        action="agent_oncall_report",
        target_type="oncall_report",
        target_id=report_row.id,
        after={"window": window, "events": len(events), "priority": report["priority"], "trust_index": (report_quality or {}).get("trust_index"), "status": status},
    )
    return {
        "status": status,
        "session_id": row.id,
        "message": "值班报告已生成" if status == "succeeded" else "AI 报告失败已降级为确定性统计报告",
        "report": {
            "id": report_row.id,
            "time_window": window,
            "event_count": len(events),
            "by_severity": severity,
            "affected_systems": stats["affected_systems"],
            "unmapped_services": stats["unmapped_services"],
            **report,
        },
    }


# ------------------ 查询接口 ------------------

async def list_sessions(db: AsyncSession, limit: int = 20, session_type: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(Agent_sessions)
    if session_type:
        stmt = stmt.where(Agent_sessions.session_type == session_type)
    stmt = stmt.order_by(Agent_sessions.id.desc()).limit(min(limit, 100))
    return [ser_session(row) for row in (await db.execute(stmt)).scalars().all()]


async def list_cmdb_assets(db: AsyncSession, q: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    stmt = select(Cmdb_assets)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Cmdb_assets.hostname.ilike(like))
            | (Cmdb_assets.ip.ilike(like))
            | (Cmdb_assets.service_name.ilike(like))
            | (Cmdb_assets.system_name.ilike(like))
        )
    stmt = stmt.order_by(Cmdb_assets.id).limit(min(limit, 200))
    return [ser_asset(asset) for asset in (await db.execute(stmt)).scalars().all()]


async def list_oncall_reports(db: AsyncSession, limit: int = 10) -> List[Dict[str, Any]]:
    stmt = select(Oncall_reports).order_by(Oncall_reports.id.desc()).limit(min(limit, 50))
    return [ser_oncall_report(row) for row in (await db.execute(stmt)).scalars().all()]
