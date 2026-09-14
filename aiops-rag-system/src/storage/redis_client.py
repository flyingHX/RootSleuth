"""Redis 操作封装：指纹去重、时间窗口聚合、CMDB 拓扑缓存。"""
import json
from typing import Dict, List, Optional

from redis import Redis

from ..utils.logger import get_logger

logger = get_logger(__name__)


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
