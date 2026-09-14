"""Reset PostgreSQL id sequences for tables seeded with explicit IDs.

Seed scripts insert rows with explicit primary keys, which leaves the
underlying id sequences untouched. Any later INSERT without an explicit id
then collides with existing rows (UniqueViolationError on the pkey).

This script walks the console business tables and advances each id sequence
to MAX(id) + 1 so that normal ORM inserts allocate fresh keys.

Usage (from /workspace/app/backend):
    DATABASE_URL=... python scripts/fix_sequences.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

# Console business tables seeded with explicit ids by scripts/seed_console_demo.py
TABLES = [
    "events",
    "kb_cases",
    "approval_requests",
    "approval_steps",
    "kb_change_sets",
    "kb_versions",
    "kb_merge_proposals",
    "rule_versions",
    "unknown_templates",
    "audit_logs",
    "console_configs",
]


def _to_asyncpg_url(raw_url: str) -> str:
    """Normalize common SQLAlchemy URL prefixes to a plain asyncpg URL."""
    url = raw_url.strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; nothing to do", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(_to_asyncpg_url(database_url))
    try:
        failures = 0
        for table in TABLES:
            row = await conn.fetchrow("SELECT pg_get_serial_sequence($1, 'id') AS seq", table)
            seq = row["seq"] if row else None
            if not seq:
                print(f"SKIP {table}: no serial sequence on id")
                continue
            await conn.execute(
                f"SELECT setval($1, GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, 1), false)",
                seq,
            )
            next_id = await conn.fetchval(f"SELECT last_value FROM {seq}")
            print(f"OK {table}: sequence {seq} -> next id {next_id}")
        return 0 if failures == 0 else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
