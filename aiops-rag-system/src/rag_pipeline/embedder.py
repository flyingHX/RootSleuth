"""BGE-M3 Embedding 服务：本地 FlagEmbedding -> OpenAI 兼容 API -> 确定性降级向量。

语义缓存失效（补偿闭环增强）：
- `cache_epoch`：进程内缓存版本号；`invalidate()` 递增并清空 LRU；
- `_check_remote_epoch()`：每次 embed 前对账 Redis 全局 epoch，落后则清空本地缓存；
  对账间隔 `epoch_check_interval` 秒（默认 5s），避免每个请求都打 Redis；
- Redis 不可用时 epoch 对账静默跳过（本地单实例语义仍正确）。
"""
import hashlib
import math
import random
import threading
import time
from typing import List, Optional

import httpx

from ..utils.logger import get_logger
from ..utils.metrics import rag_embed_circuit_open_total, rag_embed_fallback_total

logger = get_logger(__name__)


class BGEEmbedder:
    """生成 1024 维查询/文档向量，带 LRU 结果缓存、失败熔断与三级降级。

    P99 长尾治理：
    - API 连续失败达到阈值后熔断，熔断窗口内直接使用确定性降级向量，
      避免每个请求都等待完整 HTTP 超时（默认 10s）形成长尾。
    - 本地模型在后台线程预热加载；请求路径上模型未就绪时立即走降级向量，
      避免 BGE-M3 冷启动（可达数十秒）阻塞首个请求。

    缓存失效（本轮新增）：
    - `invalidate()` 清空 LRU 并递增 `cache_epoch`；
    - `_check_remote_epoch()` 周期对账 Redis epoch（多实例广播对账）；
    - 缓存满时批量淘汰（clear all，与旧行为一致，避免 LRU 链开销）。
    """

    def __init__(self, config: dict):
        self.model_path: str = config.get("model_path") or "BAAI/bge-m3"
        self.api_url: str = (config.get("api_url") or "").rstrip("/")
        self.api_key: str = config.get("api_key", "")
        self.dim: int = int(config.get("dim", 1024))
        self.timeout: float = float(config.get("timeout", 10))
        self.fail_threshold: int = int(config.get("fail_threshold", 3))
        self.circuit_seconds: float = float(config.get("circuit_seconds", 30))
        self._model = None  # None=未尝试加载, False=不可用
        self._model_ready = threading.Event()  # 置位表示本地模型已就绪
        self._lock = threading.Lock()
        self._cache: dict = {}
        self.cache_epoch: int = 0  # 进程内缓存版本号（invalidate 递增）
        # Redis epoch 对账（多实例缓存失效）
        self._redis_client = config.get("redis_client")  # 可选注入，None=不启用对账
        self.epoch_check_interval: float = float(config.get("epoch_check_interval", 5))
        self._last_epoch_check: float = 0.0
        self._remote_epoch: int = -1  # -1=未探测
        # 失败熔断状态
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_lock = threading.Lock()
        # 无 API 端点时后台预热本地模型，避免首个请求承担冷启动
        if not self.api_url:
            threading.Thread(
                target=self._warmup_local_model, daemon=True, name="embedder-warmup"
            ).start()

    # ---------- 对外接口 ----------
    def embed(self, text: str) -> List[float]:
        self._check_remote_epoch()
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        circuit_open = False
        vec = None
        if self.api_url:
            circuit_open = self._circuit_is_open()
            vec = None if circuit_open else self._embed_api(text)
        elif self._model_ready.is_set():
            vec = self._embed_local(text)
        if vec is None:
            if self.api_url:
                reason = "circuit_open" if circuit_open else "api_error"
            else:
                reason = "local_unavailable"
            rag_embed_fallback_total.labels(reason=reason).inc()
            vec = self._embed_fallback(text)
        vec = self._normalize(vec)
        if len(self._cache) >= 4096:
            self._cache.clear()
        self._cache[text] = vec
        return vec

    def set_epoch_source(self, redis_client) -> None:
        """注入 Redis 全局 epoch 源（多实例缓存失效对账）；None 表示单实例模式。"""
        self._redis_client = redis_client

    def invalidate(self, case_id: str = "") -> int:
        """语义缓存失效：清空进程内 LRU 并递增 epoch。

        当前实现为全量清空（Embedding 以文本为 key，无法按 case_id 反查文本），
        case_id 参数保留接口语义（后续可扩展 key 化缓存实现精准失效）。
        Returns:
            清空的缓存条目数。
        """
        with self._lock:
            cleared = len(self._cache)
            self._cache.clear()
            self.cache_epoch += 1
        if cleared:
            logger.info("Embedding cache invalidated (case_id=%s, cleared=%d, epoch=%d)",
                        case_id or "*", cleared, self.cache_epoch)
        return cleared

    def _check_remote_epoch(self) -> None:
        """周期对账 Redis 全局 epoch：本地落后则清空缓存（多实例广播的兜底语义）。

        - Redis 不可用 / 未注入 → 静默跳过（单实例语义不受影响）；
        - 对账间隔 epoch_check_interval 秒（默认 5s），避免每请求打 Redis。
        """
        redis_client = self._redis_client
        if redis_client is None:
            return
        now = time.monotonic()
        if now - self._last_epoch_check < self.epoch_check_interval:
            return
        self._last_epoch_check = now
        try:
            remote = redis_client.get_cache_epoch()
            if remote < 0:  # Redis 不可用哨兵
                return
            if self._remote_epoch >= 0 and remote > self._remote_epoch:
                logger.info("Remote cache epoch advanced (%d -> %d), clearing local cache",
                            self._remote_epoch, remote)
                self.invalidate(case_id="epoch_sync")
            self._remote_epoch = remote
        except Exception:  # noqa: BLE001 - epoch 对账失败不影响主流程
            pass

    # ---------- 1. OpenAI 兼容 Embedding API（带失败熔断） ----------
    def _circuit_is_open(self) -> bool:
        """熔断窗口内返回 True；窗口过期后半开重置计数，放行一次探测。"""
        with self._circuit_lock:
            if self._circuit_open_until <= 0.0:
                return False
            if time.monotonic() < self._circuit_open_until:
                return True
            self._circuit_open_until = 0.0
            self._consecutive_failures = 0
            return False

    def _record_api_failure(self) -> None:
        with self._circuit_lock:
            self._consecutive_failures += 1
            if self.fail_threshold > 0 and self._consecutive_failures >= self.fail_threshold:
                self._circuit_open_until = time.monotonic() + self.circuit_seconds
                rag_embed_circuit_open_total.inc()
                logger.warning(
                    "Embedding API circuit OPEN for %ss after %d consecutive failures",
                    self.circuit_seconds,
                    self._consecutive_failures,
                )

    def _record_api_success(self) -> None:
        with self._circuit_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    def _embed_api(self, text: str) -> Optional[List[float]]:
        # 熔断判断由调用方（embed）完成，此处只负责请求与成败记录
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = httpx.post(
                f"{self.api_url}/embeddings",
                json={"model": self.model_path, "input": text},
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            vec = resp.json()["data"][0]["embedding"]
            self._record_api_success()
            return vec
        except Exception as exc:  # noqa: BLE001
            self._record_api_failure()
            logger.warning("Embedding API failed, fallback: %s", exc)
            return None

    # ---------- 2. 本地 FlagEmbedding（BGE-M3，后台预热加载） ----------
    def _warmup_local_model(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                from FlagEmbedding import BGEM3FlagModel

                self._model = BGEM3FlagModel(self.model_path, use_fp16=True)
                self._model_ready.set()
                logger.info("Local embedding model warmed up: %s", self.model_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("FlagEmbedding unavailable, fallback to hash embedder: %s", exc)
                self._model = False

    def _embed_local(self, text: str) -> Optional[List[float]]:
        # 仅在预热完成且模型可用时被调用（embed 已用 _model_ready 门控）
        try:
            vecs = self._model.encode([text], batch_size=1)["dense_vecs"]
            return vecs[0].tolist()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Local embedding failed, fallback: %s", exc)
            return None

    # ---------- 3. 确定性降级向量（仅联调/熔断使用）----------
    def _embed_fallback(self, text: str) -> List[float]:
        seed = hashlib.md5(text.encode("utf-8")).hexdigest()
        rng = random.Random(seed)
        return [rng.gauss(0.0, 1.0) for _ in range(self.dim)]

    @staticmethod
    def _normalize(vec: List[float]) -> List[float]:
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
