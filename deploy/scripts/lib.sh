#!/usr/bin/env bash
# Shared by the FinPilot deployment scripts. Sourced, never run on its own.
#
# Every script here does nothing without an explicit flag, prints no secret, and touches only
# the compose project named by FINPILOT_PROJECT (default "finpilot").

set -euo pipefail
umask 077

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${FINPILOT_PROJECT:-finpilot}"
SITE_ENV="${FINPILOT_SITE_ENV:-$DEPLOY_DIR/finpilot.env}"
STATE_DIR="${FINPILOT_STATE_DIR:-$DEPLOY_DIR/state}"
RELEASE_ENV="${FINPILOT_RELEASE_ENV:-$STATE_DIR/current.env}"
DB_SERVICE_TIMEOUT="${FINPILOT_HEALTH_TIMEOUT:-180}"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required and isn't installed"
  done
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 (docker compose) is required"
}

# KEY's value in an env file, without sourcing (and so without running) the file.
env_value() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

site_value() { env_value "$SITE_ENV" "$1"; }

database_name() {
  local name
  name="$(site_value FINPILOT_DATABASE)"
  name="${name:-finpilot}"
  [[ "$name" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || die "FINPILOT_DATABASE must be lowercase letters, digits and _"
  echo "$name"
}

# "direct": the FinPilot proxy terminates TLS and publishes loopback ports (compose.direct.yaml).
# "behind": it sits behind the server's existing proxy on the internal edge network, and nothing
# is published.
# "tunnel": plain HTTP on one loopback port for cloudflared, trusting only the egress gateway
# (where cloudflared's connections arrive from) for Cf-Connecting-Ip (compose.tunnel.yaml).
# The site file decides; an exported FINPILOT_PROXY_MODE wins, so compose and this script agree.
proxy_mode() {
  local mode="${FINPILOT_PROXY_MODE:-$(site_value FINPILOT_PROXY_MODE)}"
  mode="${mode:-direct}"
  [[ "$mode" == direct || "$mode" == behind || "$mode" == tunnel ]] \
    || die "FINPILOT_PROXY_MODE must be direct, behind or tunnel"
  echo "$mode"
}

compose() {
  [ -f "$SITE_ENV" ] || die "missing $SITE_ENV (copy finpilot.env.example and fill it in)"
  [ -f "$RELEASE_ENV" ] || die "missing release file $RELEASE_ENV"
  local mode files
  mode="$(proxy_mode)"
  files=(--file "$DEPLOY_DIR/compose.yaml")
  [ "$mode" = direct ] && files+=(--file "$DEPLOY_DIR/compose.direct.yaml")
  [ "$mode" = tunnel ] && files+=(--file "$DEPLOY_DIR/compose.tunnel.yaml")
  # Only stack-test.sh sets this: the test runners and the stand-in edge proxy.
  [ "${FINPILOT_COMPOSE_TEST:-}" = 1 ] && files+=(--file "$DEPLOY_DIR/compose.stacktest.yaml")
  FINPILOT_PROXY_MODE="$mode" \
  FINPILOT_CADDY_CONFIG="$([ "$mode" = direct ] && echo direct || echo behind)" \
  FINPILOT_API_SECCOMP_PROFILE="${FINPILOT_API_SECCOMP_PROFILE:-$DEPLOY_DIR/seccomp/api-chromium.json}" \
  docker compose \
    --project-directory "$DEPLOY_DIR" \
    --project-name "$PROJECT" \
    "${files[@]}" \
    --env-file "$SITE_ENV" \
    --env-file "$RELEASE_ENV" \
    "$@"
}

# A release names both application images by digest, so what runs is exactly what was tested.
require_pinned_release() {
  local file="$1" key value
  [ -f "$file" ] || die "release file $file doesn't exist"
  for key in FINPILOT_API_IMAGE FINPILOT_WEB_IMAGE; do
    value="$(env_value "$file" "$key")"
    [ -n "$value" ] || die "$key is missing from $file"
    if [[ "$value" != *@sha256:* ]] && [ "${FINPILOT_ALLOW_UNPINNED:-}" != "local-test-only" ]; then
      die "$key must be pinned by digest (name@sha256:...), not a tag"
    fi
  done
}

container_health() {
  local id
  id="$(compose ps -q "$1" 2>/dev/null || true)"
  [ -n "$id" ] || { echo "missing"; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id"
}

wait_healthy() {
  local service="$1" waited=0 status
  while [ "$waited" -lt "$DB_SERVICE_TIMEOUT" ]; do
    status="$(container_health "$service")"
    [ "$status" = "healthy" ] && { say "$service is healthy"; return 0; }
    sleep 3
    waited=$((waited + 3))
  done
  echo "$service didn't become healthy (last status: ${status:-unknown})" >&2
  return 1
}

record() {
  mkdir -p "$STATE_DIR"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$STATE_DIR/deployments.log"
}
