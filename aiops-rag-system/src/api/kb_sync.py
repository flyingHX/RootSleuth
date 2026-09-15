"""知识索引同步接口（评审 P0-1 + 补偿闭环增强：幂等 / 内容指纹 / 缓存失效）。

调用方：运营控制台 `services/rag_sync.py`（审批发布 / 归档 / 合并 / 回滚 / 生命周期巡检）。

闭环语义：
- `POST /api/v1/kb-sync/upsert`：控制台发布（create/update/rollback）知识案例后，按 case_id
  直接 upsert Milvus 索引（无需 StandardizedEvent），并回读验证检索侧可见性；
  幂等增强：同 case_id + 同核心文本内容 → 跳过重新 Embedding，直接返回成功（防重放）；
  版本守卫：内容变更且 kb_version 低于索引版本 → 409 拒绝旧版本覆盖（乱序补偿/重放安全）；
- `POST /api/v1/kb-sync/delete`：案例归档 / 合并冗余后，从索引删除并失效语义缓存；
- `GET  /api/v1/kb-sync/verify`：按 case_id 查询索引行，供控制台发布后的检索验证。

认证：内网服务间调用（与 /api/v1/feedback 同级）。遵循全系统 fail-open 语义：
依赖不可用返回 503，由调用方降级为补偿任务入队，不阻断控制台业务。
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..runtime import get_pipeline

router = APIRouter()


class CaseUpsertRequest(BaseModel):
    """控制台知识案例 → Milvus 索引行的同步载荷。"""

    case_id: str
    service_name: str = ""
    cluster: str = ""
    error_type: str = ""
    severity: int = 2
    root_cause: str = ""
    solution: str = ""
    alert_template: str = ""
    topology_snapshot: str = ""
    fingerprint: str = ""
    feedback_score: int = 0
    upvotes: int = 0
    downvotes: int = 0
    hit_count: int = 0
    recall_count: int = 0
    resolved_by: str = "console"
    # 幂等增强字段（补偿重试时复用同一键与内容指纹）
    kb_version: Optional[int] = Field(default=None, description="知识案例版本号（发布/回滚时递增）")
    idempotency_key: Optional[str] = Field(default=None, description="幂等键：upsert:{case_id}:{content_hash}")
    content_hash: Optional[str] = Field(default=None, description="载荷内容指纹（16 位 sha256 前缀）")
    created_at: Optional[int] = Field(default=None, description="毫秒时间戳；缺省取当前时间")


class CaseDeleteRequest(BaseModel):
    case_ids: List[str]


_PRESERVE_COUNT_FIELDS = ("upvotes", "downvotes", "hit_count", "recall_count")


@router.post("/kb-sync/upsert")
async def kb_sync_upsert(req: CaseUpsertRequest):
    """发布/更新/回滚同步：按 case_id upsert 索引并回读验证（幂等：同内容跳过重嵌入）。"""
    pipeline = get_pipeline()
    try:
        payload = req.model_dump()
        existing = pipeline.milvus.query_by_case_id(req.case_id)
        if existing:
            prev = existing[0]
            # 幂等短路（旧集合兼容）：以核心文本字段等值比较作为内容指纹的落地实现，
            # 不依赖 Milvus Schema 新增 content_hash 列（旧集合无该列，写入会失败）。
            same_content = (
                str(prev.get("root_cause") or "") == (req.root_cause or "")[:2048]
                and str(prev.get("solution") or "") == (req.solution or "")[:2048]
                and str(prev.get("alert_template") or "") == (req.alert_template or "")[:1024]
            )
            if same_content:
                return {
                    "status": "success",
                    "case_id": req.case_id,
                    "exists": True,
                    "idempotent": True,
                    "row": prev,
                }
            # 版本守卫：内容有变更且请求版本低于索引版本 → 拒绝旧版本覆盖
            #（乱序补偿/人工重放旧任务时安全；同内容旧版本已在上面的幂等短路放行）
            prev_version = int(prev.get("kb_version") or 0)
            if req.kb_version is not None and prev_version > int(req.kb_version):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "stale_version_rejected",
                        "case_id": req.case_id,
                        "index_version": prev_version,
                        "request_version": int(req.kb_version),
                    },
                )
            # 内容更新不清零检索侧统计：保留 RAG 反馈/召回累计计数
            for key in _PRESERVE_COUNT_FIELDS:
                if prev.get(key) is not None:
                    payload[key] = int(prev.get(key) or 0)
        case_id = pipeline.upsert_console_case(payload)
    except HTTPException:
        raise  # 版本守卫 409 等业务语义异常原样透出，不得降级为 503
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"索引同步失败：{exc}")
    except Exception as exc:  # noqa: BLE001 - Embedding/向量维度等异常统一降级为 503
        raise HTTPException(status_code=503, detail=f"索引同步异常：{type(exc).__name__}: {exc}")
    rows = pipeline.milvus.query_by_case_id(case_id)
    return {
        "status": "success",
        "case_id": case_id,
        "exists": bool(rows),
        "idempotent": False,
        "row": rows[0] if rows else None,
    }


@router.post("/kb-sync/delete")
async def kb_sync_delete(req: CaseDeleteRequest):
    """归档/合并同步：按 case_id 列表批量删除索引行并失效语义缓存。"""
    if not req.case_ids:
        raise HTTPException(status_code=422, detail="case_ids 不能为空")
    pipeline = get_pipeline()
    ok = pipeline.milvus.delete_cases(req.case_ids)
    if not ok:
        raise HTTPException(status_code=503, detail="索引删除失败（Milvus 不可用）")
    # 删除后失效语义缓存（best effort：缓存失败不影响删除闭环）
    for case_id in req.case_ids:
        try:
            pipeline.invalidate_case_cache(case_id)
        except Exception:  # noqa: BLE001,S110
            pass
    return {"status": "success", "deleted": req.case_ids}


@router.get("/kb-sync/verify")
async def kb_sync_verify(case_id: str):
    """检索验证：按 case_id 查询索引行，控制台发布后调用以确认检索侧可见。"""
    rows = get_pipeline().milvus.query_by_case_id(case_id)
    return {"case_id": case_id, "exists": bool(rows), "row": rows[0] if rows else None}
