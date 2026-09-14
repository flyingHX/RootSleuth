"""消费者 2：AI 检索引擎（group=alert-rag-group）。
仅 severity=3 (critical) 或人工触发执行 RAG：Milvus 双路召回 + LLM 推理 -> 返回根因建议。
延迟要求 < 5 秒；积压时采样分析（每 10 条处理 1 条）。
"""
import json

from ..rag_pipeline.pipeline import RAGPipeline
from ..utils.logger import get_logger
from ..utils.metrics import rag_search_total

logger = get_logger(__name__)

_LAG_SAMPLE_THRESHOLD = 10000  # 消费积压超过该值时采样分析
_SAMPLE_EVERY = 10


class RAGConsumer:
    """critical 告警自动触发根因推理，结果推送 ChatOps。"""

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str, pipeline: RAGPipeline):
        self.pipeline = pipeline
        self._consumer = None
        self._sampling = False
        try:
            from kafka import KafkaConsumer

            self._consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="latest",
                enable_auto_commit=False,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG consumer connect failed: %s", exc)

    def run(self) -> None:
        if self._consumer is None:
            logger.warning("RAG consumer not available, exit")
            return
        logger.info("RAGConsumer started (group=alert-rag-group)")
        for records in self._consumer:
            self._check_lag_sampling()
            for i, record in enumerate(records):
                event = record.value
                try:
                    # 积压采样：每 10 条处理 1 条
                    if self._sampling and i % _SAMPLE_EVERY != 0:
                        continue
                    # 仅 critical 或人工触发才执行 RAG
                    if int(event.get("severity", 2)) != 3 and not event.get("manual_trigger"):
                        continue
                    result = self.pipeline.search(event)
                    self._send_to_chatops(event, result)
                except Exception as exc:  # noqa: BLE001
                    logger.error("RAG failed for %s: %s", event.get("event_id"), exc)
                finally:
                    self._consumer.commit()

    def _check_lag_sampling(self) -> None:
        """积压超过阈值时切换为采样模式，恢复后关闭。"""
        try:
            total_lag = 0
            for tp, offsets in self._consumer.end_offsets(
                list(self._consumer.assignment())
            ).items():
                position = self._consumer.position(tp)
                total_lag += max(0, offsets - position)
            self._sampling = total_lag > _LAG_SAMPLE_THRESHOLD
            if self._sampling:
                logger.warning("Consumer lag %d > %d, sampling mode ON", total_lag, _LAG_SAMPLE_THRESHOLD)
        except Exception:  # noqa: BLE001
            pass

    def _send_to_chatops(self, event: dict, result: dict) -> None:
        """推送根因诊断卡片（含反馈评分按钮回调所需 case_id）。"""
        card = {
            "title": f"根因诊断: {event.get('service_name')} / {event.get('error_type')}",
            "root_cause": result.get("root_cause"),
            "solution": result.get("solution"),
            "confidence": result.get("confidence"),
            "is_fallback": result.get("is_fallback"),
            "event_id": result.get("event_id"),
            "actions": ["👍 有用", "👎 没用"],
        }
        logger.info("Diagnosis card pushed: %s", json.dumps(card, ensure_ascii=False))

    def close(self) -> None:
        if self._consumer:
            self._consumer.close()
