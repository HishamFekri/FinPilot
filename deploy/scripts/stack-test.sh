#!/usr/bin/env bash
# The whole production stack in containers, with throwaway everything, isolated from whatever
# else the Docker host runs. Built for the home server's integration test and for any developer machine
# with Docker.
#
#   deploy/scripts/stack-test.sh --run
#
# Order:
#   preflight (stops if anything is unsafe) -> snapshot of the rest of the host -> watchdog ->
#   build -> Chromium sandbox probes -> provision -> deploy (direct mode) -> refusals ->
#   stack tests -> sandbox in the running API -> PDF load with CPU/memory sampling -> isolation ->
#   restart and persistence -> encrypted (restic) backup and restore -> second deploy ->
#   rollback -> behind mode with a stand-in for the existing proxy -> logs ->
#   cleanup (only what this run created) -> second snapshot and comparison.
#
# Isolation: compose project "finpilot-stacktest", ports 127.0.0.1:18443 and :18080 only, its own
# subnets (172.30.250-253.x), its own volumes and secrets, synthetic data, a fake Google client.
# Nothing outside that project is started, stopped, changed or removed. The watchdog stops the
# run (deploy/scripts/stack-test-stop.sh) if any other container changes, memory runs low, the
# disk fills, or it runs over 90 minutes.
#
# Results: stacktest-report/ next to deploy/ (run.log, checks.txt, the build logs, the probes,
# stats, the snapshots and their comparison, summary.txt). It's kept; the work folder isn't.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "${1:-}" = "--run" ] || die "nothing done: pass --run to build and test a throwaway stack"
require_tools docker timeout ss python3 restic comm curl
SCRIPTS="$DEPLOY_DIR/scripts"
REPO="$(cd "$DEPLOY_DIR/.." && pwd)"
REPORT="${FINPILOT_STACKTEST_REPORT:-$REPO/stacktest-report}"
[ ! -e "$REPORT" ] || die "$REPORT exists (a previous run's results): move it away first"

# Preflight first: if anything is unsafe, nothing at all is created.
preflight="$(mktemp)"
if ! bash "$DEPLOY_DIR/inspection/stack-test-preflight.sh" --in-place > "$preflight" 2>&1; then
  cat "$preflight"
  rm -f "$preflight"
  die "preflight failed: nothing was started"
fi
mkdir -p "$REPORT"
chmod 0700 "$REPORT"
mv "$preflight" "$REPORT/preflight.txt"
exec > >(tee -a "$REPORT/run.log") 2>&1
echo "$$" > "$REPORT/stack-test.pid"
started_at="$(date +%s)"
say "FinPilot stack test, $(date -u +%Y-%m-%dT%H:%M:%SZ), on $(hostname)"
say "Preflight passed ($(grep -c '^PASS' "$REPORT/preflight.txt") checks)"

work="$(mktemp -d "$REPO/.stacktest-work.XXXXXX")"
export FINPILOT_PROJECT="finpilot-stacktest"
export FINPILOT_SITE_ENV="$work/finpilot.env"
export FINPILOT_STATE_DIR="$work/state"
export FINPILOT_BACKUP_PATH="$work/backups"
export FINPILOT_ALLOW_UNPINNED="local-test-only"
export FINPILOT_COMPOSE_TEST=1
export COMPOSE_PROFILES="ops,test"
export RESTIC_CACHE_DIR="$work/restic-cache"
PROJECT="$FINPILOT_PROJECT"
SITE_ENV="$FINPILOT_SITE_ENV"
STATE_DIR="$FINPILOT_STATE_DIR"
RELEASE_ENV="$work/release.env"
TEST_IMAGES=(finpilot-api:stacktest finpilot-api-stack-test:local finpilot-web:stacktest)
# The watchdog's memory floor. CI runners have less memory than the server, and nothing else.
MIN_MEM_MIB="${FINPILOT_STACKTEST_MIN_MEM_MIB:-3072}"
[[ "$MIN_MEM_MIB" =~ ^[0-9]+$ ]] || die "FINPILOT_STACKTEST_MIN_MEM_MIB must be a number"
BASE_IMAGES=(postgres:18.1-trixie caddy:2.10.2-alpine python:3.14.6-slim-trixie
             ghcr.io/astral-sh/uv:0.11.21 node:24.12.0-trixie-slim)
