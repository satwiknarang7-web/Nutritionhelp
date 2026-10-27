"""Apply SQL migrations in migrations/ in order.  Usage: python -m app.migrate

Safe to run concurrently (e.g. several serverless instances starting at once):
pending migrations are applied in one transaction that holds a transaction-scoped
advisory lock, which also works behind Neon's PgBouncer pooler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LOCK_ID = 727_274_001  # arbitrary, app-wide


def _pending(conn: psycopg.Connection, files: list[Path]) -> list[Path]:
    exists = conn.execute("select to_regclass('public.schema_migrations') is not null").fetchone()[0]
    done = {r[0] for r in conn.execute("select name from schema_migrations").fetchall()} if exists else set()
    return [p for p in files if p.name not in done]


def migrate(database_url: str | None = None, verbose: bool = True) -> list[str]:
    url = database_url or get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=15) as conn:
        if _pending(conn, files):  # cheap check first, so normal cold starts don't take the lock
            with conn.transaction():
                conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_ID,))
                conn.execute(
                    "create table if not exists schema_migrations "
                    "(name text primary key, applied_at timestamptz not null default now())"
                )
                for path in _pending(conn, files):  # re-check while holding the lock
                    conn.execute(path.read_text())
                    conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
                    applied.append(path.name)
    if verbose:
        for name in applied:
            print(f"applied {name}")
        if not applied:
            print("database is up to date")
    return applied


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else None)
