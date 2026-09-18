# RootSleuth Operations Console Runbook

English | [简体中文](OPERATIONS_RUNBOOK.md)

> Emergency handling handbook for on-call and SRE teams. See OPERATIONS_DEPLOYMENT_GUIDE.md for deployment details.

## 1. Service Inspection (Daily)

| Check | Command/Entry | Expected |
|-------|---------------|----------|
| Backend health | `curl http://localhost:8000/health` | 200 |
| Frontend reachable | Open the console home page in a browser | Login page/dashboard render normally |
| To-do backlog | Dashboard "To-do" cards or `GET /api/v1/console/dashboard` | pending_approvals / pending_unknowns not continuously growing |
| LLM health | Dashboard "Dependency Health" llm entry | healthy; when degraded, check diagnosis degradation reasons |
| Audit continuity | Audit log page sorted by time desc | All key operations of the day have audit records |

## 2. Common Alert Scenarios & Handling

### 2.1 Approval/promotion APIs return 500 (primary key conflict)
- **Root cause**: PostgreSQL sequences were not advanced after explicit-ID seeding/import.
- **Handling**:
  ```bash
  cd app/backend && python scripts/fix_sequences.py
  ```
- **Verify**: re-trigger a promotion or approval, expect 200; the audit log shows the corresponding record.

### 2.2 AI diagnosis timeouts in bulk (llm_timeout)
- **Handling**: sys_admin → Configuration Center → raise `llm_timeout_seconds` (e.g. 45 → 90, cap 300) → retry one diagnosis.
- **Still failing**: check the platform AIHub status; alert viewing is unaffected meanwhile, and RAG recalled cases can be used for handling in the interim.

### 2.3 Emergency change requires skipping review
- **Handling**: sys_admin → Configuration Center → `approval_mode=OFF` → submit the change, which publishes directly.
- **Restore**: switch back to `SINGLE_REVIEW` after the incident is handled, and review the `kb_publish` audit records produced during the OFF window.

### 2.4 Accidental knowledge/rule publication
- **Knowledge cases**: Knowledge Base → case detail → version history → roll back to the previous version (generates a new version, effective within seconds).
- **Rules**: Rule Management → version list → roll back to the previous version (reactivates and hot reloads).
- Both rollbacks write audit records; no database operation is needed.

### 2.5 RAG returns no candidates / rising unknown rate
- Check the "Rule Management → Unknown templates" queue: promote templates with large sample sizes promptly (fill in the target error_type → approval).
- In the knowledge base, confirm that active cases exist for the target error type; cases archived through merges are no longer recalled — switch to "Archived" in the case list to verify.

### 2.6 Frontend 401 / blank screen
- 401: login state expired, log in again; confirm the `token` exists in browser localStorage.
- /api 404: the Vite proxy target port does not match the backend's actual port (`BACKEND_PORT` environment variable).
- Blank screen: confirm the backend `/health` is fine and the frontend build artifacts are complete.

### 2.7 Diagnosis quality falls below the 0.85 alert line (P1-2)
- **Semantics**: Trust Index `T = 0.4×Faithfulness + 0.35×Citation Accuracy + 0.25×(1−hallucination rate)`; a 7-day rolling average < **0.85** triggers the quality alert; the Golden Set gate evaluation (P@1/MRR/HitRate) falling below its gate is also treated as a breach.
- **Observability entries**: the dashboard "Diagnosis quality trends" card, the `quality` aggregate of `GET /api/v1/console/dashboard`, and the RAG-side `/metrics` Golden Set / generation quality / Trust Index metrics (`rag_golden_*`, `rag_generation_*`, etc., subject to actual output; Prometheus alert rule thresholds are configured at 0.85).
- **Handling**:
  1. Locate low-scoring diagnoses on the Events page (Faithfulness / citation accuracy) and check whether their candidate cases are outdated or contain wrong remediations;
  2. Knowledge governance: fix or archive problematic cases on the Kb page; if necessary, check the red/yellow/green distribution and aging risks in the Ops page "Knowledge Health" report;
  3. Retrieval quality investigation: re-run the `aiops-rag-system/evaluation/` Golden Set evaluation to confirm why P@1 / MRR regressed (embedding changes, Milvus index rebuild, tenant filter adjustments);
  4. Observe for 24h after remediation; Trust Index returning to ≥ 0.85 closes the loop; record key remediations in the audit log.

