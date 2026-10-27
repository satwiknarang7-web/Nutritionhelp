import os
import threading
from urllib.parse import urlsplit, urlunsplit

import psycopg

from app.migrate import migrate


def test_concurrent_migrations_apply_once():
    base = os.environ["DATABASE_URL"]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("drop database if exists nt_migrate_test")
        conn.execute("create database nt_migrate_test")
    parts = urlsplit(base)
    url = urlunsplit(parts._replace(path="/nt_migrate_test"))
    results, errors = [], []

    def run():
        try:
            results.append(migrate(url, verbose=False))
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(len(r) for r in results) == [0, 0, 0, 1]  # exactly one instance applied it
    assert migrate(url, verbose=False) == []
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("drop database nt_migrate_test with (force)")
