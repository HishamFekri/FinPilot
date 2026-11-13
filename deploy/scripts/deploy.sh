#!/usr/bin/env bash
# Deploys one release: images by digest, after a backup, with migrations and hardening re-checked.
#
#   deploy/scripts/deploy.sh --release release.env --confirm
#
# Steps, and what a failure does:
#   1. check the release pins both images by digest              stop, nothing changed
#   2. pull the images (skip with --no-pull for local images)     stop, nothing changed
#   3. back up the database (skipped only on the very first deploy)  stop, nothing changed
#   4. migrate as the owner                                        stop; the old release keeps running
#   5. re-apply grants and RLS, then verify as the runtime role    stop; the old release keeps running
#   6. replace the API, wait for /readyz                           put the previous images back
#   7. replace web and proxy, wait for health                      put the previous images back
# The database is never rolled back automatically: see rollback.sh and deploy/README.md.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

release=""
confirmed=0
pull=1
backup_args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --release) release="$2"; shift ;;
    --confirm) confirmed=1 ;;
    --no-pull) pull=0 ;;
    --backup-option) backup_args+=("$2"); shift ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ -n "$release" ] || die "pass --release FILE"
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm to deploy $release"
require_pinned_release "$release"
require_tools docker
db="$(database_name)"

mkdir -p "$STATE_DIR"
current="$STATE_DIR/current.env"
previous="$STATE_DIR/previous.env"
candidate="$STATE_DIR/candidate.env"
cp "$release" "$candidate"
RELEASE_ENV="$candidate"

roll_back() {
  echo "Deployment failed at: $1" >&2
  if [ -f "$current" ]; then
    echo "Putting the previous images back (the database is left as it is)." >&2
    RELEASE_ENV="$current" compose up -d --no-deps api web proxy || true
  else
    echo "No previous release: stopping the application containers." >&2
    compose stop api web proxy || true
  fi
  record "FAILED release=$(env_value "$candidate" FINPILOT_API_IMAGE) step=$1"
  exit 1
}

if [ "$pull" -eq 1 ]; then
  say "Pulling images"
  compose pull --quiet api web proxy db
fi

if [ -f "$current" ]; then
  say "Backing up before migrating"
  FINPILOT_RELEASE_ENV="$current" "$(dirname "${BASH_SOURCE[0]}")/backup.sh" --confirm \
    "${backup_args[@]}"
else
  say "First deployment: nothing to back up yet"
fi

say "Migrations"
compose run --rm migrate || roll_back "migrate"
say "Grants and row-level security"
compose run --rm db-apply apply --database "$db" --owner-approved || roll_back "apply"
compose run --rm db-verify || roll_back "verify"

say "Replacing the API"
compose up -d --no-deps api || roll_back "start api"
wait_healthy api || roll_back "api health"
say "Replacing the web server and proxy"
compose up -d --no-deps web proxy || roll_back "start web and proxy"
wait_healthy web || roll_back "web health"
wait_healthy proxy || roll_back "proxy health"

[ -f "$current" ] && cp "$current" "$previous"
mv "$candidate" "$current"
record "deployed api=$(env_value "$current" FINPILOT_API_IMAGE) web=$(env_value "$current" FINPILOT_WEB_IMAGE)"
say "Deployed. The previous release is kept in $previous for rollback.sh."
