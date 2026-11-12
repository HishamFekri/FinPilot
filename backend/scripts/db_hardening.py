"""Set up and verify least-privilege database access. Connects only when told to.

    uv run python -m scripts.db_hardening print-sql --database finpilot_x
    uv run python -m scripts.db_hardening create-owner --database finpilot_x
    uv run python -m scripts.db_hardening create-role --runtime-role finpilot_app
    uv run python -m scripts.db_hardening apply --database finpilot_x --runtime-role finpilot_app
    uv run python -m scripts.db_hardening verify

Connection URLs and passwords come from the environment: ADMIN_DATABASE_URL (a role able to create
roles), OWNER_DATABASE_URL (the schema owner), RUNTIME_DATABASE_URL, OWNER_ROLE_PASSWORD and
RUNTIME_ROLE_PASSWORD. Each can instead be a file, named by `NAME_FILE` or found as `name` in
FINPILOT_SECRETS_DIR (Docker secrets). No URL, password or verifier is ever printed.

Outside databases named *_test, `create-owner`, `create-role` and `apply` also need
--owner-approved. This script never changes who owns an existing object, never drops anything,
and never deletes data. `create-owner` and `create-role` change nothing that already exists.

See docs/SECURITY.md §12 for the order to do this in.
"""

import argparse
import sys

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import read_secret
from app.db import hardening
from scripts.provision_local_db import scram_verifier


def _url(variable: str) -> str:
    url = read_secret(variable)
    if not url:
        raise SystemExit(f"{variable} isn't set.")
    return url


def _engine(variable: str, *, autocommit: bool = False) -> Engine:
    if autocommit:
        return create_engine(
            _url(variable), poolclass=NullPool, hide_parameters=True, isolation_level="AUTOCOMMIT"
        )
    return create_engine(_url(variable), poolclass=NullPool, hide_parameters=True)


def _password(variable: str) -> str:
    password = read_secret(variable)
    if not password or len(password) < 24:
        raise SystemExit(f"{variable} must be set, with at least 24 characters.")
    return password


def _database_name(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT current_database()")))


def _allow(database: str, *, confirm: str | None, owner_approved: bool) -> None:
    """Trusts what the server reports, and needs the name typed, so a wrong URL stops here."""
    if confirm != database:
        raise SystemExit(f"Connected to {database!r}. Repeat that name with --database to go on.")
    if not database.endswith("_test") and not owner_approved:
        raise SystemExit(f"{database!r} isn't a test database. This needs --owner-approved.")


def print_sql(args: argparse.Namespace) -> int:
    print("-- 1. As an administrator, once:")
    print(hardening.role_sql(args.runtime_role, "<SCRAM verifier generated at run time>"))
    print("\n-- 2. As the schema owner, after migrations:")
    tables = sorted(hardening.classified_tables())
    for statement in [
        *hardening.grants_sql(args.database, args.runtime_role, tables),
        *hardening.rls_sql(args.runtime_role),
    ]:
        print(f"{statement.strip()};")
    return 0


def create_owner(args: argparse.Namespace) -> int:
    """The schema owner and its database, on a new server. Run once, as an administrator."""
    owner = hardening.check_role_name(args.owner_role)
    database = hardening.check_role_name(args.database)
    if not database.endswith("_test") and not args.owner_approved:
        raise SystemExit(f"{database!r} isn't a test database. This needs --owner-approved.")
    password = _password("OWNER_ROLE_PASSWORD")
    # CREATE DATABASE can't run inside a transaction.
    engine = _engine("ADMIN_DATABASE_URL", autocommit=True)
    with engine.connect() as connection:
        role_exists = connection.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": owner}
        )
        if role_exists:
            print(f"Role {owner} already exists. It was not changed.")
        else:
            try:
                connection.exec_driver_sql(
                    hardening.owner_role_sql(owner, scram_verifier(password))
                )
            except SQLAlchemyError as exc:
                # A database error repeats the statement, and this one holds the verifier.
                raise SystemExit(f"Creating the owner failed: {type(exc).__name__}.") from None
            print(f"Created role {owner}. No secret was printed.")
        current_owner = connection.scalar(
            text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :name"),
            {"name": database},
        )
        if current_owner is None:
            connection.execute(text(hardening.database_sql(database, owner)))
            print(f"Created database {database}, owned by {owner}.")
        elif current_owner != owner:
            # Never reassigned: that would hand another role's data to this one.
            raise SystemExit(f"Database {database} exists and is owned by another role. Stopped.")
        else:
            print(f"Database {database} already exists, owned by {owner}. It was not changed.")
    engine.dispose()
    return 0


