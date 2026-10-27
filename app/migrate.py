"""Apply SQL migrations in migrations/ in order.  Usage: python -m app.migrate"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(database_url: str | None = None, verbose: bool = True) -> list[str]:
    url = database_url or get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())"
        )
        done = {r[0] for r in conn.execute("select name from schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
            if verbose:
                print(f"applied {path.name}")
    if verbose and not applied:
        print("database is up to date")
    return applied


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else None)
