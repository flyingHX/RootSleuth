"""知识检索接口（评审 P0-1）：供控制台诊断 Agent 的 search_kb 工具调用。

与 /diagnostic 的差异：只做召回 + 四层重排（不调用诊断 LLM），返回 Top-K 相似案例，
供 Agent 自行交叉验证。遵循全系统 fail-open 语义：召回异常返回空列表，
由调用方（控制台）降级为本地 PostgreSQL 候选召回。
"""
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..runtime import get_pipeline

router = APIRouter()


class KBSearchRequest(BaseModel):
    """控制台 search_kb 工具 → RAG 检索载荷（宽松字段，均可缺省）。"""

    service_name: str = ""
    error_type: str = ""
    template: str = ""
    cluster: str = ""
    top_k: int = Field(default=5, ge=1, le=10)


@router.post("/kb-search")
def kb_search(req: KBSearchRequest):
    """向量召回 + 四层重排的只读知识检索（无 LLM，低延迟）。

    使用同步 def 路由：与 /diagnostic 一致放入线程池执行，避免
    Embedding/Milvus 检索阻塞事件循环。
    """
    event = {
        "event_id": "kb-search-adhoc",
        "fingerprint": "",
        "service_name": req.service_name or "",
        "cluster": req.cluster or "",
        "error_type": req.error_type or "",
        "template": req.template or "",
        "timestamp": int(time.time() * 1000),
    }
    cases = get_pipeline().retrieve_top_cases(event, top_k=req.top_k)
    return {"source": "milvus_rag", "cases": cases}
