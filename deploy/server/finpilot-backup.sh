#!/usr/bin/env bash
# Nightly encrypted backup of the FinPilot production database (finpilot-backup.timer, as root,
# with /etc/finpilot/deployer.env):
#   1. pg_dump as the schema owner, streamed straight into restic (deploy/scripts/backup.sh):
#      encrypted on this machine, never a plaintext file on disk, no snapshot if the dump fails;
#   2. retention: 7 daily, 4 weekly, 6 monthly snapshots, then prune;
#   3. restic check of the repository's structure;
#   4. a heartbeat to the Uptime Kuma push monitor: "up" on success, "down" with the failing step.
# The repository is on the external SSD (a different disk from the live database). An off-site
# copy isn't configured: docs/SERVER_READINESS.md lists it as an open launch risk.

set -euo pipefail
umask 077
: "${FINPILOT_DIR:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_RESTIC_REPOSITORY:?set in /etc/finpilot/deployer.env}"
: "${FINPILOT_RESTIC_PASSWORD_FILE:?set in /etc/finpilot/deployer.env}"
DEPLOY_DIR="$FINPILOT_DIR/deploy"
export RESTIC_CACHE_DIR="/var/cache/finpilot-restic"
export DOCKER_CONFIG="/etc/finpilot/docker"

kuma() {
  local file="${FINPILOT_KUMA_BACKUP_URL_FILE:-}" message
  [ -n "$file" ] && [ -s "$file" ] || return 0
  message="$(printf '%s' "$2" | tr -c 'A-Za-z0-9 ._:-' '_' | tr ' ' '+')"
  curl -fsS --max-time 10 -o /dev/null "$(head -n 1 "$file")?status=$1&msg=$message" || true
}
step="start"
trap 'kuma down "backup failed at: $step"' ERR

exec 9> /run/finpilot-deploy.lock
flock -w 1800 9  # never at the same time as a deployment

restic_cmd() {
  restic --repo "$FINPILOT_RESTIC_REPOSITORY" --password-file "$FINPILOT_RESTIC_PASSWORD_FILE" "$@"
}
step="dump"
snapshot="$("$DEPLOY_DIR/scripts/backup.sh" --confirm --restic-repo "$FINPILOT_RESTIC_REPOSITORY" \
  --restic-password-file "$FINPILOT_RESTIC_PASSWORD_FILE" | tail -n 1)"
step="retention"
restic_cmd forget --quiet --tag finpilot --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
step="check"
restic_cmd check --quiet
echo "finpilot-backup: $snapshot, retention applied, repository checked"
kuma up "${snapshot#restic:}"
