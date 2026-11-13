#!/usr/bin/env bash
# Adds finpilot.hishamjouda.com to the EXISTING Cloudflare Tunnel (/etc/cloudflared/config.yml).
# Run it yourself, with sudo.
#
#   sudo bash cloudflared-route.sh --check            read-only: prepares the change on a copy and
#                                                     validates it; the live file and cloudflared
#                                                     are untouched
#   sudo bash cloudflared-route.sh --apply --confirm  only after you approve the brief restart
#
# The change is one ingress rule, inserted directly above the catch-all (http_status:404):
#     - hostname: finpilot.hishamjouda.com
#       service: http://127.0.0.1:18085
# --apply: refuses unless FinPilot already answers on 127.0.0.1:18085; backs up the live file;
# installs the validated copy; restarts cloudflared once; then checks every existing hostname
# through Cloudflare. If any of them stops answering, it restores the backup and restarts again.
# It never touches Caddy, Docker, DNS or any other service.

set -euo pipefail
umask 022
CONFIG=/etc/cloudflared/config.yml
HOST=finpilot.hishamjouda.com
TARGET=http://127.0.0.1:18085

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
mode="${1:-}"
[ "$mode" = --check ] || { [ "$mode" = --apply ] && [ "${2:-}" = --confirm ]; } \
  || die "nothing done: pass --check, or --apply --confirm after you approve the restart"
[ "$(id -u)" -eq 0 ] || die "run it with sudo"
[ -f "$CONFIG" ] || die "$CONFIG doesn't exist"
command -v cloudflared > /dev/null || die "cloudflared isn't installed"

hostnames() { sed -n 's/^[[:space:]]*-[[:space:]]*hostname:[[:space:]]*//p' "$1" | tr -d "\"'"; }
ingress() { grep -E '^\s*(-\s*)?(hostname|service):' "$1"; }

say "Current ingress rules"
ingress "$CONFIG"
existing="$(hostnames "$CONFIG")"

candidate="$(mktemp)"
trap 'rm -f "$candidate"' EXIT
if grep -qE "^\s*-\s*hostname:\s*\"?$HOST\"?\s*$" "$CONFIG"; then
  say "$HOST is already in the tunnel's config"
  cp "$CONFIG" "$candidate"
  already=1
else
  already=0
  # Insert above the catch-all, with the same indentation as the existing rules.
  awk -v host="$HOST" -v target="$TARGET" '
    !done && /^[[:space:]]*-[[:space:]]*service:[[:space:]]*http_status:404/ {
      match($0, /^[[:space:]]*/); pad = substr($0, 1, RLENGTH)
      print pad "- hostname: " host
      print pad "  service: " target
      done = 1
    }
    { print }
    END { if (!done) exit 3 }
  ' "$CONFIG" > "$candidate" || die "no catch-all rule (http_status:404) found; not changing anything"
  say "Proposed change"
  diff -u "$CONFIG" "$candidate" | sed -n '/^[-+][^-+]/p' || true
fi

say "Validating the proposed config (a copy; the live one is untouched)"
cloudflared tunnel --config "$candidate" ingress validate
rule_for() { cloudflared tunnel --config "$1" ingress rule "https://$2" 2>&1 | grep -iE 'service' | head -n 1; }
rule="$(rule_for "$candidate" "$HOST")"
echo "  $HOST -> $rule"
grep -q "127.0.0.1:18085" <<< "$rule" || die "the new rule doesn't send $HOST to $TARGET"
for name in $existing; do
  [ "$name" = "$HOST" ] && continue
  before="$(rule_for "$CONFIG" "$name")"
  after="$(rule_for "$candidate" "$name")"
  [ "$before" = "$after" ] || die "$name would change: '$before' -> '$after'"
  echo "  $name unchanged -> $after"
done

say "DNS"
tunnel="$(sed -n 's/^tunnel:[[:space:]]*//p' "$CONFIG" | tr -d "\"' ")"
if getent hosts "$HOST" > /dev/null; then
  echo "  $HOST resolves (the DNS record exists)"
else
  echo "  $HOST doesn't resolve yet. In Cloudflare DNS for hishamjouda.com, add:"
  echo "    type CNAME, name finpilot, target ${tunnel:-<tunnel ID>}.cfargotunnel.com, Proxied (orange cloud)"
fi

say "FinPilot on its loopback port"
if curl -fsS --max-time 10 -H "Host: $HOST" "$TARGET/api/v1/readyz" | grep -q '"ready"'; then
  ready=1; echo "  ready"
else
  ready=0; echo "  not answering yet (run the first deployment with --local-only first)"
fi

if [ "$mode" = --check ]; then
  say "Check complete. Nothing was changed."
  exit 0
fi

# --- --apply --confirm
[ "$ready" -eq 1 ] || die "FinPilot isn't ready on $TARGET: not routing traffic to it"
answers() {  # answers HOST: Cloudflare reaches this tunnel's origin (no 502/503/530 from the edge)
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$1/" || echo 000)"
  [ "$code" != 000 ] && [ "$code" != 502 ] && [ "$code" != 503 ] && [ "$code" != 530 ]
}
wait_for() {
  local name="$1" n
  for n in $(seq 1 20); do answers "$name" && return 0; sleep 3; done
  return 1
}
if [ "$already" -eq 1 ]; then
  say "Nothing to change; not restarting cloudflared"
  exit 0
fi
backup="$CONFIG.before-finpilot-$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$CONFIG" "$backup"
say "Backed up the live config to $backup"
install -m "$(stat -c '%a' "$CONFIG")" -o "$(stat -c '%u' "$CONFIG")" -g "$(stat -c '%g' "$CONFIG")" \
  "$candidate" "$CONFIG"
say "Restarting cloudflared (the brief interruption you approved)"
systemctl restart cloudflared
for name in $existing; do
  [ "$name" = "$HOST" ] && continue
  if wait_for "$name"; then
    echo "  $name answers"
  else
    echo "  $name does NOT answer: restoring the previous config" >&2
    cp -a "$backup" "$CONFIG"
    systemctl restart cloudflared
    die "rolled back to $backup; FinPilot's route wasn't kept"
  fi
done
if wait_for "$HOST"; then
  say "$HOST answers through the tunnel"
else
  echo "  $HOST doesn't answer yet (DNS may still be propagating; the existing sites are fine)"
fi
say "Done. To undo: sudo cp -a $backup $CONFIG && sudo systemctl restart cloudflared"
