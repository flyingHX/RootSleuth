"""知识检索接口（评审 P0-1）：供控制台诊断 Agent 的 search_kb 工具调用。

与 /diagnostic 的差异：只做召回 + 四层重排（不调用诊断 LLM），返回 Top-K 相似案例，
供 Agent 自行交叉验证。遵循全系统 fail-open 语义：召回异常返回空列表，
由调用方（控制台）降级为本地 PostgreSQL 候选召回。

P0 安全增强：
- 服务间鉴权：需要 API Key（read scope），见 src/api/security.py；
- 租户隔离：按调用方 tenant_id 过滤召回（default 租户仅见公共层；非 default
  租户可见 公共层+自身），越权召回率目标 0%；
- 旧 Milvus 集合缺失 tenant_id 字段时跳过过滤（Schema 交集兼容）。
"""
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..utils.metrics import kb_search_requests_total
from .security import ServiceIdentity, require_auth

router = APIRouter()


class KBSearchRequest(BaseModel):
    """控制台 search_kb 工具 → RAG 检索载荷（宽松字段，均可缺省）。"""

    service_name: str = ""
    error_type: str = ""
    template: str = ""
    cluster: str = ""
    environment: str = Field(default="", description="部署环境（P0-2 环境隔离过滤；空=不过滤）")
    top_k: int = Field(default=5, ge=1, le=10)


@router.post("/kb-search")
def kb_search(req: KBSearchRequest, identity: ServiceIdentity = Depends(require_auth("read"))):
    """向量召回 + 四层重排的只读知识检索（无 LLM，低延迟，按租户隔离）。

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
        "environment": (req.environment or "").strip() or None,
        "timestamp": int(time.time() * 1000),
    }
    cases = get_pipeline_tenant_safe(req, event, identity)
    kb_search_requests_total.labels(result="hit" if cases else "empty").inc()
    return {"source": "milvus_rag", "cases": cases, "tenant_id": identity.tenant_id}


def get_pipeline_tenant_safe(req: KBSearchRequest, event: dict, identity: ServiceIdentity):
    """检索入口（租户透传），异常 fail-open 返回空列表。"""
    from ..runtime import get_pipeline

    try:
        return get_pipeline().retrieve_top_cases(
            event, top_k=req.top_k, tenant_id=identity.tenant_id
        )
    except Exception:  # noqa: BLE001 - 检索异常静默降级为空列表（调用方降级本地召回）
        return []
