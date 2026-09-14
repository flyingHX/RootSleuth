"""消费者 3：冷存储（group=alert-storage-group）。
批量异步（攒 100 条或 5 秒）写入 Elasticsearch / S3 -> 审计与离线训练。
"""
import json

from ..storage.es_client import ESClient
from ..utils.logger import get_logger

logger = get_logger(__name__)


class StorageConsumer:
    """全量事件落 Elasticsearch，供审计与离线模型训练。"""

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str, es_host: str, es_index: str):
        self.es = ESClient(es_host, es_index)
        self._consumer = None
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
            logger.warning("Storage consumer connect failed: %s", exc)

    def run(self) -> None:
        if self._consumer is None:
            logger.warning("Storage consumer not available, exit")
            return
        logger.info("StorageConsumer started (group=alert-storage-group)")
        for record in self._consumer:
            try:
                self.es.write(record.value)
            except Exception as exc:  # noqa: BLE001
                logger.error("Storage write failed: %s", exc)
            finally:
                self._consumer.commit()

    def close(self) -> None:
        if self._consumer:
            self._consumer.close()
        self.es.close()
