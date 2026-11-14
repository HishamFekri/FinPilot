"""The production stack on this machine, without Docker, for local verification.

    uv run python -m tests_stack.local_stack                         # no proxy
    CADDY_BIN=/path/to/caddy uv run python -m tests_stack.local_stack   # with the real proxy

It starts, all on loopback and all throwaway:
1. a private PostgreSQL server (tests/pg_cluster.py), provisioned with the real scripts:
   create-owner, create-role, migrations as the owner, apply, verify;
2. the API in production mode, as the restricted role, exactly as the image runs it;
3. the standalone web server. Build it first, pointing its /api/v1 fallback at this runner's API:
   `FINPILOT_STANDALONE=1 API_PROXY_TARGET=http://127.0.0.1:8200 npm run build -w @finpilot/web`;
4. with CADDY_BIN: Caddy with deploy/caddy/Caddyfile.direct and its internal CA (never installed
   into any trust store). Without it, the tests go to the web server, which forwards /api/v1 to
   the API itself, and the proxy-only tests are skipped (STACK_DIRECT=1).

Then it runs tests_stack, restarts everything, runs the persistence phase, checks the logs, and
removes it all. It never reads backend/.env's database: every URL is passed explicitly.
What it can't show is anything about containers: networks, read-only filesystems, the Linux
Chromium sandbox, volumes. deploy/scripts/stack-test.sh covers those where Docker exists.
"""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from app.core.config import BACKEND_DIR
from tests.pg_cluster import ADMIN, Cluster, find_binaries

REPO = BACKEND_DIR.parent
WEB = REPO / "apps" / "web"
DATABASE = "finpilot"
OWNER, RUNTIME = "finpilot_owner", "finpilot_app"
HOST, HTTPS, HTTP, API_PORT, WEB_PORT = "localhost", 8443, 8080, 8200, 3200
ORIGIN = f"https://{HOST}:{HTTPS}"
CLIENT_ID = "finpilot-local-stack.apps.googleusercontent.com"


def _url(cluster: Cluster, user: str, password: str, database: str) -> str:
    return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{cluster.port}/{database}"


