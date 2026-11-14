#!/usr/bin/env bash
# Installs (or updates) FinPilot's production files on the home server. Run it yourself, with sudo:
#
#   sudo bash deploy/server/install.sh --source /path/to/finpilot-checkout --commit SHA --confirm
#
# --source is a copy of the repository at a commit you reviewed. Only its deploy/ folder is used.
# What it does, and never does:
#   - copies deploy/ to /srv/docker/finpilot/deploy (root-owned), keeping secrets/, state/ and
#     finpilot.env exactly as they are; records the commit in /srv/docker/finpilot/SOURCE_COMMIT;
#   - creates /etc/finpilot (0700): deployer.env from the template, the restic password (generated
#     once, never printed, never replaced), the registry login folder and the approvals folder;
#   - creates /srv/storage/backups/finpilot (0700) and an encrypted restic repository in it, once;
#   - writes finpilot.env (tunnel mode, finpilot.hishamjouda.com) and generates the database
#     secrets, only if they don't exist yet;
#   - installs the four systemd units, but does NOT enable or start them.
# It never touches another project, Caddy, cloudflared, the firewall, or any existing secret.

set -euo pipefail
umask 077

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

source_dir=""
commit=""
confirmed=0
host="finpilot.hishamjouda.com"
while [ $# -gt 0 ]; do
  case "$1" in
    --source) source_dir="$2"; shift ;;
    --commit) commit="$2"; shift ;;
    --host) host="$2"; shift ;;
    --confirm) confirmed=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm (and --source DIR --commit SHA)"
[ "$(id -u)" -eq 0 ] || die "run it with sudo: it installs root-owned files and systemd units"
[ -f "$source_dir/deploy/compose.yaml" ] || die "--source must be a FinPilot checkout"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die "--commit must be the full 40-character commit SHA"
[[ "$host" =~ ^[a-z0-9.-]+$ ]] || die "--host must be a host name"
for tool in docker restic flock curl tar openssl; do
  command -v "$tool" > /dev/null || die "$tool is required (nothing is installed by this script)"
done
mountpoint -q /srv/storage || die "/srv/storage isn't mounted"

target="/srv/docker/finpilot"
deploy="$target/deploy"

say "Copying deploy/ from commit ${commit:0:12}"
install -d -m 0750 -o root -g root "$target" "$deploy"
(cd "$source_dir" && tar -cf - \
   --exclude='deploy/secrets' --exclude='deploy/state' --exclude='deploy/finpilot.env' \
   --exclude='deploy/release.env' --exclude='deploy/backups' deploy) \
  | (cd "$target" && tar -xf - --no-same-owner)
echo "$commit" > "$target/SOURCE_COMMIT"
# Root owns the copied code, but never secrets/ (each file's group is the container user that
# reads it, set by init-secrets.sh), state/ or finpilot.env: a re-install must not reset them.
find "$deploy" \( -path "$deploy/secrets" -o -path "$deploy/state" \) -prune \
  -o ! -path "$deploy/finpilot.env" -exec chown root:root {} +
find "$deploy" \( -path "$deploy/secrets" -o -path "$deploy/state" \) -prune \
  -o -name '*.sh' -exec chmod 0750 {} +

say "Configuration in /etc/finpilot"
install -d -m 0700 /etc/finpilot /etc/finpilot/docker /etc/finpilot/approved /var/cache/finpilot-restic
if [ ! -f /etc/finpilot/deployer.env ]; then
  sed "s#^FINPILOT_PUBLIC_URL=.*#FINPILOT_PUBLIC_URL=https://$host#" \
    "$deploy/server/deployer.env.example" > /etc/finpilot/deployer.env
  chmod 0600 /etc/finpilot/deployer.env
fi
if [ ! -s /etc/finpilot/restic-password ]; then
  openssl rand -hex 32 > /etc/finpilot/restic-password
  chmod 0400 /etc/finpilot/restic-password
  say "Generated the backup password: /etc/finpilot/restic-password. Copy it to your password manager"
  say "now (sudo cat it in your own terminal). Without it the backups can't be read."
fi

say "Backup repository on the external disk"
install -d -m 0700 /srv/storage/backups/finpilot
export RESTIC_CACHE_DIR=/var/cache/finpilot-restic
repo=/srv/storage/backups/finpilot/restic
if ! restic --repo "$repo" --password-file /etc/finpilot/restic-password cat config > /dev/null 2>&1; then
  restic --repo "$repo" --password-file /etc/finpilot/restic-password init --quiet > /dev/null
  say "Created the encrypted repository $repo"
fi

say "Site settings and database secrets (only if missing)"
if [ ! -f "$deploy/finpilot.env" ]; then
  sed -e "s#^FINPILOT_PROXY_MODE=.*#FINPILOT_PROXY_MODE=tunnel#" \
      -e "s#^FINPILOT_HOST=.*#FINPILOT_HOST=$host#" \
      -e "s#^FINPILOT_ORIGIN=.*#FINPILOT_ORIGIN=https://$host#" \
      "$deploy/finpilot.env.example" > "$deploy/finpilot.env"
  chmod 0640 "$deploy/finpilot.env"
fi
if [ ! -e "$deploy/secrets/database_url" ]; then
  "$deploy/scripts/init-secrets.sh" --generate
fi
# Each secret readable by root and the one container user that needs it (as init-secrets.sh
# sets them): the database (999) for its password, the app and jobs (group 10201) for the rest.
# Ownership and mode only: no file is read, rewritten or replaced.
chmod 0751 "$deploy/secrets"
for name in postgres_password owner_role_password runtime_role_password admin_database_url \
            owner_database_url database_url google_client_secret beta_allowlist; do
  file="$deploy/secrets/$name"
  [ -f "$file" ] || continue
  if [ "$name" = postgres_password ]; then chown root:999 "$file"; else chown root:10201 "$file"; fi
  chmod 0640 "$file"
done

say "systemd units (installed, not enabled)"
install -m 0644 "$deploy"/server/systemd/finpilot-*.service "$deploy"/server/systemd/finpilot-*.timer \
  /etc/systemd/system/
systemctl daemon-reload

say "Installed from ${commit:0:12}. Still to do by hand (deploy/server/README.md):"
echo "  1. FINPILOT_GOOGLE_CLIENT_ID in $deploy/finpilot.env, and the secret in $deploy/secrets/google_client_secret"
echo "  2. only for an invitation-only launch: FINPILOT_REGISTRATION_MODE=allowlist and the addresses in $deploy/secrets/beta_allowlist"
echo "  3. a read:packages token in /etc/finpilot/ghcr-token (0400)"
echo "  4. the Cloudflare Tunnel public hostname $host -> http://127.0.0.1:18085"
echo "  5. sudo systemd-run --wait --pty -p EnvironmentFile=/etc/finpilot/deployer.env $deploy/server/finpilot-deployer.sh --first-deploy"
echo "  6. sudo systemctl enable --now finpilot-deploy.timer finpilot-backup.timer"
