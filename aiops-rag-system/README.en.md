# RootSleuth — Intelligent Alert RAG Knowledge Base System

English | [简体中文](README.md)

An AIOps alert diagnosis system built on the **LangChain architecture**: alert ingestion → log standardization (Drain templates) → Kafka decoupling → Redis deduplication & aggregation → Milvus vector retrieval + business reranking → LLM root cause analysis → human feedback closed loop for continuous self-evolution.

> This directory is the **RAG alert diagnosis pipeline** subsystem of RootSleuth; the operations console lives in `../app`. See the repository root `README.md` for the project overview and directory layout, and `../docs/` for project-level documentation (user guide, FAQ, deployment guide, and runbook).

## System Architecture

```
Alertmanager/Grafana Webhook
        │
        ▼
┌──────────────────┐    Kafka: standardized-events
│ FastAPI Webhook  │────────────────────────────┐
│ (Standardizer)   │                            ▼
└──────────────────┘                  ┌─────────────────────┐
        │                             │ Dedup Consumer      │
        │ Event staging (60s)          │ Redis fingerprint    │──▶ ChatOps push
        ▼                             │ dedup/aggregation    │
┌──────────────┐                      └─────────────────────┘
│ Redis Client │                              │ (can be re-triggered
└──────────────┘                              ▼  via the diagnostic API)
                                  ┌─────────────────────┐
                                  │ RAG Consumer        │
                                  │ Embedding → Milvus  │
                                  │ dual-path recall →  │
                                  │ rerank → Few-shot   │
                                  │ → LLM               │
                                  └─────────────────────┘
                                              │
        ┌─────────────────────────────────────┤
        ▼                                     ▼
┌──────────────┐                    ┌─────────────────────┐
│ ES Consumer  │                    │ Human diagnosis /    │
│ cold storage │                    │ Feedback API         │
│ / audit      │                    │ case closed loop     │
└──────────────┘                    │ (score ±1)           │
                                    └─────────────────────┘
```

## Core Features

| Feature | Description |
|---------|-------------|
| Log standardization | YAML rules with hot reload + Drain template extraction + LRU cache + plugin mechanism (burst detection, etc.) |
| Fingerprint deduplication | `md5(source\|service\|error_type)` fingerprint, Redis TTL debounce + 5-minute window counters, automatic severity escalation on bursts |
| RAG retrieval | BGE-M3 Embedding → Milvus HNSW search (monthly partitions) → four-layer business reranking funnel compressing candidates to Top-3 |
| LLM diagnosis | LangChain ChatOpenAI-compatible client, Few-shot prompt with enforced JSON output, timeout circuit breaking with fallback |
| Knowledge closed loop | Solved alerts are written back as knowledge base cases; human feedback (±1 score) influences reranking; quarterly near-duplicate cleanup |
| Observability | Prometheus metrics (standardization/RAG/dedup), `/metrics` endpoint, structured logging, health checks |

## Project Structure

```
aiops-rag-system/
├── config/
│   ├── rules.yaml            # Alert classification rules (hot-reloadable)
│   ├── drain_patterns.yaml   # Drain template variable substitution rules
│   └── logging.yaml          # Unified logging configuration
├── scripts/
│   ├── init_milvus.py        # Idempotent Milvus collection/index/partition initialization
│   ├── seed_cases.py         # Seed 10 knowledge base cases
│   └── cleanup_cases.py      # Quarterly near-duplicate case cleanup (supports --dry-run)
├── src/
│   ├── config.py             # Configuration loading (.env / environment variables)
│   ├── runtime.py            # Runtime dependency container
│   ├── main.py               # FastAPI entry point (lifespan manages consumer startup/shutdown)
│   ├── kafka_producer.py     # Kafka event producer
│   ├── kafka_consumers/      # dedup / rag / storage consumers
│   ├── standardization/      # Rule loading, Drain extraction, plugins, standardization engine
│   ├── rag_pipeline/         # embedder / milvus / reranker / prompt / llm / pipeline
│   ├── storage/              # redis_client / es_client
│   ├── models/               # RawAlert / StandardizedEvent / KnowledgeCase / response models
│   └── utils/                # logger / metrics
├── tests/                    # Standardization / RAG / API / consumer / storage tests
├── docker-compose.yml        # Milvus / Kafka / Redis / ES / etcd / MinIO
├── requirements.txt
├── .env.example
└── pytest.ini
```

