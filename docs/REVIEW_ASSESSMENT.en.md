# RootSleuth Review Assessment Report

> Assessment baseline: main branch as of 2026-09-18; review comments source: third-party architecture review.
> Verdict vocabulary: **Adopted** (landed in this round) / **Phased** (on the roadmap with explicit priority) / **Not adopted for now** (keep the status quo, with rationale and re-trigger conditions).
> Companion landing notes: `docs/OPERATIONS_DEPLOYMENT_GUIDE.en.md` §24 (Hybrid LLM routing & compliance audit) and §25 (Data classification & inbound masking).

## 1. Assessment Overview

| # | Review comment | Verdict | Status this round |
|---|----------------|---------|-------------------|
| 1 | Hybrid LLM tiered routing (sensitive data non-egress) | Adopted | ✅ Landed |
| 2 | Compliance audit hash chain for LLM calls | Adopted | ✅ Landed |
| 3 | Inbound data masking pipeline | Adopted | ✅ Landed |
| 4 | Middleware slim-down (Milvus/Kafka/ES replacements) | Assessment only, no code change this round | 📋 See §3 |
| 5 | Unify all configuration into the config center (drop pydantic-settings) | Not adopted for now | 📋 See §4 |
| 6 | Replace LangChain (native SDKs) | Not adopted for now, keep watching | 📋 See §5 |
| 7 | High availability / K8s / API gateway | Phased (P1/P2) | 📋 Roadmap in §6 |

## 2. Items Adopted This Round (landed and regression-covered)

### 2.1 Hybrid LLM tiered routing ✅

**Review comment**: in financial-industry scenarios, sensitive alert data must not leave the domain; remote LLM calls require compliance control.

**Why adopted**: directly consistent with the project positioning (financial industry · on-premises distributed · hybrid LLM); this is a hard compliance requirement, not an optional one.

**Landing locations**:

- Routing decision module: `app/backend/services/llm_routing.py` (three dimensions: data sensitivity → alert severity → service tier);
- Single-round diagnosis hook: `app/backend/services/console_ai.py` (routing before every LLM call, with deterministic degradation and `llm_invocation` audit);
- Deep-diagnosis Agent hook: `app/backend/services/console_agent.py` (route passthrough across the full ReAct chain);
- Config center keys: `llm_local_base_url` / `llm_local_model` / `llm_local_api_key` / `llm_routing_policy` / `llm_remote_approval_id` (`app/backend/services/console_common.py`);
- Local LLM connects via OpenAI-compatible endpoints (Ollama / vLLM) without new SDK dependencies.

**Red-line semantics** (covered by dedicated tests: `tests/test_llm_routing_compliance.py`):

- Sensitive data is forced local; when local is unavailable, degrade to a deterministic conclusion (reusing knowledge-base candidates) — **never a remote fallback**;
- When `llm_remote_approval_id` is empty, remote routing is rejected under any policy (double safeguard at the end of `decide_llm_route`);
- Under the `remote_only` policy, sensitive data is still forced local (the red line overrides the policy).

### 2.2 Compliance audit hash chain for LLM calls ✅

**Review comment**: LLM calls must be traceable and tamper-proof to satisfy financial audit requirements.

**Why adopted**: the hash chain meets the tamper-proofing and traceability needs at a very low cost (no new table, no new middleware) — high cost-effectiveness.

**Landing locations**:

- The existing `audit_logs` table is reused; chain fields live in `after_json.chain` (`prev_hash` / `hash`), **no table schema change**;
- Chain construction: `hash = sha256(prev_hash | actor | action | target_type | target_id | normalized after JSON)`;
- Covered actions: `llm_route_decision` (routing decisions) and `llm_invocation` (LLM calls), constant `COMPLIANCE_CHAIN_ACTIONS` (`console_common.py`);
- Tamper-verification endpoint: `GET /api/v1/console/audit-logs/chain-verify` (sys_admin), returning `ok/total/head_hash/broken_id` and pinpointing the first broken link on tampering;
- Audit content contains only sensitivity category labels and routing metadata, never raw alert text.

### 2.3 Inbound data masking pipeline ✅

**Review comment**: externally pushed alerts contain PII/credentials; persisting raw text is a compliance risk.

**Why adopted**: masking should happen at the data entry point (entry-point governance beats egress remediation); enabled-by-default follows the financial-industry "secure by default" principle.

**Landing locations**:

