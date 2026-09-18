<div align="right">

English | [简体中文](README.md)

</div>

<p align="center">
  <img src="assets/images/rootsleuth-repo-banner-magnifier-ecg.png" alt="RootSleuth — a detective's magnifier uncovering alert root causes, with an ECG pulse symbolizing a continuously running knowledge loop" width="920"/>
</p>

# RootSleuth — Intelligent Alert RAG Knowledge Base (Project Overview)

An intelligent alert diagnosis and knowledge operations platform for SREs, composed of two independently deployed subsystems:

1. **RAG Alert Diagnosis Pipeline** (`aiops-rag-system/`, pure Python backend):
   Alert webhook ingestion → Drain log template extraction → Kafka decoupling → Redis dedup/aggregation → Milvus vector retrieval + business reranking → LLM root-cause diagnosis → human-feedback knowledge loop.
2. **Operations Console** (`app/`):
   React + Vite frontend + FastAPI backend, providing the alert workspace, AI diagnosis, knowledge base, approval center, rule management, agent workspace, audit, and configuration pages.

The two subsystems are **deployed independently on separate ports** and connected through business semantics (events / cases / rules / approvals): the pipeline handles diagnosis and knowledge production, while the console handles human operations and governance.

## Core Features

### Alert Workspace
- Unified alert ingestion and lifecycle: webhook alerts → aggregation & dedup → end-to-end tracking across pending / diagnosed / degraded / unknown states, searchable by service, error type, and time range.
- One-click AI deep diagnosis: ReAct multi-turn tool reasoning with a fully traceable evidence chain; conclusions are written back to the event automatically. Re-running a diagnosis preserves the previous conclusion as a historical session, keeping audit trails intact.
- Quality visualization: every diagnosis carries a confidence & Trust Index quality card, explicitly separating "model self-assessed confidence" from "objective evidence quality".

### Agent Platform (Three Ops Agents)
- **Deep Diagnosis Agent**: a ReAct engine driving multi-turn tool calls (local knowledge search / RAG retrieval / log sampling / CMDB lookup, etc.), with parallel tools, a dedicated format-retry budget, and wall-clock budget protection; temperature=0 fixed sampling across the chain with persisted input/context fingerprints, so repeated diagnosis of the same event is reproducible and variance is attributable.
- **Knowledge Governance Agent**: automatically clusters high-frequency similar alerts → drafts knowledge cases with AI → submits them into the approval flow → generates merge proposals; each draft is scored against the Trust Index standard and low-quality drafts are blocked automatically.
- **On-Call Report Agent**: CMDB-based blast-radius aggregation + AI ChatOps on-call reports; report grounding quality is persisted with the report, with deterministic fallback when the LLM is unavailable.
- Session traces, tool-call details, and token usage of all three agents are fully persisted and can be replayed turn by turn for audit.

### Knowledge Base
- Full lifecycle management of knowledge cases: create, edit, approve, publish, archive; after publishing, cases sync to the RAG vector index with upsert+verify verification, and failures enter a compensation queue (exponential-backoff retries / dead-letter isolation / manual replay).
- Version guard: when content changes but the version is lower, a 409 rejection prevents out-of-order compensation from causing "knowledge rollback"; same-content retries short-circuit idempotently instead of piling up dead tasks.
- Dedup & merge: merge suggestions are grouped only when error type and service match and template similarity ≥ 80%; cases covered by in-flight merge proposals are excluded automatically and become scannable again after rejection/withdrawal.

### Rule Management
- Centralized management of alert clustering and knowledge-recall filter rules with versioned releases; every diagnosis snapshots the rule version it hit, and all changes are fully audited.
- Rules are wired into the pipeline: the RAG L1 rule hard filter (expired / blacklisted / downvoted / deprecated) and governance clustering both run on managed rules — rules as code, auditable and rollback-able.

### Knowledge Health
- Red/yellow/green risk report: aggregates sync verification, content safety, and freshness aging (90-day negative-feedback policy + 180-day unconditional aging threshold) into one view, with Trust Index and health rate at a glance.
- Content safety gate: 18 poison-detection rules (secret leakage / PII / dangerous commands / prompt injection) scan knowledge before it enters the base; scan / block / false-positive-allow counts are all metricized.
- Closed-loop risk handling: one-click dead-letter replay, unconfirmed-sync retry, aging patrol preview before batch archiving (archiving also deletes vector index entries and writes audit logs), and high-risk content routed back to the knowledge base for re-review; operations require kb_admin or above, and reports & the overview refresh automatically.

### Agent Evaluation
- Generation quality evaluation: faithfulness / citation / hallucination-rate heuristic comparison, with Trust Index = 0.4×Faithfulness + 0.35×Citation + 0.25×(1−Hallucination Rate) and 0.85 as the alert threshold.
- Retrieval quality evaluation: Golden Set v1 (50 labeled queries) measures P@1 / P@5 / R@10 / R@20 / MRR / HitRate, making the four-layer reranking funnel quantitatively regression-testable.
- End-to-end observability: quality metrics are exposed as Prometheus Gauges; diagnosis / governance / on-call agents share one quality standard so long-term drift is trackable.