## Quick Start

### 1. Start the infrastructure

```bash
docker compose up -d
# Includes: etcd, minio, milvus, kafka (zookeeper), redis, elasticsearch
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env: LLM_API_KEY is required (OpenAI-compatible endpoint, e.g. DeepSeek)
```

### 3. Initialize the knowledge base and seed cases

```bash
pip install -r requirements.txt
python scripts/init_milvus.py      # Create collections/indexes/partitions
python scripts/seed_cases.py       # Seed 10 typical incident SOP cases
```

### 4. Start the service

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
# Starts the 3 Kafka consumers automatically on boot, graceful shutdown on exit
```

### 5. Send a test alert

```bash
curl -X POST http://localhost:8000/api/v1/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "source": "apm",
    "raw_message": "redis.clients.jedis.exceptions.JedisConnectionException: connection timeout",
    "labels": {"service": "order-service", "cluster": "prod", "severity": "3"},
    "timestamp": 1700000000000
  }'
```

## API Overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/webhook` | Alert ingestion: standardization → Kafka → Redis staging |
| POST | `/api/v1/diagnostic` | Human-triggered diagnosis: run the full RAG pipeline for an event_id |
| POST | `/api/v1/feedback` | Case feedback scoring (+1 useful / -1 useless), influences reranking |
| POST | `/api/v1/cases/close` | Alert resolution closed loop: write case into the knowledge base |
| GET  | `/healthz` | Liveness probe |
| GET  | `/readyz` | Readiness probe (Kafka/Redis/Milvus status) |
| GET  | `/metrics` | Prometheus metrics |

## Operations Scripts

```bash
python scripts/cleanup_cases.py --dry-run   # Preview near-duplicate cases (cosine > 0.99)
python scripts/cleanup_cases.py             # Perform deletion (keeps the higher-feedback case)
```

## Testing

```bash
python -m pytest tests/ -v
```

## Four-Layer Business Reranking

The recalled Top-20 candidates are compressed to Top-3 through a four-layer funnel (implemented in `src/rag_pipeline/`, with LangChain `Document` as the data carrier between layers):

| Layer | Component | Funnel | Mechanism |
|-------|-----------|--------|-----------|
| L1 | `reranker_l1.RuleFilter` | 20 → ~15 | Rule-based hard filtering: removes expired / blacklisted / low-feedback / deprecated-solution cases |
| L2 | `reranker_l2.BusinessReranker` | ~15 → 10 | Seven-feature weighted fusion: semantic similarity, topology, time, feedback (Bayesian smoothing), template Jaccard, freshness, hit rate |
| L3 | `reranker_l3.BGEReranker` | 10 → 5 | BGE-Reranker Cross-Encoder pairwise fine ranking; automatically degrades to pass-through when FlagEmbedding is unavailable |
| L4 | `reranker_l4.LLMListwiseReranker` | 5 → 3 | LLM Listwise global ordering (LCEL chain): hallucinated ID validation + missing-case completion + rank→score normalization |
| Fusion | `rerank_pipeline.fuse_final_scores` | — | Weight re-normalization across available layers (0.2/0.4/0.4); no single-layer failure collapses the pipeline |

