#!/usr/bin/env bash
# A full dump of the FinPilot database, encrypted before it touches the disk.
#
#   deploy/scripts/backup.sh --confirm --restic-repo REPO --restic-password-file FILE [--restic-init]
#   deploy/scripts/backup.sh --confirm [--recipients FILE]          (age, if installed)
#
# The dump runs as the schema owner (a role that RLS applies to can't dump), in a one-off
# container on the internal network, and is streamed straight into an encrypted store:
# - restic (recommended; installed on the home server): a snapshot in REPO, which can be a local
#   folder on a different disk or an off-site repository. restic encrypts on the client. The
#   repository password file is root-only, and a copy is kept off the server. --restic-init
#   creates REPO first, and only if it doesn't exist yet.
# - age: a file encrypted to the recipients' public keys; the private key stays off the server.
# If the dump fails part-way, no snapshot or file is kept. Copying off-site is separate
# (deploy/README.md): a backup on the same disk isn't a backup.
#
# --unencrypted-local-test writes a plain dump, and is refused unless FINPILOT_HOST is a local
# test name (*.test or localhost).

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

confirmed=0
plain=0
restic_repo="${FINPILOT_RESTIC_REPOSITORY:-}"
restic_password="${FINPILOT_RESTIC_PASSWORD_FILE:-}"
restic_init=0
recipients="${FINPILOT_BACKUP_RECIPIENTS:-$DEPLOY_DIR/backup-recipients.txt}"
out_dir="${FINPILOT_BACKUP_PATH:-$DEPLOY_DIR/backups}"
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) confirmed=1 ;;
    --recipients) recipients="$2"; shift ;;
    --restic-repo) restic_repo="$2"; shift ;;
    --restic-password-file) restic_password="$2"; shift ;;
    --restic-init) restic_init=1 ;;
    --unencrypted-local-test) plain=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm to back up"
require_tools docker

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
db="$(database_name)"

if [ -n "$restic_repo" ]; then
  require_tools restic
  [ -s "$restic_password" ] || die "pass --restic-password-file FILE (the repository password)"
  restic_cmd() { restic --repo "$restic_repo" --password-file "$restic_password" "$@"; }
  if ! restic_cmd cat config > /dev/null 2>&1; then
    [ "$restic_init" -eq 1 ] || die "no restic repository at $restic_repo (pass --restic-init to create it)"
    restic_cmd init --quiet > /dev/null || die "couldn't create the restic repository"
  fi
  summary="$(mktemp)"
  set +e
  compose run --rm -T backup \
    | restic_cmd backup --stdin --stdin-filename finpilot.dump --tag finpilot --tag "db=$db" \
        --host finpilot --json > "$summary"
  status=("${PIPESTATUS[@]}")
  set -e
  snapshot="$(grep -o '"snapshot_id":"[0-9a-f]*"' "$summary" | tail -n 1 | cut -d'"' -f4)"
  processed="$(grep -o '"total_bytes_processed":[0-9]*' "$summary" | tail -n 1 | cut -d: -f2)"
  rm -f "$summary"
  if [ "${status[0]}" -ne 0 ] || [ "${status[1]}" -ne 0 ] || [ -z "$snapshot" ] \
     || [ "${processed:-0}" -eq 0 ]; then
    [ -n "$snapshot" ] && restic_cmd forget --quiet "$snapshot" > /dev/null 2>&1 || true
    die "the dump failed (pg_dump exit ${status[0]}, restic exit ${status[1]}); no snapshot kept"
  fi
  record "backup restic snapshot=$snapshot bytes=$processed"
  say "Backup written: restic snapshot $snapshot ($processed bytes before compression)"
  echo "restic:$snapshot"
  exit 0
fi

mkdir -p "$out_dir"
chmod 0700 "$out_dir"
if [ "$plain" -eq 1 ]; then
  host="$(site_value FINPILOT_HOST)"
  [[ "$host" == *.test || "$host" == localhost ]] || die "--unencrypted-local-test is only for local test hosts"
  file="$out_dir/finpilot-$stamp.dump"
  compose run --rm -T backup > "$file.partial"
else
  require_tools age
  [ -s "$recipients" ] || die "no age recipients in $recipients (public keys only)"
  file="$out_dir/finpilot-$stamp.dump.age"
  compose run --rm -T backup | age --encrypt --recipients-file "$recipients" > "$file.partial"
fi
[ -s "$file.partial" ] || die "the dump is empty"
mv "$file.partial" "$file"
record "backup $(basename "$file") bytes=$(wc -c < "$file")"
say "Backup written: $file"
echo "$file"
