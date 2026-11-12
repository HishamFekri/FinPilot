#!/usr/bin/env bash
# Puts the previous release's images back. The database is NOT changed.
#
#   deploy/scripts/rollback.sh --confirm
#
# Safe whenever the newer release's migrations were additive, which expand-then-contract
# guarantees for one release (docs/ARCHITECTURE.md §11.8). If a migration itself was wrong, this
# isn't enough: restore the pre-migration backup into a new database (restore.sh, then
# deploy/README.md "Recovering from a bad migration").

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

confirmed=0
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) confirmed=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
current="$STATE_DIR/current.env"
previous="$STATE_DIR/previous.env"
[ -f "$previous" ] || die "no previous release is recorded in $previous"
if [ "$confirmed" -ne 1 ]; then
  echo "Would replace:"
  echo "  api $(env_value "$current" FINPILOT_API_IMAGE)"
  echo "  web $(env_value "$current" FINPILOT_WEB_IMAGE)"
  echo "with:"
  echo "  api $(env_value "$previous" FINPILOT_API_IMAGE)"
  echo "  web $(env_value "$previous" FINPILOT_WEB_IMAGE)"
  die "nothing done: pass --confirm to roll back"
fi
require_pinned_release "$previous"
require_tools docker

RELEASE_ENV="$previous"
say "Starting the previous release"
compose up -d --no-deps api
wait_healthy api || die "the previous API isn't healthy either: stop and investigate"
compose up -d --no-deps web proxy
wait_healthy web
wait_healthy proxy

cp "$current" "$STATE_DIR/rolled-back.env"
cp "$previous" "$current"
record "rolled back to api=$(env_value "$current" FINPILOT_API_IMAGE)"
say "Rolled back. The release that was replaced is in $STATE_DIR/rolled-back.env."
