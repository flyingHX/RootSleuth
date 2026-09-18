# Demo Accounts

English | [简体中文](DEMO_ACCOUNTS.md)

> Scope: RootSleuth console (`app/`) preview/demo environments.
> Last updated: 2026-09-12 (aligned one-by-one with `app/backend/routers/auth.py` · `DEMO_LOGIN_ACCOUNTS` and the seed data)

## 1. Important: Passwordless Mechanism

The console **does not use password login**, and demo accounts have no passwords either:

- Production authentication goes through Atoms OIDC (`/api/v1/auth/login` → `/api/v1/auth/callback`) or platform token exchange (`POST /api/v1/auth/token/exchange`);
- Preview/demo environments cannot complete external OIDC redirects, so a **passwordless demo login** endpoint `POST /api/v1/auth/demo-login` is provided: the client only submits an email; the backend verifies the email against the preset whitelist and reuses the existing JWT issuance chain to issue a token directly;
- The `DemoLoginRequest` body has a single `email` field and **contains no password field whatsoever** (a repository-wide search confirms no password credentials exist);
- The email is the credential: emails outside the whitelist receive 400 "only preset demo accounts are allowed to log in".

## 2. Demo Account List

| Email (login name) | Display Name | Console Role | Role Level | Typical Use |
|--------------------|--------------|--------------|-----------|-------------|
| `demo-operator@atoms.dev` | Demo On-call Operator | `operator` | viewer < **operator** < sre < approver < kb_admin < sys_admin | Alert viewing, AI diagnosis triggering, diagnosis feedback; read-only for rules/unknown queue |
| `demo-sre@atoms.dev` | Demo SRE | `sre` | … < **sre** < approver … | Knowledge base change set creation, merge proposals, unknown template promotion (initiate approvals) |
| `demo-lead@atoms.dev` | Demo Approver | `approver` | … < **approver** < kb_admin … | Approval center decisions (approve/reject/withdraw), viewing approval content and diffs |
| `demo-admin@atoms.dev` | Demo Administrator | `sys_admin` | Highest level | Rule publish/rollback/validate, audit logs, configuration center, user & role management |

Role source: configuration center `role_bindings_json` (email → role mapping) with default value
`{"demo-operator@atoms.dev":"operator","demo-sre@atoms.dev":"sre","demo-lead@atoms.dev":"approver","demo-admin@atoms.dev":"sys_admin"}`;
users without a binding are resolved by `default_role` (default `viewer`). sys_admin can adjust bindings under "Ops → Users & Roles".

## 3. How to Log In

### 3.1 Frontend (preview environment)

The console frontend automatically performs demo login as `demo-admin@atoms.dev` in preview environments (`ConsoleLayout` auto-calls demo-login when no credentials are detected); after a manual logout, auto-login no longer happens, and you can enter a logged-in state directly via `?token=<JWT>` in the URL.

### 3.2 API (curl example)

```bash
# Log in as demo-sre (replace email with any demo address from the table above to switch roles)
curl -s -X POST http://localhost:8000/api/v1/auth/demo-login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo-sre@atoms.dev"}'
# Response: {"token": "<JWT>"}; carry Authorization: Bearer <JWT> on subsequent requests
```

## 4. Security Constraints (Must-Read for Production)

- The demo login endpoint is controlled by the `ENABLE_DEMO_LOGIN` environment variable; it is enabled by default for preview convenience;
- **Production must set `ENABLE_DEMO_LOGIN=false`**, after which the endpoint returns 404 "demo login not enabled";
- Demo accounts are for feature demos and acceptance only; do not keep their role bindings in production databases.

## 5. Middleware/Service Daemon Accounts and Default Credentials

All services run as system processes/containers with no console login accounts; their service-level (daemon) accounts and default credentials are listed below, sourced from `aiops-rag-system/docker-compose.yml` and the deployment guide:

| Component | Account | Password | Notes |
|-----------|---------|----------|-------|
| PostgreSQL | Platform-managed databases get their connection string injected by the platform; for self-hosted instances the recommended account is `aiops_console` | Set a strong password yourself when creating the account (never stored in plaintext in any document) | Connection string goes into `DATABASE_URL`: `postgresql+asyncpg://aiops_console:<password>@<host>:5432/aiops_console` |
| MinIO | `minioadmin` | `minioadmin` | compose default (MINIO_ACCESS_KEY/SECRET_KEY), local development only |
| etcd | No authentication | None | Listens only inside the container network; Milvus metadata store |
| Kafka | No authentication (PLAINTEXT) | None | KRaft single node, `ALLOW_PLAINTEXT_LISTENER=yes` |
| Redis | No password | None | `redis-server --appendonly yes`, no requirepass |
| Elasticsearch | No authentication | None | `xpack.security.enabled=false` |
| Console frontend/backend | No daemon login account | None | Runs as OS processes/containers; authentication is unified through Atoms OIDC + JWT |

> Security note: the default credentials above exist only for local development/demo convenience. Production deployments must: change the MinIO access keys, enable authentication with strong passwords for Kafka/Redis/ES, and use a dedicated minimal-privilege PostgreSQL account (see `docs/OPERATIONS_DEPLOYMENT_GUIDE.md` §15 "PostgreSQL Production Hardening" and §21 "Middleware Architecture Overview").