- Masking & detection: `app/backend/services/console_common.py` (`mask_alert_text` / `detect_sensitivity`);
- Integration point: `app/backend/routers/ingest.py` (masking before persistence in `POST /api/v1/ingest/alerts`);
- Toggle: `data_masking_enabled` (default `true`, **raw content is never stored**; when explicitly disabled, the `event_ingest` audit records that choice for traceability);
- Covered categories: ID cards / phone numbers / bank cards (15~19-digit runs) / private IPs / key assignments / Bearer tokens / emails / long tokens (≥32 chars) — 8 categories;
- Routing linkage: mask placeholders preserve a recognizable format, so `detect_sensitivity` can re-classify persisted text and drive the sensitivity dimension of LLM routing;
- Preserved items: public IPs and hostnames are not masked (needed for diagnosis and CMDB correlation).

### 2.4 Frontend & test companions ✅

- The frontend OpsPage adds the "Hybrid LLM Routing" and "Data Classification & Masking" config groups with bilingual i18n copy (zh-CN / en-US), plus the `console-api.ts` type sync (`DiagnosisResult.diagnosis.route`);
- Dedicated tests `app/backend/tests/test_llm_routing_compliance.py`, 15 cases: routing decision matrix / sensitive-data non-egress (zero LLM calls and local routing) / per-category masking and re-classification / hash-chain write and tamper detection / ingest default masking and disabled-keeps-raw / route audit persisted even when diagnosis fails;
- Existing test adaptations: `tests/test_agent_repeat_consistency.py` adjusted to the new semantics (seed IP changed to public, model expectation after critical-forced-local).

## 3. Middleware slim-down: assessment only, no code change this round 📋

**Review comment**: to reduce operational cost, replace heavyweight middleware — Milvus → Qdrant (removing etcd + MinIO), Kafka → Redis Streams, Elasticsearch → Loki.

**Verdict**: the direction is right and the benefits are real, but replacement is a cross-cutting overhaul; this round only records the assessment and **changes no code**. Re-trigger condition: after the P1 HA overhaul completes, or as a standalone initiative.

### 3.1 Milvus → Qdrant

| Dimension | Assessment |
|-----------|------------|
| Ops benefit | High: removes two dependency components (etcd + MinIO; Qdrant is a single binary/container), components 3 → 1, materially smaller failure domain |
| Migration cost | Medium-high: the pymilvus call surface is large (collection management / dual-path recall / upsert / feedback stats / tenant expr filtering / schema-intersection compatibility) and would require rewriting `milvus_client.py` and `documents.py`; scalar-filter expr syntax must be re-expressed as Qdrant filters item by item |
| Compatibility risk | Medium: tenant isolation (tenant_id expr), schema-intersection degradation, and the kb_version guard must be rebuilt equivalently on Qdrant payload filters and re-tested |
| Performance | Both are sufficient at our scale (tens of thousands of cases); Milvus only wins at much larger scale / GPU indexes, which this project does not need today |
| Verdict | **On the P2 roadmap**. Trigger: after P1 completes, or when an actual ops pain point (etcd/MinIO incidents) occurs. During migration, keep the `documents.py` row↔Document bridge and replace only the underlying client; the four-layer rerank and retrieval contracts stay unchanged |

### 3.2 Kafka → Redis Streams

| Dimension | Assessment |
|-----------|------------|
| Ops benefit | Medium-high: the alert pipeline uses Kafka purely for event decoupling with low throughput (single-digit events per second); Redis Streams + consumer groups suffice — a net component reduction if Redis is already in the stack |
| Migration cost | Medium: producer/consumer semantics map directly (XADD/XREADGROUP/XACK); offset management and retry semantics need rework |
| Compatibility risk | Low: the event chain has no transactional/strong-ordering needs; at-least-once + idempotent dedup (event_id) already exists |
| Verdict | **On the P2 roadmap**. Note: Redis must enable AOF persistence to avoid event loss; the existing Kafka deployment can keep running with no forced migration deadline |

### 3.3 Elasticsearch → Loki

| Dimension | Assessment |
|-----------|------------|
| Ops benefit | Medium: Loki's footprint is far below ES (indexes labels, not full text), but this project's log-search dependency is low — console audits live in PostgreSQL and diagnosis context in the knowledge base |
| Migration cost | Low: affects only deployment templates and the log-collection side (Promtail); application code barely changes |
| Compatibility risk | Low |
| Verdict | **On the P2 roadmap (lower priority than 3.1/3.2)**. If a deployer already runs ES stably, we do not recommend replacing for replacement's sake |

### 3.4 Overall trade-off

- The common benefit of all three replacements is **lower self-hosted ops cost**, aligned with the user's stated concern about operational cost;
- The common cost is a one-time overhaul and regression burden, and dual-write/canary during migration briefly increases complexity;
- Decision: keep the middleware status quo this round (functional correctness first); all replacements enter the roadmap with explicit trigger conditions, avoiding premature work with no benefit window.

