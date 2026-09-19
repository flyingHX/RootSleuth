# RootSleuth Operations Console User Guide

English | [简体中文](CONSOLE_USER_GUIDE.md)

> Audience: business operators, on-call engineers, SREs, approvers, and system administrators.
> The console has a built-in "Help Center" page (sidebar → User Guide) that provides an abbreviated version of this document.

## 1. Login & Role Permissions

### Login methods
- Production login: click "Log in with Atoms Account"; after completing OIDC authorization you are redirected back into the console automatically.
- Preview/demo environments: the login page offers one-click login for the four demo accounts (via the backend `/api/v1/auth/demo-login`, sharing the same JWT issuance chain as production login):
  - `demo-operator@atoms.dev` → On-call operator
  - `demo-sre@atoms.dev` → SRE
  - `demo-lead@atoms.dev` → Approver / SRE Lead
  - `demo-admin@atoms.dev` → System administrator

### Role hierarchy (low to high)
| Role | Label | Key Capabilities |
|------|-------|------------------|
| viewer | Read-only auditor | View all pages, audit logs, duplicate case scanning |
| operator | On-call operator | AI diagnosis, alert feedback (upvote/downvote) |
| sre | SRE | Edit knowledge base, initiate changes/merges/template promotions |
| approver | Approver / SRE Lead | Approve/reject |
| kb_admin | Knowledge base admin | Knowledge case version rollback, publishing |
| sys_admin | System administrator | Rule publish/rollback, configuration center management |

- Users without a role binding default to `viewer`; you can adjust `default_role` or `role_bindings_json` under "Audit & Config → Configuration Center".
- Permissions are cumulative by level: higher roles automatically have all capabilities of lower roles.

## 2. Operations Dashboard

- **Core metrics**: total events, noise reduction rate (fingerprint dedup), unknown rate, RAG success rate, RAG P99/average latency.
- **To-do cards**: count of approvals pending my action, count of unknown templates pending promotion.
- **Dependency health**: status summary for vector retrieval, cache, archive storage, and the LLM service.
- **Distribution charts**: by severity, top error types, top services, and a recent alerts list.

## 3. Alert Workbench

1. **Filtering**: severity, service, cluster, error type, status, time range (1h/24h/7d/all), and keywords (event ID / template / raw log).
2. **Details**: view raw logs, fingerprints, topology, and RAG recalled cases (score, root cause, remediation plan, feedback score).
3. **AI diagnosis**: uses the LLM model configured in the configuration center to output root cause / recommendations / commands; when confidence falls below the threshold (default 0.75), it is flagged "low confidence" for human review. Every diagnosis also outputs a **quality assessment card** (Trust Index, Faithfulness, Context Coverage, Answer Relevance, hallucination rate, and unsupported assertions); below the 0.85 quality line it likewise prompts for review.
4. **Degradation notes**: on model timeout (`llm_timeout`) or abnormal output (`invalid_json`), the system degrades automatically; the detail page shows the degradation reason while alert viewing is unaffected.
5. **Feedback loop**:
   - 👍/👎 updates the recalled case feedback score (±1, range -5 ~ 5).
   - Filling in manual corrections (root cause / remediation) **automatically generates a knowledge base change set** that enters the approval flow.

### Quality metric semantics (confidence vs Trust Index)

The two numbers measure different objects and are shown together without contradiction:

- **Confidence (e.g. 86%)**: the model's subjective self-assessed certainty about the **root cause conclusion**, threshold `confidence_threshold=0.75`;
- **Trust Index (e.g. 72.1%)**: an **objective quality score** measuring how much evidence the conclusion's assertions can find in recalled cases/alert evidence, `T = 0.4×Faithfulness + 0.35×Context Coverage + 0.25×(1−hallucination rate)`, per-diagnosis quality line 0.85;
- Typical scenarios: high confidence + low Trust Index = the model is confident but its assertions lack evidence → human review is mandatory; low confidence + high Trust Index = a cautious conclusion with sufficient evidence → safe to trust.

The same semantics apply to the **Agent Workbench**: deep diagnosis (conclusion quality), knowledge governance (draft case quality and aggregate pass rate), and on-call reports (report quality, persisted with the current and historical reports).

## 4. Knowledge Base Governance

### Case editing
- Field edits show a live **before/after preview**; after submission the change enters the approval flow or publishes directly depending on the current approval mode.
- A change rationale is required; every operation is written to the audit log.