failures=0
watchdog_pid=""
sampler_pid=""

check() {
  local name="$1"; shift
  if "$@"; then
    echo "PASS  $name" | tee -a "$REPORT/checks.txt"
  else
    echo "FAIL  $name" | tee -a "$REPORT/checks.txt"
    failures=$((failures + 1))
  fi
}
fails() { ! "$@" > /dev/null 2>&1; }
note() { echo "NOTE  $*" | tee -a "$REPORT/checks.txt"; }

# --- Sampling: every container's CPU and memory, plus the host's memory and pressure.
sample_start() {
  local label="$1"
  (
    set +e
    while :; do
      ts="$(date +%s)"
      docker stats --no-stream --format "$ts,{{.Name}},{{.CPUPerc}},{{.MemUsage}}" \
        >> "$REPORT/stats-$label.csv" 2>/dev/null
      printf '%s,host,%s,%s\n' "$ts" \
        "$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)" \
        "$(awk '/^some/{split($2,a,"=");print a[2]}' /proc/pressure/memory)" \
        >> "$REPORT/host-$label.csv"
      sleep 1
    done
  ) &
  sampler_pid=$!
}
sample_stop() {
  if [ -n "$sampler_pid" ]; then
    kill "$sampler_pid" 2>/dev/null || true
    wait "$sampler_pid" 2>/dev/null || true
  fi
  sampler_pid=""
  python3 - "$REPORT/stats-$1.csv" "$REPORT/host-$1.csv" <<'PY' | tee "$REPORT/stats-$1-summary.txt"
import csv, re, sys
units = {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}
def mib(text):
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    return float(m.group(1)) * units.get(m.group(2), 1) if m else 0.0
peak, cpu, n = {}, {}, {}
try:
    for row in csv.reader(open(sys.argv[1])):
        if len(row) < 4:
            continue
        name, used = row[1], mib(row[3].split("/")[0])
        try:
            c = float(row[2].rstrip("%"))
        except ValueError:
            c = 0.0
        peak[name] = max(peak.get(name, 0), used)
        cpu[name] = max(cpu.get(name, 0), c)
        n[name] = n.get(name, 0) + 1
except FileNotFoundError:
    pass
print(f"{'container':28} {'peak MiB':>9} {'peak CPU%':>9} samples")
for name in sorted(peak, key=lambda k: -peak[k]):
    print(f"{name:28} {peak[name]:9.0f} {cpu[name]:9.1f} {n[name]:7d}")
try:
    rows = [r for r in csv.reader(open(sys.argv[2])) if len(r) == 4]
    if rows:
        lowest = min(int(r[2]) for r in rows)
        worst = max(float(r[3] or 0) for r in rows)
        print(f"host: lowest MemAvailable {lowest} MiB, highest memory pressure avg10 {worst}")
except FileNotFoundError:
    pass
PY
}

