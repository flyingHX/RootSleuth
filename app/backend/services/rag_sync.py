"""控制台 → RAG 检索侧同步服务（评审 P0-1/P0-3 闭环落地）。

闭环语义：
- 发布（create/update/rollback）→ `POST /api/v1/kb-sync/upsert` 同步 Milvus 索引 →
  `GET /api/v1/kb-sync/verify` 回读验证，检索侧"真正可见"才闭环；
- 归档（手工归档 / 合并冗余 / 生命周期巡检）→ `POST /api/v1/kb-sync/delete` 删除索引，
  避免"僵尸知识"继续被召回；
- 反馈（👍/👎）→ `POST /api/v1/feedback` 同步 Milvus feedback_score/upvotes/downvotes，
  与 PostgreSQL 双写保持一致，供 L2 重排 f4/f7 特征使用。

降级语义（不阻断业务）：
- `rag_base_url` 未配置 → 视为未部署 RAG 服务，同步跳过并留审计（skipped）；
- HTTP/超时/服务异常 → 同步失败只写审计（ok=false），业务发布/归档/反馈照常完成；
  由运维按审计 `rag_index_sync` / `rag_feedback_sync` 记录补偿（重放发布或重跑巡检）。
"""
import logging
from typing import Any, Dict, List

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from services.console_common import get_config, write_audit

logger = logging.getLogger(__name__)

SYNC_TIMEOUT_SECONDS = 5.0


async def _rag_base_url(db: AsyncSession) -> str:
    """读取 RAG 服务地址（配置中心实时生效；留空表示未部署，同步跳过）。"""
    return str(await get_config(db, "rag_base_url", "") or "").strip().rstrip("/")


def _case_payload(case: Any) -> Dict[str, Any]:
    """Kb_cases ORM → kb-sync/upsert 载荷（含反馈计数，upsert 全量覆盖索引行）。"""
    return {
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
    }


async def sync_case_upsert(db: AsyncSession, actor: str, case: Any) -> Dict[str, Any]:
    """发布/更新/回滚后的索引同步：upsert + 回读验证，结果写审计（不抛异常）。"""
    base = await _rag_base_url(db)
    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过索引同步", "case_id": case.case_id}
        await write_audit(
            db, actor=actor, action="rag_index_sync", target_type="kb_case",
            target_id=case.case_id, after={"trigger": "upsert", **result},
        )
        return result
    try:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{base}/api/v1/kb-sync/upsert", json=_case_payload(case))
            resp.raise_for_status()
            upsert_body = resp.json()
            verify_resp = await client.get(f"{base}/api/v1/kb-sync/verify", params={"case_id": case.case_id})
            verify_resp.raise_for_status()
            verified = bool(verify_resp.json().get("exists"))
        result = {
            "ok": True,
            "case_id": case.case_id,
            "verified": verified,
            "upsert": {"status": upsert_body.get("status"), "exists": upsert_body.get("exists")},
        }
    except Exception as exc:  # noqa: BLE001 - 同步失败降级为审计标记
        logger.warning("RAG index sync (upsert) failed for %s: %s", case.case_id, exc)
        result = {"ok": False, "case_id": case.case_id, "error": f"{type(exc).__name__}: {exc}"}
    await write_audit(
        db, actor=actor, action="rag_index_sync", target_type="kb_case",
        target_id=case.case_id, after={"trigger": "upsert", **result},
    )
    return result


async def sync_cases_delete(db: AsyncSession, actor: str, case_ids: List[str]) -> Dict[str, Any]:
    """归档/合并后的索引删除同步，结果写审计（不抛异常）。"""
    if not case_ids:
        return {"ok": True, "skipped": "无删除对象"}
    base = await _rag_base_url(db)
    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过索引删除", "case_ids": case_ids}
        await write_audit(
            db, actor=actor, action="rag_index_sync", target_type="kb_case",
            target_id=",".join(case_ids), after={"trigger": "delete", **result},
        )
        return result
    try:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{base}/api/v1/kb-sync/delete", json={"case_ids": case_ids})
            resp.raise_for_status()
        result = {"ok": True, "deleted": case_ids}
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG index sync (delete) failed for %s: %s", case_ids, exc)
        result = {"ok": False, "case_ids": case_ids, "error": f"{type(exc).__name__}: {exc}"}
    await write_audit(
        db, actor=actor, action="rag_index_sync", target_type="kb_case",
        target_id=",".join(case_ids), after={"trigger": "delete", **result},
    )
    return result


async def sync_feedback(db: AsyncSession, actor: str, case_id: str, delta: int) -> Dict[str, Any]:
    """反馈回流同步（P0-3）：👍/👎 同步 Milvus feedback_score（+1/-1），结果写审计。"""
    base = await _rag_base_url(db)
    if not base:
        result = {"ok": False, "skipped": "rag_base_url 未配置，跳过反馈同步", "case_id": case_id}
    else:
        try:
            async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{base}/api/v1/feedback", json={"case_id": case_id, "score": delta}
                )
                resp.raise_for_status()
                result = {"ok": True, "case_id": case_id, "delta": delta, "feedback_score": resp.json().get("feedback_score")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG feedback sync failed for %s: %s", case_id, exc)
            result = {"ok": False, "case_id": case_id, "delta": delta, "error": f"{type(exc).__name__}: {exc}"}
    await write_audit(
        db, actor=actor, action="rag_feedback_sync", target_type="kb_case",
        target_id=case_id, after=result,
    )
    return result