def _run(args: list[str], env: dict[str, str], *, expect: int | None = 0) -> str:
    result = subprocess.run(  # noqa: S603 - our own interpreter and fixed arguments
        args, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=600, check=False
    )
    output = result.stdout + result.stderr
    if expect is not None and result.returncode != expect:
        raise SystemExit(f"{' '.join(args[:4])} exited {result.returncode}:\n{output[-3000:]}")
    return output


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _wait(url: str, *, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit(f"{url} didn't become ready")


class Stack:
    def __init__(self, caddy: Path | None, cluster: Cluster, root: Path) -> None:
        self.caddy = caddy
        self.cluster = cluster
        self.root = root
        self.logs = root / "logs"
        self.logs.mkdir()
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.owner_password = secrets.token_urlsafe(32)
        self.runtime_password = secrets.token_urlsafe(32)
        self.owner_url = _url(cluster, OWNER, self.owner_password, DATABASE)
        self.runtime_url = _url(cluster, RUNTIME, self.runtime_password, DATABASE)
        self.secrets = root / "secrets"
        self.secrets.mkdir()

    def secret(self, name: str, value: str) -> str:
        path = self.secrets / name
        path.write_text(value, encoding="utf-8")
        return str(path)

    def base_env(self) -> dict[str, str]:
        # System variables only: nothing that could name a database or carry a credential.
        # Playwright finds Chrome through the Program Files variables.
        keep = {
            "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
            "PROGRAMW6432", "COMMONPROGRAMFILES", "PROGRAMDATA", "COMPUTERNAME", "OS",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "HOME", "LANG",
        }  # fmt: skip
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        return {**env, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    def provision(self) -> list[str]:
        """The deploy scripts' order, with secrets passed as files, as in the containers."""
        env = {
            **self.base_env(),
            "ADMIN_DATABASE_URL_FILE": self.secret(
                "admin_database_url",
                _url(self.cluster, ADMIN, self.cluster.admin_password, "postgres"),
            ),
            "OWNER_ROLE_PASSWORD_FILE": self.secret("owner_role_password", self.owner_password),
            "RUNTIME_ROLE_PASSWORD_FILE": self.secret(
                "runtime_role_password", self.runtime_password
            ),
            "OWNER_DATABASE_URL_FILE": self.secret("owner_database_url", self.owner_url),
            "MIGRATION_DATABASE_URL_FILE": str(self.secrets / "owner_database_url"),
            "RUNTIME_DATABASE_URL_FILE": self.secret("database_url", self.runtime_url),
            "APP_ENV": "production",
        }
        tool = [sys.executable, "-m", "scripts.db_hardening"]
        out = [
            _run([*tool, "create-owner", "--database", DATABASE, "--owner-approved"], env),
            _run([*tool, "create-role", "--owner-approved"], env),
            _run([sys.executable, "-m", "alembic", "upgrade", "head"], env),
            _run([*tool, "apply", "--database", DATABASE, "--owner-approved"], env),
            _run([*tool, "verify"], env),
        ]
        again = _run([*tool, "create-owner", "--database", DATABASE, "--owner-approved"], env)
        assert "already exists" in again, again
        return out

    def api_env(self, **overrides: str) -> dict[str, str]:
        return {
            **self.base_env(),
            "APP_ENV": "production",
            # Explicit, so backend/.env's development database can never be used.
            "DATABASE_URL": self.runtime_url,
            "WEB_ORIGIN": ORIGIN,
            "GOOGLE_CLIENT_ID": CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": "local-stack-only-not-a-real-secret",
            "GOOGLE_REDIRECT_URI": f"{ORIGIN}/api/v1/auth/google/callback",
            "TRUSTED_PROXIES": "127.0.0.1",
            "BETA_ALLOWLIST": "stack-owner@example.test",
            "PDF_BROWSER_CHANNEL": os.environ.get("PDF_BROWSER_CHANNEL", "chrome"),
            **overrides,
        }

    def check_config_is_refused(self) -> list[str]:
        """The API refuses to start with unsafe production settings."""
        probe = [sys.executable, "-c", "from app.main import create_app; create_app()"]
        callback = "/api/v1/auth/google/callback"
        no_google = {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_REDIRECT_URI": ""}
        cases: dict[str, dict[str, str]] = {
            "http redirect": {"GOOGLE_REDIRECT_URI": f"http://{HOST}:{HTTPS}{callback}"},
            "other host": {"GOOGLE_REDIRECT_URI": f"https://elsewhere.example{callback}"},
            "no Google": no_google,
            "half Google": {"GOOGLE_CLIENT_SECRET": ""},
            "bad client id": {"GOOGLE_CLIENT_ID": "not-a-google-client"},
            "catch-all proxy": {"TRUSTED_PROXIES": "0.0.0.0/0"},
            "no proxy": {"TRUSTED_PROXIES": ""},
            "http origin": {"WEB_ORIGIN": f"http://{HOST}:{HTTPS}"},
            "empty allowlist": {"REGISTRATION_MODE": "allowlist", "BETA_ALLOWLIST": ""},
        }
        refused = []
        for name, change in cases.items():
            output = _run(probe, self.api_env(**change), expect=None)
            if "ValidationError" not in output:
                raise SystemExit(f"Unsafe setting accepted: {name}\n{output[-1500:]}")
            for value in (self.runtime_password, "local-stack-only-not-a-real-secret"):
                if value in output:
                    raise SystemExit(f"A secret appeared in the refusal for {name}")
            refused.append(name)
        _run(probe, self.api_env())  # and the real settings are accepted
        return refused

    def start(self, name: str, args: list[str], env: dict[str, str], cwd: Path) -> None:
        log = (self.logs / f"{name}.log").open("ab")
        self.processes[name] = subprocess.Popen(  # noqa: S603 - fixed arguments
            args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
        )

    def start_all(self) -> None:
        self.start(
            "api",
            [
                sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(API_PORT), "--workers", "1",
                "--no-proxy-headers", "--no-server-header", "--no-access-log",
            ],
            self.api_env(),
            BACKEND_DIR,
        )  # fmt: skip
        standalone = WEB / ".next" / "standalone" / "apps" / "web"
        web_env = {
            **self.base_env(),
            "NODE_ENV": "production",
            "PORT": str(WEB_PORT),
            "HOSTNAME": "127.0.0.1",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        self.start("web", ["node", "server.js"], web_env, standalone)
        _wait(f"http://127.0.0.1:{API_PORT}/api/v1/readyz")
        _wait(f"http://127.0.0.1:{WEB_PORT}/en")
        if self.caddy is None:
            return
        caddy_env = {
            **self.base_env(),
            "FINPILOT_HOST": HOST,
            "FINPILOT_ORIGIN": ORIGIN,
            "FINPILOT_TLS": "internal",
            "FINPILOT_API_UPSTREAM": f"127.0.0.1:{API_PORT}",
            "FINPILOT_WEB_UPSTREAM": f"127.0.0.1:{WEB_PORT}",
            "XDG_DATA_HOME": str(self.root / "caddy-data"),
            "XDG_CONFIG_HOME": str(self.root / "caddy-config"),
        }
        config = REPO / "deploy" / "caddy" / "Caddyfile.direct"
        self.start(
            "caddy",
            [str(self.caddy), "run", "--config", str(config), "--adapter", "caddyfile"],
            caddy_env,
            self.root,
        )
        deadline = time.monotonic() + 60
        while not self.ca.exists() and time.monotonic() < deadline:
            time.sleep(0.5)

    @property
    def ca(self) -> Path:
        return self.root / "caddy-data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"

    def stop_all(self) -> None:
        for process in self.processes.values():
            process.terminate()
        for process in self.processes.values():
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
        self.processes.clear()

    def restart_database(self) -> None:
        data = str(self.cluster.root / "data")
        subprocess.run(  # noqa: S603 - fixed arguments
            [str(self.cluster.bin_dir / "pg_ctl"), "-D", data, "-m", "fast", "-w", "restart"],
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def tests(self, phase: str) -> int:
        through_proxy = {
            "STACK_URL": ORIGIN,
            "STACK_HTTP_URL": f"http://{HOST}:{HTTP}",
            "STACK_CA": str(self.ca),
        }
        direct = {"STACK_URL": f"http://127.0.0.1:{WEB_PORT}", "STACK_DIRECT": "1"}
        env = {
            **self.base_env(),
            **(direct if self.caddy is None else through_proxy),
            "STACK_ORIGIN": ORIGIN,
            "STACK_OWNER_DATABASE_URL": self.owner_url,
            "STACK_RUNTIME_DATABASE_URL": self.runtime_url,
            "STACK_PHASE": phase,
        }
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests_stack"]
        return subprocess.run(  # noqa: S603 - our own interpreter
            [*command, "-rs"], cwd=BACKEND_DIR, env=env, check=False
        ).returncode

    def check_logs(self) -> None:
        """No secrets in any log, and the proxy kept no query strings or headers."""
        api = (self.logs / "api.log").read_text(encoding="utf-8", errors="replace")
        web = (self.logs / "web.log").read_text(encoding="utf-8", errors="replace")
        logs = [api, web]
        assert '"route": "/api/v1/auth/me"' in api, "API access log missing"
        assert "__Host-fp_session" not in api and "?" not in "".join(
            line for line in api.splitlines() if '"route"' in line
        ), "the API logged a raw URL or a cookie"
        if self.caddy is not None:
            caddy = (self.logs / "caddy.log").read_text(encoding="utf-8", errors="replace")
            assert '"uri":"/api/v1/auth/google/start"' in caddy, "proxy access log missing"
            assert "return_to=" not in caddy and "?x=1" not in caddy, "query string logged"
            assert "__Host-fp_session" not in caddy and "Cookie" not in caddy, "headers logged"
            logs.append(caddy)
        for text in logs:
            for value in (self.owner_password, self.runtime_password, self.cluster.admin_password):
                assert value not in text, "a database password appeared in a log"
        print("Logs: no raw URLs, cookies or database passwords in the logs checked.")


def main() -> int:
    caddy: Path | None = None
    if os.environ.get("CADDY_BIN"):
        caddy = Path(os.environ["CADDY_BIN"]).resolve()
        if not caddy.is_file():
            print("CADDY_BIN doesn't name a file.")
            return 2
    else:
        print("No CADDY_BIN: running without the proxy. Proxy-only tests will be skipped.")
    if not (WEB / ".next" / "standalone" / "apps" / "web" / "server.js").exists():
        print("Build the web app first (see this file's docstring for API_PROXY_TARGET).")
        return 2
    ports = (API_PORT, WEB_PORT) + ((HTTPS, HTTP, 2019) if caddy else ())
    busy = [p for p in ports if not _port_free(p)]
    if busy:
        print(f"Ports already in use: {busy}. Nothing was started.")
        return 2
    binaries = find_binaries()
    if binaries is None:
        print("PostgreSQL's initdb wasn't found.")
        return 2
    standalone = WEB / ".next" / "standalone" / "apps" / "web"
    shutil.copytree(WEB / ".next" / "static", standalone / ".next" / "static", dirs_exist_ok=True)
    shutil.copytree(WEB / "public", standalone / "public", dirs_exist_ok=True)

    root = Path(tempfile.mkdtemp(prefix="finpilot-stack-"))
    cluster = Cluster(binaries)
    stack = Stack(caddy, cluster, root)
    try:
        cluster.start()
        for line in stack.provision():
            print(line.strip())
        print("Refused unsafe settings:", ", ".join(stack.check_config_is_refused()))
        stack.start_all()
        main_result = stack.tests("main")
        print("Restarting the database, API, web server and proxy.")
        stack.stop_all()
        stack.restart_database()
        stack.start_all()
        restart_result = stack.tests("after_restart")
        stack.stop_all()
        stack.check_logs()
        return main_result or restart_result
    finally:
        stack.stop_all()
        cluster.stop()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