# --- Watchdog: stops everything this run started if the rest of the host is affected.
watchdog() {
  # A failed check must never end the watchdog quietly: errors here are reasons, not exits.
  set +e
  local baseline now reason=""
  baseline="$("$SCRIPTS/host-snapshot.sh" --containers)"
  while sleep 10; do
    if ! now="$("$SCRIPTS/host-snapshot.sh" --containers 2>&1)"; then
      reason="the watchdog couldn't read the host's containers: $(echo "$now" | tail -n 1)"
    elif [ "$now" != "$baseline" ]; then
      reason="an existing container changed state, health, restarts, ports or mounts"
      diff <(echo "$baseline") <(echo "$now") > "$REPORT/EMERGENCY-diff.txt" || true
    elif [ "$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)" -lt "$MIN_MEM_MIB" ]; then
      reason="host memory available fell below $MIN_MEM_MIB MiB"
    elif [ "$(df -P / | awk 'NR==2{print int($4/1048576)}')" -lt 8 ]; then
      reason="free disk on / fell below 8 GiB"
    elif [ $(( $(date +%s) - started_at )) -gt 5400 ]; then
      reason="the run took longer than 90 minutes"
    fi
    if [ -n "$reason" ]; then
      echo "EMERGENCY STOP: $reason ($(date -u +%H:%M:%SZ))" | tee "$REPORT/EMERGENCY.txt"
      FINPILOT_STACKTEST_REPORT="$REPORT" "$SCRIPTS/stack-test-stop.sh" --confirm >> "$REPORT/EMERGENCY.txt" 2>&1
      return
    fi
  done
}

