"""Elasticsearch 冷存储封装：批量异步写入（攒 100 条或 5 秒刷一次）。"""
import threading
import time
from typing import List

from ..utils.logger import get_logger

logger = get_logger(__name__)

_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "event_id": {"type": "keyword"},
            "fingerprint": {"type": "keyword"},
            "service_name": {"type": "keyword"},
            "cluster": {"type": "keyword"},
            "namespace": {"type": "keyword"},
            "error_type": {"type": "keyword"},
            "severity": {"type": "integer"},
            "confidence": {"type": "float"},
            "template": {"type": "text"},
            "raw_log": {"type": "text"},
            "topology": {"type": "object", "enabled": False},
            "timestamp": {"type": "long"},
        }
    }
}

_BUFFER_SIZE = 100
_FLUSH_INTERVAL_SECONDS = 5.0


class ESClient:
    """审计/离线训练用的冷存储客户端，后台线程批量刷盘。"""

    def __init__(self, es_host: str, index: str = "aiops-events"):
        self.index = index
        self._es = None
        self._buffer: List[dict] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._connect(es_host)
        self._ensure_index()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True, name="es-flusher")
        self._flusher.start()

    def _connect(self, es_host: str) -> None:
        try:
            from elasticsearch import Elasticsearch

            self._es = Elasticsearch(es_host, request_timeout=5)
            if not self._es.ping():
                logger.warning("Elasticsearch ping failed: %s", es_host)
                self._es = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Elasticsearch connect failed: %s", exc)
            self._es = None

    def _ensure_index(self) -> None:
        try:
            if self._es and not self._es.indices.exists(index=self.index):
                self._es.indices.create(index=self.index, **_INDEX_MAPPING)
        except Exception as exc:  # noqa: BLE001
            logger.error("ES ensure index failed: %s", exc)

    # ---------- 对外接口 ----------
    def write(self, event: dict) -> None:
        """追加到缓冲区，达到阈值立即刷盘。"""
        with self._lock:
            self._buffer.append(event)
            should_flush = len(self._buffer) >= _BUFFER_SIZE
        if should_flush:
            self.flush()

    def flush(self) -> int:
        """批量写入缓冲区数据，返回成功条数。"""
        with self._lock:
            batch, self._buffer = self._buffer, []
        if not batch:
            return 0
        if self._es is None:
            logger.debug("ES unavailable, drop %d events (audit only)", len(batch))
            return 0
        try:
            from elasticsearch.helpers import bulk

            actions = [
                {"_index": self.index, "_source": e, "_id": e.get("event_id")}
                for e in batch
            ]
            success, _ = bulk(self._es, actions, raise_on_error=False)
            logger.info("ES bulk written %d events", success)
            return success
        except Exception as exc:  # noqa: BLE001
            logger.error("ES bulk write failed: %s", exc)
            return 0

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(_FLUSH_INTERVAL_SECONDS):
            self.flush()

    def close(self) -> None:
        self._stop_event.set()
        self._flusher.join(timeout=3)
        self.flush()
