#!/usr/bin/env bash
# Read-only check of FinPilot's production configuration on the server. Prints formats, counts,
# owners and permissions: never a value. Run it with sudo:
#
#   sudo bash /srv/docker/finpilot/deploy/server/check-config.sh
#
# Checks: the installed commit; finpilot.env (tunnel mode, host, origin, a real Google client ID);
# the Google client secret (present, one line, Google's format); the beta allowlist (valid
# addresses, shown masked); each secret file's owner and mode; the registry token (it can log in
# and read the private release manifest); the restic repository (it opens with its password);
# the deployer settings; the systemd units (installed, timers still off). Ends with
# "CONFIG OK" (exit 0) or "CONFIG NOT READY" (exit 1).

set -uo pipefail
umask 077
D=/srv/docker/finpilot/deploy
failures=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
info() { echo "INFO  $*"; }
value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1; }

[ "$(id -u)" -eq 0 ] || { echo "run it with sudo"; exit 1; }

# Asks the registry itself, per package, and tells the three failures apart: no such package or
# tag (no release published yet), the token isn't allowed to read it (read:packages), or the
# token isn't accepted. The token goes to curl through stdin, never the command line, and no
# response body is printed: only status codes, registry error codes and tag names.
registry_report() {
  local user="$1" token_file="$2" package bearer response code tags
  for package in finpilot-release finpilot-api finpilot-web; do
    response="$(printf 'user = "%s:%s"\n' "$user" "$(cat "$token_file")" \
      | curl -sS --max-time 20 -K - \
        "https://ghcr.io/token?service=ghcr.io&scope=repository:hishamfekri/$package:pull")"
    bearer="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)"
    if [ -z "$bearer" ]; then
      fail "$package: the registry refused the token (expired, or not the HishamFekri account)"
      continue
    fi
    response="$(printf 'header = "Authorization: Bearer %s"\n' "$bearer" \
      | curl -sS --max-time 20 -K - -w '\n%{http_code}' \
        "https://ghcr.io/v2/hishamfekri/$package/tags/list")"
    code="${response##*$'\n'}"
    case "$code" in
      200)
        tags="$(printf '%s' "${response%$'\n'*}" | python3 -c 'import json,sys; t=json.load(sys.stdin).get("tags") or []; print(len(t), "tags; latest" if "latest" in t else "tags", "; commit tags:", " ".join(sorted(x[4:16] for x in t if x.startswith("sha-"))[-3:]))' 2>/dev/null)"
        if [ "$package" = finpilot-release ] && ! grep -q "latest" <<< "$tags"; then
          fail "$package: readable, but has no 'latest' tag yet ($tags)"
        else
          pass "$package: readable ($tags)"
        fi ;;
      401|403)
        fail "$package: HTTP $code, the token may not read it (needs read:packages; package owned by HishamFekri?)" ;;
      404)
        fail "$package: HTTP 404, no such package (has a Release run published it?)" ;;
      *)
        fail "$package: unexpected HTTP $code from the registry" ;;
    esac
  done
}
info "installed commit: $(cat /srv/docker/finpilot/SOURCE_COMMIT 2>/dev/null || echo unknown)"

site="$D/finpilot.env"
if [ -f "$site" ]; then
  [ "$(value "$site" FINPILOT_PROXY_MODE)" = tunnel ] && pass "proxy mode: tunnel" || fail "FINPILOT_PROXY_MODE isn't tunnel"
  host="$(value "$site" FINPILOT_HOST)"
  [ "$host" = finpilot.hishamjouda.com ] && pass "host: $host" || fail "FINPILOT_HOST is '$host'"
  [ "$(value "$site" FINPILOT_ORIGIN)" = "https://$host" ] && pass "origin: https://$host" || fail "FINPILOT_ORIGIN isn't https://$host"
  client="$(value "$site" FINPILOT_GOOGLE_CLIENT_ID)"
  if [[ "$client" =~ ^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$ ]]; then
    pass "Google client ID: a real-looking ID (${#client} characters)"
  else
    fail "FINPILOT_GOOGLE_CLIENT_ID isn't a Google client ID yet (still the placeholder?)"
  fi
  mode="$(value "$site" FINPILOT_REGISTRATION_MODE)"
  [ "${mode:-allowlist}" = allowlist ] && pass "registration: allowlist" || info "registration mode: $mode"
else
  fail "$site is missing (run install.sh)"
