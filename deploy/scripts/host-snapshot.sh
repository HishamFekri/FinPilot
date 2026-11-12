#!/usr/bin/env bash
# Read-only record of everything on the Docker host that ISN'T the FinPilot stack test, so a run
# can prove it left the rest of the server exactly as it found it.
#
#   deploy/scripts/host-snapshot.sh --write FILE        record the current state
#   deploy/scripts/host-snapshot.sh --containers        print only the containers section
#   deploy/scripts/host-snapshot.sh --compare A B       compare two records; exit 1 on a difference
#
# Compared: containers (state, health, restart count, start time, restart policy, image, ports,
# mounts), networks (driver, internal, subnets, members), volumes, compose projects and TCP
# listeners. Informational only: UDP listeners (client sockets come and go), images and disk use.
# Excluded: the "finpilot-stacktest" compose project and containers labelled finpilot.stacktest.
# Reads no environment, label value, file content or secret: only names, states and addresses.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# FINPILOT_SNAPSHOT_EXCLUDE=finpilot when checking that the production deployment touched nothing else.
EXCLUDE_PROJECT="${FINPILOT_SNAPSHOT_EXCLUDE:-finpilot-stacktest}"

others() {
  # Container IDs that aren't the stack test's.
  comm -23 \
    <(docker ps -aq --no-trunc | sort) \
    <({ docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$EXCLUDE_PROJECT"
        docker ps -aq --no-trunc --filter "label=finpilot.stacktest"; } | sort -u)
}

containers() {
  local id
  for id in $(others); do
    docker inspect --format \
      '{{.Name}} image={{.Image}} state={{.State.Status}} health={{with index .State "Health"}}{{index . "Status"}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}} policy={{.HostConfig.RestartPolicy.Name}} ports={{json .NetworkSettings.Ports}} mounts={{range .Mounts}}{{.Type}}:{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}->{{.Destination}}:{{.RW}},{{end}}' \
      "$id"
  done | sort
}

write_snapshot() {
  echo "## taken $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "## containers"
  containers
  echo "## networks"
  local name
  for name in $(docker network ls --format '{{.Name}}' | grep -v "^${EXCLUDE_PROJECT}_" | sort); do
    docker network inspect --format \
      '{{.Name}} driver={{.Driver}} internal={{.Internal}} subnets={{range .IPAM.Config}}{{.Subnet}} {{end}}members={{range $id, $c := .Containers}}{{$c.Name}},{{end}}' \
      "$name"
  done
  echo "## volumes"
  comm -23 \
    <(docker volume ls -q | sort) \
    <(docker volume ls -q --filter "label=com.docker.compose.project=$EXCLUDE_PROJECT" | sort)
  echo "## compose"
  docker compose ls -a | tail -n +2 | grep -v "^${EXCLUDE_PROJECT} " | sort || true
  echo "## listeners-tcp"
  ss -tlnH | awk '{print $4}' | sort -u
  echo "## listeners-udp"
  ss -ulnH | awk '{print $4}' | sort -u
  echo "## images"
  docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' | sort
  echo "## disk"
  df -hP / /srv/storage 2>/dev/null | tail -n +2 | awk '{print $6, "avail=" $4}'
  docker system df --format '{{.Type}} size={{.Size}}' 2>/dev/null || true
}

section() { awk -v want="## $2" '/^## /{on=($0==want); next} on' "$1"; }

compare() {
  local a="$1" b="$2" name differ=0
  [ -f "$a" ] && [ -f "$b" ] || die "compare needs two snapshot files"
  for name in containers networks volumes compose listeners-tcp; do
    if diff <(section "$a" "$name") <(section "$b" "$name") > /dev/null; then
      echo "SAME  $name"
    else
      echo "DIFF  $name"
      diff <(section "$a" "$name") <(section "$b" "$name") | sed 's/^/      /' || true
      differ=1
    fi
  done
  for name in listeners-udp images disk; do
    echo "INFO  $name (not compared)"
    diff <(section "$a" "$name") <(section "$b" "$name") | sed 's/^/      /' || true
  done
  return "$differ"
}

case "${1:-}" in
  --write) [ -n "${2:-}" ] || die "pass --write FILE"; write_snapshot > "$2"; say "Snapshot written: $2" ;;
  --containers) containers ;;
  --compare) compare "${2:-}" "${3:-}" ;;
  *) die "nothing done: pass --write FILE, --containers or --compare A B" ;;
esac
