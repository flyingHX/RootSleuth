"""控制台 → RAG 同步补偿闭环（评审 P0-1 增强：重试 / 死信 / 重放 / 幂等 / 缓存失效）。

闭环语义（升级版）：
- 发布（create/update/rollback）→ `POST /api/v1/kb-sync/upsert` 同步 Milvus 索引 →
  `GET /api/v1/kb-sync/verify` 回读验证，检索侧"真正可见"才闭环；
- 归档（手工归档 / 合并冗余 / 生命周期巡检）→ `POST /api/v1/kb-sync/delete` 删除索引，
  避免"僵尸知识"继续被召回；
- 反馈（👍/👎）→ `POST /api/v1/feedback` 同步 Milvus feedback_score/upvotes/downvotes，
  与 PostgreSQL 双写保持一致，供 L2 重排 f4/f7 特征使用；
- 缓存失效（发布/更新/回滚/归档/删除）→ `POST /api/v1/kb-cache/invalidate`
  触发 RAG 侧语义缓存按 case_id 失效 + 多实例广播。

补偿语义（本轮新增）：
- 同步失败（HTTP/超时/verify 失败）→ 固化为 Rag_sync_tasks 补偿任务（幂等键去重）；
- 任务由 `retry_due_tasks` 按指数退避自动重试（`rag_sync_retry_base_seconds` 配置基数）；
- 超过 `rag_sync_max_attempts` 进入死信（dead），由运维 API 人工重放；
- 补偿重试复用同一幂等键与任务载荷：upsert 幂等短路、feedback 防重复加分，保证最终一致。

降级语义（不阻断业务）：
- `rag_base_url` 未配置 → 视为未部署 RAG 服务，同步跳过并留审计（skipped）；
- 首次同步失败 → 业务照常完成，任务入补偿队列由后台重试；
- 补偿任务执行失败 → 指数退避重试直至死信，全程审计留痕。
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.rag_sync_tasks import Rag_sync_tasks
from services.console_common import get_config, write_audit

logger = logging.getLogger(__name__)

SYNC_TIMEOUT_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 3600.0

# 任务类型与状态常量
TASK_UPSERT = "upsert"
TASK_DELETE = "delete"
TASK_FEEDBACK = "feedback"
TASK_CACHE = "cache_invalidate"
TASK_TYPES = (TASK_UPSERT, TASK_DELETE, TASK_FEEDBACK, TASK_CACHE)
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_DEAD = "dead"


# ------------------ 配置读取 ------------------

async def _rag_base_url(db: AsyncSession) -> str:
    """读取 RAG 服务地址（配置中心实时生效；留空表示未部署，同步跳过）。"""
    return str(await get_config(db, "rag_base_url", "") or "").strip().rstrip("/")


async def _rag_api_key(db: AsyncSession) -> str:
    """RAG 服务间鉴权 API Key（P0-1）：配置后以 X-API-Key 透传；留空 = RAG 侧鉴权关闭（对称 fail-open）。"""
    return str(await get_config(db, "rag_api_key", "") or "").strip()


def _auth_headers(api_key: str) -> Dict[str, str]:
    """服务间鉴权 Header：仅在配置了 Key 时携带，兼容 RAG 鉴权未启用的存量部署。"""
    return {"X-API-Key": api_key} if api_key else {}


async def _retry_base_seconds(db: AsyncSession) -> int:
    """补偿重试基础间隔（秒），指数退避基数。"""
    try:
        return max(1, int(await get_config(db, "rag_sync_retry_base_seconds", "30")))
    except (TypeError, ValueError):
        return 30


async def _max_attempts_default(db: AsyncSession) -> int:
    """补偿最大尝试次数默认值（任务级可覆盖）。"""
    try:
        return max(1, int(await get_config(db, "rag_sync_max_attempts", "5")))
    except (TypeError, ValueError):
        return 5


# ------------------ 幂等与指纹 ------------------

def _content_hash(payload: Dict[str, Any]) -> str:
    """载荷内容指纹：sort_keys 确保同内容不同序产生相同 hash。"""
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _make_task_key(task_type: str, case_id: str, content_hash: str) -> str:
    """补偿任务幂等键：同类型 + 同 case + 同内容 → 不重复入队。"""
    return f"{task_type}:{case_id}:{content_hash}"


def _backoff_seconds(base_seconds: int, attempt: int) -> float:
    """指数退避：base * 2^(attempt-1)，上限 MAX_BACKOFF_SECONDS。"""
    delay = float(base_seconds) * (2 ** max(0, attempt - 1))
    return min(delay, MAX_BACKOFF_SECONDS)


# ------------------ 载荷构造 ------------------

def _case_payload(case: Any, idempotency_key: str = "", content_hash: str = "") -> Dict[str, Any]:
    """Kb_cases ORM → kb-sync/upsert 载荷（含反馈计数与幂等指纹）。"""
    payload = {
        "case_id": case.case_id,
        "service_name": case.service_name or "",
        "cluster": case.cluster or "",
        "error_type": case.error_type or "",
        "severity": 2,
        "root_cause": case.root_cause or "",
        "solution": case.solution or "",
        "alert_template": case.alert_template or "",
        "topology_snapshot": case.topology_snapshot or "",
        "fingerprint": getattr(case, "fingerprint", "") or "",
        "feedback_score": int(round(case.feedback_score or 0)),
        "resolved_by": "console",
        "content_hash": content_hash,
    }
    version = getattr(case, "version", None)
    if version is not None:
        payload["kb_version"] = int(version)
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return payload


# ------------------ HTTP 帮助函数 ------------------

async def _http_post(
    base: str, path: str, payload: Dict[str, Any], api_key: str = ""
) -> httpx.Response:
    """POST 请求封装（raise_for_status 由调用方处理；配置 rag_api_key 时透传 X-API-Key）。"""
    async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
        return await client.post(f"{base}{path}", json=payload, headers=_auth_headers(api_key))


async def _http_verify(base: str, case_id: str, api_key: str = "") -> bool:
    """按 case_id 回读验证索引行存在性。"""
    async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
        resp = await client.get(
            f"{base}/api/v1/kb-sync/verify",
            params={"case_id": case_id},
            headers=_auth_headers(api_key),
        )
        resp.raise_for_status()
        return bool(resp.json().get("exists"))


def _response_body(response: httpx.Response) -> Any:
    """安全解析响应体 JSON（非 JSON 响应返回 None）。"""
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


def _is_stale_version_rejected(exc: httpx.HTTPStatusError) -> bool:
    """识别 RAG 版本守卫 409（stale_version_rejected）：旧版本覆盖被终态拒绝。

    该结果重试无意义（同载荷永远 409），不得进入补偿队列/死信占用人工重放资源。
    """
    if exc.response.status_code != 409:
        return False
    body = _response_body(exc.response)
    if not isinstance(body, dict):
        return False
    if str(body.get("error") or "") == "stale_version_rejected":
        return True
    detail = body.get("detail")  # FastAPI HTTPException 将 dict detail 包裹为 {"detail": {...}}
    return isinstance(detail, dict) and str(detail.get("error") or "") == "stale_version_rejected"


# ------------------ 补偿任务入队 ------------------

async def _enqueue_task(
    db: AsyncSession,
    task_type: str,
    case_id: str,
    payload: Dict[str, Any],
    content_hash: str,
    created_by: str = "system",
    max_attempts: Optional[int] = None,
) -> Optional[Rag_sync_tasks]:
    """幂等入队补偿任务：幂等键已存在且非死信 → 跳过；死信 → 重置为待重试。"""
    key = _make_task_key(task_type, case_id, content_hash)
    result = await db.execute(
        select(Rag_sync_tasks).where(Rag_sync_tasks.idempotency_key == key).limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.status == STATUS_DEAD:
            # 重放入口：死信任务重置计数后重新排队（保留原任务行，审计连续）
            existing.status = STATUS_PENDING
            existing.attempt_count = 0
            existing.next_retry_at = None
            existing.last_error = None
            logger.info("Requeued dead task %s", key)
            return existing
        return existing  # 幂等去重：已有 pending/done 任务不重复入队

    if max_attempts is None:
        max_attempts = await _max_attempts_default(db)
    task = Rag_sync_tasks(
        idempotency_key=key,
        task_type=task_type,
        case_id=case_id,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        status=STATUS_PENDING,
        max_attempts=max_attempts,
        created_by=created_by,
        next_retry_at=None,  # 立即可重试
    )
    db.add(task)
    return task


# ------------------ 同步入口（业务层调用） ------------------

async def sync_case_upsert(db: AsyncSession, actor: str, case: Any) -> Dict[str, Any]:
    """发布/更新/回滚后的索引同步：upsert + 回读验证 + 缓存失效，失败自动入补偿队列。"""
    content_hash = _content_hash(_case_payload(case))
    idem_key = _make_task_key(TASK_UPSERT, case.case_id, content_hash)
    payload = _case_payload(case, idempotency_key=idem_key, content_hash=content_hash)
    base = await _rag_base_url(db)
    api_key = await _rag_api_key(db)

    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过索引同步", "case_id": case.case_id}
        await write_audit(
            db, actor=actor, action="rag_index_sync", target_type="kb_case",
            target_id=case.case_id, after={"trigger": "upsert", **result},
        )
        return result

    cache_payload = {"case_id": case.case_id, "reason": "upsert"}
    try:
        resp = await _http_post(base, "/api/v1/kb-sync/upsert", payload, api_key=api_key)
        resp.raise_for_status()
        verified = await _http_verify(base, case.case_id, api_key=api_key)
        if not verified:
            raise RuntimeError("upsert succeeded but verify failed (row not found in index)")
        # 缓存失效（best effort，失败不阻断同步闭环；HTTP 5xx/4xx 同样视为失败入队）
        try:
            cache_resp = await _http_post(base, "/api/v1/kb-cache/invalidate", cache_payload, api_key=api_key)
            cache_resp.raise_for_status()
        except Exception as cache_exc:  # noqa: BLE001
            logger.warning("Cache invalidate failed for %s: %s", case.case_id, cache_exc)
            await _enqueue_task(db, TASK_CACHE, case.case_id, cache_payload,
                                _content_hash(cache_payload), created_by=actor)
        result = {
            "ok": True,
            "case_id": case.case_id,
            "verified": True,
            "upsert": {"status": resp.json().get("status"), "exists": resp.json().get("exists")},
        }
    except httpx.HTTPStatusError as exc:
        if _is_stale_version_rejected(exc):
            # 版本守卫终态：RAG 拒绝旧版本覆盖 → 索引保持新版本。
            # 旧版本载荷重试永远 409，不入补偿队列；索引未变更，缓存无需失效。
            logger.info("RAG index sync (upsert) rejected stale version for %s", case.case_id)
            result = {
                "ok": False,
                "case_id": case.case_id,
                "stale_version": True,
                "error": "stale_version_rejected: index holds a newer kb_version",
            }
        else:
            logger.warning("RAG index sync (upsert) failed for %s: %s", case.case_id, exc)
            result = {"ok": False, "case_id": case.case_id, "error": f"{type(exc).__name__}: {exc}"}
            await _enqueue_task(db, TASK_UPSERT, case.case_id, payload, content_hash, created_by=actor)
            await _enqueue_task(db, TASK_CACHE, case.case_id, cache_payload,
                                _content_hash(cache_payload), created_by=actor)
    except Exception as exc:  # noqa: BLE001 - 同步失败降级为补偿任务
        logger.warning("RAG index sync (upsert) failed for %s: %s", case.case_id, exc)
        result = {"ok": False, "case_id": case.case_id, "error": f"{type(exc).__name__}: {exc}"}
        await _enqueue_task(db, TASK_UPSERT, case.case_id, payload, content_hash, created_by=actor)
        await _enqueue_task(db, TASK_CACHE, case.case_id, cache_payload,
                            _content_hash(cache_payload), created_by=actor)
    await write_audit(
        db, actor=actor, action="rag_index_sync", target_type="kb_case",
        target_id=case.case_id, after={"trigger": "upsert", **result},
    )
    return result


async def sync_cases_delete(db: AsyncSession, actor: str, case_ids: List[str]) -> Dict[str, Any]:
    """归档/合并后的索引删除同步 + 缓存失效，失败按 case_id 逐个入补偿队列。"""
    if not case_ids:
        return {"ok": True, "skipped": "无删除对象"}
    base = await _rag_base_url(db)
    api_key = await _rag_api_key(db)

    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过索引删除", "case_ids": case_ids}
        await write_audit(
            db, actor=actor, action="rag_index_sync", target_type="kb_case",
            target_id=",".join(case_ids), after={"trigger": "delete", **result},
        )
        return result

    try:
        resp = await _http_post(base, "/api/v1/kb-sync/delete", {"case_ids": case_ids}, api_key=api_key)
        resp.raise_for_status()
        # 删除后逐个验证（任一仍存在 → 视为失败）
        failed_verifies = []
        for cid in case_ids:
            if await _http_verify(base, cid, api_key=api_key):
                failed_verifies.append(cid)
        if failed_verifies:
            raise RuntimeError(f"delete succeeded but {failed_verifies} still in index")
        # 缓存失效（best effort；HTTP 5xx/4xx 同样视为失败入队）
        for cid in case_ids:
            cache_payload = {"case_id": cid, "reason": "delete"}
            try:
                cache_resp = await _http_post(base, "/api/v1/kb-cache/invalidate", cache_payload, api_key=api_key)
                cache_resp.raise_for_status()
            except Exception as cache_exc:  # noqa: BLE001
                logger.warning("Cache invalidate failed for %s: %s", cid, cache_exc)
                await _enqueue_task(db, TASK_CACHE, cid, cache_payload,
                                    _content_hash(cache_payload), created_by=actor)
        result = {"ok": True, "deleted": case_ids}
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG index sync (delete) failed for %s: %s", case_ids, exc)
        result = {"ok": False, "case_ids": case_ids, "error": f"{type(exc).__name__}: {exc}"}
        for cid in case_ids:
            delete_payload = {"case_ids": [cid]}
            await _enqueue_task(db, TASK_DELETE, cid, delete_payload,
                                _content_hash(delete_payload), created_by=actor)
            cache_payload = {"case_id": cid, "reason": "delete"}
            await _enqueue_task(db, TASK_CACHE, cid, cache_payload,
                                _content_hash(cache_payload), created_by=actor)
    await write_audit(
        db, actor=actor, action="rag_index_sync", target_type="kb_case",
        target_id=",".join(case_ids), after={"trigger": "delete", **result},
    )
    return result


async def sync_feedback(db: AsyncSession, actor: str, case_id: str, delta: int) -> Dict[str, Any]:
    """反馈回流同步（P0-3 增强）：👍/👎 同步 Milvus 计数，带幂等键防重放加分，失败入补偿队列。

    幂等键 = `feedback:{case_id}:{uuid}`，每次用户点击生成新键；
    补偿重试复用同一键 → RAG 侧按幂等键去重，不重复加分。
    """
    idem_key = f"feedback:{case_id}:{uuid.uuid4().hex[:12]}"
    payload = {"case_id": case_id, "score": delta, "idempotency_key": idem_key}
    base = await _rag_base_url(db)
    api_key = await _rag_api_key(db)

    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过反馈同步", "case_id": case_id}
    else:
        try:
            resp = await _http_post(base, "/api/v1/feedback", payload, api_key=api_key)
            resp.raise_for_status()
            result = {
                "ok": True, "case_id": case_id, "delta": delta,
                "feedback_score": resp.json().get("feedback_score"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG feedback sync failed for %s: %s", case_id, exc)
            result = {"ok": False, "case_id": case_id, "delta": delta,
                      "error": f"{type(exc).__name__}: {exc}"}
            # 反馈任务以幂等键本身为任务内容指纹（每次点击独立任务，重试复用同键防重复加分）
            await _enqueue_task(db, TASK_FEEDBACK, case_id, payload, idem_key, created_by=actor)
    await write_audit(
        db, actor=actor, action="rag_feedback_sync", target_type="kb_case",
        target_id=case_id, after=result,
    )
    return result


# ------------------ 补偿任务执行与重试 ------------------

async def _execute_task(db: AsyncSession, task: Rag_sync_tasks, base: str) -> bool:
    """执行单个补偿任务：成功清账 / 失败退避 / 重试耗尽入死信。"""
    payload = json.loads(task.payload_json or "{}")
    task.attempt_count = (task.attempt_count or 0) + 1
    task.last_attempt_at = datetime.now(timezone.utc)
    retry_base = await _retry_base_seconds(db)
    max_attempts = task.max_attempts or await _max_attempts_default(db)
    resp = None
    api_key = await _rag_api_key(db)

    try:
        if task.task_type == TASK_UPSERT:
            resp = await _http_post(base, "/api/v1/kb-sync/upsert", payload, api_key=api_key)
            resp.raise_for_status()
            verified = await _http_verify(base, task.case_id or "", api_key=api_key)
            if not verified:
                raise RuntimeError("upsert succeeded but verify failed (row not found in index)")
            task.verified = True
        elif task.task_type == TASK_DELETE:
            resp = await _http_post(base, "/api/v1/kb-sync/delete", payload, api_key=api_key)
            resp.raise_for_status()
            for cid in payload.get("case_ids", [task.case_id]):
                if await _http_verify(base, cid, api_key=api_key):
                    raise RuntimeError(f"delete succeeded but case {cid} still in index")
            task.verified = True
        elif task.task_type == TASK_FEEDBACK:
            resp = await _http_post(base, "/api/v1/feedback", payload, api_key=api_key)
            resp.raise_for_status()
            task.verified = True  # 反馈带幂等键，重放安全
        elif task.task_type == TASK_CACHE:
            resp = await _http_post(base, "/api/v1/kb-cache/invalidate", payload, api_key=api_key)
            resp.raise_for_status()
            task.verified = True
        else:
            raise RuntimeError(f"unknown task_type: {task.task_type}")

        task.status = STATUS_DONE
        task.next_retry_at = None
        task.last_error = None
        task.verify_detail = json.dumps({"http_status": resp.status_code}, ensure_ascii=False)
        return True
    except Exception as exc:  # noqa: BLE001
        task.last_error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, httpx.HTTPStatusError):
            task.last_http_status = exc.response.status_code
            if _is_stale_version_rejected(exc):
                # 版本守卫终态清账：旧版本 upsert 被 RAG 409 拒绝（stale_version_rejected）。
                # 同载荷重试永远 409 → 标记 done 结束重试链，不进死信；verify_detail 留痕供审计。
                task.status = STATUS_DONE
                task.verified = False
                task.next_retry_at = None
                task.verify_detail = json.dumps(
                    {"resolved": "stale_version", "http_status": 409,
                     "body": _response_body(exc.response)},
                    ensure_ascii=False,
                )
                logger.info("Task %s resolved as stale version (409), no retry", task.idempotency_key)
                return True
        if task.attempt_count >= max_attempts:
            task.status = STATUS_DEAD
            task.next_retry_at = None
            logger.warning("Task %s -> DEAD after %d attempts: %s",
                           task.idempotency_key, task.attempt_count, task.last_error)
        else:
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=_backoff_seconds(retry_base, task.attempt_count)
            )
        return False


async def retry_due_tasks(
    db: AsyncSession,
    limit: int = 20,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """扫描到期补偿任务并执行（由运维 API 或定时调度触发）。

    Returns:
        {"executed", "succeeded", "dead_lettered", "pending_remaining", "dry_run"}
    """
    now = datetime.now(timezone.utc)
    # P2-3 并发领取：FOR UPDATE SKIP LOCKED 行级锁，多实例/并发触发时同一到期任务仅被一个 worker 领取，
    # 其余 worker 跳过被锁行，避免重复补偿执行（幂等键仍作最终兜底）。
    result = await db.execute(
        select(Rag_sync_tasks)
        .where(
            Rag_sync_tasks.status == STATUS_PENDING,
            (Rag_sync_tasks.next_retry_at.is_(None)) | (Rag_sync_tasks.next_retry_at <= now),
        )
        .order_by(Rag_sync_tasks.next_retry_at.asc().nullsfirst())
        .limit(max(1, limit))
        .with_for_update(skip_locked=True)
    )
    tasks = list(result.scalars().all())

    executed = 0
    succeeded = 0
    dead_lettered = 0
    base = None
    if not dry_run and tasks:
        base = await _rag_base_url(db)

    for task in tasks:
        if dry_run:
            executed += 1
            continue
        if not base:
            # RAG 未部署 → 标记错误但保持 pending（base 配置好后自动重试）
            task.last_error = "rag_base_url not configured"
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=await _retry_base_seconds(db)
            )
            continue
        ok = await _execute_task(db, task, base)
        executed += 1
        if ok:
            succeeded += 1
        elif task.status == STATUS_DEAD:
            dead_lettered += 1

    if not dry_run:
        await db.commit()

    return {
        "executed": executed,
        "succeeded": succeeded,
        "dead_lettered": dead_lettered,
        "pending_remaining": await _count_by_status(db, STATUS_PENDING),
        "dry_run": dry_run,
    }


async def _count_by_status(db: AsyncSession, status: str) -> int:
    """按状态统计补偿任务数量。"""
    result = await db.execute(
        select(func.count(Rag_sync_tasks.id)).where(Rag_sync_tasks.status == status)
    )
    return int(result.scalar() or 0)


# ------------------ 统计与查询 ------------------

async def task_stats(db: AsyncSession) -> Dict[str, Any]:
    """按状态统计补偿任务数量（供运维 Dashboard 展示）。"""
    result = await db.execute(
        select(Rag_sync_tasks.status, func.count(Rag_sync_tasks.id)).group_by(Rag_sync_tasks.status)
    )
    rows = result.all()
    status_counts = {row[0]: int(row[1]) for row in rows}
    return {
        "pending": status_counts.get(STATUS_PENDING, 0),
        "done": status_counts.get(STATUS_DONE, 0),
        "dead": status_counts.get(STATUS_DEAD, 0),
        "total": sum(status_counts.values()),
    }


def _serialize_task(t: Rag_sync_tasks) -> Dict[str, Any]:
    """补偿任务行 → API 响应字典。"""
    return {
        "id": t.id,
        "idempotency_key": t.idempotency_key,
        "task_type": t.task_type,
        "case_id": t.case_id,
        "status": t.status,
        "attempt_count": t.attempt_count,
        "max_attempts": t.max_attempts,
        "next_retry_at": t.next_retry_at.isoformat() if t.next_retry_at else None,
        "last_attempt_at": t.last_attempt_at.isoformat() if t.last_attempt_at else None,
        "last_error": t.last_error,
        "last_http_status": t.last_http_status,
        "verified": t.verified,
        "verify_detail": t.verify_detail,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


async def list_tasks(
    db: AsyncSession,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    case_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """补偿任务列表查询（供运维 API 与控制台展示）。"""
    query = select(Rag_sync_tasks)
    count_query = select(func.count(Rag_sync_tasks.id))
    if status:
        query = query.where(Rag_sync_tasks.status == status)
        count_query = count_query.where(Rag_sync_tasks.status == status)
    if task_type:
        query = query.where(Rag_sync_tasks.task_type == task_type)
        count_query = count_query.where(Rag_sync_tasks.task_type == task_type)
    if case_id:
        query = query.where(Rag_sync_tasks.case_id == case_id)
        count_query = count_query.where(Rag_sync_tasks.case_id == case_id)

    query = query.order_by(Rag_sync_tasks.created_at.desc().nullslast()).limit(limit).offset(offset)
    rows = (await db.execute(query)).scalars().all()
    total = (await db.execute(count_query)).scalar() or 0
    return {"total": int(total), "items": [_serialize_task(t) for t in rows]}


async def replay_task(db: AsyncSession, actor: str, task_id: int) -> Dict[str, Any]:
    """人工重放单个补偿任务：立即执行一次（忽略 next_retry_at 与退避窗口）。"""
    result = await db.execute(
        select(Rag_sync_tasks).where(Rag_sync_tasks.id == task_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if task is None:
        return {"ok": False, "error": f"task {task_id} not found"}
    if task.status == STATUS_DONE:
        return {"ok": True, "status": STATUS_DONE, "message": "任务已完成，无需重放"}

    base = await _rag_base_url(db)
    if not base:
        return {"ok": False, "error": "rag_base_url 未配置，无法重放"}

    task.status = STATUS_PENDING  # 死信任务先重置
    ok = await _execute_task(db, task, base)
    await db.commit()
    await write_audit(
        db, actor=actor, action="rag_sync_task_replay", target_type="rag_sync_task",
        target_id=task_id, after={"ok": ok, "status": task.status, "attempt_count": task.attempt_count},
    )
    return {"ok": ok, "status": task.status, "attempt_count": task.attempt_count,
            "last_error": task.last_error}


async def replay_dead_tasks(db: AsyncSession, actor: str, limit: int = 50) -> Dict[str, Any]:
    """批量重放死信任务：重置计数后重新排队（等待下次 retry_due_tasks 执行）。"""
    result = await db.execute(
        select(Rag_sync_tasks)
        .where(Rag_sync_tasks.status == STATUS_DEAD)
        .order_by(Rag_sync_tasks.updated_at.asc().nullsfirst())
        .limit(max(1, limit))
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        t.status = STATUS_PENDING
        t.attempt_count = 0
        t.next_retry_at = None
        t.last_error = None
    await db.commit()
    await write_audit(
        db, actor=actor, action="rag_sync_task_replay_batch", target_type="rag_sync_task",
        target_id=f"batch:{len(tasks)}", after={"requeued": len(tasks)},
    )
    return {"requeued": len(tasks), "task_ids": [t.id for t in tasks]}
