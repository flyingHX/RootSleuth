"""LangChain Retriever 适配层：把 Milvus 双路召回包装为 BaseRetriever 生态组件。

双路召回（粗筛 + 精筛）逻辑保持在 MilvusClient 内不变，本模块负责：
- Embedder -> LangChain Embeddings 接口适配（embed_query / embed_documents）；
- StandardizedEvent 结构化过滤条件 + 查询向量 -> 召回结果；
- 两条对外路径：
  * retrieve_event()：主流水线快速路径，返回 Milvus 原始 dict 行
    （四层重排内部统一做 row -> Document 转换，避免重复转换开销）；
  * BaseRetriever 标准协议：get_relevant_documents(query) 返回 Document 列表，
    可直接接入 ContextualCompressionRetriever 等 LangChain 生态组件。
"""
import json
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from ..models.event import StandardizedEvent
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Milvus 召回输出字段（与 pipeline._OUTPUT_FIELDS / documents 白名单保持一致）
OUTPUT_FIELDS = [
    "case_id", "fingerprint", "service_name", "cluster", "error_type",
    "severity", "start_time", "feedback_score", "upvotes", "downvotes",
    "hit_count", "recall_count", "root_cause", "solution", "alert_template",
    "topology_snapshot", "resolved_by", "created_at",
]


class AtomsEmbeddings(Embeddings):
    """将项目内 BGEEmbedder 适配为 LangChain Embeddings 接口（含降级向量语义）。"""

    def __init__(self, embedder: Any):
        self._embedder = embedder

    def embed_query(self, text: str) -> List[float]:
        return self._embedder.embed(text)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embedder.embed(t) for t in texts]


class MilvusEventRetriever(BaseRetriever):
    """告警事件检索器：事件 -> 结构化过滤 + 向量召回。"""

    milvus_client: Any
    embedder: Any
    top_k: int = 20
    recent_window_days: int = 30

    # ---------- LangChain Embeddings 适配 ----------
    def embed_query_text(self, text: str) -> List[float]:
        return AtomsEmbeddings(self.embedder).embed_query(text)

    # ---------- 主流水线快速路径（dict 行，供四层重排消费）----------
    def retrieve_event(
        self, event: StandardizedEvent, query_vector: Optional[List[float]] = None
    ) -> List[dict]:
        """事件级检索入口（向量可复用，避免重复 embedding）。"""
        if query_vector is None:
            query_vector = self.embed_query_text(self.build_query_text(event))
        return self._search_rows(event, query_vector)

    @staticmethod
    def build_query_text(event: StandardizedEvent) -> str:
        return (
            f"【故障类型】: {event.error_type} "
            f"【影响服务】: {event.service_name} "
            f"【告警特征】: {event.template}"
        )

    def _build_filter_expr(self, event: StandardizedEvent) -> str:
        since = event.timestamp - self.recent_window_days * 24 * 3600 * 1000
        return (
            f'service_name == "{event.service_name}" and '
            f'error_type == "{event.error_type}" and '
            f"start_time > {since}"
        )

    def _search_rows(
        self, event: StandardizedEvent, query_vector: List[float]
    ) -> List[dict]:
        return self.milvus_client.search(
            query_vector=query_vector,
            expr=self._build_filter_expr(event),
            partition_names=self.milvus_client.recent_partitions(3),
            limit=self.top_k,
            output_fields=OUTPUT_FIELDS,
        )

    # ---------- BaseRetriever 标准协议 ----------
    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun, **kwargs: Any
    ) -> List[Document]:
        """标准 Retriever 接口：query 为告警事件 dict 或 JSON 字符串。"""
        from .documents import rows_to_documents

        if isinstance(query, str):
            try:
                query = json.loads(query)
            except json.JSONDecodeError:
                logger.warning("Retriever query is not an event dict, using raw text")
                query = {"template": str(query)}
        if isinstance(query, StandardizedEvent):
            event = query
        else:
            # 宽松构造：缺失字段给中性默认值，容错原始文本/残缺 dict
            base = {
                "event_id": "adhoc", "fingerprint": "", "service_name": "",
                "cluster": "", "error_type": "", "template": "",
            }
            base.update(query if isinstance(query, dict) else {"template": str(query)})
            event = StandardizedEvent(**base)
        rows = self._search_rows(event, self.embed_query_text(self.build_query_text(event)))
        return rows_to_documents(rows)
