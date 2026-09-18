# RootSleuth Operations Console Backend

English | [简体中文](README.md)

> Positioning: this directory contains the FastAPI backend of the RootSleuth operations console. See the repository root `README.md` for the project overview and directory layout, `docs/CONSOLE_USER_GUIDE.md` for the user guide, and `docs/OPERATIONS_DEPLOYMENT_GUIDE.md` for the deployment guide.

## Tech Stack

- Python 3.10+ / FastAPI / SQLAlchemy (async) / pydantic-settings
- Database: PostgreSQL (Alembic migrations)
- Authentication: OIDC + JWT (passwordless demo-login is provided in preview/demo environments)
- Testing: pytest

## Directory Structure

```
app/backend/
├── main.py            # Backend entry point
├── routers/           # API routes (auto-discovered, mounted under the unified /api/v1/ prefix)
├── services/          # Business logic (Agents, knowledge base, rules, approvals, audit, etc.)
├── models/            # ORM models
├── schemas/           # Pydantic request/response models
├── dependencies/      # Dependency injection (auth, database session, etc.)
├── middlewares/       # Middlewares
├── core/              # Configuration and runtime infrastructure
├── alembic/           # Database migrations
├── tests/             # pytest tests
├── scripts/           # Operations and verification scripts
└── requirements.txt
```

## Local Development

```bash
cd app/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in DATABASE_URL, JWT_SECRET_KEY, MASK_KEY, etc. per the comments
uvicorn main:app --host 0.0.0.0 --port 8000
```

One-command startup for frontend and backend (ports auto-assigned): run `bash app/start_app_v2.sh` from the repository root.

## Running Tests

```bash
python -m pytest tests/ -v
```

## API Conventions

- Business routes are mounted under the unified `/api/v1/` prefix and auto-registered from the `routers/` directory.
- Health check: `/health`.
- See `docs/CONSOLE_USER_GUIDE.md` and the deployment guide for the full API list and calling conventions.
