#!/usr/bin/env bash
# Emergency stop for the stack test: stops a running stack-test.sh and removes ONLY what the
# stack test owns. Safe to run at any time, as often as needed.
#
#   deploy/scripts/stack-test-stop.sh --confirm
#
# Removes, selected by label, never by guesswork:
#   containers  labelled com.docker.compose.project=finpilot-stacktest, or finpilot.stacktest
#   networks    labelled com.docker.compose.project=finpilot-stacktest
#   volumes     labelled com.docker.compose.project=finpilot-stacktest
#   images      exactly finpilot-api:stacktest, finpilot-api-stack-test:local, finpilot-web:stacktest
# Never touches another project, never prunes, never restarts Docker. Prints what is left over.
# --sweep-only skips stopping the running script (stack-test.sh's own cleanup uses it).

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

confirmed=0
sweep_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) confirmed=1 ;;
    --sweep-only) sweep_only=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm to stop the stack test and remove its own resources"
require_tools docker

TEST_PROJECT="finpilot-stacktest"
TEST_IMAGES=(finpilot-api:stacktest finpilot-api-stack-test:local finpilot-web:stacktest)
REPORT="${FINPILOT_STACKTEST_REPORT:-$(cd "$DEPLOY_DIR/.." && pwd)/stacktest-report}"
label="label=com.docker.compose.project=$TEST_PROJECT"

if [ "$sweep_only" -eq 0 ] && [ -f "$REPORT/stack-test.pid" ]; then
  pid="$(cat "$REPORT/stack-test.pid")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    say "Stopping the running stack test (pid $pid) and its commands"
    # Only its docker commands (a build, a compose run), so its own cleanup and log still run.
    pkill -TERM -P "$pid" -x docker 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
    sleep 5
  fi
fi

say "Removing the stack test's containers"
ids="$( { docker ps -aq --filter "$label"; docker ps -aq --filter "label=finpilot.stacktest"; } | sort -u)"
[ -z "$ids" ] || docker rm -f $ids > /dev/null
say "Removing its networks and volumes"
ids="$(docker network ls -q --filter "$label")"
[ -z "$ids" ] || docker network rm $ids > /dev/null
ids="$(docker volume ls -q --filter "$label")"
[ -z "$ids" ] || docker volume rm $ids > /dev/null
say "Removing its images (by exact name only)"
for image in "${TEST_IMAGES[@]}"; do
  docker image inspect "$image" > /dev/null 2>&1 && docker image rm "$image" > /dev/null || true
done

say "Left over (should be empty):"
docker ps -a --filter "$label" --format '  container {{.Names}}'
docker ps -a --filter "label=finpilot.stacktest" --format '  container {{.Names}}'
docker network ls --filter "$label" --format '  network {{.Name}}'
docker volume ls --filter "$label" --format '  volume {{.Name}}'
for image in "${TEST_IMAGES[@]}"; do
  docker image inspect "$image" > /dev/null 2>&1 && echo "  image $image" || true
done
say "Done. Nothing outside the $TEST_PROJECT project was touched."
