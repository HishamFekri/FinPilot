#!/usr/bin/env bash
# Restores a backup into a NEW database on the same server. Never into an existing one.
#
#   deploy/scripts/restore.sh --from FILE --into NEW_NAME --confirm [--identity KEYFILE]
#   deploy/scripts/restore.sh --from-restic SNAPSHOT --restic-repo REPO \
#       --restic-password-file FILE --into NEW_NAME --confirm
#
# For restore drills and for recovering from a bad migration. The live database isn't touched:
# a name that already exists is refused. Afterwards grants and RLS are re-applied to the new
# database, and the row counts of every table are shown next to the live database's.
# Switching the application to the restored database is a separate, deliberate step
# (deploy/README.md), never done here.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

from=""
snapshot=""
restic_repo="${FINPILOT_RESTIC_REPOSITORY:-}"
restic_password="${FINPILOT_RESTIC_PASSWORD_FILE:-}"
into=""
identity=""
confirmed=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) from="$2"; shift ;;
    --from-restic) snapshot="$2"; shift ;;
    --restic-repo) restic_repo="$2"; shift ;;
    --restic-password-file) restic_password="$2"; shift ;;
    --into) into="$2"; shift ;;
    --identity) identity="$2"; shift ;;
    --confirm) confirmed=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
if [ -n "$snapshot" ]; then
  [[ "$snapshot" =~ ^[0-9a-f]{8,64}$ ]] || die "--from-restic takes a snapshot ID"
  [ -n "$restic_repo" ] && [ -s "$restic_password" ] \
    || die "a restic restore needs --restic-repo and --restic-password-file"
  from="restic:$snapshot"
else
  [ -f "$from" ] || die "pass --from with an existing backup file, or --from-restic SNAPSHOT"
fi
[[ "$into" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || die "--into must be lowercase letters, digits and _"
live="$(database_name)"
[ "$into" != "$live" ] || die "refusing to restore into the live database $live"
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm to restore $from into $into"
require_tools docker

sql() { compose exec -T db psql -X -q -t -A -U postgres -d "$1" -c "$2"; }
exists="$(sql postgres "SELECT 1 FROM pg_database WHERE datname = '$into'")"
[ -z "$exists" ] || die "database $into already exists. Choose a new name."

say "Creating $into, owned by the schema owner"
compose run --rm db-provision create-owner --database "$into" --owner-approved

say "Restoring"
if [ -n "$snapshot" ]; then
  require_tools restic
  restic --repo "$restic_repo" --password-file "$restic_password" dump "$snapshot" /finpilot.dump \
    | RESTORE_DATABASE="$into" compose run --rm -T restore
elif [[ "$from" == *.age ]]; then
  require_tools age
  [ -f "$identity" ] || die "an encrypted backup needs --identity KEYFILE (the private key)"
  age --decrypt --identity "$identity" "$from" | RESTORE_DATABASE="$into" compose run --rm -T restore
else
  RESTORE_DATABASE="$into" compose run --rm -T restore < "$from"
fi

say "Re-applying grants and row-level security to $into"
compose run --rm db-apply apply --database "$into" --switch-database "$into" --owner-approved

say "Row counts, live ($live) and restored ($into)"
count_sql="SELECT string_agg(relname || '=' || n, ' ' ORDER BY relname) FROM (
  SELECT c.relname, (xpath('/row/n/text()', query_to_xml(
    format('SELECT count(*) AS n FROM %I', c.relname), false, true, '')))[1]::text AS n
  FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
  WHERE s.nspname = 'public' AND c.relkind = 'r') t"
live_counts="$(sql "$live" "$count_sql" | tr ' ' '\n')"
restored_counts="$(sql "$into" "$count_sql" | tr ' ' '\n')"
if diff <(echo "$live_counts") <(echo "$restored_counts") > /dev/null; then
  say "Every table has the same number of rows in both."
else
  echo "Tables whose counts differ (live, then restored). Expected if the live database changed"
  echo "since the backup:"
  diff <(echo "$live_counts") <(echo "$restored_counts") || true
fi
record "restored $(basename "$from") into $into"
say "Restored into $into. The application still uses $live."
