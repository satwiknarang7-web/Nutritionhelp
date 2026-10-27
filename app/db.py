"""Neon / PostgreSQL connection pool and transaction helpers.

Use Neon's *pooled* connection string (host contains `-pooler`). Statements are
never server-side prepared so the app works behind PgBouncer in transaction mode.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

log = logging.getLogger(__name__)

APP_ROLE = "nutritrack_user"

_pool: ConnectionPool | None = None


class DatabaseNotConfigured(RuntimeError):
    pass


def _configure(conn: Connection) -> None:
    conn.prepare_threshold = None  # PgBouncer transaction pooling friendly


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        if not settings.database_url:
            raise DatabaseNotConfigured("DATABASE_URL is not set. Copy your Neon connection string into .env.")
        _pool = ConnectionPool(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            # Neon suspends idle computes; drop idle connections before the server does
            max_idle=120,
            timeout=30,
            kwargs={"row_factory": dict_row, "connect_timeout": 15},
            configure=_configure,
            check=ConnectionPool.check_connection,
            open=True,
            name="nutritrack",
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def system_tx() -> Iterator[Connection]:
    """Transaction as the owner role - only for auth tables and migrations."""
    with get_pool().connection() as conn, conn.transaction():
        yield conn


@contextmanager
def user_tx(user_id: str) -> Iterator[Connection]:
    """Transaction restricted by row level security to one user's rows."""
    with get_pool().connection() as conn, conn.transaction():
        conn.execute(f"set local role {APP_ROLE}")
        conn.execute("select set_config('app.user_id', %s, true)", (str(user_id),))
        yield conn
