"""A private, throwaway PostgreSQL server for tests that need to create roles.

The application's test role can't create roles, and shouldn't. So these tests start their own
server: a new data directory in a temp folder, a random port on loopback, a random password that
exists only in this process, and everything removed afterwards. It never touches the machine's
real PostgreSQL service or its databases.

    uv run python -m tests.pg_cluster tests/          # the whole suite, as the runtime role
"""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import BACKEND_DIR
from app.db import hardening
from scripts.provision_local_db import scram_verifier

ADMIN = "finpilot_cluster_admin"
OWNER = "finpilot_owner"
RUNTIME = "finpilot_app"
DATABASE = "finpilot_security_test"


def find_binaries() -> Path | None:
    found = shutil.which("initdb")
    if found:
        return Path(found).parent
    patterns = (
        (r"C:\Program Files\PostgreSQL", "*/bin/initdb.exe"),
        ("/usr/lib/postgresql", "*/bin/initdb"),
    )
    for root, pattern in patterns:
        hits = sorted(Path(root).glob(pattern), reverse=True) if Path(root).exists() else []
        if hits:
            return hits[0].parent
    return None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Cluster:
    bin_dir: Path
    root: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="finpilot-pg-")))
    port: int = field(default_factory=_free_port)
    admin_password: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    owner_password: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    runtime_password: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)

    def _url(self, user: str, password: str, database: str) -> str:
        return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{self.port}/{database}"

    @property
    def admin_url(self) -> str:
        return self._url(ADMIN, self.admin_password, "postgres")

    @property
    def owner_url(self) -> str:
        return self._url(OWNER, self.owner_password, DATABASE)

    @property
    def runtime_url(self) -> str:
        return self._url(RUNTIME, self.runtime_password, DATABASE)

    @property
    def _data(self) -> Path:
        return self.root / "data"

    def start(self) -> None:
        secret = self.root / "pw"
        secret.write_text(self.admin_password, encoding="utf-8")
        subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [
                str(self.bin_dir / "initdb"), "-D", str(self._data), "-U", ADMIN,
                "--auth=scram-sha-256", f"--pwfile={secret}", "-E", "UTF8", "--locale=C",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )  # fmt: skip
        secret.unlink()
        options = f"-p {self.port} -c listen_addresses=127.0.0.1 -c max_connections=60"
        if os.name != "nt":
            options += f" -c unix_socket_directories={self.root}"
        subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [
                str(self.bin_dir / "pg_ctl"), "-D", str(self._data), "-w", "-t", "60",
                "-l", str(self.root / "log"), "-o", options, "start",
            ],
            check=True,
            timeout=120,
            # Not captured: the server inherits these handles, and a pipe would never close.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # fmt: skip

    def provision(self) -> None:
        """Administrator work: the schema owner, its database, and the runtime role."""
        admin = create_engine(self.admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            # exec_driver_sql: text() would read the verifier's colons as bind parameters.
            connection.exec_driver_sql(
                f"CREATE ROLE {OWNER} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                f"NOBYPASSRLS PASSWORD '{scram_verifier(self.owner_password)}'"
            )
            connection.execute(
                text(f"CREATE DATABASE {DATABASE} OWNER {OWNER} ENCODING 'UTF8' TEMPLATE template0")
            )
            connection.exec_driver_sql(
                hardening.role_sql(RUNTIME, scram_verifier(self.runtime_password))
            )
        admin.dispose()

    def migrate(self) -> None:
        from tests.conftest import alembic_config

        command.upgrade(alembic_config(self.owner_url), "head")

    def harden(self) -> None:
        owner = create_engine(self.owner_url, poolclass=NullPool)
        hardening.apply_hardening(owner, DATABASE, RUNTIME)
        owner.dispose()

    def stop(self) -> None:
        subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [str(self.bin_dir / "pg_ctl"), "-D", str(self._data), "-m", "immediate", "-w", "stop"],
            check=False,
            capture_output=True,
            timeout=60,
        )
        shutil.rmtree(self.root, ignore_errors=True)


def main() -> int:
    """Runs pytest against a private cluster, with the app connected as the runtime role."""
    binaries = find_binaries()
    if binaries is None:
        print("PostgreSQL's initdb wasn't found.")
        return 2
    cluster = Cluster(binaries)
    try:
        cluster.start()
        cluster.provision()
        environment = {
            **os.environ,
            "TEST_DATABASE_URL": cluster.owner_url,
            "TEST_RUNTIME_DATABASE_URL": cluster.runtime_url,
            "TEST_RUNTIME_ROLE": RUNTIME,
        }
        command_line = [sys.executable, "-m", "pytest", *(sys.argv[1:] or ["tests"])]
        return subprocess.run(  # noqa: S603 - our own interpreter and arguments
            command_line, cwd=BACKEND_DIR, env=environment, check=False
        ).returncode
    finally:
        cluster.stop()


if __name__ == "__main__":
    sys.exit(main())
