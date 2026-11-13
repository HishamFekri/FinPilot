#!/usr/bin/env bash
# FinPilot P4: read-only preflight for the isolated Docker integration test.
#
# Before anything is copied to the server (from Windows; cmd keeps the bytes exact):
#   cmd /c "ssh -o ConnectTimeout=10 SERVER_USER@SERVER_TAILSCALE_IP bash -s < deploy\inspection\stack-test-preflight.sh > %USERPROFILE%\Documents\finpilot-p4-preflight.txt 2>&1"
# From inside the copied bundle (stack-test.sh runs it again before it starts anything):
#   bash deploy/inspection/stack-test-preflight.sh --in-place
#
# Changes nothing: no sudo, no writes, no container started. Prints PASS/FAIL/INFO lines and
# ends with "PREFLIGHT PASSED" (exit 0) or "PREFLIGHT FAILED" (exit 1). Any FAIL means: stop.

set -u
export LC_ALL=C
in_place=0
[ "${1:-}" = "--in-place" ] && in_place=1

PROJECT="finpilot-stacktest"
PORTS="18443 18080 18085"
SUBNETS="172.30.250.0/24 172.30.251.0/24 172.30.252.0/24 172.30.253.0/29"
TEST_UIDS="10201 10202"
TEST_IMAGES="finpilot-api:stacktest finpilot-api-stack-test:local finpilot-web:stacktest"
# FINPILOT_PREFLIGHT_PROFILE=ci: a throwaway GitHub-hosted runner (2 CPUs, less memory and disk, and no
# existing applications or external disk to protect). Default: the home server.
PROFILE="${FINPILOT_PREFLIGHT_PROFILE:-server}"
MIN_CPUS=4
MIN_MEM_MIB=6144
MIN_DOCKER_FREE_GIB=20
if [ "$PROFILE" = ci ]; then
  MIN_CPUS=2
  MIN_MEM_MIB=4096
  MIN_DOCKER_FREE_GIB=10
fi
TEST_DIR="${HOME}/finpilot-stacktest"

