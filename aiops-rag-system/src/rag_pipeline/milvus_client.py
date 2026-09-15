"""Milvus 连接与操作封装：集合管理、双路召回检索、upsert、反馈更新。"""
import time
from datetime import datetime, timedelta
from typing import List, Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_FIELDS = [
    ("case_id", "VARCHAR", {"max_length": 64}, {"is_primary": True}),
    ("fingerprint", "VARCHAR", {"max_length": 64}, {}),
    ("service_name", "VARCHAR", {"max_length": 128}, {}),
    ("cluster", "VARCHAR", {"max_length": 64}, {}),
    ("error_type", "VARCHAR", {"max_length": 64}, {}),
    ("severity", "INT64", {}, {}),
    ("start_time", "INT64", {}, {}),
    ("feedback_score", "INT64", {}, {"default_value": 0}),
    ("upvotes", "INT64", {}, {"default_value": 0}),
    ("downvotes", "INT64", {}, {"default_value": 0}),
    ("hit_count", "INT64", {}, {"default_value": 0}),
    ("recall_count", "INT64", {}, {"default_value": 0}),
    ("root_cause", "VARCHAR", {"max_length": 2048}, {}),
    ("solution", "VARCHAR", {"max_length": 2048}, {}),
    ("alert_template", "VARCHAR", {"max_length": 1024}, {}),
    ("topology_snapshot", "VARCHAR", {"max_length": 1024}, {}),
    ("resolved_by", "VARCHAR", {"max_length": 64}, {}),
    ("embedding", "FLOAT_VECTOR", {"dim": 1024}, {}),
    ("created_at", "INT64", {}, {}),
]

_SCALAR_INDEX_FIELDS = ["fingerprint", "service_name", "error_type", "cluster"]