## 3. Data Maintenance

### 3.1 Demo data reset
```bash
cd app/backend
python scripts/seed_console_demo.py   # Append demo data
python scripts/fix_sequences.py       # Must be executed
```

### 3.2 Backup (daily recommended)
```bash
pg_dump "$DATABASE_URL" -Fc -f console_backup_$(date +%F).dump
```
Audit logs are retained ≥ 180 days.

### 3.3 Restore
```bash
pg_restore --clean --if-exists -d "$DATABASE_URL" <dump file>
python scripts/fix_sequences.py
```

## 4. Upgrade Window Procedure

1. Notify users + freeze approval operations.
2. Take a database backup (§3.2).
3. Pull the new code and install dependencies (backend uv / frontend pnpm).
4. Restart the backend (drop `--reload` in production) and rebuild the frontend.
5. Unfreeze after the inspection (§1) fully passes.
6. Rollback plan: switch back to the previous code version and restart; for data, prefer the console's built-in version rollback.

## 5. Escalation / On-call Contact Matrix

| Matter | Responsible Role | Entry Point |
|--------|------------------|-------------|
| Approval backlog reminders | Approvers (approver+) | Approval Center |
| Approval mode/threshold changes | sys_admin | Configuration Center |
| Rule publish/rollback | sys_admin | Rule Management |
| Case rollback/merge | kb_admin+ | Knowledge Base |
| Database/sequences/deployment | Operations | Server + scripts/ |
| Platform login/AIHub issues | Platform side | Atoms platform support |

## 6. Emergency Quick Reference

```
Sequence conflict      -> python scripts/fix_sequences.py
Emergency no-review    -> Configuration Center approval_mode=OFF (switch back afterwards)
Accidental publication -> In-console version rollback (both knowledge and rules supported)
Diagnosis timeouts     -> Raise llm_timeout_seconds in the Configuration Center
Backend won't start    -> Check logs/restart.log + logs/app_YYYYMMDD.log
Frontend /api 404      -> Verify BACKEND_PORT against the Vite proxy port
Account suspension     -> Disable on the user management page, 403 takes effect immediately
Key leakage            -> Rotate JWT_SECRET_KEY immediately per §10.1
Milvus down            -> Diagnosis degrades automatically; docker compose restart milvus + healthz
```

## 7. Log Locations

| Service | Location | Content |
|---------|----------|---------|
| Console backend | `app/backend/logs/app_YYYYMMDD.log` | Startup, requests, exception stacks (daily rotation) |
| Console restart | `app/backend/logs/restart.log` | Restart and crash records |
| systemd service | `journalctl -u aiops-console -f` | uvicorn stdout/stderr |
| Containerized backend | `docker logs -f <container>` | Same as above |
| Frontend/Nginx | `/var/log/nginx/access.log`, `/var/log/nginx/error.log` | Access and proxy errors |
| RAG pipeline | `docker compose logs -f <svc>` or uvicorn stdout | Webhook/consumer logs |
| Milvus | `cd aiops-rag-system && docker compose logs milvus` | Collection loading, retrieval errors |
| etcd / MinIO | `docker compose logs etcd` / `logs minio` | Metadata, object storage errors |
| Kafka / Redis / ES | `docker compose logs kafka` / `redis` / `elasticsearch` | Their respective runtime logs |

Log retention recommendations: backend application logs ≥ 30 days, audit-related query records ≥ 180 days; container stdout should be rotated on the host (json-file max-size 50m × 5 or log shipping).

## 8. Dependency Failure Handling Matrix

> Design principle: all external dependencies of the RAG pipeline degrade silently (fail-open); a single point of failure never loses alerts, it only lowers diagnosis quality. For each middleware's architectural role, data flow, and full deployment/verification commands, see OPERATIONS_DEPLOYMENT_GUIDE.md §21~§22.

