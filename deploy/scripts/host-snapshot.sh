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

# Docker's JSON, read by Python: format templates fail on fields some containers don't have
# (no health check, no ports), which once broke this snapshot.
describe() {  # describe containers|networks: one stable line per object, from `docker inspect`
  python3 -c '
import json, sys
kind = sys.argv[1]
def mount(m):
    return "%s:%s->%s:%s" % (m.get("Type"), m.get("Name") or m.get("Source"),
                              m.get("Destination"), m.get("RW"))
for item in sorted(json.load(sys.stdin), key=lambda i: i.get("Name", "")):
    if kind == "containers":
        state = item.get("State") or {}
        health = (state.get("Health") or {}).get("Status", "none")
        policy = ((item.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name", "")
        ports = json.dumps((item.get("NetworkSettings") or {}).get("Ports") or {}, sort_keys=True)
        mounts = ",".join(mount(m) for m in item.get("Mounts") or [])
        print("%s image=%s state=%s health=%s restarts=%s started=%s policy=%s ports=%s mounts=%s" % (
            item.get("Name"), item.get("Image"), state.get("Status"), health,
            item.get("RestartCount"), state.get("StartedAt"), policy, ports, mounts))
    else:
        subnets = " ".join(c.get("Subnet", "") for c in (item.get("IPAM") or {}).get("Config") or [])
        members = ",".join(sorted(c.get("Name", "") for c in (item.get("Containers") or {}).values()))
        print("%s driver=%s internal=%s subnets=%s members=%s" % (
            item.get("Name"), item.get("Driver"), item.get("Internal"), subnets, members))
' "$1"
}

containers() {
  local ids
  ids="$(others)"
  [ -n "$ids" ] || return 0
  # shellcheck disable=SC2086 - one argument per container ID
  docker inspect $ids | describe containers
}

write_snapshot() {
  echo "## taken $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "## containers"
  containers
  echo "## networks"
  local names
  names="$(docker network ls --format '{{.Name}}' | grep -v "^${EXCLUDE_PROJECT}_" | sort || true)"
  # shellcheck disable=SC2086 - one argument per network name
  [ -z "$names" ] || docker network inspect $names | describe networks
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
  df -hP / /srv/storage 2>/dev/null | tail -n +2 | awk '{print $6, "avail=" $4}' || true
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
