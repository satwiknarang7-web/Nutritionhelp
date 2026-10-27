"""Start a throwaway local PostgreSQL for tests (or use TEST_DATABASE_URL)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path


def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for base in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (base / name).exists():
            return str(base / name)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TempPostgres:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="nt-pg-")
        self.port = _free_port()
        self.as_user: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # initdb refuses to run as root
            self.as_user = ["runuser", "-u", "postgres", "--"]
            shutil.chown(self.dir, "postgres")

    def _run(self, *cmd: str) -> None:
        subprocess.run([*self.as_user, *cmd], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def start(self) -> str:
        initdb, pg_ctl = _find_bin("initdb"), _find_bin("pg_ctl")
        if not initdb or not pg_ctl:
            raise RuntimeError("PostgreSQL binaries not found")
        data = os.path.join(self.dir, "data")
        self._run(initdb, "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8")
        self._run(
            pg_ctl, "-D", data, "-l", os.path.join(self.dir, "log"), "-w",
            "-o", f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1 -c fsync=off", "start",
        )
        self._pg_ctl, self._data = pg_ctl, data
        subprocess.run(
            [*self.as_user, _find_bin("createdb") or "createdb", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "nutritrack"],
            check=True,
        )
        return f"postgresql://postgres@127.0.0.1:{self.port}/nutritrack"

    def stop(self) -> None:
        try:
            self._run(self._pg_ctl, "-D", self._data, "-m", "immediate", "stop")
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)
