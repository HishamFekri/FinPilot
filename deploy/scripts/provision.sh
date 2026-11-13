#!/usr/bin/env bash
# One time, on a new and empty database volume: roles, schema, hardening, verification.
#
#   deploy/scripts/provision.sh --release release.env --confirm-new-database
#
# Order (docs/PRODUCTION_READINESS.md §2.2):
#   1. the schema owner and its database (as the PostgreSQL superuser, inside the network);
#   2. the runtime role, with no rights yet;
#   3. migrations, as the owner;
#   4. grants and row-level security, as the owner;
#   5. verification, as the runtime role: every forbidden action must be refused.
# Each step changes nothing that already exists, and any failure stops the script.
# It doesn't start the application: deploy.sh does.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

release=""
confirmed=0
while [ $# -gt 0 ]; do
  case "$1" in
    --release) release="$2"; shift ;;
    --confirm-new-database) confirmed=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ -n "$release" ] || die "pass --release FILE (the images to provision with)"
[ "$confirmed" -eq 1 ] || die "nothing done: pass --confirm-new-database to provision"
require_pinned_release "$release"
require_tools docker
RELEASE_ENV="$release"
db="$(database_name)"

say "Starting the database only"
compose up -d db
wait_healthy db

say "1. Schema owner and database $db"
compose run --rm db-provision create-owner --database "$db" --owner-approved
say "2. Runtime role"
compose run --rm db-provision create-role --owner-approved
say "3. Migrations, as the owner"
compose run --rm migrate
say "4. Grants and row-level security"
compose run --rm db-apply apply --database "$db" --owner-approved
say "5. Verification, as the runtime role"
compose run --rm db-verify

record "provisioned database=$db"
say "Provisioned. Next: deploy/scripts/deploy.sh --release $release --confirm"