| Dependency | Typical Symptoms | Diagnostic Commands | Handling |
|------------|------------------|---------------------|----------|
| PostgreSQL | Backend 500s, connection refused | `pg_isready -d "$DATABASE_URL"`; `psql -c "SELECT pid, state, wait_event FROM pg_stat_activity;"` | Pool exhausted → restart backend; long transactions → `SELECT pg_terminate_backend(<pid>)`; disk full → clean WAL/expand |
| Milvus | Retrieval timeouts/no recall, diagnosis degradation | `curl http://localhost:9091/healthz`; `docker compose ps milvus` | `docker compose restart milvus`; still failing → check etcd/MinIO then `docker compose restart etcd minio milvus` |
| etcd | Milvus won't start, mvcc/quota errors in logs | `docker compose logs etcd \| grep -i quota` | Quota full (4GB) → emergency compaction + defragmentation; data corruption → restore volume then restart |
| MinIO | Milvus reports object storage errors | `docker compose logs minio`; `curl http://localhost:9000/minio/health/live` | Disk full → expand/clean; wrong credentials → verify compose environment |
| Redis | Dedup broken, duplicate alerts | `redis-cli ping`; `redis-cli info memory` | Memory full → raise maxmemory/LRU; connection refused → `docker compose restart redis` |
| Kafka | Event stalls, consumer backlog | `docker compose logs kafka`; consumer group lag script | Backlog → scale consumers/skip temporarily; broker down → `restart kafka` (KRaft single node) |
| Elasticsearch | Cold storage buffering fails | `curl http://localhost:9200/_cluster/health` | Disk watermark triggers read-only → clean old indexes/expand then lift read-only via `_settings`; heap 85% → tune ES_JAVA_OPTS |

**General steps**: first `docker compose ps` for container status → read the relevant logs to locate the root cause → restart the single service → full-chain health verification (§9) → write the post-mortem to the audit log.

## 9. Health Checks, Resource Exhaustion, and Network Issues

### 9.1 Health check checklist
```bash
curl http://localhost:8000/health                          # Console backend -> 200
curl http://localhost:8001/health                          # RAG pipeline API
curl http://localhost:9091/healthz                         # Milvus -> ok
curl http://localhost:9200/_cluster/health                 # ES -> status green/yellow
redis-cli ping                                             # Redis -> PONG
pg_isready -h <pg-host> -p 5432                            # PostgreSQL -> accepting
```
In K8s environments readiness/liveness probes run these automatically (deployment guide §18.2); Nginx can additionally probe `/health` passively.

### 9.2 Resource exhaustion
| Resource | Detection | Handling |
|----------|-----------|----------|
| CPU sustained >90% | `top`/`kubectl top pods` | Scale replicas/capacity; locate hotspots (usually LLM waits and reranking) |
| Memory OOM | `dmesg \| grep -i oom`; container restart count | Raise limits; control ES/Milvus heap |
| Disk >85% | `df -h`; PG/Milvus data volumes | Clean logs, clean old backups, expand WAL/indexes (full PG disks are a common root cause) |
| File descriptors | `lsof -p <pid> \| wc -l` vs `ulimit -n` | systemd `LimitNOFILE=65535` |

### 9.3 Network issues
1. Frontend 502/504: is the Nginx → backend port (`BACKEND_PORT`) alive; verify with direct `curl 127.0.0.1:8000/health`.
2. Backend cannot reach the managed DB: `telnet <pg-host> 5432` to verify egress/intranet policy; double-check `DATABASE_URL`.
3. OIDC login redirect fails: verify the callback domain matches the Atoms OIDC app configuration and the HTTPS certificate is valid (`openssl s_client`).
4. Milvus gRPC unreachable: inside the container network `nc -zv localhost 19530`; confirm no firewall blocks it.

## 10. Security Incidents

### 10.1 Key rotation
| Key | Steps | Impact |
|-----|-------|--------|
| JWT_SECRET_KEY | ① Update the K8s Secret/environment variable to the new value → ② rolling restart of the backend | All online sessions invalidated; users log in again (minutes) |
| Database password | ① PG `ALTER ROLE aiops_console PASSWORD '<new>'` → ② update Secret → ③ rolling restart of the backend | Brief connection failures; rollback restarts with the old password |
| LLM_API_KEY | Update in the console Configuration Center (stored Fernet-encrypted) + click "Test connectivity" after saving | Effective immediately, no restart |

