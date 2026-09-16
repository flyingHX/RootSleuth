"""人工触发诊断接口：按 event_id 加载事件并执行完整 RAG 检索推理。

P0 鉴权：read scope（服务间 Token），检索按调用方租户隔离。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models.event import StandardizedEvent
from ..models.response import DiagnosticResult
from ..runtime import get_pipeline, get_redis
from ..utils.logger import get_logger
from .security import ServiceIdentity, require_auth

logger = get_logger(__name__)
router = APIRouter()


class DiagnosticRequest(BaseModel):
    event_id: str


@router.post("/diagnostic", response_model=DiagnosticResult)
def manual_diagnostic(req: DiagnosticRequest, identity: ServiceIdentity = Depends(require_auth("read"))):
    """人工触发根因诊断：等效于消费者 2 的 manual_trigger 路径。

    使用同步 def 路由：FastAPI 会将其放入线程池执行，避免 RAG 全链路
    （Embedding/Milvus/LLM 最多可达数秒）阻塞事件循环、拖慢 webhook 等
    其他并发请求（此前 async def + 同步 pipeline.search 是 P99 长尾的首要来源）。
    """
    event_dict = get_redis().get_event(req.event_id)
    if not event_dict:
        raise HTTPException(status_code=404, detail="Event not found")

    event = StandardizedEvent(**event_dict)
    result = get_pipeline().search(event, tenant_id=identity.tenant_id)
    result.setdefault("latency_ms", 0)
    result.setdefault("similar_cases", [])
    return DiagnosticResult(**result)
