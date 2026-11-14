#!/usr/bin/env bash
# FinPilot's pull-based deployer, run by finpilot-deploy.timer every two minutes (as root, via
# systemd, with /etc/finpilot/deployer.env). One cycle:
#
#   1. fetch the release manifest that the Release workflow publishes LAST
#      (ghcr.io/hishamfekri/finpilot-release:latest) and validate every line of it;
#   2. nothing new: check the running stack (health, restart loops) and report to Uptime Kuma;
#   3. new release: hold it if its NEW migrations contain destructive statements and no approval
#      exists (/etc/finpilot/approved/<sha>), otherwise run deploy/scripts/deploy.sh, which backs up
#      (restic) before migrating, re-applies and verifies RLS, and puts the previous images back if
#      the API or web server isn't healthy;
#   4. check the public URL through Cloudflare; if it fails, roll back the images and mark the
#      release failed, so it isn't retried until a newer one appears.
#
# Trust boundary: GitHub supplies only image digests from this one registry path. The scripts that
# run here are the ones installed by deploy/server/install.sh from a commit the owner chose; they
# are never updated from GitHub automatically. Code from GitHub runs only inside the containers.
#
#   finpilot-deployer.sh                             one cycle (what the timer runs)
#   finpilot-deployer.sh --first-deploy              provision the empty production database,
#                                                    deploy, then check the public URL
#   finpilot-deployer.sh --first-deploy --local-only the same, but check only the loopback port
#                                                    cloudflared will use (before the hostname
#                                                    is added to the tunnel)
#   finpilot-deployer.sh --verify-public             check loopback and the public URL, nothing else

set -euo pipefail
umask 077

: "${FINPILOT_DIR:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_PUBLIC_URL:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_GHCR_USER:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_GHCR_TOKEN_FILE:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_RESTIC_REPOSITORY:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_RESTIC_PASSWORD_FILE:?set in /etc/finpilot/deployer.env}"
REGISTRY="ghcr.io/hishamfekri"
DEPLOY_DIR="$FINPILOT_DIR/deploy"
APPROVALS="/etc/finpilot/approved"
export DOCKER_CONFIG="/etc/finpilot/docker"
export RESTIC_CACHE_DIR="/var/cache/finpilot-restic"

# deploy/scripts/lib.sh: compose(), env_value(), container_health(), wait_healthy(), record().
# shellcheck source=../scripts/lib.sh
source "$DEPLOY_DIR/scripts/lib.sh"
STATE="$STATE_DIR"
mkdir -p "$STATE"

log() { echo "finpilot-deployer: $*"; }
kuma() {  # kuma up|down MESSAGE: a heartbeat to the Uptime Kuma push monitor, if one is set
  local file="${FINPILOT_KUMA_DEPLOY_URL_FILE:-}" message
  [ -n "$file" ] && [ -s "$file" ] || return 0
  message="$(printf '%s' "$2" | tr -c 'A-Za-z0-9 ._:-' '_' | tr ' ' '+')"
  curl -fsS --max-time 10 -o /dev/null "$(head -n 1 "$file")?status=$1&msg=$message" || true
}

mode="${1:-}"
local_only=0
[ "${2:-}" = --local-only ] && local_only=1
[ "$local_only" -eq 0 ] || [ "$mode" = --first-deploy ] || die "--local-only goes with --first-deploy"
public_host="${FINPILOT_PUBLIC_URL#https://}"
public_host="${public_host%%/*}"
tunnel_port="$(site_value FINPILOT_TUNNEL_PORT)"
LOOPBACK="http://127.0.0.1:${tunnel_port:-18085}"

site_ok() {  # site_ok BASE [HOST]: readyz says ready and the home page answers, within a minute
  local base="$1" n header=()
  [ -n "${2:-}" ] && header=(-H "Host: $2")
  for n in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 10 "${header[@]}" "$base/api/v1/readyz" | grep -q '"ready"' \
       && curl -fsS --max-time 10 -o /dev/null "${header[@]}" "$base/en"; then
      return 0
    fi
    sleep 6
  done
  return 1
}

if [ "$mode" = --verify-public ]; then
  if site_ok "$LOOPBACK" "$public_host"; then log "loopback: ok ($LOOPBACK, Host $public_host)"
  else die "loopback: FinPilot isn't answering on $LOOPBACK"; fi
  if site_ok "$FINPILOT_PUBLIC_URL"; then log "public: ok ($FINPILOT_PUBLIC_URL)"; exit 0; fi
  die "public: $FINPILOT_PUBLIC_URL doesn't answer yet (the tunnel hostname, DNS, or cloudflared)"
