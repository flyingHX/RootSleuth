# Backend Development Guide

> 项目定位：本目录是 RootSleuth 运营控制台后端（Atoms 平台托管）。项目总览与目录关系见仓库根目录 `README.md`，项目文档见 `docs/`，对外接口文档见 `.wiki.md`「API Documentation」。

Atoms Cloud is enabled, use it as the backend service (provides Auth, Database, File Storage, Edge Functions, AI Capabilities).

## CRITICAL: DATABASE SETUP MUST BE COMPLETED BEFORE ANY CODE IMPLEMENTATION OR MODIFICATION

Follow These Steps in Order:

### Before Starting
- Review "Table Management Rules" section below for table operation guidelines

### For New Project Development
1. Install Dependencies (Authenticate and access the backend APIs) for frontend
   - Don't do the Row Level Security with user_id(or user.id) in the frontend. In fact, it imposes restrictions at the backend.
2. Database First (if you need to create tables)
   - Create all necessary tables using BackendManager.create_tables
     - If the table's entity is public data and unrelated to users, MUST to set `create_only=False`.
   - Generate the mock data for the necessary tables (data model) using BackendManager.insert_table_data if database table data not exists. So no need to generate mock data in the frontend.
     - Use ImageCreator.generate_images to generate images before BackendManager.insert_table_data if the mock data contains images. Check each result item: if it returns a URL (`result_item["url"]` is not empty), store that URL directly in the database; otherwise use the local path.
3. New features (if needed)
   - Generate the remaining or new features using BackendManager.create_function
4. Payment ability (if needed. After todo.md is completed)
   - Before implementing `backend/routers/payments.py`, read `skills_docs/custom_api.md` first and follow its payment, request/response contract, and backend session-boundary rules.
   - Use Editor.write to create `backend/routers/payments.py` in absolute path with the following API routes which stripe package
     - MUST use `/create_payment_session` to handle payment session initialization from the frontend. CRITICAL, Don't mock data.
       - Follow the backend "Create order + PaymentSession in one step" approach when user clicks Checkout
     - MUST use `/verify_payment` to verify the payment status after a successful transaction from the frontend. CRITICAL, Don't mock data.
   - Before implementing payment, document the purchase path in `todo.md` and follow the specified billing UX. If the billing UX is not specified, choose the simplest product-appropriate pattern.
   - After implementing the payment routes, authorize Stripe by calling `SecretManager.builtin_intg_authz`.
5. ObjectStorage ability (if needed)
   - Use BackendManager.create_bucket to store files like images, videos and files
   - Use BackendManager.upload_objects after ImageCreator.generate_images executed, and use the absolute path as input params and filename as object key.

### For Incremental Development
1. Database Changes (if needed)
   - Check existing tables using BackendManager.get_schemas
   - Create new tables or modify existing one as needed using BackendManager.create_tables
2. New features (if needed)
   - Generate the remaining or new features using BackendManager.create_function

## Web Application with Backend Guideline

When developing a web application with backend capabilities:
- Define database table schema using json-schema (Don't use UUID; Define id as Integer if it's autoincrement.), and create new tables or modify an existing one as needed using BackendManager.create_tables.
  - json-schema will automatically generate the relevant code files and save them under `backend/models`, `backend/routes`, and `backend/services`. Don't use Editor.write to update these auto-generated files unless it is necessary.
  - The generated ORM automatically adds and maintains `created_at` and `updated_at`. Do not define, validate, or manually assign these two fields in the business json-schema or normal request payloads.
- Generate the mock data for the necessary tables (data model) using BackendManager.insert_table_data if database table data not exists.
  - If the table uses a system-managed `user_id`, omit `user_id` from the mock rows and let BackendManager.insert_table_data inject the current user's `user_id`.
- For auto-managed Datetime fields like `created_at` or `updated_at`, rely on the generated model defaults instead of manually assigning them in normal backend CRUD logic. Only provide explicit values when backfilling historical records.
- If a backend route mixes database work with slow external work such as AI generation, Stripe/payment APIs, ObjectStorage, or third-party HTTP calls, split it into short DB phases. Do NOT keep an open SQLAlchemy transaction across the slow step. Follow the database session boundary rules in `skills_docs/custom_api.md`.
- Use metagptx/web-sdk@latest to develop frontend code to fetch backend data. And no need to generate mock data in the frontend.
- When generating code, ensure that UI state updates correctly after user actions — e.g., clear the cart after payment, and update cart item count after adding a product.
- Before implementing any upload, object listing, preview, or download flow, MUST read `skills_docs/object_storage.md`. For database-backed previews, implement the complete path: stored `object_key` -> `getDownloadUrl` -> `response.data.download_url` -> UI preview.
- If this request creates or modifies Python files, check syntax before finishing: run `mgx-pycheck <changed_python_files>` (e.g. `mgx-pycheck services/foo.py routers/foo.py`); if `mgx-pycheck` is not available, fall back to `python -m py_compile <changed_python_files>`. Only pass `.py` files. If it reports errors, fix them.