cleanup() {
  local status=$?
  set +e
  [ -n "$sampler_pid" ] && kill "$sampler_pid" 2>/dev/null
  [ -n "$watchdog_pid" ] && kill "$watchdog_pid" 2>/dev/null
  say "Cleanup: removing only what this run created"
  { (compose down --volumes --remove-orphans) > /dev/null 2>&1
    keep=()
    [ "${FINPILOT_STACKTEST_KEEP_IMAGES:-}" = 1 ] && keep=(--keep-images)
    FINPILOT_STACKTEST_REPORT="$REPORT" "$SCRIPTS/stack-test-stop.sh" --confirm --sweep-only "${keep[@]}"
    for image in "${BASE_IMAGES[@]}"; do
      if ! grep -qx "$image" "$REPORT/images-before.txt" 2>/dev/null \
         && docker image inspect "$image" > /dev/null 2>&1; then
        docker image rm "$image" > /dev/null 2>&1 && echo "removed base image pulled by this run: $image" \
          || echo "kept base image $image (in use, or shared with another tag)"
      fi
    done
  } 2>&1 | tee "$REPORT/cleanup.txt"
  rm -rf "$work"
  [ -e "$work" ] && echo "LEFTOVER  work folder $work" | tee -a "$REPORT/cleanup.txt"
  "$SCRIPTS/host-snapshot.sh" --write "$REPORT/after.txt" > /dev/null
  say "Comparing the rest of the host, before and after"
  if "$SCRIPTS/host-snapshot.sh" --compare "$REPORT/before.txt" "$REPORT/after.txt" > "$REPORT/comparison.txt"; then
    echo "PASS  nothing outside the test changed" | tee -a "$REPORT/checks.txt"
  else
    echo "FAIL  something outside the test changed (comparison.txt)" | tee -a "$REPORT/checks.txt"
    status=1
  fi
  cat "$REPORT/comparison.txt"
  {
    echo "FinPilot stack test on $(hostname), finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "duration: $(( $(date +%s) - started_at )) s, exit status: $status"
    [ -f "$REPORT/EMERGENCY.txt" ] && cat "$REPORT/EMERGENCY.txt"
    echo "checks: $(grep -c '^PASS' "$REPORT/checks.txt" 2>/dev/null) passed, $(grep -c '^FAIL' "$REPORT/checks.txt" 2>/dev/null) failed"
    grep '^FAIL' "$REPORT/checks.txt" 2>/dev/null
    echo "build cache and images after cleanup:"
    docker system df 2>/dev/null
  } > "$REPORT/summary.txt"
  cat "$REPORT/summary.txt"
  rm -f "$REPORT/stack-test.pid"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

say "Snapshot of everything else on this host"
"$SCRIPTS/host-snapshot.sh" --write "$REPORT/before.txt"
docker image ls --format '{{.Repository}}:{{.Tag}}' | sort -u > "$REPORT/images-before.txt"
docker system df > "$REPORT/docker-df-before.txt" 2>&1
watchdog &
watchdog_pid=$!

# --- Build
build() {
  local name="$1" began
  shift
  began="$(date +%s)"
  if docker build --progress=plain "$@" > "$REPORT/build-$name.log" 2>&1; then
    echo "PASS  build $name in $(( $(date +%s) - began )) s" | tee -a "$REPORT/checks.txt"
  else
    echo "FAIL  build $name (build-$name.log, last lines below)" | tee -a "$REPORT/checks.txt"
    tail -n 40 "$REPORT/build-$name.log"
    die "image build failed"
  fi
}
say "Building images (CPU and memory sampled meanwhile)"
sample_start build
build api -t finpilot-api:stacktest "$REPO/backend"
build stack-test --target stack-test -t finpilot-api-stack-test:local "$REPO/backend"
build web -f "$REPO/apps/web/Dockerfile" -t finpilot-web:stacktest "$REPO"
sample_stop build || true
for image in "${TEST_IMAGES[@]}"; do
  note "image $image: $(docker image inspect --format '{{.Size}}' "$image" | awk '{printf "%.0f MB", $1/1000000}')"
done

# --- Chromium's sandbox, before anything else runs: Docker's default profile, then the API's.
probe() {
  local label="$1" code
  shift
  docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true "$@" \
    --user 10201:10201 --tmpfs /tmp:size=256m \
    --tmpfs /home/finpilot:size=64m,uid=10201,gid=10201,mode=0700 \
    --shm-size 256m --memory 1g --cpus 1 --pids-limit 512 \
    --label finpilot.stacktest=probe --name "finpilot-stacktest-probe-$label" \
    finpilot-api:stacktest python -m scripts.sandbox_probe > "$REPORT/probe-$label.json" 2>&1
  code=$?
  echo "$code"
}
say "Chromium sandbox probes (throwaway containers, no network, synthetic page)"
set +e
builtin_code="$(probe docker-builtin)"
profile_code="$(probe api-profile --security-opt "seccomp=$DEPLOY_DIR/seccomp/api-chromium.json")"
set -e
note "sandbox under Docker's built-in seccomp profile: exit $builtin_code ($(head -c 300 "$REPORT/probe-docker-builtin.json"))"
check "Chromium's sandbox works under the API's seccomp profile" test "$profile_code" = 0
if [ "$profile_code" != 0 ]; then
  head -c 2000 "$REPORT/probe-api-profile.json"; echo
  set +e
  diag_code="$(probe api-profile-apparmor-unconfined --security-opt "seccomp=$DEPLOY_DIR/seccomp/api-chromium.json" \
    --security-opt apparmor=unconfined)"
  set -e
  note "DIAGNOSTIC ONLY (never a configuration): same, with AppArmor unconfined: exit $diag_code"
  if [ "$profile_code" = 125 ]; then
    note "Docker refused the API profile itself: the rest of this run uses Docker's default profile"
    export FINPILOT_API_SECCOMP_PROFILE="$DEPLOY_DIR/seccomp/docker-default-29.9.2.json"
  fi
fi

# --- Site settings for the run (direct mode first)
cat > "$SITE_ENV" <<EOF
FINPILOT_PROXY_MODE=direct
FINPILOT_HOST=finpilot.test
FINPILOT_ORIGIN=https://finpilot.test:18443
FINPILOT_TLS=internal
FINPILOT_BIND_ADDRESS=127.0.0.1
FINPILOT_HTTPS_PORT=18443
FINPILOT_HTTP_PORT=18080
FINPILOT_GOOGLE_CLIENT_ID=finpilot-stack-test.apps.googleusercontent.com
FINPILOT_DATABASE=finpilot
FINPILOT_FRONT_SUBNET=172.30.250.0/24
FINPILOT_FRONT_IP_RANGE=172.30.250.128/25
FINPILOT_PROXY_IP=172.30.250.2
FINPILOT_DATA_SUBNET=172.30.251.0/24
FINPILOT_EGRESS_SUBNET=172.30.252.0/24
FINPILOT_EDGE_SUBNET=172.30.253.0/29
FINPILOT_EDGE_PROXY_IP=172.30.253.2
FINPILOT_EDGE_IP=172.30.253.3
FINPILOT_STACK_TESTER_EDGE_IP=172.30.253.4
FINPILOT_TUNNEL_PORT=18085
FINPILOT_EGRESS_GATEWAY=172.30.252.1
FINPILOT_SECRETS_PATH=$work/secrets
FINPILOT_STACK_TEST_IMAGE=finpilot-api-stack-test:local
EOF
cat > "$RELEASE_ENV" <<EOF
FINPILOT_API_IMAGE=finpilot-api:stacktest
FINPILOT_WEB_IMAGE=finpilot-web:stacktest
EOF

FINPILOT_SECRETS_PATH="$work/secrets" "$SCRIPTS/init-secrets.sh" --generate --local-test-permissions
printf 'stack-test-only-not-a-real-secret' > "$work/secrets/google_client_secret"
echo "stack-owner@example.test" > "$work/secrets/beta_allowlist"

say "Provisioning a disposable database and deploying (direct mode)"
before_step="$failures"
check "provision: owner, runtime role, migrations, RLS, verification" \
  "$SCRIPTS/provision.sh" --release "$RELEASE_ENV" --confirm-new-database
[ "$failures" -eq "$before_step" ] || die "provisioning failed"
check "deploy (direct mode)" "$SCRIPTS/deploy.sh" --release "$RELEASE_ENV" --confirm --no-pull
[ "$failures" -eq "$before_step" ] || die "the first deployment failed"
RELEASE_ENV="$STATE_DIR/current.env"
migrations_at_head() {
  local current
  current="$(compose run --rm migrate alembic current 2>/dev/null)"
  echo "    alembic current: $current"
  grep -q '(head)' <<< "$current"
}
check "migrations are at head" migrations_at_head

say "Unsafe settings are refused at startup"
probe_cmd=(python -c "from app.main import create_app; create_app()")
check "refuses a catch-all trusted proxy" fails compose run --rm --no-deps -e TRUSTED_PROXIES=0.0.0.0/0 api "${probe_cmd[@]}"
check "refuses an http Google redirect" fails compose run --rm --no-deps \
  -e GOOGLE_REDIRECT_URI=http://finpilot.test/api/v1/auth/google/callback api "${probe_cmd[@]}"
check "refuses a missing Google client" fails compose run --rm --no-deps -e GOOGLE_CLIENT_ID= api "${probe_cmd[@]}"
check "refuses allowlist mode with an empty allowlist" fails compose run --rm --no-deps \
  -e REGISTRATION_MODE=allowlist -e BETA_ALLOWLIST= api "${probe_cmd[@]}"
check "refuses an http origin" fails compose run --rm --no-deps -e WEB_ORIGIN=http://finpilot.test api "${probe_cmd[@]}"
check "migrations refuse to run without separate migration credentials" fails compose run --rm --no-deps \
  -e MIGRATION_DATABASE_URL_FILE= migrate

say "Stack tests through the FinPilot proxy (direct mode)"
check "stack tests (direct mode)" compose run --rm stack-test
running_api_sandboxed() {
  compose exec -T api python -m scripts.sandbox_probe > "$REPORT/probe-running-api.json"
}
check "Chromium's sandbox is on inside the running API" running_api_sandboxed
head -c 1500 "$REPORT/probe-running-api.json"; echo

say "PDF exports under load, sampled"
sample_start load
check "10 PDF exports (6 in a row, 2 pairs at once), then the rate limit" compose run --rm -e STACK_PHASE=load stack-test \
  python -m pytest -q -s -p no:cacheprovider -rs tests_stack -k under_load
sample_stop load || true

say "Isolation"
check "web can't reach the database" compose exec -T web node -e \
  "const s=require('net').connect(5432,'db');s.on('connect',()=>process.exit(1));s.on('error',()=>process.exit(0));setTimeout(()=>process.exit(0),5000)"
check "web has no internet" compose exec -T web node -e \
  "fetch('https://example.com').then(()=>process.exit(1),()=>process.exit(0))"
check "the database has no internet" fails compose exec -T db getent hosts example.com
publishes_nothing() {  # a running container with no host port mapping at all
  local id
  id="$(compose ps -q "$1")"
  [ -n "$id" ] && [ -z "$(docker port "$id")" ]
}
check "the database publishes no port" publishes_nothing db
check "the API publishes no port" publishes_nothing api
check "only the proxy publishes, on 127.0.0.1:18443 and :18080" bash -c \
  "[ \"\$(docker ps --filter label=com.docker.compose.project=$PROJECT --format '{{.Ports}}' | grep -o '[0-9.]*:[0-9]*->' | sort -u | tr '\n' ' ')\" = '127.0.0.1:18080-> 127.0.0.1:18443-> ' ]"
check "the API runs as 10201" test "$(compose exec -T api id -u | tr -d '\r')" = 10201
check "web runs as 10202" test "$(compose exec -T web id -u | tr -d '\r')" = 10202
check "the database runs as 999" test "$(compose exec -T db id -u | tr -d '\r')" = 999
check "the API's filesystem is read-only" fails compose exec -T api sh -c "touch /app/written"
check "the web filesystem is read-only" fails compose exec -T web sh -c "touch /app/written"
check "the edge network is internal" test \
  "$(docker network inspect --format '{{.Internal}}' "${PROJECT}_edge")" = true
test_subnets() {
  local n
  for n in front data egress edge; do
    printf '%s ' "$(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "${PROJECT}_$n")"
  done
}
check "each test network has its own fixed subnet" test "$(test_subnets)" = \
  "172.30.250.0/24 172.30.251.0/24 172.30.252.0/24 172.30.253.0/29 "

say "Restarting every container"
compose restart db > /dev/null
wait_healthy db
compose restart api web proxy > /dev/null
wait_healthy api && wait_healthy web && wait_healthy proxy
check "records survive restarting every container" compose run --rm -e STACK_PHASE=after_restart stack-test

say "Encrypted backup (restic) and restore into a new database"
repo="$work/restic-repo"
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$work/restic-password"
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$work/wrong-password"
set +e
backup_out="$("$SCRIPTS/backup.sh" --confirm --restic-repo "$repo" \
  --restic-password-file "$work/restic-password" --restic-init | tail -n 1)"
set -e
snapshot="${backup_out#restic:}"
check "an encrypted backup is written" test -n "$snapshot" -a "$backup_out" != "$snapshot"
check "the backup can't be read without its password" fails \
  restic --repo "$repo" --password-file "$work/wrong-password" snapshots
check "the repository holds no readable dump" fails grep -rqaE 'PGDMP|CREATE TABLE|example\.test' "$repo"
restore_log="$work/restore.log"
check "the encrypted backup restores into a new database" bash -c \
  "'$SCRIPTS/restore.sh' --from-restic '$snapshot' --restic-repo '$repo' \
   --restic-password-file '$work/restic-password' --into finpilot_restore_check --confirm > '$restore_log' 2>&1"
cat "$restore_log"
check "the restored copy has the same rows as the live database" \
  grep -q "Every table has the same number of rows in both" "$restore_log"
check "restore refuses the live database" fails "$SCRIPTS/restore.sh" --from-restic "$snapshot" \
  --restic-repo "$repo" --restic-password-file "$work/restic-password" --into finpilot --confirm

say "A second deployment (backs up first), then a rollback"
check "second deployment" "$SCRIPTS/deploy.sh" --release "$work/release.env" --confirm --no-pull \
  --backup-option --restic-repo --backup-option "$repo" \
  --backup-option --restic-password-file --backup-option "$work/restic-password"
check "rollback" "$SCRIPTS/rollback.sh" --confirm

say "Behind mode: the FinPilot proxy behind a stand-in for the existing proxy"
sed -i 's/^FINPILOT_PROXY_MODE=.*/FINPILOT_PROXY_MODE=behind/' "$SITE_ENV"
check "the proxy restarts in behind mode" compose up -d --no-deps proxy
check "the proxy is healthy in behind mode" wait_healthy proxy
check "the proxy publishes nothing in behind mode" test -z \
  "$(docker ps --filter label=com.docker.compose.project=$PROJECT --filter label=com.docker.compose.service=proxy --format '{{.Ports}}' | grep -- '->' || true)"
check "the stand-in edge proxy starts" compose up -d --no-deps edge
check "the stand-in edge proxy is healthy" wait_healthy edge
check "stack tests (behind the stand-in edge proxy)" compose run --rm stack-test-behind

say "Tunnel mode: cloudflared's view, from this host's loopback"
sed -i 's/^FINPILOT_PROXY_MODE=.*/FINPILOT_PROXY_MODE=tunnel/' "$SITE_ENV"
check "the proxy restarts in tunnel mode" compose up -d --no-deps proxy
check "the proxy is healthy in tunnel mode" wait_healthy proxy
tunnel() {  # tunnel PATH [HEADER...]: the response code, as cloudflared would get it
  local path="$1" args=() header
  shift
  for header in "$@"; do args+=(-H "$header"); done
  curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: finpilot.test" "${args[@]}" \
    "http://127.0.0.1:18085$path"
}
sign_in_start='/api/v1/auth/google/start?return_to=https://elsewhere.example'
tunnel_limited() {  # 20 attempts are allowed per client address; the 21st is refused
  local n code=""
  for n in $(seq 1 21); do code="$(tunnel "$sign_in_start" "$@")"; done
  [ "$code" = 303 ]
}
check "tunnel mode publishes only 127.0.0.1:18085" test \
  "$(docker ps --filter label=com.docker.compose.project=$PROJECT --filter label=com.docker.compose.service=proxy --format '{{.Ports}}' | grep -o '[0-9.]*:[0-9]*->' | sort -u | tr '\n' ' ')" \
  = "127.0.0.1:18085-> "
check "tunnel mode serves the site" test "$(tunnel /api/v1/readyz)" = 200
check "tunnel mode serves only its own host" test \
  "$(curl -s --max-time 20 -H 'Host: other.example' http://127.0.0.1:18085/api/v1/healthz)" != '{"status":"ok"}'
check "Cloudflare's client address is used: its 21st sign-in attempt is refused" \
  tunnel_limited "Cf-Connecting-Ip: 203.0.113.50"
check "another Cloudflare client still gets in (the gateway alone is trusted, per address)" \
  test "$(tunnel "$sign_in_start" "Cf-Connecting-Ip: 203.0.113.51")" = 422
check "a forged X-Forwarded-For buys nothing in tunnel mode" test \
  "$(tunnel "$sign_in_start" "Cf-Connecting-Ip: 203.0.113.50" "X-Forwarded-For: 198.51.100.9")" = 303

say "Logs keep no query strings, cookies or credentials"
logs="$(compose logs --no-color proxy edge api web 2>&1)"
check "no query strings logged by the proxies" fails grep -q 'return_to=' <<< "$(compose logs --no-color proxy edge 2>&1)"
check "no session cookie values logged" fails grep -q '__Host-fp_session=' <<< "$logs"
check "no database credentials logged" fails grep -qE 'postgresql(\+psycopg)?://[^ ]*:[^ @]*@' <<< "$logs"
note "API log lines: $(compose logs --no-color api 2>&1 | wc -l), tracebacks: $(compose logs --no-color api 2>&1 | grep -c Traceback)"

kill "$watchdog_pid" 2>/dev/null || true
watchdog_pid=""
if [ "$failures" -eq 0 ]; then say "All stack checks passed"; else die "$failures stack check(s) failed"; fi