fi

secret="$D/secrets/google_client_secret"
if [ -s "$secret" ]; then
  lines="$(wc -l < "$secret")"; chars="$(tr -d '\n' < "$secret" | wc -c)"
  if [ "$lines" -le 1 ] && grep -Eq '^GOCSPX-[A-Za-z0-9_-]{20,}$' "$secret"; then
    pass "Google client secret: one line, Google's format ($chars characters)"
  else
    fail "the Google client secret should be one line starting with GOCSPX- (found $lines lines, $chars characters)"
  fi
else
  fail "the Google client secret is empty"
fi

allow="$D/secrets/beta_allowlist"
if [ -s "$allow" ]; then
  count=0; bad=0
  while IFS= read -r line; do
    line="$(echo "$line" | tr -d '[:space:]')"
    [ -n "$line" ] || continue
    if [[ "$line" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[a-z]{2,}$ ]]; then
      count=$((count + 1)); info "allowlisted: ${line:0:1}***@${line#*@}"
    else
      bad=$((bad + 1))
    fi
  done < "$allow"
  [ "$bad" -eq 0 ] && [ "$count" -gt 0 ] && pass "beta allowlist: $count address(es)" || fail "beta allowlist: $bad line(s) aren't email addresses"
else
  fail "the beta allowlist is empty (production refuses to start)"
fi

for name in postgres_password owner_role_password runtime_role_password admin_database_url \
            owner_database_url database_url google_client_secret beta_allowlist; do
  file="$D/secrets/$name"
  if [ -f "$file" ]; then
    perms="$(stat -c '%U:%G %a' "$file")"
    case "$name:$perms" in
      postgres_password:"root:999 640"|*:"root:10201 640") pass "secret $name: $perms" ;;
      *) fail "secret $name has $perms (expected root, group 999 or 10201, mode 640)" ;;
    esac
  else
    fail "secret $name is missing"
  fi
done

token=/etc/finpilot/ghcr-token
if [ -s "$token" ]; then
  perms="$(stat -c '%U %a' "$token")"
  [ "$perms" = "root 400" ] || [ "$perms" = "root 600" ] && pass "registry token: $perms" || fail "registry token has $perms (expected root 400)"
  user="$(value /etc/finpilot/deployer.env FINPILOT_GHCR_USER)"
  if DOCKER_CONFIG=/etc/finpilot/docker docker login ghcr.io -u "$user" --password-stdin < "$token" > /dev/null 2>&1; then
    pass "registry token: logs in to ghcr.io (that proves only that the token is valid)"
    registry_report "$user" "$token"
  else
    fail "the registry token doesn't log in (expired, wrong, or missing read:packages)"
  fi
else
  fail "the registry token is missing: /etc/finpilot/ghcr-token"
fi

repo="$(value /etc/finpilot/deployer.env FINPILOT_RESTIC_REPOSITORY)"
if RESTIC_CACHE_DIR=/var/cache/finpilot-restic restic --repo "$repo" \
     --password-file /etc/finpilot/restic-password cat config > /dev/null 2>&1; then
  pass "backup repository opens with its password"
else
  fail "the restic repository doesn't open"
fi

for key in FINPILOT_DIR FINPILOT_PUBLIC_URL FINPILOT_GHCR_USER FINPILOT_GHCR_TOKEN_FILE \
           FINPILOT_RESTIC_REPOSITORY FINPILOT_RESTIC_PASSWORD_FILE; do
  [ -n "$(value /etc/finpilot/deployer.env "$key")" ] && pass "deployer setting $key" || fail "deployer setting $key is missing"
done
for unit in finpilot-deploy.service finpilot-deploy.timer finpilot-backup.service finpilot-backup.timer; do
  [ -f "/etc/systemd/system/$unit" ] && pass "unit installed: $unit" || fail "unit missing: $unit"
done
info "timers: deploy $(systemctl is-enabled finpilot-deploy.timer 2>/dev/null), backup $(systemctl is-enabled finpilot-backup.timer 2>/dev/null) (enable only after the first deployment)"
if ss -tlnH | awk '{print $4}' | grep -qE ':18085$'; then info "port 18085 is listening (FinPilot already running?)"; else pass "port 18085 is free"; fi

echo
if [ "$failures" -eq 0 ]; then echo "CONFIG OK"; exit 0; fi
echo "CONFIG NOT READY ($failures)"
exit 1