failures=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
info() { echo "INFO  $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "FinPilot stack-test preflight, $(date -u +%Y-%m-%dT%H:%M:%SZ), $(hostname)"

# --- Who and what
[ "$(id -u)" -ne 0 ] && pass "runs as an ordinary user ($(id -un)), no sudo" || fail "running as root; run as your own user"
for tool in bash timeout ss tar python3 restic awk sed comm sha256sum; do
  have "$tool" && pass "tool: $tool" || fail "tool missing: $tool (installing packages isn't allowed; stop)"
done
have openssl && pass "tool: openssl" || info "openssl missing: secrets fall back to /dev/urandom"
if docker info > /dev/null 2>&1; then
  pass "Docker answers without sudo (docker group)"
else
  fail "docker info failed for $(id -un)"
fi
if docker compose version > /dev/null 2>&1; then
  pass "$(docker compose version)"
else
  fail "the docker compose plugin is missing"
fi
info "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), cgroup v$(docker info --format '{{.CgroupVersion}}' 2>/dev/null), root $(docker info --format '{{.DockerRootDir}}' 2>/dev/null)"
[ "$(docker info --format '{{.CgroupVersion}}' 2>/dev/null)" = 2 ] && pass "cgroup v2: memory, CPU and process limits are enforced" || fail "cgroup v2 isn't in use"

# --- Capacity
cpus="$(nproc)"
[ "$cpus" -ge "$MIN_CPUS" ] && pass "CPUs: $cpus" || fail "CPUs: $cpus (need ${MIN_CPUS})"
avail_mib="$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)"
[ "$avail_mib" -ge "$MIN_MEM_MIB" ] && pass "memory available: ${avail_mib} MiB" || fail "memory available: ${avail_mib} MiB (need ${MIN_MEM_MIB})"
mem_some="$(awk '/^some/{split($3,a,"=");print a[2]}' /proc/pressure/memory)"
io_some="$(awk '/^some/{split($3,a,"=");print a[2]}' /proc/pressure/io)"
awk -v m="$mem_some" 'BEGIN{exit !(m < 5)}' && pass "memory pressure avg60: $mem_some" || fail "memory pressure avg60: $mem_some (need < 5)"
awk -v m="$io_some" 'BEGIN{exit !(m < 15)}' && pass "I/O pressure avg60: $io_some" || fail "I/O pressure avg60: $io_some (need < 15)"
info "load: $(cut -d' ' -f1-3 /proc/loadavg)"
free_gib="$(df -P / | awk 'NR==2{print int($4/1048576)}')"
[ "$free_gib" -ge "$MIN_DOCKER_FREE_GIB" ] && pass "free on / (Docker's root): ${free_gib} GiB" || fail "free on /: ${free_gib} GiB (need ${MIN_DOCKER_FREE_GIB})"
if [ "$PROFILE" = ci ]; then
  info "CI runner: no external disk to check"
elif mountpoint -q /srv/storage; then
  pass "/srv/storage is mounted ($(df -hP /srv/storage | awk 'NR==2{print $4}') free)"
else
  fail "/srv/storage isn't mounted (Docker and the existing apps depend on it)"
fi
[ -f /var/run/reboot-required ] && info "a reboot is pending (not done here; not a blocker for the test)"

# --- Existing applications are healthy before we start
running="$(docker ps --format '{{.Names}}' | sort)"
info "running containers: $(echo "$running" | tr '\n' ' ')"
unhealthy="$(docker ps --filter health=unhealthy --format '{{.Names}}')"
[ -z "$unhealthy" ] && pass "no running container is unhealthy" || fail "unhealthy containers: $unhealthy"
starting="$(docker ps --filter health=starting --format '{{.Names}}')"
[ -z "$starting" ] && pass "no container is still starting" || fail "containers still starting: $starting (wait, then rerun)"
if [ "$PROFILE" != ci ]; then
  for name in caddy ai-frontend ai-backend hesham-postgres hesham-redis; do
    echo "$running" | grep -qx "$name" && pass "existing service running: $name" || fail "existing service not running: $name"
  done
fi

# --- The test's names, ports, subnets and IDs are unused
if [ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ] \
   && [ -z "$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] \
   && [ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] \
   && [ -z "$(docker ps -aq --filter label=finpilot.stacktest)" ]; then
  pass "no container, network or volume belongs to $PROJECT yet"
else
  fail "resources of $PROJECT already exist (run deploy/scripts/stack-test-stop.sh --confirm)"
fi
if docker ps -a --format '{{.Names}}' | grep -qi finpilot \
   || docker network ls --format '{{.Name}}' | grep -qi finpilot \
   || docker volume ls -q | grep -qi finpilot \
   || docker compose ls -a | tail -n +2 | grep -qi finpilot; then
  fail "something named finpilot already exists on Docker"
else
  pass "no container, network, volume or compose project is named finpilot*"
fi
for image in $TEST_IMAGES; do
  docker image inspect "$image" > /dev/null 2>&1 && fail "image $image already exists" || pass "image name free: $image"
done
for port in $PORTS; do
  if ss -tlnH | awk '{print $4}' | grep -qE "[:.]${port}\$"; then fail "port $port is in use"; else pass "port $port is free"; fi
done
python3 - "$SUBNETS" <<'PY' && pass "test subnets ($SUBNETS) overlap no route or Docker network" || fail "a test subnet overlaps something in use"
import ipaddress, json, subprocess, sys
wanted = [ipaddress.ip_network(s) for s in sys.argv[1].split()]
used = []
for line in subprocess.run(["ip", "-4", "route"], capture_output=True, text=True).stdout.splitlines():
    first = line.split()[0]
    if "/" in first:
        used.append(("route", ipaddress.ip_network(first, strict=False)))
ids = subprocess.run(["docker", "network", "ls", "-q"], capture_output=True, text=True).stdout.split()
if ids:
    out = subprocess.run(["docker", "network", "inspect", *ids], capture_output=True, text=True).stdout
    for net in json.loads(out or "[]"):
        for cfg in (net.get("IPAM") or {}).get("Config") or []:
            if cfg.get("Subnet") and ":" not in cfg["Subnet"]:
                used.append((net["Name"], ipaddress.ip_network(cfg["Subnet"], strict=False)))
clash = [(w, name, u) for w in wanted for name, u in used if w.overlaps(u)]
for w, name, u in clash:
    print(f"      {w} overlaps {u} ({name})")
sys.exit(1 if clash else 0)
PY
for uid in $TEST_UIDS; do
  if getent passwd "$uid" > /dev/null || getent group "$uid" > /dev/null; then
    fail "uid/gid $uid exists on the host"
  elif ps -eo uid= | awk -v u="$uid" '$1 == u {found=1} END {exit !found}'; then
    fail "a process runs as uid $uid"
  else
    pass "uid/gid $uid is unused on the host"
  fi
done
users="$(docker ps -aq | xargs -r docker inspect --format '{{.Config.User}}' 2>/dev/null | sort -u | tr '\n' ' ')"
info "users of existing containers: ${users:-none set}"
echo "$users" | grep -qE '(^| )(10201|10202)(:| |$)' && fail "an existing container runs as 10201 or 10202" || pass "no existing container uses 10201 or 10202"

# --- What will be copied, and synthetic data only
if [ "$in_place" -eq 1 ]; then
  bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  found="$(find "$bundle" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' -o -name 'id_rsa*' -o -name 'id_ed25519*' \) ! -name '*.example' 2>/dev/null)"
  [ -z "$found" ] && pass "the bundle holds no .env, key or certificate files" || fail "secret-looking files in the bundle: $found"
  secrets_dir="$bundle/deploy/secrets"
  extra="$(find "$secrets_dir" -type f ! -name README.md 2>/dev/null)"
  [ -z "$extra" ] && pass "deploy/secrets holds no secret (only its README)" || fail "deploy/secrets isn't empty"
  [ ! -e "$bundle/deploy/finpilot.env" ] && [ ! -e "$bundle/deploy/release.env" ] \
    && pass "no production site or release file in the bundle" || fail "a production env file is in the bundle"
  [ ! -e "$bundle/stacktest-report" ] && pass "no previous report folder" || fail "stacktest-report exists: move it away first"
else
  [ ! -e "$TEST_DIR" ] && pass "$TEST_DIR doesn't exist yet" || fail "$TEST_DIR already exists"
fi
info "test data: synthetic people under example.test; secrets generated per run; Google client is a fake ID"

echo
if [ "$failures" -eq 0 ]; then
  echo "PREFLIGHT PASSED"
  exit 0
fi
echo "PREFLIGHT FAILED ($failures)"
exit 1
