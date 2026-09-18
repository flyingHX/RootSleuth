"""诊断后通知推送服务。

诊断结论落库后，将根因/处置建议/置信度/Trust Index 等推送到配置中心
notify_webhook_url 指定的外部系统（如 ITSM 工单系统），可携带
Authorization: Bearer <notify_webhook_token> 鉴权头。

设计要点：
- 未配置 notify_webhook_url 时静默跳过；
- 推送以 fire-and-forget 后台任务执行，不阻塞诊断响应；
- 推送失败仅记录日志，绝不影响诊断主链路；
- notify_webhook_token 加密存储（复用 SECRET_CONFIG_KEYS 机制），推送前解密。
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from services.console_common import get_config
from services.llm_runtime import decrypt_secret

logger = logging.getLogger(__name__)

# 单次推送超时（秒）：通知是尽力而为的旁路，超时不宜过长
NOTIFY_TIMEOUT_SECONDS = 10.0

# fire-and-forget 任务引用集中持有，防止任务被 GC 中途取消
_notification_tasks: set = set()


def build_notification_payload(event: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造通知载荷：事件元信息 + 诊断结论（根因/建议/命令/置信度/Trust Index）。

    trust_index 从 ai_output_json 的 quality 块提取；extra 用于补充
    diagnosis_source（single_round / agent）、model、diagnosed_at 等链路元信息，
    且 extra 中非空值优先覆盖同名字段。
    """
    output: Dict[str, Any] = {}
    if event.ai_output_json:
        try:
            output = json.loads(event.ai_output_json)
        except (TypeError, ValueError):
            output = {}
    if not isinstance(output, dict):
        output = {}
    quality = output.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    diagnosed_at = (extra or {}).get("diagnosed_at")
    if not diagnosed_at:
        updated = getattr(event, "updated_at", None)
        diagnosed_at = updated.isoformat() if isinstance(updated, datetime) else None
    payload: Dict[str, Any] = {
        "source": "rootsleuth",
        "event_type": "diagnosis_completed",
        "event_id": event.event_id,
        "internal_id": event.id,
        "service_name": event.service_name,
        "cluster": event.cluster,
        "topology": event.topology,
        "error_type": event.error_type,
        "severity": event.severity,
        "template": event.template,
        "status": event.status,
        "degraded_reason": event.degraded_reason,
        "root_cause": event.ai_root_cause,
        "solution": event.ai_solution,
        "command": event.ai_command,
        "confidence": event.confidence,
        "trust_index": quality.get("trust_index"),
        "model": output.get("model"),
        "diagnosed_at": diagnosed_at,
    }
    for key, value in (extra or {}).items():
        if value is not None:
            payload[key] = value
    return payload


async def prepare_notification(
    db: AsyncSession, event: Any, extra: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """读取通知配置并构造载荷（在请求作用域内完成，后台任务不再访问数据库）。

    返回 None 表示未配置 notify_webhook_url（跳过推送）。
    """
    url = ((await get_config(db, "notify_webhook_url", "")) or "").strip()
    if not url:
        return None
    token = decrypt_secret((await get_config(db, "notify_webhook_token", "")) or "").strip()
    return {"url": url, "token": token, "payload": build_notification_payload(event, extra)}


async def send_notification(
    url: str,
    token: str,
    payload: Dict[str, Any],
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """推送一次通知；任何异常都转为结构化结果返回，绝不抛出。"""
    started = time.perf_counter()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    summary: Dict[str, Any] = {
        "ok": False,
        "status_code": None,
        "error": None,
        "url": url,
        "latency_ms": None,
    }
    try:
        if client is not None:
            response = await client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT_SECONDS) as own_client:
                response = await own_client.post(url, json=payload, headers=headers)
        summary["status_code"] = response.status_code
        summary["ok"] = 200 <= response.status_code < 300
        if not summary["ok"]:
            summary["error"] = f"非 2xx 响应：{response.status_code}"
    except Exception as exc:  # noqa: BLE001 - 通知失败不影响诊断主链路
        summary["error"] = f"{type(exc).__name__}: {exc}"
    summary["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return summary


async def _send_and_log(url: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = await send_notification(url, token, payload)
    if summary["ok"]:
        logger.info(
            "诊断通知推送成功: url=%s status=%s latency=%sms",
            url,
            summary["status_code"],
            summary["latency_ms"],
        )
    else:
        logger.warning(
            "诊断通知推送失败（不影响诊断结果）: url=%s error=%s latency=%sms",
            url,
            summary["error"],
            summary["latency_ms"],
        )
    return summary


def schedule_notification(url: str, token: str, payload: Dict[str, Any]) -> Optional[asyncio.Task]:
    """fire-and-forget 推送：诊断响应立即返回，通知在后台执行。"""
    try:
        task = asyncio.create_task(_send_and_log(url, token, payload))
    except RuntimeError:
        # 无运行中事件循环（如同步脚本环境）时退化为日志告警
        logger.warning("诊断通知无法调度（无运行中的事件循环），已跳过推送")
        return None
    _notification_tasks.add(task)
    task.add_done_callback(_notification_tasks.discard)
    return task


async def notify_after_diagnosis(
    db: AsyncSession, event: Any, extra: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """诊断后通知统一入口：准备（读配置+构载荷）后调度 fire-and-forget 推送。

    返回 {"url": ..., "scheduled": True} 表示已调度；
    None 表示未配置或准备失败（均已记日志，不影响诊断响应）。
    """
    try:
        ctx = await prepare_notification(db, event, extra)
    except Exception as exc:  # noqa: BLE001 - 通知准备失败不影响诊断
        logger.warning("诊断通知准备失败（不影响诊断结果）: %s", exc)
        return None
    if not ctx:
        return None
    task = schedule_notification(ctx["url"], ctx["token"], ctx["payload"])
    if task is None:
        return None
    return {"url": ctx["url"], "scheduled": True}
