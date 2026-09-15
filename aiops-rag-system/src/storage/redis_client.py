"""Redis 操作封装：指纹去重、时间窗口聚合、CMDB 拓扑缓存、语义缓存失效广播。

缓存失效广播（补偿闭环）：
- `publish_cache_invalidate(case_id, reason)`：INCR 全局 epoch + PUBLISH 广播；
- `subscribe_cache_invalidate(callback)`：后台订阅，收到广播后回调（清本地缓存）；
- `get_cache_epoch()`：读取全局 epoch（本地 epoch 落后时触发全量清空）。

fail-open：Redis 不可用时发布/订阅/epoch 读取均静默降级，单实例语义仍正确。
"""
import json
import threading
from typing import Any, Callable, Dict, List, Optional

from redis import Redis

from ..utils.logger import get_logger

logger = get_logger(__name__)

CACHE_EPOCH_KEY = "rag:cache:epoch"
CACHE_INVALIDATE_CHANNEL = "rag:cache:invalidate"


class RedisClient:
    """去重聚合器使用的 Redis 封装，异常时静默降级（保证主流程不中断）。"""

    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """连通性探测（供就绪探针使用）。"""
        try:
            return bool(self._redis.ping())
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis ping failed: %s", exc)
            return False

    # ---------- 指纹去重 ----------
    def first_seen(self, fingerprint: str, ttl_seconds: int = 3600) -> bool:
        """指纹首次出现返回 True，重复返回 False（SETNX 语义）。"""
        try:
            key = f"dedup:{fingerprint}"
            if self._redis.set(key, "1", nx=True, ex=ttl_seconds):
                return True
            self._redis.incr(f"dedup:count:{fingerprint}")
            self._redis.expire(f"dedup:count:{fingerprint}", ttl_seconds)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis first_seen failed: %s", exc)
            return True  # Redis 不可用时放行，避免告警丢失

    def get_repeat_count(self, fingerprint: str) -> int:
        try:
            return int(self._redis.get(f"dedup:count:{fingerprint}") or 0)
        except Exception:  # noqa: BLE001
            return 0

    # ---------- 时间窗口聚合 ----------
    def window_count(self, fingerprint: str, window_seconds: int = 300) -> int:
        """滑动窗口内同一指纹的告警次数（Redis ZSET 实现）。"""
        import time

        try:
            key = f"window:{fingerprint}"
            now = time.time()
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zcard(key)
            pipe.expire(key, window_seconds)
            _, count, _ = pipe.execute()
            return int(count)
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis window_count failed: %s", exc)
            return 0

    def window_add(self, fingerprint: str, payload: dict, window_seconds: int = 300) -> None:
        import time

        try:
            key = f"window:{fingerprint}"
            self._redis.zadd(key, {json.dumps(payload, ensure_ascii=False): time.time()})
            self._redis.expire(key, window_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis window_add failed: %s", exc)

    # ---------- CMDB 拓扑缓存（TTL 1 小时）----------
    def get_topology(self, service_name: str) -> Optional[Dict[str, List[str]]]:
        try:
            raw = self._redis.get(f"topology:{service_name}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def set_topology(self, service_name: str, topology: Dict[str, List[str]], ttl: int = 3600) -> None:
        try:
            self._redis.setex(
                f"topology:{service_name}", ttl, json.dumps(topology, ensure_ascii=False)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis set_topology failed: %s", exc)

    # ---------- 事件暂存（供人工诊断 / 告警闭环接口查询）----------
    def save_event(self, event: dict, ttl_seconds: int = 7 * 24 * 3600) -> None:
        try:
            self._redis.setex(
                f"event:{event.get('event_id')}",
                ttl_seconds,
                json.dumps(event, ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Redis save_event failed: %s", exc)

    def get_event(self, event_id: str) -> Optional[dict]:
        try:
            raw = self._redis.get(f"event:{event_id}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def mark_feedback_once(self, idempotency_key: str, ttl_seconds: int = 7 * 24 * 3600) -> bool:
        """反馈幂等去重：幂等键首次出现返回 True；重复返回 False（调用方跳过加分）。"""
        try:
            key = f"feedback:seen:{idempotency_key}"
            return bool(self._redis.set(key, "1", nx=True, ex=ttl_seconds))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feedback idempotency check failed (fail-open): %s", exc)
            return True  # Redis 不可用时放行，避免反馈丢失

    # ---------- 语义缓存失效（补偿闭环：多实例广播）----------
    def publish_cache_invalidate(self, case_id: str, reason: str = "manual") -> bool:
        """缓存失效广播：INCR 全局 epoch + PUBLISH 事件到全部 RAG 实例。

        Returns:
            True=广播成功；False=Redis 不可用（本地缓存仍会被调用方清空）。
        """
        try:
            self._redis.incr(CACHE_EPOCH_KEY)
            message = json.dumps({"case_id": case_id, "reason": reason}, ensure_ascii=False)
            self._redis.publish(CACHE_INVALIDATE_CHANNEL, message)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cache invalidate broadcast failed: %s", exc)
            return False

    def get_cache_epoch(self) -> int:
        """读取全局缓存 epoch（本地 epoch 落后时调用方应清空本地缓存）。"""
        try:
            return int(self._redis.get(CACHE_EPOCH_KEY) or 0)
        except Exception:  # noqa: BLE001
            return -1  # 不可用时返回哨兵值，调用方跳过对账

    def subscribe_cache_invalidate(self, callback: Callable[[str, str], None]) -> None:
        """后台订阅缓存失效广播（阻塞线程，由 main.py startup 以 daemon 线程启动）。

        callback(case_id, reason) 在收到广播时被调用（清本地缓存）。
        连接断开时自动重连（5s 间隔），Redis 不可用时静默重试。
        """
        def _listen():
            while True:
                try:
                    pubsub = self._redis.pubsub()
                    pubsub.subscribe(CACHE_INVALIDATE_CHANNEL)
                    logger.info("Subscribed to cache invalidate channel: %s", CACHE_INVALIDATE_CHANNEL)
                    for message in pubsub.listen():
                        if message.get("type") != "message":
                            continue
                        try:
                            data = json.loads(message.get("data") or "{}")
                            callback(data.get("case_id", ""), data.get("reason", "broadcast"))
                        except (json.JSONDecodeError, TypeError):
                            logger.warning("Invalid cache invalidate message: %s", message.get("data"))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Cache invalidate subscriber disconnected, retrying in 5s: %s", exc)
                    import time
                    time.sleep(5)

        thread = threading.Thread(target=_listen, daemon=True, name="cache-invalidate-subscriber")
        thread.start()
        return thread
