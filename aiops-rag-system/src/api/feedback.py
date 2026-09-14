"""反馈评分与告警闭环接口：知识库自进化闭环的入口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.event import StandardizedEvent
from ..models.response import FeedbackResponse
from ..runtime import get_pipeline, get_redis
from ..utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


class FeedbackRequest(BaseModel):
    case_id: str
    score: int = Field(description="+1 有用 / -1 没用")


class CaseCloseRequest(BaseModel):
    event_id: str
    root_cause: str
    solution: str
    resolved_by: str = "human"  # human=人工关闭 / auto=自愈脚本成功


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest):
    """ChatOps 卡片 👍/👎 回调：更新 Milvus 中对应案例的 feedback_score。"""
    if req.score not in (1, -1):
        raise HTTPException(status_code=422, detail="score must be +1 or -1")
    new_score = get_pipeline().milvus.update_feedback(req.case_id, req.score)
    return FeedbackResponse(status="success", case_id=req.case_id, feedback_score=new_score)


@router.post("/cases/close", response_model=FeedbackResponse)
async def close_case(req: CaseCloseRequest):
    """告警被人工关闭 / 自愈成功时，将根因与方案写入知识库（按 fingerprint 去重 upsert）。"""
    event_dict = get_redis().get_event(req.event_id)
    if not event_dict:
        raise HTTPException(status_code=404, detail="Event not found")

    event = StandardizedEvent(**event_dict)
    case_id = get_pipeline().write_case(
        event,
        root_cause=req.root_cause,
        solution=req.solution,
        resolved_by=req.resolved_by,
    )
    return FeedbackResponse(status="success", case_id=case_id, feedback_score=0)
