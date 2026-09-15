"""知识索引同步接口（评审 P0-1：发布 → 索引 → 检索验证 闭环）。

调用方：运营控制台 `services/rag_sync.py`（审批发布 / 归档 / 合并 / 回滚 / 生命周期巡检）。

闭环语义：
- `POST /api/v1/kb-sync/upsert`：控制台发布（create/update/rollback）知识案例后，按 case_id
  直接 upsert Milvus 索引（无需 StandardizedEvent），并回读验证检索侧可见性；
- `POST /api/v1/kb-sync/delete`：案例归档 / 合并冗余后，从索引删除，避免"僵尸知识"继续被召回；
- `GET  /api/v1/kb-sync/verify`：按 case_id 查询索引行，供控制台发布后的检索验证。

认证：内网服务间调用（与 /api/v1/feedback 同级）。遵循全系统 fail-open 语义：
依赖不可用返回 503，由调用方降级为审计标记（rag_index_sync ok=false），不阻断控制台业务。
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
    created_at: Optional[int] = Field(default=None, description="毫秒时间戳；缺省取当前时间")


class CaseDeleteRequest(BaseModel):
    case_ids: List[str]


_PRESERVE_COUNT_FIELDS = ("upvotes", "downvotes", "hit_count", "recall_count")


@router.post("/kb-sync/upsert")
async def kb_sync_upsert(req: CaseUpsertRequest):
    """发布/更新/回滚同步：按 case_id upsert 索引并回读验证。"""
    pipeline = get_pipeline()
    try:
        payload = req.model_dump()
        existing = pipeline.milvus.query_by_case_id(req.case_id)
        if existing:
            prev = existing[0]
            # 内容更新不清零检索侧统计：保留 RAG 反馈/召回累计计数
            for key in _PRESERVE_COUNT_FIELDS:
                if prev.get(key) is not None:
                    payload[key] = int(prev.get(key) or 0)
        case_id = pipeline.upsert_console_case(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"索引同步失败：{exc}")
    except Exception as exc:  # noqa: BLE001 - Embedding/向量维度等异常统一降级为 503
        raise HTTPException(status_code=503, detail=f"索引同步异常：{type(exc).__name__}: {exc}")
    rows = pipeline.milvus.query_by_case_id(case_id)
    return {
        "status": "success",
        "case_id": case_id,
        "exists": bool(rows),
        "row": rows[0] if rows else None,
    }


@router.post("/kb-sync/delete")
async def kb_sync_delete(req: CaseDeleteRequest):
    """归档/合并同步：按 case_id 列表批量删除索引行。"""
    if not req.case_ids:
        raise HTTPException(status_code=422, detail="case_ids 不能为空")
    ok = get_pipeline().milvus.delete_cases(req.case_ids)
    if not ok:
        raise HTTPException(status_code=503, detail="索引删除失败（Milvus 不可用）")
    return {"status": "success", "deleted": req.case_ids}


@router.get("/kb-sync/verify")
async def kb_sync_verify(case_id: str):
    """检索验证：按 case_id 查询索引行，控制台发布后调用以确认检索侧可见。"""
    rows = get_pipeline().milvus.query_by_case_id(case_id)
    return {"case_id": case_id, "exists": bool(rows), "row": rows[0] if rows else None}