### Case creation (auto-generated ID)
- **No need to hand-write a case ID**. After submission the backend automatically generates a unique ID in the `KB-YYYYMMDD-NNN` format (current-day sequence, advancing after de-duplicating against stored cases and in-flight creation change sets).
- The generated case ID is shown both in the creation success toast and in the approval request content; concurrent creation is still guaranteed unique.

### Version rollback
- `kb_admin` and above can roll back to any historical version; rollback **generates a new version snapshot** and the full history remains traceable.

### Deduplication & merging
- "Scan similar cases" clusters by same error type + same service with template similarity ≥80%; only suggestions meeting the threshold are shown (below 80% nothing is shown, and same-cluster alone is no longer a fallback condition).
- Each cluster designates one primary case (the one with the highest feedback score by default); the remaining cases are **archived** after merging; merge proposals go through the approval flow.
- After a merge approval is submitted, that cluster's card disappears immediately and further scans exclude it (cases covered by in-flight proposals are excluded); it is restored automatically if the approval is rejected, and the merge can be resubmitted.

## 5. Approval Center

### Three views
- **Pending my approval**: requests whose current step matches your role and whose applicant is not you.
- **Initiated by me**: all requests you submitted (pending ones can be withdrawn).
- **Processed by me**: history of requests you have approved.

### Approval modes (configuration center `approval_mode`)
| Mode | Description |
|------|-------------|
| OFF | No review, publish directly: changes publish immediately after submission |
| SINGLE_REVIEW | Single-level approval: one approver step |
| MULTI_LEVEL | Multi-level approval: approver → knowledge base admin, two steps |

### Approval content & before/after comparison
- Each approval request has a "View approval content & before/after comparison" expander:
  - **Knowledge base change (kb_edit)**: field-level before/after comparison table (version numbers and other metadata and unchanged fields are filtered out).
  - **Knowledge merge (merge)**: the primary case and the list of redundant cases to be archived.
  - **Template promotion (rule_promote)**: the unknown template's original text, sample size, most recent services, and the promotion target classification.

### Operation rules
- **Self-approval forbidden**: an applicant cannot approve their own request; the system enforces this check.
- **Approve**: on final approval the system executes the publish (knowledge base writes a new version and invalidates the semantic cache) / merge / rule promotion automatically and writes an audit record.
- **Reject**: the change set status becomes rejected and its content is not published.
- **Withdraw**: only the applicant themself and only while the request is pending.
- The timeline shows each step's role, approver, action, comment, and time.

## 6. Rule Management

- **YAML validation**: rule ids are unique, score ∈ (0,1], at least one of keywords/pattern, and severity must be valid.
- **Publish** (sys_admin): after a successful parse a new version is written, the previously active version is automatically marked "superseded", and hot reload takes effect immediately.
- **Version rollback** (sys_admin): reactivates the target version and hot reloads.
- **Unknown template queue**: shows templates, sample sizes, most recent services, and status; promotion requires a target error type and, after final approval, appends a rule at score 0.6 / warning level and publishes a new version; templates not worth distilling can be discarded.

## 7. Audit & Configuration

### Audit logs
- Records all key operations: operator, action, object type/ID, before/after states, and time.
- Filterable by action, operator, and object type. Typical actions: `change_set_create`, `kb_publish`, `kb_rollback`, `kb_merge`, `approval_approve/reject/withdraw`, `rule_publish/reload/rollback`, `unknown_promote(_request)/discard`, `feedback`, `config_update`, `semantic_cache_invalidate`.

