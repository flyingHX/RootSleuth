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

# ===== 四层业务重排指标（L1~L4 分层可观测）=====
rerank_layer_latency = Histogram(
    "rerank_layer_latency_seconds",
    "Rerank layer latency (l1/l2/l3/l4/fusion)",
    ["layer"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
rerank_degraded_total = Counter(
    "rerank_degraded_total", "Rerank layer degradation events", ["layer"]
)
rerank_final_total = Counter(
    "rerank_final_total", "Rerank funnel final output size", ["size"]
)

# ===== 知识内容安全（投毒预检 P1）=====
kb_poison_scan_total = Counter(
    "kb_poison_scan_total", "知识入库投毒预检扫描次数", ["result"]
)
kb_poison_blocked_total = Counter(
    "kb_poison_blocked_total", "投毒预检拦截次数（按类别）", ["category"]
)

# ===== kb-search 检索 API（鉴权 + 租户隔离后）=====
kb_search_requests_total = Counter(
    "kb_search_requests_total", "kb-search 检索请求数", ["result"]
)

# ===== Golden Set 质量评测（P1-2；0.85 告警线见 docs/OPERATIONS_RUNBOOK.md）=====
rag_quality_faithfulness = Gauge(
    "rag_quality_faithfulness", "Golden Set 评测 Faithfulness（0~1）"
)
rag_quality_citation_accuracy = Gauge(
    "rag_quality_citation_accuracy", "Golden Set 评测 Citation Accuracy（0~1）"
)
rag_quality_hallucination_rate = Gauge(
    "rag_quality_hallucination_rate", "Golden Set 评测幻觉率（1 - Faithfulness 代理，0~1）"
)
rag_quality_trust_index = Gauge(
    "rag_quality_trust_index", "Trust Index T = 0.4F + 0.35C + 0.25(1-P)（0~1）"
)

# ===== Golden Set 检索评测（P0-3：评测运行器回写最新值，/metrics 暴露趋势）=====
rag_eval_precision_at1 = Gauge("rag_eval_precision_at1", "Golden Set 检索评测 Precision@1（AP@1）")
rag_eval_precision_at5 = Gauge("rag_eval_precision_at5", "Golden Set 检索评测 Precision@5（AP@5 语义）")
rag_eval_recall_at10 = Gauge("rag_eval_recall_at10", "Golden Set 检索评测 Recall@10")
rag_eval_recall_at20 = Gauge("rag_eval_recall_at20", "Golden Set 检索评测 Recall@20")
rag_eval_mrr = Gauge("rag_eval_mrr", "Golden Set 检索评测 MRR")
rag_eval_hit_rate = Gauge("rag_eval_hit_rate", "Golden Set 检索评测 Hit@5")
