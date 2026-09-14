"""Milvus row ↔ LangChain Document 桥接层。

四层业务重排（L1~L4）统一以 LangChain Document 为数据载体：
- page_content：供 Cross-Encoder（L3）与 LLM Listwise（L4）阅读的扁平文本；
- metadata：仅存标量（LangChain/pydantic 校验限制），含召回距离、反馈统计与各层分数。
"""
from typing import Any, Dict, List

from langchain_core.documents import Document

# Milvus row -> metadata 白名单（与 milvus_client._SCHEMA_FIELDS 对齐）
_METADATA_SCALARS: List[tuple] = [
    ("case_id", ""),
    ("fingerprint", ""),
    ("service_name", ""),
    ("cluster", ""),
    ("error_type", ""),
    ("severity", 2),
    ("start_time", 0),
    ("feedback_score", 0),
    ("upvotes", 0),
    ("downvotes", 0),
    ("hit_count", 0),
    ("recall_count", 0),
    ("root_cause", ""),
    ("solution", ""),
    ("alert_template", ""),
    ("topology_snapshot", ""),
    ("resolved_by", "human"),
    ("created_at", 0),
]

# L3 Cross-Encoder 阅读的文本顺序：故障特征在前，根因/方案随后
_CONTENT_ORDER = ("alert_template", "service_name", "error_type", "root_cause", "solution")


def row_to_document(row: Dict[str, Any]) -> Document:
    """将 Milvus 检索 row 转换为 Document（distance 存入 metadata 供 L2 使用）。"""
    metadata: Dict[str, Any] = {key: row.get(key, default) for key, default in _METADATA_SCALARS}
    # case_id 兜底：Milvus hit.id（主键缺失时）
    if not metadata.get("case_id"):
        metadata["case_id"] = str(row.get("case_id") or row.get("id") or "")
    metadata["distance"] = float(row.get("distance", 0.5) or 0.0)
    content = " | ".join(
        f"{key}: {row.get(key, '') or '无'}" for key in _CONTENT_ORDER
    )
    return Document(page_content=content, metadata=metadata)


def document_to_case_dict(doc: Document) -> Dict[str, Any]:
    """将 Document 还原为流水线内部使用的 case dict（保留各层重排分数）。"""
    case: Dict[str, Any] = {key: doc.metadata.get(key, default) for key, default in _METADATA_SCALARS}
    case["distance"] = float(doc.metadata.get("distance", 0.0) or 0.0)
    for score_key in (
        "_l2_score", "_l3_score", "_l4_score", "_final_score",
        "_f1_semantic", "_f2_topo", "_f3_time", "_f4_feedback",
        "_f5_template", "_f6_freshness", "_f7_hitrate",
    ):
        if score_key in doc.metadata:
            case[score_key] = float(doc.metadata.get(score_key, 0.0) or 0.0)
    if "_l4_reason" in doc.metadata:
        case["_l4_reason"] = str(doc.metadata.get("_l4_reason", ""))
    return case


def rows_to_documents(rows: List[Dict[str, Any]]) -> List[Document]:
    """批量转换（过滤 None 行）。"""
    return [row_to_document(r) for r in rows if r]