## 4. Unifying all configuration into the config center: not adopted for now 📋

**Review comment**: configuration management should converge entirely into the config center, removing scattered pydantic-settings / .env.

**Verdict**: **not adopted for now**; keep the two-layer status quo of "config center runtime push + .env/pydantic-settings bootstrap".

Rationale:

1. **Different responsibilities**: .env carries bootstrap parameters (database connection strings, Redis/Kafka/Milvus addresses, RAG auth keys and other infrastructure settings), while the config center carries runtime business parameters (diagnosis temperature/budget, routing policy, masking toggle, approval mode, etc.). The former requires a process restart to change; the latter must "take effect immediately" — the layering is by design;
2. **The RAG subsystem deploys independently**: `aiops-rag-system/` is a standalone FastAPI process that may run in a network partition without reachability to the console database; forcing its runtime config through the console config center would introduce a reverse dependency and a single point of failure;
3. **Cost outpaces benefit**: convergence means migrating every .env read site, redoing fail-open default semantics (e.g. the `RAG_AUTH_ENABLED=false` compatibility logic), and regressing the entire test suite, while the current two-layer model has produced no real configuration-drift incidents;
4. **Re-trigger condition**: revisit only after a real incident where "the same parameter is configured in two places with conflicting semantics", or when the config center needs to push infrastructure parameters in reverse.

## 5. Replacing LangChain: not adopted for now, keep watching 📋

**Review comment**: LangChain's abstraction layer is heavy and versions move fast; replace it with vendor-native SDK direct calls.

**Verdict**: **not adopted for now** (the user explicitly asked to watch and wait; the team agrees).

Rationale:

1. **The abstraction benefits still pay off**: the four-layer rerank's `BaseRetriever` / `BaseDocumentCompressor` / LCEL chains let L1-L4 compose, degrade, and be tested independently; the LCEL pipelines (`ChatPromptTemplate | ChatOpenAI | JsonOutputParser`) for L4 Listwise and diagnosis are roughly half the code of a native implementation;
2. **Large migration surface with no pressing pain**: five modules in `src/rag_pipeline/` (llm_client / prompt_builder / retriever / reranker_l4 / pipeline) are deeply coupled to LCEL; replacement means rewriting prompt orchestration, streaming & JSON parsing, and timeout-circuit-breaker semantics, and regressing all 132 RAG tests, while the current version (0.3.x with a fixed upper bound) runs stably;
3. **Risk hedges already in place**: `requirements` pins the LangChain 0.3.x upper bound; `BaseDocumentCompressor` has a dual-location compatible import (`documents.compressor` first, `documents.base` fallback) and upstream minor upgrades have historically not broken it;
4. **Watch triggers** (re-evaluate when any holds): a) a new major upgrade breaks the compatibility layer; b) a critical capability becomes available only in native SDKs (e.g. official deterministic structured output / caching semantics); c) a measured LCEL performance bottleneck under high concurrency.

## 6. Phased roadmap (HA / K8s / API gateway) 📋

| Priority | Item | Key content | Prerequisites |
|----------|------|-------------|---------------|
| P1 | High availability | PostgreSQL primary/standby + read/write split; Redis Sentinel; Kafka multi-replica; console/RAG multi-replica + statelessness (externalized sessions); diagnosis task dedup locks | Current features stable |
| P1 | API gateway | Unified entry (routing / rate limiting / auth offload / audit header injection); isolated rate limits for ingest vs web traffic; log/audit aggregation | P1 HA done or multi-replica ready |
| P2 | Kubernetes | Helm Chart / Kustomize; HPA (diagnosis workers scaled by Kafka lag); PodDisruptionBudget; probes aligned with /healthz /readyz | P1 done |
| P2 | Middleware replacement | Item-by-item initiatives per §3 (Milvus → Qdrant first) | Benefits are largest after K8s |
| P2 | Multi-tenant enhancements | Tenant-level config overrides (tenant-dimension defaults layered over config-center keys) | Real multi-tenant demand emerges |

## 7. Verification & Evidence

- Dedicated tests: `app/backend/tests/test_llm_routing_compliance.py` all 15 pass; backend full pytest 53 passed; `python -m py_compile` clean;
- Frontend regression: eslint + vite build with zero errors;
- E2E: sensitive alert (card number/phone) pushed via ingest → `raw_log` contains only mask placeholders → diagnosis writes the `llm_route_decision` audit (route=local, sensitive=true) → `chain-verify` returns `ok=true`;
- Bilingual docs: `docs/OPERATIONS_DEPLOYMENT_GUIDE.md(.en.md)` §24/§25 and the `docs/CONSOLE_USER_GUIDE.md(.en.md)` config table plus new sections are in sync.