### Configuration center (sys_admin)
| Key | Description |
|-----|-------------|
| approval_mode | Approval mode OFF / SINGLE_REVIEW / MULTI_LEVEL |
| confidence_threshold | Diagnosis confidence threshold (0~1) |
| rerank_weight_json | Rerank weights (cosine/topology/time_decay/feedback) |
| diagnose_temperature | Diagnosis Agent dedicated sampling temperature (default 0: repeated deep diagnoses of the same event produce stable output; governance/on-call Agents still use llm_temperature=0.2) |
| diagnose_time_budget_seconds | Diagnosis wall-clock budget (30~600 seconds, default 90): the total duration cap for deep diagnosis and single-round diagnosis (the one-click /events/{id}/diagnose route and the Agent degradation path); each LLM call's timeout is truncated against the remaining budget, and exhaustion returns a structured 502 with automatic degradation, preventing requests from crossing edge proxies (e.g. Cloudflare's 100s) |
| llm_provider / llm_base_url / llm_api_key / llm_model / llm_temperature / llm_timeout_seconds | LLM global defaults and model parameters (the three Agents' independent configs inherit each item individually when left empty; API keys are stored encrypted and displayed masked) |
| diagnose_llm_provider / diagnose_llm_base_url / diagnose_llm_api_key | Diagnosis Agent dedicated LLM access: provider (atoms_hub / openai_compatible) + OpenAI-compatible Base URL + API key (inherits each global item when empty; API keys are stored encrypted and displayed masked) |
| diagnose_llm_model / diagnose_llm_timeout_seconds | Diagnosis Agent dedicated model and per-call timeout (10~300 seconds; inherits global when empty) |
| kb_governance_llm_provider / kb_governance_llm_base_url / kb_governance_llm_api_key / kb_governance_llm_model / kb_governance_temperature / kb_governance_llm_timeout_seconds | Knowledge governance Agent dedicated access and model parameters (inherits each global item when empty; you can override only some of them) |
| oncall_llm_provider / oncall_llm_base_url / oncall_llm_api_key / oncall_llm_model / oncall_temperature / oncall_llm_timeout_seconds | On-call Agent dedicated access and model parameters (inherits each global item when empty; you can override only some of them) |
| feature_flags_json | Feature flags (auto_diagnose/dedup_scan) |
| default_role | Default role for unbound users |
| role_bindings_json | Email → role binding mapping |
| notify_webhook_url | Post-diagnosis notification callback URL (http(s), e.g. ITSM/UMPS webhook; leave empty to disable pushes) |
| notify_webhook_token | Notification callback Bearer token (stored encrypted, displayed masked; leave empty to omit the auth header) |
| event_ingest_token | X-Ingest-Token auth token for the event ingest API (stored encrypted, displayed masked; leave empty to allow unauthenticated pushes) |
| llm_local_base_url / llm_local_model / llm_local_api_key | Local LLM OpenAI-compatible access (Ollama / vLLM): Base URL + model name + API key (Ollama/vLLM usually anonymous when left empty; stored encrypted, displayed masked; belongs to the "Hybrid LLM Routing" group) |
| llm_routing_policy | Hybrid LLM routing policy: auto (default, three-dimensional decision by sensitivity / alert severity / service tier) / local_only (all local) / remote_only (all remote; sensitive data is still forced local) |
| llm_remote_approval_id | Remote LLM data-egress compliance approval ID (when empty, remote routing is rejected — local or deterministic degradation only) |
| data_masking_enabled | Inbound data masking toggle (default true: alert text is masked before persistence, raw content is never stored; masking also drives the LLM routing sensitivity dimension) |

- Changes take effect immediately and write a `config_update` audit record. Each Agent group offers a "Test connectivity" button (`POST /api/v1/console/configs/llm-test?agent=diagnose|kb_governance|oncall`) that returns the resolved model/timeout/provider and `access_source` (agent=independent config in effect / global=inherited), and API keys are never echoed.

### External system integration & post-diagnosis notification push

RootSleuth provides two external integration paths: upstream pipelines push alerts into the console via the "event ingest API"; after diagnosis completes, conclusions are pushed to external systems (e.g. ITSM ticketing / UMPS) via "notification push".

**Event ingest API (alert intake)**

- `POST /api/v1/ingest/alerts` for single alerts and `POST /api/v1/ingest/alerts/batch` for batches (≤ 200 per call); payloads follow the RAG webhook contract (`source` / `raw_message` / `labels` / `timestamp`, optional `event_id` / `error_type` / `template` / `severity` / `confidence` / `topology`).
- When `error_type` is missing, events are auto-classified by keywords (gateway 502 / OOM / connection refused / disk full / CPU throttling / Redis pool exhausted / timeout); deduplication is keyed by `event_id` — duplicate pushes return `duplicated` without re-inserting; events enter the Alert Workbench as `pending` awaiting diagnosis.
- Auth: when `event_ingest_token` is non-empty, requests must carry an exactly matching `X-Ingest-Token` header (mismatch returns 401); when empty, requests pass through (consistent with the RAG fail-open semantics).

```bash
curl -X POST http://<console-host>:8000/api/v1/ingest/alerts \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Token: <event_ingest_token>" \
  -d '{"source":"webhook","raw_message":"Pod payment-7d9 OOMKilled, memory limit exceeded","labels":{"service":"payment","cluster":"c1"},"timestamp":1758209400000}'
# Expected: {"accepted":1,"duplicated":0,"event_id":"...","total":1}
```

**Post-diagnosis notification push (console → ITSM/UMPS)**

- After a successful single-round diagnosis or deep-diagnosis Agent run, the system asynchronously pushes the root cause (`root_cause`), remediation suggestion (`solution`), fix command (`command`), confidence (`confidence`), Trust Index (`trust_index`), and event metadata to `notify_webhook_url`; when `notify_webhook_token` is set, an `Authorization: Bearer <token>` header is attached.
- The push is best-effort (10s timeout, fire-and-forget): failures are logged only and never block the diagnosis or trigger retries; without a callback URL the push is silently skipped.
- The "Integration & Notification" group offers a "Test connectivity" button (`POST /api/v1/console/notify/test`, sys_admin): it sends a sample payload to the callback URL and returns the status code/latency, for pre-go-live verification of external reachability.

For the field mapping table and UMPS/ITSM mapping suggestions (operator view), see `docs/OPERATIONS_DEPLOYMENT_GUIDE.en.md` §23.

### Hybrid LLM Routing & Compliance Audit

Diagnosis (single-round one-click diagnosis and the deep-diagnosis Agent) performs a three-dimensional routing decision before every LLM call: **data sensitivity → alert severity → service tier**.

- Sensitive data (including recognition of mask placeholders) is always forced to the local LLM and **never sent remote**; when the local LLM is not deployed, diagnosis automatically degrades to a "deterministic conclusion" (reusing knowledge-base candidates, root cause prefixed "Sensitive data stays on-premises · deterministic conclusion"), again without a remote fallback.
- Under the `auto` policy, remote routing requires all of: non-sensitive + non-critical + non-production environment (CMDB) + remote approval ID configured (`llm_remote_approval_id`); any unmet condition conservatively chooses local. When `llm_remote_approval_id` is empty, remote routing is rejected outright (red-line double safeguard).
- The local LLM connects via OpenAI-compatible endpoints (Ollama / vLLM); fill in the Base URL / model name / API key in the config center "Hybrid LLM Routing" group (deployment examples: `docs/OPERATIONS_DEPLOYMENT_GUIDE.en.md` §24).
- Every routing decision and LLM call is written to the **compliance audit hash chain** (actions `llm_route_decision` / `llm_invocation`, chain hashes stored in the audit record's `after_json.chain`) and can be filtered by action in the audit log; sys_admin can call the tamper-verification endpoint:

```bash
curl http://<console-host>:8000/api/v1/console/audit-logs/chain-verify -H "Authorization: Bearer <token>"
# Expected: {"ok":true,"total":N,"head_hash":"...","broken_id":null}
```

### Data Classification & Masking

The `data_masking_enabled` toggle (enabled by default) in the "Data Classification & Masking" group controls inbound masking on the event ingest API: when enabled, externally pushed alert text is masked before persistence for ID cards / phone numbers / bank card numbers / private IPs / key assignments / Bearer tokens / emails / long tokens (e.g. `138****5678`, `10.1.2.x`), and **raw content is never stored**; mask placeholders preserve a recognizable format so routing sensitivity detection is unaffected. Public IPs and hostnames are preserved to satisfy diagnosis correlation needs. The `event_ingest` audit records `data_masking_enabled` / `masked` / `sensitivity_categories` for traceability.

### Knowledge health risk closed loop (kb_admin and above)

"Audit & Config → Knowledge Health Report" provides in-place remediation entries for red/yellow graded cases:

- **Sync dead letter (red)**: "Replay dead-letter tasks" batch-resets dead-letter tasks and immediately triggers one due retry; on success the sync returns to confirmed; repeated failures stay in the compensation queue with exponential backoff.
- **Sync unconfirmed (yellow)**: per-row "Retry sync" or the top "Retry sync" triggers due compensation tasks, or you can wait for the scheduler's automatic retries.
- **Feedback score ≤ -2 or aged past threshold with negative feedback (red / yellow)**: per-row "Archive" takes a single case offline, or "Lifecycle patrol (preview)" previews candidates before confirming a batch archive; archiving also deletes the Milvus retrieval index and writes a `kb_archive` audit record.
- **High-risk content (red, including false-negative pass-through records)**: edit the case content in "Knowledge Base" and resubmit (re-passing the content safety scan), or archive it directly.
- **Archived**: terminal lifecycle state, no action needed; the health report and operations dashboard refresh automatically after remediation.

## 8. Help Center

The sidebar "User Guide" embeds an abbreviated version of this document, the FAQ, and an operations deployment quick reference; full documentation lives under `docs/` at the repository root.