## Repository Layout

| Directory / File | Role | Notes |
|------------------|------|-------|
| `aiops-rag-system/` | RAG pipeline subsystem | Python 3.10 + FastAPI + LangChain; ships `docker-compose.yml` (etcd/MinIO/Milvus/Kafka/Redis/ES), a pytest suite, init & seed scripts |
| `app/` | Operations console subsystem | React + Vite frontend + FastAPI backend; one-command start `bash app/start_app_v2.sh` |
| `docs/` | Project-level docs (shared) | Console user guide, FAQ, operations deployment guide, operations runbook |
| `assets/` | Repo visual assets | README banner & logo (magnifier + ECG pulse theme) |
| `.github/` | CI workflows | RAG pytest, backend syntax check, frontend lint + build on push / PR |
| `app/start_app_v2.sh` | Console one-click launcher | Auto-allocates port pairs (backend from 8000, frontend from 3000) and installs dependencies |

## Quick Start

```bash
# ① Console (frontend 3000 + backend 8000, auto port-avoidance)
bash app/start_app_v2.sh

# ② RAG pipeline (middleware orchestration + API; default port from .env SERVICE_PORT, example 8001)
cd aiops-rag-system
cp .env.example .env          # at minimum set LLM_API_KEY
docker compose up -d          # etcd / minio / milvus / kafka / redis / elasticsearch
python scripts/init_milvus.py # idempotently create collections/indexes/partitions
python scripts/seed_cases.py  # seed knowledge cases
uvicorn src.main:app --host 0.0.0.0 --port 8001
```

## Documentation

| Document | Content |
|----------|---------|
| [docs/CONSOLE_USER_GUIDE.md](docs/CONSOLE_USER_GUIDE.md) · [English](docs/CONSOLE_USER_GUIDE.en.md) | Console user guide (roles, pages, workflows) |
| [docs/CONSOLE_FAQ.md](docs/CONSOLE_FAQ.md) · [English](docs/CONSOLE_FAQ.en.md) | Console FAQ |
| [docs/OPERATIONS_DEPLOYMENT_GUIDE.md](docs/OPERATIONS_DEPLOYMENT_GUIDE.md) · [English](docs/OPERATIONS_DEPLOYMENT_GUIDE.en.md) | Operations deployment guide (middleware architecture §21, deployment commands §22) |
| [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) · [English](docs/OPERATIONS_RUNBOOK.en.md) | On-call runbook (incident matrix, backup & restore, security response) |
| [docs/DEMO_ACCOUNTS.md](docs/DEMO_ACCOUNTS.md) · [English](docs/DEMO_ACCOUNTS.en.md) | Demo accounts (per-role accounts, passwordless login, middleware defaults, production constraints) |
| [aiops-rag-system/README.md](aiops-rag-system/README.md) · [English](aiops-rag-system/README.en.md) | Pipeline architecture, quick start, API overview, key designs |
| [app/backend/README.md](app/backend/README.md) · [English](app/backend/README.en.md) | Console backend guide |
| [app/frontend/README.md](app/frontend/README.md) · [English](app/frontend/README.en.md) | Console frontend guide |

> Design & evaluation deep-dives are Chinese-only for now: `docs/DIAGNOSE_AGENT_DESIGN.md`, `docs/KB_GOVERNANCE_AGENT_DESIGN.md`, `docs/ONCALL_AGENT_DESIGN.md`, `docs/RAG_TRUST_EVALUATION.md`.

## Open Source & Security

This repository is maintained to open-source standards; see [docs/OPEN_SOURCE_RELEASE_CHECKLIST.md](docs/OPEN_SOURCE_RELEASE_CHECKLIST.md) (Chinese) for the pre-release sensitive-file audit conclusions and steps.

- **License**: [Apache-2.0](LICENSE); see [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the code of conduct.
- **Security**: please do not report vulnerabilities publicly; submit them privately per [SECURITY.md](SECURITY.md). Deployment must-change items (`DATABASE_URL`, `JWT_SECRET_KEY`, `MASK_KEY`, etc.) are documented there.
- **Environment variables**: the repo keeps sanitized templates only — `aiops-rag-system/.env.example` (RAG pipeline), `app/backend/.env.example` and `app/frontend/.env.example` (console). Real `.env` files, secrets, logs, packages (`aiops-suite-*.tar.gz`), and temp scripts are never committed (see root `.gitignore`).
- **CI**: `.github/workflows/ci.yml` runs RAG pytest (Fake stubs, no middleware needed), console backend syntax checks, and frontend ESLint + production build on push/PR.