### Requirement-Critical Delivery Rules
- Before implementing a requirement-critical flow, map each explicit user requirement to a concrete page, CTA, API call, and persisted result path in `todo.md` when that file exists.
- Build the core flow first: input -> processing -> result -> persistence/history -> payment/upgrade if required. Do not spend most implementation effort on landing-page polish before the core feature works.
- Do not silently narrow the user's stated input contract just to simplify implementation. If a limitation is unavoidable, make it explicit in the UI with a graceful fallback.
- Requirement-related CTAs must lead to a real next action and durable result. This includes create, upload, generate, retry, history, download/export, purchase, and upgrade flows.
- If authentication is not itself the explicit core requirement, users should usually be able to browse the landing page and any requirement-critical purchase or upgrade entry before login, unless the user or system design explicitly requires a gated experience. Requirement-critical pages should include clear loading, not-logged-in, empty, error, and success states, and login should not be used as a shortcut for unfinished navigation or business logic.
- When payment is required, provide at least one real purchase path that is reachable through the intended purchaser flow. Do not assume one fixed billing UI for every app, and do not hide the only purchase path behind an obscure or exhausted-state-only entry unless explicitly required.

## Project Structure

```
./                              # Working dir
├── app                         # Project Folder
│    ├── backend                # Backend code folder
│    │    ├── main.py           # [PROTECTED] Backend startup entry
│    │    ├── lambda_handler.py # [PROTECTED] AWS Lambda entry + routing
│    │    ├── core/             # [PROTECTED] Config and runtime infrastructure
│    │    │    └── telemetry/   # Fail-open request diagnostics
│    │    ├── models/           # [PROTECTED] ORM models — auto-generated by BackendManager
│    │    ├── requirements.txt
│    │    ├── routers/          # API routes (auto-discovered, prefix MUST be /api/v1/)
│    │    ├── services/         # Business logic
│    │    ├── schemas/          # Pydantic request/response models
│    │    ├── alembic/          # Database migrations
│    ├── frontend               # Frontend code folder, usually use shadcn-ui
│    │    ├── public            # store generated or uploaded materials like images
│    │    ├── src
│    │    ├── index.html        # DO NOT modify. Title, description, and logo are configured via environment variables at deployment time
```

### Strict Rules
1. Do NOT modify, create, delete, or rewrite any files or subfolders under:
    - app/backend/core/**
    - app/backend/models/**
    - app/backend/main.py
    - app/backend/lambda_handler.py
2. All code generation and edits must be limited to non-protected directories only.
3. Never suggest changes, patches, diffs, or refactors that touch protected paths.

### Router Development Notes
- Routers under `routers/` are **auto-discovered** — no manual `include_router` call needed.
- All API routes MUST use prefix `/api/v1/` to be correctly proxied through Lambda.
- Read `core/config.py` `settings` for env vars: `settings.stripe_secret_key` reads `STRIPE_SECRET_KEY` dynamically.

## Table Management Rules
- IMPORTANT: DO NOT CREATE ANY USER TABLES. User management is FULLY handled by Atoms Cloud's builtin user table.

## Privacy / Header UI
- By default, do NOT display the logged-in user's email/name/userId in the header/top-right area.
- Use an avatar/person icon only, unless the user explicitly asks to show user info.

---
## Development Guides

### Step 2: Read Frontend README
After reading this file, use `Editor.read` to also load the frontend README:
- `/workspace/app/frontend/README.md` — Frontend tech stack, component usage, and build commands.

### Step 3: Read Skill Guides

**Must-read for ALL full-stack projects** (read immediately after the frontend README):
- **Web SDK**: Frontend authorization and data model access via metagptx/web-sdk. Covers Auth flow, Entity CRUD operations (query/queryAll/get/create/update/delete), and Backend Custom API invocation using client.apiCall.invoke.
  - Document: `/workspace/app/backend/skills_docs/web_sdk.md`

**Read on demand** (use `Editor.read` to load the relevant guide based on your project requirements, before implementing that feature):
- **AI Capability**: AI text, image, video, audio generation, single-PDF analysis, and speech transcription via the aihub module. Covers frontend `client.ai` / `client.apiCall.invoke` usage, backend `AIHubService` usage, and the distinction between direct aihub endpoints and custom wrapper APIs.
  - Document: `/workspace/app/backend/skills_docs/ai_capability.md`
- **Backend Custom API**: Backend custom API development with FastAPI. Covers service logic implementation under services/, API endpoint routing under routers/, automatic routing, environment variables, and a complete Stripe payment integration example including create_payment_session and verify_payment.
  - Document: `/workspace/app/backend/skills_docs/custom_api.md`
- **Object Storage**: File and media storage integration via ObjectStorage. Covers upload/download presigned URLs, bucket management, database object_key storage pattern, and web-sdk client.storage APIs for managing files, images, and videos.
  - Document: `/workspace/app/backend/skills_docs/object_storage.md`