- **Per-layer degradation**: LLM failure falls back to L3 ordering, Cross-Encoder failure falls back to L2, total failure keeps L1 results only; L4 runs on a background thread with an independent timeout (`RERANK_L4_TIMEOUT_SECONDS`) and never blocks the main pipeline.
- **LangChain ecosystem**: L2/L3 are implemented as `BaseDocumentCompressor` (can be plugged directly into `ContextualCompressionRetriever`), the retrieval side is wrapped as a `BaseRetriever` (`retriever.MilvusEventRetriever`), and L4 plus diagnosis are LCEL chains (`ChatPromptTemplate | ChatOpenAI | JsonOutputParser`).
- **Feedback closed loop**: `/api/v1/feedback` maintains upvotes/downvotes (with the derived `feedback_score` compatibility field); recall events write back hit_count/recall_count, driving L2's f4 feedback score and f7 hit rate respectively.
- **Observability**: `/metrics` exposes per-layer latency `rerank_layer_latency_seconds{layer}`, per-layer degradation counters `rerank_degraded_total{layer}`, and final output count `rerank_final_total`.

## Key Design

- **Degradation strategy**: Milvus unavailable → LLM answers directly without context; LLM timeout → return the top similar cases; Embedding failure → deterministic fallback vector; Redis failure → dedup fail-open (alerts are never dropped); ES failure → in-memory buffering, then discard (audit only).
- **Hot rule reload**: modifying `config/rules.yaml` triggers automatic mtime-based hot reloading; broken configs fall back to the in-memory snapshot.
- **Prompt safety**: user logs are injected after template variable substitution; LLM output is parsed against a strict JSON schema with automatic degradation on parse failure.

## P99 Long-Tail Treatment

### Staged latency monitoring

`/metrics` exposes `rag_stage_latency_seconds` (Histogram, by `stage` label) covering the
`embedding` / `milvus_search` / `rerank` / `llm` stages; combined with the end-to-end
`rag_search_latency_seconds` and `rag_search_total{status}` (success/llm_timeout/fallback, etc.),
long-tail latency can be localized to a specific stage. Recommended PromQL (P99):

```promql
histogram_quantile(0.99, sum(rate(rag_stage_latency_seconds_bucket[5m])) by (stage, le))
```

### Optimizations already implemented

| Stage | Risk | Mitigation |
|-------|------|------------|
| Embedding | Slow/down API makes every request wait out the full timeout (default 10s) | Circuit breaker on consecutive failures (`EMBEDDING_FAIL_THRESHOLD`/`EMBEDDING_CIRCUIT_SECONDS`), deterministic fallback vectors within the open window; LRU result cache; local model preheating in the background so cold start never blocks requests |
| Milvus | Repeated has_collection RPC per search; ANN has no timeout | Collection handle TTL cache (`MILVUS_COLLECTION_CACHE_TTL`); `search` timeout (`MILVUS_SEARCH_TIMEOUT`) + configurable `nprobe` |
| Rerank | Excessive candidates slow down business reranking | Configurable `RAG_TOP_K` recall size and `RAG_FINAL_K` cases sent to the LLM |
| LLM | Underlying retries amplify blocking (timeout×(retries+1)); thread-pool queuing | Default `LLM_MAX_RETRIES=0`, end-to-end budget enforced by `RAG_LLM_TIMEOUT_SECONDS`; adjustable thread-pool capacity `RAG_LLM_MAX_WORKERS` |
| Human diagnosis | Synchronous RAG calls inside async routes block the event loop | Switched to synchronous `def` routes so FastAPI runs them on the thread pool automatically |
| Kafka consumption | dict events passed to `pipeline.search` fail silently | `search` accepts both Pydantic models and dict inputs |

The timeout degradation semantics remain unchanged: on LLM timeout/error the top-1 similar case is returned immediately (`is_fallback=true`) and already-completed retrieval results are never discarded.

### Load testing procedure

1. Start the infrastructure with real Embedding/LLM services and seed the cases;
2. Load `/api/v1/diagnostic` with concurrency gradients (e.g. 1/5/20/50) and scrape `/metrics`;
3. Compare per-stage P50/P95/P99 with the end-to-end P99 to confirm the long tail concentrates in the expected stage;
4. Tune per the table above (prioritize `RAG_TOP_K`, `RAG_LLM_MAX_WORKERS`, `MILVUS_NPROBE`) and re-test for comparison.
