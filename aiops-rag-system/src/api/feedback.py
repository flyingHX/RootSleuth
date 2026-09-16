"""反馈评分与告警闭环接口：知识库自进化闭环的入口。

反馈幂等（补偿闭环增强）：
- 控制台 👍/👎 同步携带 `idempotency_key`（补偿任务重试复用同一键）；
- RAG 侧以 Redis SETNX 标记幂等键：重复提交直接返回当前分数，不重复加分；
- Redis 不可用时 fail-open（放行执行），避免反馈丢失——可用性优先于精确去重。

P0 鉴权：/feedback 与 /cases/close 均为知识写路径，需要 write scope。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models.event import StandardizedEvent
from ..models.response import FeedbackResponse
from ..rag_pipeline.content_guard import PoisonedContentError, assert_case_content_safe
from ..runtime import get_pipeline, get_redis
from ..utils.logger import get_logger
from .security import ServiceIdentity, require_auth

logger = get_logger(__name__)
router = APIRouter()


class FeedbackRequest(BaseModel):
    case_id: str
    score: int = Field(description="+1 有用 / -1 没用")
    idempotency_key: Optional[str] = Field(
        default=None, description="幂等键：补偿重试复用同一键，防重复加分"
    )


class CaseCloseRequest(BaseModel):
    event_id: str
    root_cause: str
    solution: str
    resolved_by: str = "human"  # human=人工关闭 / auto=自愈脚本成功


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest, identity: ServiceIdentity = Depends(require_auth("write"))
):
    """ChatOps 卡片 👍/👎 回调：更新 Milvus 中对应案例的 feedback_score（幂等去重）。"""
    if req.score not in (1, -1):
        raise HTTPException(status_code=422, detail="score must be +1 or -1")
    pipeline = get_pipeline()

    if req.idempotency_key:
        try:
            first_seen = get_redis().mark_feedback_once(req.idempotency_key)
        except Exception as exc:  # noqa: BLE001 - 幂等探测失败 fail-open
            logger.warning("Feedback idempotency probe failed (fail-open): %s", exc)
            first_seen = True
        if not first_seen:
            # 重放/补偿重试：返回当前分数，不重复加分
            rows = pipeline.milvus.query_by_case_id(req.case_id)
            prev_score = int(rows[0].get("feedback_score", 0) or 0) if rows else 0
            logger.info(
                "Feedback deduplicated (key=%s, case=%s, score=%s)",
                req.idempotency_key, req.case_id, prev_score,
            )
            return FeedbackResponse(status="success", case_id=req.case_id, feedback_score=prev_score)

    new_score = pipeline.milvus.update_feedback(req.case_id, req.score)
    return FeedbackResponse(status="success", case_id=req.case_id, feedback_score=new_score)


@router.post("/cases/close", response_model=FeedbackResponse)
async def close_case(
    req: CaseCloseRequest, identity: ServiceIdentity = Depends(require_auth("write"))
):
    """告警被人工关闭 / 自愈成功时，将根因与方案写入知识库（按 fingerprint 去重 upsert）。

    P1 投毒预检卡点：人工关闭内容先扫描（PII/密钥/危险命令/注入），命中即 422 拒绝入库。
    """
    event_dict = get_redis().get_event(req.event_id)
    if not event_dict:
        raise HTTPException(status_code=404, detail="Event not found")

    event = StandardizedEvent(**event_dict)
    try:
        assert_case_content_safe(
            req.root_cause, req.solution, event.template, source="cases_close"
        )
        case_id = get_pipeline().write_case(
            event,
            root_cause=req.root_cause,
            solution=req.solution,
            resolved_by=req.resolved_by,
            tenant_id=identity.tenant_id,
        )
    except PoisonedContentError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "poisoned_content_rejected", **exc.result.to_detail()},
        )
    return FeedbackResponse(status="success", case_id=case_id, feedback_score=0)