def create_role(args: argparse.Namespace) -> int:
    password = _password("RUNTIME_ROLE_PASSWORD")
    engine = _engine("ADMIN_DATABASE_URL")
    database = _database_name(engine)
    if not database.endswith("_test") and not args.owner_approved:
        raise SystemExit("Creating a role on a server with real data needs --owner-approved.")
    with engine.begin() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": args.runtime_role}
        )
        if exists:
            print(f"Role {args.runtime_role} already exists. Nothing was changed.")
            return 0
        try:
            # exec_driver_sql: text() would read the verifier's colons as bind parameters.
            connection.exec_driver_sql(
                hardening.role_sql(args.runtime_role, scram_verifier(password))
            )
        except SQLAlchemyError as exc:
            # A database error repeats the statement, and this one holds the verifier.
            raise SystemExit(f"Creating the role failed: {type(exc).__name__}.") from None
    print(f"Created role {args.runtime_role}. No secret was printed.")
    return 0


def apply(args: argparse.Namespace) -> int:
    if args.switch_database:
        # For a restore check: the same owner, another database on the same server. Still
        # confirmed below against what the server reports.
        url = make_url(_url("OWNER_DATABASE_URL")).set(
            database=hardening.check_role_name(args.switch_database)
        )
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    else:
        engine = _engine("OWNER_DATABASE_URL")
    database = _database_name(engine)
    _allow(database, confirm=args.database, owner_approved=args.owner_approved)
    hardening.apply_hardening(engine, database, args.runtime_role)
    print(f"Applied grants and row-level security to {database}.")
    return 0


def verify(_args: argparse.Namespace) -> int:
    engine = _engine("RUNTIME_DATABASE_URL")
    # The URL's role, compared by name only, and never printed with its password.
    print(f"Verifying as role: {make_url(_url('RUNTIME_DATABASE_URL')).username}")
    checks = hardening.verify_runtime(engine)
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'}  {check.name}")
    failed = sum(not check.passed for check in checks)
    print(f"{len(checks) - failed} passed, {failed} failed.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("print-sql", help="show the SQL, connect to nothing")
    show.add_argument("--database", required=True)
    show.add_argument("--runtime-role", default="finpilot_app")
    show.set_defaults(run=print_sql)

    owner = commands.add_parser(
        "create-owner", help="create the schema owner and its database, as an administrator"
    )
    owner.add_argument("--database", required=True)
    owner.add_argument("--owner-role", default="finpilot_owner")
    owner.add_argument("--owner-approved", action="store_true")
    owner.set_defaults(run=create_owner)

    role = commands.add_parser("create-role", help="create the runtime role, as an administrator")
    role.add_argument("--runtime-role", default="finpilot_app")
    role.add_argument("--owner-approved", action="store_true")
    role.set_defaults(run=create_role)

    harden = commands.add_parser("apply", help="grants and RLS, as the schema owner")
    harden.add_argument("--database", required=True, help="must match the connected database")
    harden.add_argument("--runtime-role", default="finpilot_app")
    harden.add_argument("--owner-approved", action="store_true")
    harden.add_argument(
        "--switch-database", help="connect to this database instead of the one in the URL"
    )
    harden.set_defaults(run=apply)

    check = commands.add_parser("verify", help="try the forbidden things, as the runtime role")
    check.set_defaults(run=verify)

    args = parser.parse_args()
    return int(args.run(args))


if __name__ == "__main__":
    sys.exit(main())