class MilvusClient:
    """aiops_knowledge_base 集合的统一访问入口，异常时静默降级。"""

    def __init__(self, config: dict):
        self.host = config.get("host", "localhost")
        self.port = int(config.get("port", 19530))
        self.collection_name = config.get("collection", "aiops_knowledge_base")
        self.nprobe = int(config.get("nprobe", 32))
        self.search_timeout = float(config.get("search_timeout", 3))
        # 集合句柄缓存：TTL 内复用 Collection 对象，避免每次检索多一次 has_collection RPC
        self.collection_cache_ttl = float(config.get("collection_cache_ttl", 5))
        self._collection_obj = None
        self._collection_checked_at = 0.0
        self._connected = False
        self._connect()

    # ---------- 连接与集合管理 ----------
    def _connect(self) -> None:
        try:
            from pymilvus import connections

            connections.connect(host=self.host, port=self.port)
            self._connected = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Milvus connect failed (%s:%s): %s", self.host, self.port, exc)

    def is_connected(self) -> bool:
        """连接状态（供就绪探针使用）。"""
        return self._connected

    def _collection(self):
        """获取集合句柄（TTL 缓存，减少每次检索的 has_collection RPC）。"""
        from pymilvus import Collection, utility

        if not self._connected:
            return None
        now = time.monotonic()
        if (
            self._collection_obj is not None
            and (now - self._collection_checked_at) < self.collection_cache_ttl
        ):
            return self._collection_obj
        try:
            if not utility.has_collection(self.collection_name):
                self._collection_obj = None
                self._collection_checked_at = now
                return None
            self._collection_obj = Collection(self.collection_name)
            self._collection_checked_at = now
            return self._collection_obj
        except Exception as exc:  # noqa: BLE001
            logger.warning("Milvus collection lookup failed: %s", exc)
            self._collection_obj = None
            self._collection_checked_at = now
            return None

    def ensure_collection(self, overwrite: bool = False) -> bool:
        """创建集合、索引与近 3 个月分区；已存在时按需跳过。"""
        try:
            from pymilvus import (
                Collection,
                CollectionSchema,
                DataType,
                FieldSchema,
                utility,
            )

            if utility.has_collection(self.collection_name):
                if not overwrite:
                    return True
                utility.drop_collection(self.collection_name)

            fields = []
            for name, dtype, params, extra in _SCHEMA_FIELDS:
                enum = getattr(DataType, dtype)
                fields.append(FieldSchema(name=name, dtype=enum, **params, **extra))
            schema = CollectionSchema(fields=fields, description="AIOps RAG Knowledge Base")
            collection = Collection(self.collection_name, schema=schema, shards_num=2)

            collection.create_index(
                field_name="embedding",
                index_params={
                    "index_type": "IVF_SQ8",
                    "metric_type": "COSINE",
                    "params": {"nlist": 4096},
                },
            )
            for field in _SCALAR_INDEX_FIELDS:
                collection.create_index(
                    field_name=field,
                    index_params={"index_type": "TRIE", "index_name": f"{field}_idx"},
                )

            for month in self.recent_partitions(3, now=datetime.now()):
                if not collection.has_partition(month):
                    collection.create_partition(month)
            collection.load()
            # 新建/覆盖集合后失效句柄缓存，下一次检索重新解析
            self._collection_obj = None
            self._collection_checked_at = 0.0
            logger.info("Collection '%s' ensured", self.collection_name)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Ensure collection failed: %s", exc)
            return False

    @staticmethod
    def recent_partitions(months: int, now: Optional[datetime] = None) -> List[str]:
        now = now or datetime.now()
        return [
            f"p_{(now - timedelta(days=30 * i)).strftime('%Y%m')}" for i in range(months)
        ]

    # ---------- 检索（Step 2 粗筛 + Step 3 ANN 精筛）----------
    def search(
        self,
        query_vector: List[float],
        expr: str,
        partition_names: List[str],
        limit: int,
        output_fields: List[str],
    ) -> List[dict]:
        collection = self._collection()
        if collection is None:
            logger.warning("Milvus collection unavailable, skip vector search")
            return []
        try:
            results = collection.search(
                data=[query_vector],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": self.nprobe}},
                limit=limit,
                expr=expr or None,
                partition_names=partition_names or None,
                output_fields=output_fields,
                timeout=self.search_timeout,
            )
            cases: List[dict] = []
            for hit in (results[0] if results else []):
                row = {f: hit.entity.get(f) for f in output_fields}
                row["distance"] = float(hit.distance)
                row["case_id"] = row.get("case_id") or str(hit.id)
                cases.append(row)
            return cases
        except Exception as exc:  # noqa: BLE001
            logger.error("Milvus search failed: %s", exc)
            return []

    # ---------- 写入与更新 ----------
    def query_by_fingerprint(self, fingerprint: str) -> List[dict]:
        collection = self._collection()
        if collection is None:
            return []
        try:
            rows = collection.query(
                expr=f'fingerprint == "{fingerprint}"',
                output_fields=["case_id", "feedback_score", "root_cause", "solution"],
            )
            return list(rows)
        except Exception as exc:  # noqa: BLE001
            logger.error("Milvus query failed: %s", exc)
            return []

    def query_by_case_id(self, case_id: str) -> List[dict]:
        """按 case_id 精确查询索引行（供控制台发布后的检索验证与同步读改写）。"""
        collection = self._collection()
        if collection is None:
            return []
        try:
            rows = collection.query(
                expr=f'case_id == "{case_id}"',
                output_fields=[
                    "case_id",
                    "service_name",
                    "error_type",
                    "alert_template",
                    "feedback_score",
                    "upvotes",
                    "downvotes",
                    "hit_count",
                    "recall_count",
                    "root_cause",
                    "solution",
                    "created_at",
                ],
            )
            return list(rows)
        except Exception as exc:  # noqa: BLE001
            logger.error("Milvus query by case_id failed: %s", exc)
            return []

    def upsert_case(self, row: dict) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        try:
            partition = self._ensure_partition(collection, row.get("created_at"))
            collection.upsert([row], partition_name=partition)
            collection.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Milvus upsert failed: %s", exc)
            return False

    def delete_cases(self, case_ids: List[str]) -> bool:
        """按 case_id 批量删除案例（供季度清理脚本使用）。"""
        collection = self._collection()
        if collection is None or not case_ids:
            return False
        try:
            ids = ",".join(f'"{c}"' for c in case_ids)
            collection.delete(expr=f"case_id in [{ids}]")
            collection.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Milvus delete failed: %s", exc)
            return False

    def _ensure_partition(self, collection, created_at_ms: Optional[int]) -> str:
        """确保目标月份分区存在并返回分区名；写入统一按 created_at 落分区。"""
        from datetime import datetime

        ts = (created_at_ms or int(time.time() * 1000)) / 1000
        name = f"p_{datetime.fromtimestamp(ts).strftime('%Y%m')}"
        if not collection.has_partition(name):
            collection.create_partition(name)
            try:
                collection.load()
            except Exception:  # noqa: BLE001
                pass
        return name

    # 除向量外的全部标量字段（update_feedback 读改写时需带全字段，避免 upsert 清空列）
    _ALL_SCALAR_FIELDS = [f[0] for f in _SCHEMA_FIELDS if f[1] != "FLOAT_VECTOR"]

    def update_feedback(self, case_id: str, delta: int) -> int:
        """反馈闭环：+1 记 upvotes、-1 记 downvotes；feedback_score 保留为兼容派生字段。"""
        collection = self._collection()
        if collection is None:
            return 0
        try:
            rows = list(
                collection.query(
                    expr=f'case_id == "{case_id}"',
                    output_fields=self._ALL_SCALAR_FIELDS,
                )
            )
            if not rows:
                logger.warning("Feedback target not found: %s", case_id)
                return 0
            row = dict(rows[0])
            row["upvotes"] = int(row.get("upvotes", 0) or 0) + (1 if delta > 0 else 0)
            row["downvotes"] = int(row.get("downvotes", 0) or 0) + (1 if delta < 0 else 0)
            row["feedback_score"] = int(row.get("feedback_score", 0) or 0) + delta
            partition = self._ensure_partition(collection, row.get("created_at"))
            collection.upsert([row], partition_name=partition)
            collection.flush()
            return int(row["feedback_score"])
        except Exception as exc:  # noqa: BLE001
            logger.error("Update feedback failed: %s", exc)
            return 0

    def update_recall_stats(self, case_ids: List[str], hit_flags: Optional[List[bool]] = None) -> None:
        """召回统计回流：Top-3 进入 LLM 上下文时 recall_count+1，被人工采纳时 hit_count+1。

        为 f7 命中频率特征提供数据基础；Milvus 无原子自增，逐条读改写，失败静默。
        """
        collection = self._collection()
        if collection is None or not case_ids:
            return
        flags = list(hit_flags) if hit_flags else [False] * len(case_ids)
        for case_id, hit in zip(case_ids, flags):
            try:
                rows = list(
                    collection.query(
                        expr=f'case_id == "{case_id}"',
                        output_fields=["case_id", "hit_count", "recall_count", "created_at"],
                    )
                )
                if not rows:
                    continue
                row = dict(rows[0])
                row["recall_count"] = int(row.get("recall_count", 0) or 0) + 1
                if hit:
                    row["hit_count"] = int(row.get("hit_count", 0) or 0) + 1
                partition = self._ensure_partition(collection, row.get("created_at"))
                collection.upsert([row], partition_name=partition)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Recall stats update skipped for %s: %s", case_id, exc)
        try:
            collection.flush()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 离线维护 ----------
    def find_near_duplicates(self, threshold: float = 0.99, sample_limit: int = 1000) -> List[tuple]:
        """扫描相似度超过阈值的冗余案例对（供季度清理脚本使用）。"""
        import json

        collection = self._collection()
        if collection is None:
            return []
        try:
            rows = list(
                collection.query(
                    expr="",
                    output_fields=["case_id", "embedding", "feedback_score"],
                    limit=sample_limit,
                )
            )
            pairs = []
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    sim = _cosine(rows[i]["embedding"], rows[j]["embedding"])
                    if sim > threshold:
                        keep, drop = (
                            (rows[i], rows[j])
                            if rows[i].get("feedback_score", 0) >= rows[j].get("feedback_score", 0)
                            else (rows[j], rows[i])
                        )
                        pairs.append((keep["case_id"], drop["case_id"], sim))
            return pairs
        except Exception as exc:  # noqa: BLE001
            logger.error("Near-duplicate scan failed: %s", exc)
            return []


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math_sqrt(sum(x * x for x in a))
    nb = math_sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def math_sqrt(x: float) -> float:
    return x ** 0.5
