#!/usr/bin/env bash
# FinPilot P5: read-only look at how the AI Document Assistant is deployed and how cloudflared and
# Caddy route the existing domains, so FinPilot's deployment can match the experience safely.
#
# Run from Windows (cmd keeps the bytes exact):
#   cmd /c "ssh -o ConnectTimeout=10 SERVER_USER@SERVER_TAILSCALE_IP bash -s < deploy\inspection\inspect-deploy-setup.sh > %USERPROFILE%\Documents\finpilot-deploy-setup.txt 2>&1"
#
# Reads only. No sudo, no writes, no docker compose config (it would expand secrets), no container
# environments. Every printed line goes through a redaction filter: tokens, passwords, keys,
# credentials in URLs, long random strings, emails and tunnel IDs become <redacted>.

set -u
export LC_ALL=C
T=20

redact() {
  sed -E \
    -e 's/((pass(word)?|passwd|secret|token|api[_-]?key|apikey|credential|credentials|auth|bearer|private[_-]?key|tunnel[_-]?id|account[_-]?tag)[^ =:]*["'"'"']?[ =:]+["'"'"']?)[^ "'"'"',]+/\1<redacted>/Ig' \
    -e 's#([a-z][a-z0-9+.-]*://)[^/@ ]+:[^/@ ]+@#\1<redacted>@#Ig' \
    -e 's/--token[= ][^ ]+/--token <redacted>/g' \
    -e 's/\b(ghp|gho|ghs|github_pat|glpat|xox[bap]|tskey|eyJ)[A-Za-z0-9_.-]{10,}/<redacted>/g' \
    -e 's/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/<uuid>/Ig' \
    -e 's/\b[A-Za-z0-9+_=]{32,}\b/<long-value>/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g'
}
section() { printf '\n===== %s =====\n' "$1"; }
run() { printf -- '--- %s\n' "$1"; shift; timeout "$T" "$@" 2>&1 | redact || true; }
show() {  # show FILE: a whole text file, redacted, if readable. Never a program's bytes.
  if [ ! -r "$1" ]; then
    printf -- '--- %s (not readable without root)\n' "$1"
  elif ! grep -Iq . "$1" 2>/dev/null; then
    printf -- '--- %s (a binary or empty file: not shown)\n' "$1"
  else
    printf -- '--- %s\n' "$1"
    head -c 200000 "$1" | redact
  fi
}

echo "FinPilot deploy-setup inspection, $(date -u +%Y-%m-%dT%H:%M:%SZ)"

section "1. AI Document Assistant: systemd"
for unit in ai-document-assistant.service ai-document-assistant-deploy.service \
            ai-document-assistant-check.service ai-document-assistant-check.timer \
            docker-projects.service hesham-databases.service tailscale-docker-start.service; do
  run "systemctl cat $unit" systemctl cat --no-pager "$unit"
  run "state of $unit" systemctl show "$unit" -p ActiveState,SubState,UnitFileState,LastTriggerUSec,Result
done
run "timers" sh -c 'systemctl list-timers --all --no-pager | grep -iE "ai-document|deploy|docker" || echo none'

section "2. Scripts those units run (redacted)"
for script in $(systemctl cat ai-document-assistant.service ai-document-assistant-deploy.service \
                  ai-document-assistant-check.service docker-projects.service 2>/dev/null \
                | grep -oE '^Exec(Start|StartPre|StartPost)=[-@:+!]*[^ ]+' | cut -d= -f2- | sed 's/^[-@:+!]*//' | sort -u); do
  case "$script" in
    /usr/bin/docker|/usr/bin/env|/bin/sh|/bin/bash|/usr/bin/bash|/usr/bin/python3*|*/python|*/python3) continue ;;
  esac
  show "$script"
done

section "3. AI Document Assistant: project folder (names only) and compose structure"
run "folder" sh -c 'ls -la /srv/docker/ai-document-assistant 2>&1 | awk "{print \$1, \$3, \$4, \$NF}"'
run "git remote and commit (no credentials)" sh -c 'cd /srv/docker/ai-document-assistant 2>/dev/null && git remote -v 2>/dev/null | head -2 && git log -1 --format="%h %cs %s" 2>/dev/null'
run "compose structure (keys that describe deployment only)" sh -c \
  'grep -nE "^[a-z_-]+:|^  [a-z0-9_-]+:$|^\s+(image|build|context|dockerfile|ports|expose|networks|restart|healthcheck|test|container_name|depends_on|user|read_only|mem_limit|cpus|labels|profiles|volumes):" /srv/docker/ai-document-assistant/compose.yaml 2>&1 | head -120'
run "workflows in the checkout" sh -c 'ls /srv/docker/ai-document-assistant/.github/workflows 2>&1'
for wf in /srv/docker/ai-document-assistant/.github/workflows/*.y*ml; do show "$wf"; done

section "4. GitHub Actions runners on this host (no tokens)"
run "runner services" sh -c 'systemctl list-units --type=service --all --no-pager --no-legend | grep -i "actions.runner" || echo none'
for unit in $(systemctl list-units --type=service --all --no-pager --no-legend | awk '/actions\.runner/{print $1}'); do
  run "systemctl cat $unit" systemctl cat --no-pager "$unit"
done
run "runner users and groups" sh -c 'for u in $(getent passwd | cut -d: -f1 | grep -iE "runner|github|deploy"); do id "$u"; done'
run "who is in the docker group" getent group docker

section "5. cloudflared: how domains reach this host"
run "systemctl cat cloudflared" systemctl cat --no-pager cloudflared
run "cloudflared version" sh -c 'cloudflared --version 2>&1 | head -1'
for file in /etc/cloudflared/config.yml /etc/cloudflared/config.yaml "$HOME/.cloudflared/config.yml"; do
  [ -e "$file" ] || continue
  if [ -r "$file" ]; then
    printf -- '--- %s (ingress rules only)\n' "$file"
    grep -nE '^\s*(-\s*)?(hostname|service|path|originRequest|httpHostHeader|noTLSVerify|ingress)\b' "$file" | redact
  else
    printf -- '--- %s exists (not readable without root)\n' "$file"
  fi
done
run "is the tunnel managed remotely (token) or by a local config file?" sh -c \
  'systemctl cat cloudflared 2>/dev/null | grep -q -- "--token" && echo "remote-managed (token in the unit): public hostnames are set in the Cloudflare dashboard" || echo "no token in the unit: likely a local config file"'

section "6. Caddy: the full Caddyfile, redacted"
show /srv/docker/caddy/Caddyfile
run "caddy compose structure" sh -c \
  'grep -nE "^[a-z_-]+:|^  [a-z0-9_-]+:$|^\s+(image|ports|networks|restart|volumes|external|name):" /srv/docker/caddy/compose.yaml 2>&1'
run "caddy restart policy and networks" docker inspect --format \
  '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} nets={{range $k, $v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' caddy

section "7. Uptime Kuma and alerting (names only)"
run "uptime-kuma container" docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.Status}}' uptime-kuma
run "telegram bot service" systemctl show heshams-telegram-bot.service -p ActiveState,ExecStart
run "monitor timers" sh -c 'systemctl list-timers --all --no-pager | grep -iE "monitor|check|resources|collector" || echo none'

section "8. Ports FinPilot will use"
run "18085 free on loopback?" sh -c 'ss -tlnH | awk "{print \$4}" | grep -E ":18085$" || echo free'
run "docker networks overlapping 172.30.10-13.x" sh -c \
  'for n in $(docker network ls -q); do docker network inspect --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}" "$n"; done | grep -E "172\.30\.1[0-3]\." || echo none'

printf '\n===== done =====\n'
