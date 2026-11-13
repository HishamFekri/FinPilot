"""The deployment files, checked without Docker.

These hold the packaging to its security properties: what's published, who runs as whom, which
secret reaches which container, how the proxy decides a client's address in each mode, and that
nothing destructive happens on start or outside the stack test's own project. The running stack
is tested by deploy/scripts/stack-test.sh (containers) and tests_stack (through the proxy).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from app.core.config import BACKEND_DIR

REPO = BACKEND_DIR.parent
DEPLOY = REPO / "deploy"


def _yaml(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))
    return loaded


COMPOSE = _yaml("compose.yaml")
DIRECT = _yaml("compose.direct.yaml")
TUNNEL = _yaml("compose.tunnel.yaml")
STACKTEST = _yaml("compose.stacktest.yaml")
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]
TEST_SERVICES: dict[str, dict[str, Any]] = STACKTEST["services"]
LONG_RUNNING = {"db", "api", "web", "proxy"}
JOBS = set(SERVICES) - LONG_RUNNING


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


# --- Compose: the production project


def test_the_stack_has_exactly_the_expected_services() -> None:
    assert set(SERVICES) >= LONG_RUNNING
    assert {"migrate", "db-provision", "db-apply", "db-verify", "backup", "restore"} == JOBS
    assert set(TEST_SERVICES) == {"stack-test", "edge", "stack-test-behind"}
    assert set(DIRECT["services"]) == {"proxy"}


@pytest.mark.parametrize(
    ("name", "service"),
    [*sorted(SERVICES.items()), *sorted(TEST_SERVICES.items())],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_container_is_locked_down(name: str, service: dict[str, Any]) -> None:
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert "no-new-privileges:true" in service["security_opt"]
    assert not service.get("privileged")
    for forbidden in ("network_mode", "pid", "ipc", "devices", "userns_mode"):
        assert forbidden not in service, forbidden
    for volume in service.get("volumes", []):
        assert "docker.sock" not in str(volume)
    for option in service["security_opt"]:
        assert "unconfined" not in option, name
    assert service["logging"]["options"]["max-size"]


@pytest.mark.parametrize("name", sorted(LONG_RUNNING))
def test_long_running_services_have_limits_health_and_restart(name: str) -> None:
    service = SERVICES[name]
    for limit in ("mem_limit", "cpus", "pids_limit"):
        assert limit in service, limit
    assert service["healthcheck"]["test"]
    assert service["restart"] == "unless-stopped"
    assert "profiles" not in service


def test_the_base_project_publishes_nothing() -> None:
    for name, service in SERVICES.items():
        assert "ports" not in service, name
        assert "expose" not in service, name


def test_direct_mode_publishes_only_the_proxy_and_by_default_on_loopback() -> None:
    ports = DIRECT["services"]["proxy"]["ports"]
    assert len(ports) == 2
    for port in ports:
        assert port.startswith("${FINPILOT_BIND_ADDRESS:-127.0.0.1}:")
    # 8080 is taken by the server's existing Caddy (docs/SERVER_READINESS.md §9).
    assert ":-8080}" not in " ".join(ports)
    for service in TEST_SERVICES.values():
        assert "ports" not in service and "expose" not in service


def test_every_network_has_its_own_fixed_subnet_and_only_egress_reaches_out() -> None:
    networks = COMPOSE["networks"]
    assert set(networks) == {"front", "data", "egress", "edge"}
    for name in ("front", "data", "edge"):
        assert networks[name]["internal"] is True, name
    assert "internal" not in networks["egress"]
    subnets = {name: net["ipam"]["config"][0]["subnet"] for name, net in networks.items()}
    assert subnets == {
        "front": "${FINPILOT_FRONT_SUBNET:-172.30.10.0/24}",
        "data": "${FINPILOT_DATA_SUBNET:-172.30.11.0/24}",
        "egress": "${FINPILOT_EGRESS_SUBNET:-172.30.12.0/24}",
        "edge": "${FINPILOT_EDGE_SUBNET:-172.30.13.0/29}",
    }


def test_who_is_on_which_network() -> None:
    assert SERVICES["db"]["networks"] == ["data"]
    assert SERVICES["web"]["networks"] == ["front"]
    assert SERVICES["api"]["networks"] == ["front", "data", "egress"]
    assert set(SERVICES["proxy"]["networks"]) == {"front", "edge", "egress"}
    for job in JOBS:
        assert SERVICES[job]["networks"] == ["data"], job
    # Only the proxy and the stand-in edge (and its test runner) ever join the edge network.
    on_edge = {n for n, s in {**SERVICES, **TEST_SERVICES}.items() if "edge" in s["networks"]}
    assert on_edge == {"proxy", "edge", "stack-test-behind"}


def test_jobs_never_run_on_start() -> None:
    for name in JOBS:
        assert SERVICES[name]["restart"] == "no", name
        assert SERVICES[name]["profiles"] == ["ops"], name
    for name, service in TEST_SERVICES.items():
        assert service["restart"] == "no" and service["profiles"] == ["test"], name
    for name in LONG_RUNNING:
        command = " ".join(map(str, SERVICES[name].get("command", [])))
        assert "alembic" not in command and "db_hardening" not in command


def test_each_container_gets_only_the_secrets_it_needs() -> None:
    expected = {
        "db": {"postgres_password"},
        "api": {"database_url", "google_client_secret", "beta_allowlist"},
        "web": set(),
        "proxy": set(),
        "migrate": {"owner_database_url"},
        "db-provision": {"admin_database_url", "owner_role_password", "runtime_role_password"},
        "db-apply": {"owner_database_url"},
        "db-verify": {"database_url"},
        "backup": {"owner_database_url"},
        "restore": {"owner_database_url"},
    }
    actual = {name: set(service.get("secrets", [])) for name, service in SERVICES.items()}
    assert actual == expected
    tests = {name: set(service.get("secrets", [])) for name, service in TEST_SERVICES.items()}
    assert tests == {
        "stack-test": {"owner_database_url", "database_url"},
        "stack-test-behind": {"owner_database_url", "database_url"},
        "edge": set(),
    }
    for secret in COMPOSE["secrets"].values():
        assert set(secret) == {"file"}  # files only: never `environment:` secrets


def test_no_secret_is_passed_as_an_environment_variable() -> None:
    for name, service in {**SERVICES, **TEST_SERVICES}.items():
        for key, value in (service.get("environment") or {}).items():
            names_a_secret = re.search(r"PASSWORD|SECRET|DATABASE_URL|TOKEN", key)
            if names_a_secret and key != "FINPILOT_SECRETS_DIR":  # a folder, not a value
                assert key.endswith("_FILE"), f"{name}: {key}"
            assert not re.search(r"://[^/\s:]+:[^@\s]+@", str(value)), f"{name}: {key}"


def test_the_api_runs_in_production_mode_behind_a_named_proxy() -> None:
    env = SERVICES["api"]["environment"]
    assert env["APP_ENV"] == "production"
    assert env["FINPILOT_SECRETS_DIR"] == "/run/secrets"
    assert env["TRUSTED_PROXIES"] == "${FINPILOT_PROXY_IP:-172.30.10.2}"
    proxy_front = SERVICES["proxy"]["networks"]["front"]
    assert proxy_front["ipv4_address"] == "${FINPILOT_PROXY_IP:-172.30.10.2}"
    assert env["GOOGLE_REDIRECT_URI"] == "${FINPILOT_ORIGIN}/api/v1/auth/google/callback"
    assert env["WEB_ORIGIN"].startswith("${FINPILOT_ORIGIN:?")


def test_containers_run_as_users_that_are_unused_on_the_server() -> None:
    # Host users on the server: 1000 is the administrator, 999 a service account, and the AI
    # app's containers run as 10001 (docs/SERVER_READINESS.md §17, S-I2).
    assert SERVICES["api"]["user"] == "10201:10201"
    assert SERVICES["web"]["user"] == "10202:10202"
    assert SERVICES["db"]["user"] == "999:999"  # the image's own, which owns its data
    for job in ("migrate", "db-provision", "db-apply", "db-verify"):
        assert SERVICES[job]["user"] == "10201:10201"
    for job in ("backup", "restore"):
        assert SERVICES[job]["user"] == "999:10201"  # may read the owner URL (group 10201)
    for service in SERVICES.values():
        assert not str(service.get("user", "")).startswith(("1000:", "10001:"))
    assert "uid=10201,gid=10201" in " ".join(SERVICES["api"]["tmpfs"])
    assert "uid=10202,gid=10202" in " ".join(SERVICES["web"]["tmpfs"])


def test_only_the_api_has_its_own_seccomp_profile() -> None:
    assert SERVICES["api"]["security_opt"] == [
        "no-new-privileges:true",
        "seccomp=${FINPILOT_API_SECCOMP_PROFILE:?set by deploy/scripts/lib.sh}",
    ]
    for name, service in {**SERVICES, **TEST_SERVICES}.items():
        if name != "api":
            assert service["security_opt"] == ["no-new-privileges:true"], name


def test_application_images_must_be_named_and_third_party_ones_pinned() -> None:
    assert SERVICES["api"]["image"].startswith("${FINPILOT_API_IMAGE:?")
    assert SERVICES["web"]["image"].startswith("${FINPILOT_WEB_IMAGE:?")
    for name in ("db", "proxy"):
        default = SERVICES[name]["image"].split(":-", 1)[1].rstrip("}")
        assert re.search(r":\d+\.\d+", default), default  # an exact version, never "latest"


def test_the_api_health_check_means_ready_not_just_alive() -> None:
    assert "/api/v1/readyz" in " ".join(SERVICES["api"]["healthcheck"]["test"])
    assert SERVICES["proxy"]["depends_on"]["api"]["condition"] == "service_healthy"


# --- Proxy: both modes share one site


CADDY = DEPLOY / "caddy"
SITE = _text(CADDY / "site.caddy")
DIRECT_CADDYFILE = _text(CADDY / "Caddyfile.direct")
BEHIND_CADDYFILE = _text(CADDY / "Caddyfile.behind")
EDGE_CADDYFILE = _text(DEPLOY / "testing" / "edge.Caddyfile")


def test_the_proxy_picks_its_mode_file_and_mounts_the_folder_read_only() -> None:
    proxy = SERVICES["proxy"]
    assert "/etc/caddy/Caddyfile.${FINPILOT_CADDY_CONFIG:-direct}" in proxy["command"]
    assert "./caddy:/etc/caddy:ro" in proxy["volumes"]
    assert {p.name for p in CADDY.iterdir()} == {
        "site.caddy",
        "Caddyfile.direct",
        "Caddyfile.behind",
    }
    assert not (DEPLOY / "Caddyfile").exists()


@pytest.mark.parametrize(
    "directive",
    [
        "max_size 1MB",
        r'request>uri regexp \?.*$ ""',
        "request>headers delete",
        "resp_headers delete",
        "handle /api/v1/*",
        "-Server",
    ],
)
def test_the_site_keeps_its_safeguards(directive: str) -> None:
    assert directive in SITE


def test_each_upstream_gets_exactly_one_client_address() -> None:
    """Whatever arrives in X-Forwarded-For, the API and web server see only {client_ip}."""
    blocks = re.findall(r"reverse_proxy [^\n]*\{(.*?)\n\t\t\}", SITE, re.DOTALL)
    assert len(blocks) == 2
    for block in blocks:
        assert "header_up X-Forwarded-For {client_ip}" in block
        assert "header_up X-Forwarded-Proto https" in block


def test_the_proxy_upstreams_name_real_services() -> None:
    upstreams = re.findall(r"reverse_proxy \{\$\w+:([\w.-]+):(\d+)\}", SITE)
    assert upstreams == [("api", "8000"), ("web", "3000")]
    assert set(dict(upstreams)) <= set(SERVICES)


@pytest.mark.parametrize(
    "caddyfile", [DIRECT_CADDYFILE, BEHIND_CADDYFILE], ids=["direct", "behind"]
)
def test_both_modes_serve_the_shared_site(caddyfile: str) -> None:
    assert "import site.caddy" in caddyfile and "import finpilot_site" in caddyfile
    assert "admin 127.0.0.1:2019" in caddyfile
    assert "max_header_size 32KB" in caddyfile


def test_direct_mode_terminates_tls_and_trusts_nobody() -> None:
    for directive in (
        "tls {$FINPILOT_TLS}",
        "redir {$FINPILOT_ORIGIN}{uri} 308",
        "skip_install_trust",
        "auto_https disable_redirects",
    ):
        assert directive in DIRECT_CADDYFILE
    assert "trusted_proxies" not in DIRECT_CADDYFILE + SITE


def test_behind_mode_trusts_exactly_the_edge_and_reads_right_to_left() -> None:
    trusted = re.findall(r"trusted_proxies static (\S+)", BEHIND_CADDYFILE)
    assert trusted == ["{$FINPILOT_EDGE_IP}/32"]
    assert "trusted_proxies_strict" in BEHIND_CADDYFILE
    assert "client_ip_headers {$FINPILOT_CLIENT_IP_HEADER:X-Forwarded-For}" in BEHIND_CADDYFILE
    assert "auto_https off" in BEHIND_CADDYFILE
    assert "http://{$FINPILOT_HOST}:8080 {" in BEHIND_CADDYFILE
    assert "\ttls " not in BEHIND_CADDYFILE and "redir " not in BEHIND_CADDYFILE


def test_the_stand_in_edge_trusts_only_the_test_runner_for_cloudflares_header() -> None:
    assert re.findall(r"trusted_proxies static (\S+)", EDGE_CADDYFILE) == [
        "{$EDGE_TRUSTED_CLIENT}/32"
    ]
    assert "client_ip_headers Cf-Connecting-Ip" in EDGE_CADDYFILE
    assert "trusted_proxies_strict" in EDGE_CADDYFILE
    assert "header_up X-Forwarded-For {client_ip}" in EDGE_CADDYFILE
    edge = TEST_SERVICES["edge"]
    assert (
        edge["environment"]["EDGE_TRUSTED_CLIENT"]
        == "${FINPILOT_STACK_TESTER_EDGE_IP:-172.30.13.4}"
    )
    tester = TEST_SERVICES["stack-test-behind"]
    assert (
        tester["networks"]["edge"]["ipv4_address"]
        == "${FINPILOT_STACK_TESTER_EDGE_IP:-172.30.13.4}"
    )
    assert (
        SERVICES["proxy"]["environment"]["FINPILOT_EDGE_IP"] == "${FINPILOT_EDGE_IP:-172.30.13.3}"
    )
    assert edge["networks"]["edge"]["ipv4_address"] == "${FINPILOT_EDGE_IP:-172.30.13.3}"


@pytest.mark.parametrize(
    "path", sorted(DEPLOY.rglob("*")), ids=lambda p: p.relative_to(DEPLOY).as_posix()
)
def test_server_side_files_use_unix_line_endings(path: Path) -> None:
    pattern = r"(\.sh|\.ya?ml|\.caddy|Caddyfile(\.\w+)?|\.json|\.service|\.timer|\.example)$"
    if path.is_file() and re.search(pattern, path.name):
        assert b"\r" not in path.read_bytes()


# --- Seccomp: Docker's default, plus exactly what Chromium's sandbox needs


def test_the_api_seccomp_profile_is_dockers_default_plus_three_calls() -> None:
    builder = DEPLOY / "seccomp" / "build_profile.py"
    result = subprocess.run(  # noqa: S603 - a fixed script in this repository
        [sys.executable, str(builder), "--check"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    default = json.loads(_text(DEPLOY / "seccomp" / "docker-default-29.9.2.json"))
    ours = json.loads(_text(DEPLOY / "seccomp" / "api-chromium.json"))
    assert ours["defaultAction"] == default["defaultAction"] == "SCMP_ACT_ERRNO"
    removed = [r for r in default["syscalls"] if r not in ours["syscalls"]]
    added = [r for r in ours["syscalls"] if r not in default["syscalls"]]
    assert all(r["names"] == ["clone"] and "args" in r for r in removed) and len(removed) == 2
    assert len(added) == 1
    assert added[0]["names"] == ["clone", "unshare", "chroot"]
    assert added[0]["action"] == "SCMP_ACT_ALLOW" and "args" not in added[0]
    assert "includes" not in added[0]  # no capability is involved


# --- Images


API_DOCKERFILE = _text(BACKEND_DIR / "Dockerfile")
WEB_DOCKERFILE = _text(REPO / "apps" / "web" / "Dockerfile")


def test_the_web_server_forwards_to_the_api_service() -> None:
    target = re.search(r"ARG API_PROXY_TARGET=http://([\w.-]+):(\d+)", WEB_DOCKERFILE)
    assert target is not None
    assert target.groups() == ("api", "8000")


@pytest.mark.parametrize("dockerfile", [API_DOCKERFILE, WEB_DOCKERFILE], ids=["api", "web"])
def test_images_use_exact_base_versions_and_no_secrets(dockerfile: str) -> None:
    for line in dockerfile.splitlines():
        if line.startswith("ARG ") and "_IMAGE=" in line:
            assert re.search(r":\d+\.\d+\.\d+", line), line
    assert "latest" not in dockerfile
    assert not re.search(r"(PASSWORD|SECRET|TOKEN)\s*=", dockerfile)
    assert "HEALTHCHECK" in dockerfile


def test_the_api_image_runs_one_unprivileged_process_with_the_sandbox_on() -> None:
    after = API_DOCKERFILE.split("FROM ${PYTHON_IMAGE} AS runtime", 1)[1]
    runtime = after.split("AS stack-test")[0]
    assert "USER 10201:10201" in runtime
    assert "--uid 10201" in runtime and "--gid 10201" in runtime
    for flag in ('"--workers", "1"', '"--no-proxy-headers"', '"--no-access-log"'):
        assert flag in runtime
    assert "playwright install --with-deps --only-shell chromium" in runtime
    assert "PDF_BROWSER_CHANNEL=" in runtime
    assert "--no-sandbox" not in API_DOCKERFILE
    assert API_DOCKERFILE.rstrip().endswith("FROM runtime")  # the default target


def test_the_web_image_runs_as_its_own_user_with_the_standalone_server() -> None:
    runtime = WEB_DOCKERFILE.split("AS runtime", 1)[1]
    assert "USER 10202:10202" in runtime and "USER node" not in runtime
    assert "--uid 10202" in runtime
    assert 'CMD ["node", "apps/web/server.js"]' in runtime
    assert "FINPILOT_STANDALONE=1" in WEB_DOCKERFILE


@pytest.mark.parametrize("ignore", [BACKEND_DIR / ".dockerignore", REPO / ".dockerignore"])
def test_build_contexts_exclude_local_secrets(ignore: Path) -> None:
    rules = _text(ignore).splitlines()
    assert rules[0 if rules[0] == "*" else rules.index("*")] == "*"  # deny by default
    for pattern in ("**/.env", "**/.env.*", "**/*.pem", "**/*.key"):
        assert pattern in rules


# --- Scripts


SCRIPTS = sorted((DEPLOY / "scripts").glob("*.sh"))


def test_the_scripts_exist() -> None:
    assert {p.name for p in SCRIPTS} == {
        "lib.sh", "init-secrets.sh", "provision.sh", "deploy.sh", "rollback.sh",
        "backup.sh", "restore.sh", "stack-test.sh", "stack-test-stop.sh", "host-snapshot.sh",
    }  # fmt: skip


def test_the_shared_library_fails_closed() -> None:
    lib = _text(DEPLOY / "scripts" / "lib.sh")
    assert "set -euo pipefail" in lib and "umask 077" in lib
    assert "*@sha256:*" in lib  # releases are pinned by digest
    assert "FINPILOT_PROXY_MODE must be direct, behind or tunnel" in lib
    test_overlay = '"$DEPLOY_DIR/compose.stacktest.yaml"'
    assert f'[ "${{FINPILOT_COMPOSE_TEST:-}}" = 1 ] && files+=(--file {test_overlay})' in lib
    assert '[ "$mode" = direct ] && files+=(--file "$DEPLOY_DIR/compose.direct.yaml")' in lib


def test_only_the_stack_test_uses_the_test_overlay() -> None:
    for script in SCRIPTS:
        if script.name not in ("stack-test.sh", "lib.sh"):
            assert "FINPILOT_COMPOSE_TEST" not in _text(script), script.name


@pytest.mark.parametrize("script", [p for p in SCRIPTS if p.name != "lib.sh"], ids=lambda p: p.name)
def test_every_script_does_nothing_without_an_explicit_flag(script: Path) -> None:
    text = _text(script)
    assert 'source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"' in text
    assert "nothing done" in text
    assert "set -x" not in text
    assert not re.search(r"\bcat\b[^|\n]*secrets", text)  # secrets are never printed


@pytest.mark.parametrize(
    "script",
    [*SCRIPTS, *sorted((DEPLOY / "inspection").glob("*.sh"))],
    ids=lambda p: p.name,
)
def test_no_script_touches_the_rest_of_the_host(script: Path) -> None:
    code = "\n".join(
        line for line in _text(script).splitlines() if not line.lstrip().startswith("#")
    )
    command = r"(^|[;&|(]\s*|\$\(\s*)"  # where a command starts
    for forbidden in (
        command + r"sudo\s", r"\bprune\b",
        r"systemctl (restart|stop|start|disable|enable|reboot)\b",
        command + r"(reboot|shutdown|poweroff)\b",
        r"\bufw (allow|deny|enable|disable|delete|reset)\b",
        command + r"(iptables|nft)\s", r"docker (restart|stop|kill)\b",
        r"apt(-get)? (install|upgrade|remove)", r"docker network (create|connect|disconnect)",
        r"tailscale (up|down|set|logout)\b", r"tailscale (serve|funnel) (?!status)",
        r"\bmount\s+/", r"\bumount\b",
    ):  # fmt: skip
        assert not re.search(forbidden, code, re.MULTILINE), f"{script.name}: {forbidden}"


def test_only_the_stack_test_ever_removes_volumes() -> None:
    for script in SCRIPTS:
        text = _text(script)
        if script.name not in ("stack-test.sh", "stack-test-stop.sh"):
            assert "--volumes" not in text and "down -v" not in text, script.name
            assert "volume rm" not in text, script.name
        sql_drop = r"\bDROP\s+(TABLE|DATABASE|SCHEMA|ROLE|OWNED|USER|INDEX|VIEW)\b"
        assert not re.search(sql_drop, text, re.IGNORECASE), script.name
    test = _text(DEPLOY / "scripts" / "stack-test.sh")
    assert 'FINPILOT_PROJECT="finpilot-stacktest"' in test
    stop = _text(DEPLOY / "scripts" / "stack-test-stop.sh")
    assert 'label="label=com.docker.compose.project=$TEST_PROJECT"' in stop
    assert 'TEST_PROJECT="finpilot-stacktest"' in stop
    for removal in ("docker rm -f", "docker network rm", "docker volume rm"):
        line = next(line for line in stop.splitlines() if removal in line)
        assert "$ids" in line
    assert stop.count('--filter "$label"') >= 4


def test_restore_never_targets_the_live_database() -> None:
    restore = _text(DEPLOY / "scripts" / "restore.sh")
    assert "refusing to restore into the live database" in restore
    assert "already exists. Choose a new name." in restore


def test_a_failed_dump_never_leaves_a_restic_snapshot() -> None:
    backup = _text(DEPLOY / "scripts" / "backup.sh")
    assert 'status=("${PIPESTATUS[@]}")' in backup
    assert "forget --quiet" in backup and "no snapshot kept" in backup
    assert "--restic-init" in backup and "pass --restic-init to create it" in backup


def test_git_ignores_deployment_secrets_and_state() -> None:
    ignored = _text(REPO / ".gitignore").splitlines()
    for rule in ("/deploy/secrets/*", "/deploy/finpilot.env", "/deploy/state/", "/deploy/backups/"):
        assert rule in ignored


# --- The scripts' refusals, run for real (no Docker needed: each stops before using it)


def _bash() -> str:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found is None or found.lower().endswith(r"system32\bash.exe"):
        pytest.skip("no bash to run the deployment scripts with")
    return found


def _script(name: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP")}
    return subprocess.run(  # noqa: S603 - a fixed script with fixed arguments
        [_bash(), str(DEPLOY / "scripts" / name), *args],
        env={**keep, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("init-secrets.sh", ()),
        ("provision.sh", ("--release", "x.env")),
        ("deploy.sh", ("--release", "x.env")),
        ("rollback.sh", ()),
        ("backup.sh", ()),
        ("restore.sh", ("--from", __file__, "--into", "finpilot_copy")),
        ("stack-test.sh", ()),
        ("stack-test-stop.sh", ()),
        ("host-snapshot.sh", ()),
    ],
)
def test_each_script_refuses_to_act_without_its_confirmation(
    name: str, args: tuple[str, ...], tmp_path: Path
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "previous.env").write_text("FINPILOT_API_IMAGE=a@sha256:1\n", encoding="utf-8")
    result = _script(name, *args, FINPILOT_STATE_DIR=str(state))
    assert result.returncode != 0
    assert "nothing done" in result.stderr, result.stderr


def test_a_release_with_tags_instead_of_digests_is_refused(tmp_path: Path) -> None:
    release = tmp_path / "release.env"
    release.write_text("FINPILOT_API_IMAGE=finpilot-api:latest\nFINPILOT_WEB_IMAGE=w:1\n", "utf-8")
    for name, confirm in (("deploy.sh", "--confirm"), ("provision.sh", "--confirm-new-database")):
        result = _script(name, "--release", str(release), confirm)
        assert result.returncode != 0
        assert "pinned by digest" in result.stderr, result.stderr


def test_restore_refuses_the_live_database_before_doing_anything(tmp_path: Path) -> None:
    site = tmp_path / "finpilot.env"
    site.write_text("FINPILOT_DATABASE=finpilot\n", encoding="utf-8")
    result = _script(
        "restore.sh", "--from", __file__, "--into", "finpilot", "--confirm",
        FINPILOT_SITE_ENV=str(site),
    )  # fmt: skip
    assert result.returncode != 0 and "live database" in result.stderr


def test_a_restic_restore_needs_its_repository_and_password() -> None:
    result = _script(
        "restore.sh", "--from-restic", "0123abcd", "--into", "finpilot_copy", "--confirm"
    )
    assert result.returncode != 0 and "needs --restic-repo" in result.stderr
    result = _script("restore.sh", "--from-restic", "not;an-id", "--into", "finpilot_copy")
    assert result.returncode != 0 and "snapshot ID" in result.stderr


def test_an_unknown_proxy_mode_is_refused(tmp_path: Path) -> None:
    site = tmp_path / "finpilot.env"
    site.write_text("FINPILOT_PROXY_MODE=public\n", encoding="utf-8")
    probe = tmp_path / "mode.sh"
    probe.write_text(
        f'source "{(DEPLOY / "scripts" / "lib.sh").as_posix()}"\nproxy_mode\n', encoding="utf-8"
    )
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP")}
    result = subprocess.run(  # noqa: S603 - a fixed script
        [_bash(), str(probe)],
        env={**keep, "FINPILOT_SITE_ENV": str(site)},
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert result.returncode != 0 and "must be direct, behind or tunnel" in result.stderr
    site.write_text("FINPILOT_PROXY_MODE=behind\n", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - a fixed script
        [_bash(), str(probe)],
        env={**keep, "FINPILOT_SITE_ENV": str(site)},
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert result.returncode == 0 and result.stdout.strip() == "behind"


def test_init_secrets_creates_strong_secrets_once_and_prints_none(tmp_path: Path) -> None:
    site = tmp_path / "finpilot.env"
    site.write_text("FINPILOT_DATABASE=finpilot_x\n", encoding="utf-8")
    folder = tmp_path / "secrets"
    args = ("--generate", "--local-test-permissions", "--secrets-dir", str(folder))
    first = _script("init-secrets.sh", *args, FINPILOT_SITE_ENV=str(site))
    assert first.returncode == 0, first.stderr

    read = {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()}
    assert set(read) == {
        "postgres_password", "owner_role_password", "runtime_role_password",
        "admin_database_url", "owner_database_url", "database_url", "google_client_secret",
        "beta_allowlist",
    }  # fmt: skip
    passwords = [read[k] for k in ("postgres_password", "owner_role_password")]
    passwords.append(read["runtime_role_password"])
    for password in passwords:
        assert re.fullmatch(r"[0-9a-f]{64}", password)
        assert password not in first.stdout + first.stderr
    assert len(set(passwords)) == 3
    assert read["database_url"] == (
        f"postgresql+psycopg://finpilot_app:{read['runtime_role_password']}@db:5432/finpilot_x"
    )
    assert read["owner_database_url"].startswith("postgresql+psycopg://finpilot_owner:")
    assert read["google_client_secret"] == ""  # the owner supplies it; empty refuses to start
    assert read["beta_allowlist"] == ""  # likewise: production refuses an empty allowlist

    again = _script("init-secrets.sh", *args, FINPILOT_SITE_ENV=str(site))
    assert again.returncode != 0 and "never replaced" in again.stderr
    assert (folder / "database_url").read_text(encoding="utf-8") == read["database_url"]
    assert "chown root:10201" in _text(DEPLOY / "scripts" / "init-secrets.sh")


# --- The preflight and the bundle


def test_the_preflight_only_reads() -> None:
    text = _text(DEPLOY / "inspection" / "stack-test-preflight.sh")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in (
        r"docker (run|rm|create|start|build|pull|compose (up|run|down|create))",
        r"docker (network|volume) (create|rm)", r"\bmkdir\b", r"\brm\b", r"\btouch\b",
        r">\s*[^&/ \d]", r"(^|[;&|]\s*)sudo\s",
    ):  # fmt: skip
        assert not re.search(forbidden, code, re.MULTILINE), forbidden
    for checked in ("18443 18080", "172.30.253.0/29", "10201 10202", "PREFLIGHT FAILED"):
        assert checked in text


@pytest.mark.parametrize(
    ("profile", "line"), [("ci", "PASS  CPUs: 2"), ("server", "FAIL  CPUs: 2 (need 4)")]
)
def test_the_preflight_needs_4_cpus_on_the_server_and_2_on_a_ci_runner(
    tmp_path: Path, profile: str, line: str
) -> None:
    # Stubs: a 2-CPU machine, and a docker/ip that answer nothing (the real ones are never used).
    stubs = {"nproc": "echo 2", "docker": "exit 1", "ip": "exit 1"}
    for name, body in stubs.items():
        stub = tmp_path / name
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP")}
    # The stubs go first inside bash: Git Bash's launcher puts its own folders ahead of PATH.
    first = 'd="$1"; command -v cygpath > /dev/null && d="$(cygpath -u "$d")"; PATH="$d:$PATH"'
    result = subprocess.run(  # noqa: S603 - a fixed, read-only script
        [_bash(), "-c", f'{first}; exec bash "$2" --in-place', "preflight", str(tmp_path),
         str(DEPLOY / "inspection" / "stack-test-preflight.sh")],
        env={**keep, "HOME": str(tmp_path), "FINPILOT_PREFLIGHT_PROFILE": profile},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip
    assert line in result.stdout.splitlines(), result.stdout


def test_the_bundle_takes_only_what_the_images_and_tests_need() -> None:
    bundle = _module(DEPLOY / "tools" / "make_bundle.py")
    chosen = {p.relative_to(REPO).as_posix() for p in bundle.selected()}
    assert "backend/.env" not in chosen
    assert not any(
        p.startswith(("backend/.venv", "apps/web/node_modules", "apps/web/.next")) for p in chosen
    )
    assert not any(p.startswith("deploy/secrets/") and not p.endswith("README.md") for p in chosen)
    assert not any(p.startswith(("backend/tests/", "backend/tests_e2e/")) for p in chosen)
    for needed in (
        "backend/Dockerfile", "apps/web/Dockerfile", "deploy/compose.yaml",
        "deploy/scripts/stack-test.sh", "deploy/seccomp/api-chromium.json",
        "backend/scripts/sandbox_probe.py", "deploy/inspection/stack-test-preflight.sh",
    ):  # fmt: skip
        assert needed in chosen, needed
    assert bundle.problems([p for p in bundle.selected() if p.exists()]) == []


def test_the_bundle_refuses_secret_looking_files(tmp_path: Path) -> None:
    bundle = _module(DEPLOY / "tools" / "make_bundle.py")
    samples = {
        ".env.local": b"X=1\n",
        "notes.txt": b"postgresql+psycopg://user:averylongpassword@db/x\n",
        "id.txt": b"-----BEGIN OPENSSH PRIVATE KEY-----\n",
        "run.sh": b"echo hi\r\n",
        "fine.txt": b"postgresql+psycopg://finpilot_app:${runtime}@db:5432/x\n",
        ".env.example": b"DATABASE_URL=\n",
    }
    paths = []
    for name, data in samples.items():
        path = tmp_path / name
        path.write_bytes(data)
        paths.append(path)
    found = bundle.problems(paths)
    assert len(found) == 4, found
    assert not any(".env.example" in issue for issue in found)
    assert not any("fine.txt" in issue for issue in found)


# --- Tunnel mode: the home server's production topology


def test_tunnel_mode_publishes_one_loopback_port_and_trusts_only_the_gateway() -> None:
    proxy = TUNNEL["services"]["proxy"]
    assert set(TUNNEL["services"]) == {"proxy"}
    assert proxy["ports"] == ["127.0.0.1:${FINPILOT_TUNNEL_PORT:-18085}:8080"]
    assert proxy["environment"] == {
        "FINPILOT_EDGE_IP": "${FINPILOT_EGRESS_GATEWAY:-172.30.12.1}",
        "FINPILOT_CLIENT_IP_HEADER": "Cf-Connecting-Ip",
    }
    # The gateway of the egress network, whose default subnet is 172.30.12.0/24.
    assert COMPOSE["networks"]["egress"]["ipam"]["config"][0]["subnet"].endswith("172.30.12.0/24}")
    lib = _text(DEPLOY / "scripts" / "lib.sh")
    assert '[ "$mode" = tunnel ] && files+=(--file "$DEPLOY_DIR/compose.tunnel.yaml")' in lib
    assert 'FINPILOT_CADDY_CONFIG="$([ "$mode" = direct ] && echo direct || echo behind)"' in lib


def test_the_stack_test_checks_tunnel_mode_the_way_cloudflared_connects() -> None:
    test = _text(DEPLOY / "scripts" / "stack-test.sh")
    for check in (
        "tunnel mode publishes only 127.0.0.1:18085",
        "Cloudflare's client address is used: its 21st sign-in attempt is refused",
        "another Cloudflare client still gets in",
        "a forged X-Forwarded-For buys nothing in tunnel mode",
        "refuses open registration in production",
    ):
        assert check in test, check
    assert "18085" in _text(DEPLOY / "inspection" / "stack-test-preflight.sh")


# --- Release workflow: GitHub-hosted, after CI, for pushes to main only


RELEASE = _text(REPO / ".github" / "workflows" / "release.yml")
CI = _text(REPO / ".github" / "workflows" / "ci.yml")


def test_release_runs_only_after_ci_passes_for_a_push_to_main_here() -> None:
    workflow = yaml.safe_load(RELEASE)
    triggers = workflow[True]  # YAML 1.1 reads the key `on` as True
    assert set(triggers) == {"workflow_run"}
    assert triggers["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    job = workflow["jobs"]["release"]
    for condition in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert condition in job["if"]
    assert workflow["permissions"] == {}
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert job["runs-on"] == "ubuntu-latest"
    assert workflow["concurrency"] == {"group": "release-main", "cancel-in-progress": False}
    for workflow in (yaml.safe_load(RELEASE), yaml.safe_load(CI)):
        assert {job["runs-on"] for job in workflow["jobs"].values()} == {"ubuntu-latest"}
    assert "pull_request_target" not in RELEASE.replace("`pull_request_target`", "") + CI.replace(
        "`pull_request_target`", ""
    )


def test_release_publishes_exactly_the_tested_images_and_the_manifest_last() -> None:
    assert "FINPILOT_STACKTEST_KEEP_IMAGES" in RELEASE
    assert RELEASE.index("stack-test.sh --run") < RELEASE.index("docker push")
    assert RELEASE.rindex("finpilot-release:latest") > RELEASE.rindex("finpilot-web:sha-")
    assert "docker tag finpilot-api:stacktest" in RELEASE
    assert "persist-credentials: false" in RELEASE
    assert set(re.findall(r"secrets\.(\w+)", RELEASE)) == {"GITHUB_TOKEN"}


@pytest.mark.parametrize("workflow", [RELEASE, CI], ids=["release", "ci"])
def test_every_action_is_pinned_to_a_full_commit(workflow: str) -> None:
    for action in re.findall(r"uses:\s*(\S+)", workflow):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action


# --- Server: the deployer, backups, units and installer


SERVER = DEPLOY / "server"
DEPLOYER = _text(SERVER / "finpilot-deployer.sh")
INSTALL = _text(SERVER / "install.sh")
BACKUP = _text(SERVER / "finpilot-backup.sh")


def test_the_deployer_takes_only_validated_digests_from_one_registry() -> None:
    assert '. "$work/release.env"' not in DEPLOYER and 'source "$work' not in DEPLOYER
    for pattern in (
        r"^FINPILOT_RELEASE_SHA=[0-9a-f]{40}$",
        r"^FINPILOT_API_IMAGE=ghcr\.io/hishamfekri/finpilot-api@sha256:[0-9a-f]{64}$",
        r"^FINPILOT_WEB_IMAGE=ghcr\.io/hishamfekri/finpilot-web@sha256:[0-9a-f]{64}$",
    ):
        assert pattern in DEPLOYER
    assert "the release manifest has an unexpected line; nothing deployed" in DEPLOYER
    assert "flock -n 9" in DEPLOYER


def test_the_deployer_holds_destructive_migrations_and_never_reprovisions() -> None:
    assert "awaiting-approval-$sha" in DEPLOYER and '"$APPROVALS/$sha"' in DEPLOYER
    assert "/^def upgrade/{on=1} /^def downgrade/{on=0} on" in DEPLOYER
    assert "finpilot_db_data already exists: refusing to provision over it" in DEPLOYER
    assert "already deployed: --first-deploy is only for the very first time" in DEPLOYER
    assert 'touch "$STATE/failed-$sha"' in DEPLOYER  # a failed release isn't retried
    assert "rollback.sh" in DEPLOYER


def test_the_migration_gate_flags_destructive_upgrades_only() -> None:
    gate = re.search(r"grep -Eiq '([^']+)'", DEPLOYER)
    assert gate is not None
    destructive = re.compile(gate.group(1), re.IGNORECASE)
    for body in (
        'op.drop_table("x")',
        'op.drop_column("t", "c")',
        'op.execute("DELETE FROM transactions")',
        'op.execute("UPDATE transactions SET amount = 0")',
        'op.alter_column("t", "c", type_=sa.Text())',
        'op.execute("TRUNCATE journal_lines")',
    ):
        assert destructive.search(body), body
    for body in (
        'op.add_column("t", sa.Column("c", sa.Text()))',
        'op.create_index("i", "t", ["c"])',
    ):
        assert not destructive.search(body), body


@pytest.mark.parametrize("script", ["finpilot-deployer.sh", "finpilot-backup.sh", "install.sh"])
def test_server_scripts_never_delete_data(script: str) -> None:
    text = _text(SERVER / script)
    for forbidden in (
        "--volumes", "down -v", "volume rm", "system prune", "image prune", "volume prune",
        "builder prune", "psql ", "docker rm -f", "rm -rf /",
        "systemctl restart", "systemctl stop", "ufw ", "iptables",
    ):  # fmt: skip
        assert forbidden not in text, f"{script}: {forbidden}"
    assert "set -euo pipefail" in text and "umask 077" in text


def test_backups_are_encrypted_scheduled_retained_and_alert_on_failure() -> None:
    assert "--restic-repo" in BACKUP
    assert "--keep-daily 7 --keep-weekly 4 --keep-monthly 6" in BACKUP
    assert "check --quiet" in BACKUP and "trap 'kuma down" in BACKUP
    assert "flock -w 1800 9" in BACKUP  # never during a deployment
    timer = _text(SERVER / "systemd" / "finpilot-backup.timer")
    assert "OnCalendar=*-*-* 03:40:00" in timer and "Persistent=true" in timer


def test_the_installer_never_replaces_secrets_or_settings() -> None:
    for guard in (
        "[ ! -f /etc/finpilot/deployer.env ]",
        "[ ! -s /etc/finpilot/restic-password ]",
        '[ ! -f "$deploy/finpilot.env" ]',
        '[ ! -e "$deploy/secrets/database_url" ]',
        "--exclude='deploy/secrets'",
        "--exclude='deploy/state'",
    ):
        assert guard in INSTALL, guard
    assert "systemctl enable" not in INSTALL.split("Still to do by hand")[0]
    assert "nothing done" in INSTALL


@pytest.mark.parametrize("unit", ["finpilot-deploy.service", "finpilot-backup.service"])
def test_server_units_need_the_disk_and_are_sandboxed(unit: str) -> None:
    text = _text(SERVER / "systemd" / unit)
    assert "Requires=docker.service srv-storage.mount" in text
    for setting in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes"):
        assert setting in text
    assert "EnvironmentFile=/etc/finpilot/deployer.env" in text


def test_deployer_settings_hold_paths_never_secrets() -> None:
    for line in _text(SERVER / "deployer.env.example").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if re.search(r"TOKEN|PASSWORD|SECRET|URL_FILE", key):
                assert value.startswith("/etc/finpilot/"), key


def test_the_first_deploy_can_wait_for_the_tunnel_but_never_skips_the_loopback_check() -> None:
    assert '"--local-only goes with --first-deploy"' in DEPLOYER
    loopback = DEPLOYER.index('site_ok "$LOOPBACK" "$public_host" || fail_release')
    local_only_exit = DEPLOYER.index('if [ "$local_only" -eq 1 ]; then', loopback)
    public = DEPLOYER.index('site_ok "$FINPILOT_PUBLIC_URL" || fail_release')
    assert loopback < local_only_exit < public
    assert 'LOOPBACK="http://127.0.0.1:${tunnel_port:-18085}"' in DEPLOYER
    assert 'if [ "$mode" = --verify-public ]; then' in DEPLOYER


def test_the_tunnel_change_is_checked_on_a_copy_and_applied_only_with_approval() -> None:
    route = _text(SERVER / "cloudflared-route.sh")
    assert "nothing done: pass --check, or --apply --confirm" in route
    assert 'cloudflared tunnel --config "$candidate" ingress validate' in route
    assert "would change:" in route  # every existing hostname must map exactly as before
    assert "die \"FinPilot isn't ready on $TARGET" in route
    assert route.index('cp -a "$CONFIG" "$backup"') < route.index("systemctl restart cloudflared")
    assert "restoring the previous config" in route
    for forbidden in ("caddy", "docker restart", "systemctl restart docker", "ufw "):
        assert forbidden not in route.lower().replace("never touches caddy", "")


def test_the_configuration_check_never_prints_a_secret() -> None:
    check = _text(SERVER / "check-config.sh")
    assert "CONFIG OK" in check and "CONFIG NOT READY" in check
    for leak in (
        'cat "$secret"',
        'cat "$token"',
        'echo "$client"',
        "cat /etc/finpilot/restic-password",
    ):
        assert leak not in check
    assert '--password-stdin < "$token"' in check


def test_reinstalling_never_resets_secret_ownership() -> None:
    assert "chown -R root:root" not in INSTALL
    assert '-path "$deploy/secrets"' in INSTALL and "-prune" in INSTALL
    # ...and a re-install repairs groups if they were ever reset: owner, group and mode only.
    assert 'chown root:999 "$file"' in INSTALL and 'chown root:10201 "$file"' in INSTALL
    assert 'chmod 0640 "$file"' in INSTALL


def test_the_registry_check_keeps_the_token_off_the_command_line() -> None:
    check = _text(SERVER / "check-config.sh")
    assert "curl -sS --max-time 20 -K -" in check
    assert not re.search(r"curl[^\n]*(-u |--user )", check)
    for package in ("finpilot-release", "finpilot-api", "finpilot-web"):
        assert package in check and package in RELEASE
    assert "ghcr.io/v2/hishamfekri/$package/tags/list" in check
    assert "REGISTRY: ghcr.io/hishamfekri" in RELEASE


def test_every_shell_script_is_executable_in_git() -> None:
    # The Release runner calls the scripts directly, so a mode lost on a Windows commit breaks it.
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    listing = subprocess.run(  # noqa: S603 - a fixed, read-only git query
        [git, "ls-files", "-s", "--", "*.sh"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert listing
    assert [line.split("\t")[1] for line in listing if not line.startswith("100755")] == []


def test_the_host_snapshot_works_without_the_storage_disk(tmp_path: Path) -> None:
    # A GitHub runner has no /srv/storage: df then fails, which must not end the snapshot early.
    stubs = {
        "docker": "exit 0",
        "ss": "exit 0",
        "df": 'echo "Filesystem Size Used Avail"; echo "/dev/root 72G 59G 13G 82% /"; exit 1',
    }
    for name, body in stubs.items():
        stub = tmp_path / name
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP")}
    first = 'd="$1"; command -v cygpath > /dev/null && d="$(cygpath -u "$d")"; PATH="$d:$PATH"'
    out = tmp_path / "snapshot.txt"
    result = subprocess.run(  # noqa: S603 - a fixed, read-only script
        [_bash(), "-c", f'{first}; exec bash "$2" --write "$3"', "snapshot", str(tmp_path),
         str(DEPLOY / "scripts" / "host-snapshot.sh"), str(out)],
        env={**keep, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "/ avail=13G" in out.read_text(encoding="utf-8").splitlines()
