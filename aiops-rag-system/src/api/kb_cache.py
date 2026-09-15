"""语义缓存失效与多实例广播接口（补偿闭环：RAG 真实缓存失效）。

闭环语义：
- 控制台发布/更新/回滚/归档/删除知识案例后 → 调用 `POST /api/v1/kb-cache/invalidate`；
- RAG 按 case_id 精准失效相关语义缓存（Embedding LRU + 知识行缓存），
  并通过 Redis Pub/Sub 广播到全部 RAG 实例（多实例部署时每台都清缓存）；
- `GET /api/v1/kb-cache/status`：查看当前缓存 epoch 与本地缓存规模（运维观测）。

fail-open：Redis 不可用时仅清本地进程内缓存（单实例语义仍正确），广播静默降级。
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..runtime import get_pipeline, get_redis

router = APIRouter()


class CacheInvalidateRequest(BaseModel):
    """缓存失效载荷（控制台 → RAG）。"""

    case_id: str = Field(description="按 case_id 精准失效（当前实现清整个 Embedding LRU，为后续 key 化预留）")
    reason: str = Field(default="upsert", description="失效原因：upsert/delete/rollback/archive/manual")


@router.post("/kb-cache/invalidate")
async def kb_cache_invalidate(req: CacheInvalidateRequest):
    """缓存失效入口：清本地缓存 + Redis Pub/Sub 广播到全部 RAG 实例。"""
    pipeline = get_pipeline()
    broadcast_ok = None
    try:
        redis_client = get_redis()
        broadcast_ok = redis_client.publish_cache_invalidate(req.case_id, req.reason)
    except Exception:  # noqa: BLE001 - 广播失败不影响本地清缓存
        broadcast_ok = False
    cleared = pipeline.invalidate_case_cache(req.case_id)
    return {
        "status": "success",
        "case_id": req.case_id,
        "reason": req.reason,
        "cleared_entries": cleared,
        "broadcast": broadcast_ok,
    }


@router.get("/kb-cache/status")
async def kb_cache_status():
    """缓存观测：epoch、本地缓存条数与 Redis 广播连通性。"""
    pipeline = get_pipeline()
    embedder = getattr(pipeline, "embedder", None)
    epoch = getattr(embedder, "cache_epoch", None)
    cache_size = 0
    if embedder is not None and hasattr(embedder, "_cache"):
        cache_size = len(embedder._cache)
    redis_ok = None
    epoch_sync = None
    try:
        redis_client = get_redis()
        redis_ok = redis_client.ping()
        if redis_ok:
            epoch_sync = redis_client.get_cache_epoch()
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {
        "cache_epoch": epoch,
        "redis_epoch": epoch_sync,
        "local_cache_entries": cache_size,
        "redis_available": redis_ok,
    }