fi

exec 9> /run/finpilot-deploy.lock
flock -n 9 || { log "another cycle is running"; exit 0; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

docker login ghcr.io -u "$FINPILOT_GHCR_USER" --password-stdin < "$FINPILOT_GHCR_TOKEN_FILE" > /dev/null

# --- 1. The release manifest, validated line by line (never sourced).
docker pull -q "$REGISTRY/finpilot-release:latest" > /dev/null
manifest="$(docker create "$REGISTRY/finpilot-release:latest" release)"
docker cp "$manifest:/release.env" "$work/release.env" > /dev/null
docker rm "$manifest" > /dev/null
while IFS= read -r line; do
  [[ "$line" =~ ^FINPILOT_RELEASE_SHA=[0-9a-f]{40}$ ]] \
    || [[ "$line" =~ ^FINPILOT_API_IMAGE=ghcr\.io/hishamfekri/finpilot-api@sha256:[0-9a-f]{64}$ ]] \
    || [[ "$line" =~ ^FINPILOT_WEB_IMAGE=ghcr\.io/hishamfekri/finpilot-web@sha256:[0-9a-f]{64}$ ]] \
    || [[ "$line" =~ ^FINPILOT_STACK_TEST=passed$ ]] \
    || [[ "$line" =~ ^FINPILOT_RELEASE_RUN=[0-9]+$ ]] \
    || die "the release manifest has an unexpected line; nothing deployed"
done < "$work/release.env"
sha="$(env_value "$work/release.env" FINPILOT_RELEASE_SHA)"
[ "$(env_value "$work/release.env" FINPILOT_STACK_TEST)" = passed ] || die "release $sha didn't pass its stack test"
[ -n "$(env_value "$work/release.env" FINPILOT_API_IMAGE)" ] && [ -n "$(env_value "$work/release.env" FINPILOT_WEB_IMAGE)" ] \
  || die "the release manifest is incomplete"
current_sha="$(env_value "$STATE/current.env" FINPILOT_RELEASE_SHA)"

health_report() {
  local service status problems="" restarts last
  for service in db api web proxy; do
    status="$(container_health "$service")"
    [ "$status" = healthy ] || problems+="$service=$status "
  done
  restarts="$(docker ps -q --filter label=com.docker.compose.project=finpilot \
    | xargs -r docker inspect --format '{{.RestartCount}}' | awk '{s+=$1} END {print s+0}')"
  last="$(cat "$STATE/restart-count" 2>/dev/null || echo "$restarts")"
  echo "$restarts" > "$STATE/restart-count"
  [ "$restarts" -le "$last" ] || problems+="restarted:$((restarts - last)) "
  curl -fsS --max-time 10 -H "Host: $public_host" "$LOOPBACK/api/v1/readyz" | grep -q '"ready"' \
    || problems+="loopback-readyz "
  curl -fsS --max-time 10 "$FINPILOT_PUBLIC_URL/api/v1/readyz" | grep -q '"ready"' || problems+="public-readyz "
  if [ -z "$problems" ]; then kuma up "ok ${current_sha:0:12}"; else log "unhealthy: $problems"; kuma down "$problems"; fi
}

if [ "$mode" != --first-deploy ]; then
  [ -f "$STATE/current.env" ] || die "not deployed yet: run finpilot-deployer.sh --first-deploy once"
  # /etc/finpilot/hold pauses automatic deployments (after a manual rollback, say). Health
  # checks continue. Remove the file to resume.
  if [ -f /etc/finpilot/hold ] && [ "$sha" != "$current_sha" ]; then
    log "deployments are on hold (/etc/finpilot/hold); not deploying ${sha:0:12}"
    health_report
    exit 0
  fi
  if [ "$sha" = "$current_sha" ]; then
    health_report
    exit 0
  fi
  if [ -f "$STATE/failed-$sha" ]; then
    log "release ${sha:0:12} failed before; waiting for a newer one"
    kuma down "release ${sha:0:12} failed; still on ${current_sha:0:12}"
    exit 0
  fi
fi

# --- 3. Destructive-migration gate: read the migration files out of the images, never run them.
migrations() {  # migrations IMAGE DIR: copies /app/migrations/versions out of IMAGE into DIR
  local id
  docker pull -q "$1" > /dev/null
  id="$(docker create "$1" none)"
  docker cp "$id:/app/migrations/versions" "$2" > /dev/null
  docker rm "$id" > /dev/null
}
new_api="$(env_value "$work/release.env" FINPILOT_API_IMAGE)"
mkdir -p "$work/new" "$work/old"
migrations "$new_api" "$work/new"
if [ -f "$STATE/current.env" ]; then
  migrations "$(env_value "$STATE/current.env" FINPILOT_API_IMAGE)" "$work/old"
fi
held=""
# The first deployment starts from an empty database: nothing there to lose.
[ -f "$STATE/current.env" ] || touch "$work/first"
for file in "$work"/new/versions/*.py; do
  [ -e "$work/first" ] && break
  name="$(basename "$file")"
  [ -e "$work/old/versions/$name" ] && continue
  # Only upgrade(): downgrade() always drops what upgrade() created.
  body="$(awk '/^def upgrade/{on=1} /^def downgrade/{on=0} on' "$file")"
  if grep -Eiq 'drop_table|drop_column|rename_table|alter_column\([^)]*type_|DROP +(TABLE|COLUMN|SCHEMA|DATABASE|OWNED)|TRUNCATE|DELETE +FROM|UPDATE +[a-z_."]+ +SET' <<< "$body"; then
    held+="$name "
  fi
done
if [ -n "$held" ] && [ ! -f "$APPROVALS/$sha" ]; then
  echo "$held" > "$STATE/awaiting-approval-$sha"
  log "release ${sha:0:12} HELD: new migrations change or remove data: $held"
  log "review them, then approve: sudo touch $APPROVALS/$sha"
  kuma down "release ${sha:0:12} awaits migration approval"
  exit 0
fi

# --- First deployment: an EMPTY database only. Never over an existing one.
if [ "$mode" = --first-deploy ]; then
  [ ! -f "$STATE/current.env" ] || die "already deployed: --first-deploy is only for the very first time"
  if docker volume inspect finpilot_db_data > /dev/null 2>&1; then
    die "finpilot_db_data already exists: refusing to provision over it"
  fi
  [ -s "$DEPLOY_DIR/secrets/google_client_secret" ] || die "add the Google client secret first"
  if grep -q '^FINPILOT_REGISTRATION_MODE=allowlist' "$DEPLOY_DIR/finpilot.env" 2>/dev/null; then
    [ -s "$DEPLOY_DIR/secrets/beta_allowlist" ] || die "allowlist mode: add the allowed addresses to secrets/beta_allowlist first"
  fi
  log "provisioning the empty production database"
  "$DEPLOY_DIR/scripts/provision.sh" --release "$work/release.env" --confirm-new-database
fi

log "deploying ${sha:0:12}"
if ! "$DEPLOY_DIR/scripts/deploy.sh" --release "$work/release.env" --confirm \
     --backup-option --restic-repo --backup-option "$FINPILOT_RESTIC_REPOSITORY" \
     --backup-option --restic-password-file --backup-option "$FINPILOT_RESTIC_PASSWORD_FILE"; then
  touch "$STATE/failed-$sha"
  kuma down "deploy of ${sha:0:12} failed; previous images kept"
  exit 1
fi

# --- 4. FinPilot on the loopback port cloudflared uses, then the public site through Cloudflare.
fail_release() {
  touch "$STATE/failed-$sha"
  if [ -f "$STATE/previous.env" ]; then
    log "$1: rolling back the images"
    "$DEPLOY_DIR/scripts/rollback.sh" --confirm || true
  fi
  kuma down "release ${sha:0:12}: $1"
  exit 1
}
site_ok "$LOOPBACK" "$public_host" || fail_release "FinPilot didn't answer on $LOOPBACK"
if [ "$local_only" -eq 1 ]; then
  touch "$STATE/provisioned"
  record "release ${sha} running (loopback verified; public check pending)"
  log "release ${sha:0:12} is running and answers on $LOOPBACK"
  log "next: add the tunnel hostname, restart cloudflared, then run --verify-public"
  exit 0
fi
site_ok "$FINPILOT_PUBLIC_URL" || fail_release "the public site didn't answer"
[ "$mode" = --first-deploy ] && touch "$STATE/provisioned"
rm -f "$STATE/awaiting-approval-$sha"
record "release ${sha} live"
log "release ${sha:0:12} is live"
kuma up "deployed ${sha:0:12}"
