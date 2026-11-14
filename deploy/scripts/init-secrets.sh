#!/usr/bin/env bash
# Creates the stack's secrets as files: random database passwords and the URLs built from them.
#
#   sudo deploy/scripts/init-secrets.sh --generate
#
# Refuses if any secret file already exists, so it can't replace credentials a database already
# uses. Prints no value. The Google client secret isn't generated: put the production client's
# secret into secrets/google_client_secret yourself. Until then the API refuses to start.
#
# Ownership (as root): readable only by root and the container user that needs each file:
# postgres_password by the database (uid 999), the rest by the app and job images (gid 10201).
# --local-test-permissions makes the files world-readable inside a private folder instead, for a
# throwaway stack on a developer machine (stack-test.sh). Never on the server.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

generate=0
local_test=0
dir="${FINPILOT_SECRETS_PATH:-$DEPLOY_DIR/secrets}"
while [ $# -gt 0 ]; do
  case "$1" in
    --generate) generate=1 ;;
    --local-test-permissions) local_test=1 ;;
    --secrets-dir) dir="$2"; shift ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$generate" -eq 1 ] || die "nothing done: pass --generate to create new secrets in $dir"

names=(postgres_password owner_role_password runtime_role_password admin_database_url
       owner_database_url database_url google_client_secret beta_allowlist)
for name in "${names[@]}"; do
  [ ! -e "$dir/$name" ] || die "$dir/$name already exists. Existing secrets are never replaced."
done

random() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

mkdir -p "$dir"
chmod 0700 "$dir"
db="$(database_name)"
postgres="$(random)"
owner="$(random)"
runtime="$(random)"

write() { printf '%s' "$2" > "$dir/$1"; }
write postgres_password "$postgres"
write owner_role_password "$owner"
write runtime_role_password "$runtime"
write admin_database_url "postgresql+psycopg://postgres:${postgres}@db:5432/postgres"
write owner_database_url "postgresql+psycopg://finpilot_owner:${owner}@db:5432/${db}"
write database_url "postgresql+psycopg://finpilot_app:${runtime}@db:5432/${db}"
: > "$dir/google_client_secret"
: > "$dir/beta_allowlist"
unset postgres owner runtime

if [ "$local_test" -eq 1 ]; then
  chmod 0644 "$dir"/*
  chmod 0755 "$dir"
elif [ "$(id -u)" -eq 0 ]; then
  chown root:999 "$dir/postgres_password"
  for name in "${names[@]}"; do
    [ "$name" = postgres_password ] || chown root:10201 "$dir/$name"
  done
  chmod 0640 "$dir"/*
  chmod 0751 "$dir"
else
  die "run as root, so each file can be given to the container user that needs it"
fi

say "Created ${#names[@]} secret files in $dir. No value was printed."
say "Next: put the production Google client secret into $dir/google_client_secret."
say "Only in allowlist mode: the addresses allowed to sign up, one per line, in $dir/beta_allowlist."
