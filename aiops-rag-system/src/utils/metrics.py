"""Prometheus 指标埋点。"""
from prometheus_client import Counter, Histogram, Gauge

# 标准化引擎指标
standardization_total = Counter(
    "standardization_total", "Total standardization requests", ["source", "error_type"]
)
standardization_latency = Histogram(
    "standardization_latency_seconds",
    "Standardization latency",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1],
)
unknown_rate = Gauge("standardization_unknown_rate", "Rate of unclassified logs")

# RAG 检索指标（端到端）
rag_search_total = Counter(
    "rag_search_total", "Total RAG search requests", ["status"]
)
rag_latency = Histogram(
    "rag_latency_seconds", "RAG pipeline end-to-end latency",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 8.0],
)

# RAG 分阶段耗时（P99 长尾定位：embedding / milvus_search / rerank / llm）
rag_stage_latency = Histogram(
    "rag_stage_latency_seconds",
    "RAG per-stage latency",
    ["stage"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Embedding 降级与熔断（长尾来源观测）
rag_embed_fallback_total = Counter(
    "rag_embed_fallback_total", "Embedding fallback vector usage", ["reason"]
)
rag_embed_circuit_open_total = Counter(
    "rag_embed_circuit_open_total", "Embedding circuit breaker open events"
)

# 去重指标
dedup_reduction_rate = Gauge("dedup_reduction_rate", "Alert reduction rate by dedup")