- Key leakage response: treat as a rotation, then review abnormal operations within the leakage window in the audit log; never write keys into code/repositories.

### 10.2 Account suspension and permission recovery
- **Immediate suspension**: sys_admin → user management → select account → disable. All in-flight requests get 403 immediately (the dependency layer checks the database on every request, without waiting for JWT expiry).
- **Re-enable**: restore from the same entry; no token reset needed.
- **Permission revocation/change**: edit role bindings in user management (e.g. sys_admin → approver); saving takes effect immediately; `role_bindings_json` matches emails exactly.
- **Last-admin protection**: when only 1 sys_admin remains, disabling/demotion returns 400 — this is expected protection; promote a new sys_admin first.
- **Privilege-escalation config protection**: `default_role` forcibly rejects `sys_admin` (backend validates with 400 + role resolution falls back to viewer); no manual review of historical dangerous configs is needed.
- **Audit review**: filter the audit log page by operator/time; or run `psql "$DATABASE_URL" -c "SELECT created_at, user_email, action, detail FROM audit_logs WHERE created_at > now() - interval '24 hours' ORDER BY created_at DESC;"`. Focus on: `user_disable`, `user_role_change`, `kb_publish`, `rule_reload`, `config_update`.

## 11. Backup Verification and Disaster Recovery

### 11.1 Backup verification (monthly)
```bash
# 1) Rehearse by restoring into a temporary database
createdb aiops_restore_test
pg_restore --clean --if-exists -d aiops_restore_test console_backup_<latest>.dump
# 2) Spot-check key table row counts and the latest audit time
psql aiops_restore_test -c "SELECT count(*) FROM kb_cases; SELECT max(created_at) FROM audit_logs;"
# 3) Clean up
dropdb aiops_restore_test
```
- Verify backup files are non-empty (`ls -lh /var/backups/aiops/`) and continuous over the last 7 days; verify the vector store snapshot chain with `milvus-backup list`.

### 11.2 Disaster recovery procedure (target RTO ≤ 1h, RPO ≤ 24h)
1. Determine scope: single-service failures follow the §8 matrix restart; execute this section only for data corruption/total database loss.
2. Isolate the failed environment (stop writes to prevent dirty data spreading).
3. Rebuild infrastructure: `docker compose up -d` → `python scripts/init_milvus.py` (idempotent).
4. Restore the database: `pg_restore --clean --if-exists -d "$DATABASE_URL" <latest dump>` → `python scripts/fix_sequences.py`.
5. Object storage backfill: `mc mirror /var/backups/aiops/minio/ local/minio_data`.
6. Start the backend/pipeline, run the §9.1 full health check + manual console spot checks (login, dashboard, approvals, diagnosis).
7. Append a `config_update` audit record (noting DR time and recovery point), then hold a post-mortem and update this runbook.

### 11.3 Data rollback
- Priority 1: the console's built-in version rollback (knowledge cases/rule versions), effective in seconds with built-in auditing.
- Priority 2: single-table accidental operations — export only that table from the latest dump to compare and repair (`pg_restore -t <table>`), avoiding a full-database overwrite.
- Priority 3: full-database rollback `pg_restore --clean` (freeze the operation window, notify users).
- Forbidden: manual UPDATE/DELETE on production tables without a backup.

## 12. Emergency Contact Matrix

| Scenario | First Responder | Escalation Path | Contact Entry |
|----------|-----------------|-----------------|---------------|
| Console unavailable/login issues | On-call SRE | Operations lead | On-call group / oncall schedule |
| Database failure/data loss | Operations | DBA + platform side (Atoms Cloud managed DB) | On-call group + platform ticket |
| RAG pipeline/Milvus failure | On-call SRE | Operations lead | On-call group |
| Security incident (privilege escalation/leakage/abnormal audit) | On-call SRE | Security lead (report within 1h) | Security response group |
| AIHub/platform capability issues | On-call SRE | Atoms platform support | Platform ticket |
| Approval backlog/business rules | Approvers (approver+) | sys_admin adjusts the mode | Console Approval Center |
