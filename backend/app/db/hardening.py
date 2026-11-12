"""Least-privilege database access: what the running app may do, and which rows it may see.

Two roles, two jobs (SECURITY.md §12):

- The **schema owner** runs migrations and maintenance. It owns every table, so row-level security
  doesn't apply to it. The app never connects as this role in production.
- The **runtime role** is what the app connects as. It owns nothing, so it can't drop or alter a
  table, disable a trigger, change a constraint, or grant itself anything, and RLS applies to it.

Row-level security is a second layer behind the per-user queries in the services. It stops a query
that forgot its `user_id` filter. It does not stop arbitrary SQL injection: the runtime role sets
`app.user_id` itself, so an attacker who can run any statement could choose that value.

This module only builds SQL and checks it. It is deliberately outside the Alembic chain, so
`alembic upgrade head` can't apply it by accident; `scripts/db_hardening.py` does, on request.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
INSUFFICIENT_PRIVILEGE = "42501"

# --- Which rows belong to whom. Every table must appear in exactly one group.

# One person's rows, identified by this column. Nobody else's request can see them.
PERSONAL: dict[str, str] = {
    "budgets": "user_id",
    "categories": "user_id",
    "category_groups": "user_id",
    "debts": "user_id",
    "duplicate_dismissals": "user_id",
    "financial_accounts": "user_id",
    "idempotency_keys": "user_id",
    "installment_items": "user_id",
    "installment_payments": "user_id",
    "installment_plans": "user_id",
    "journal_entries": "user_id",
    "journal_lines": "user_id",
    "ledger_accounts": "user_id",
    "personal_links": "owner_user_id",
    "quick_templates": "user_id",
    "recurring_occurrences": "user_id",
    "recurring_rules": "user_id",
    "savings_goal_events": "user_id",
    "savings_goals": "user_id",
    "transactions": "user_id",
    "trip_personal_budgets": "user_id",
}

# Shared between a trip's members. Finer rules (what a former member sees) stay in the services.
TRIP_SHARED = (
    "expense_shares",
    "settlements",
    "trip_disputes",
    "trip_expense_versions",
    "trip_expenses",
    "trip_invitations",
    "trip_members",
    "trip_plan_items",
    "trips",
)

# Deliberately without RLS, each for a stated reason:
# - needed before anyone is identified: users, user_identities, sessions, oauth_states (found only
#   by a 256-bit token hash or Google subject);
# - users also supplies other trip members' display names;
# - audit_events is written before sign-in finishes, and about other people's sessions;
# - currencies and fx_rates are the same reference data for everyone.
NO_RLS = (
    "alembic_version",
    "audit_events",
    "currencies",
    "fx_rates",
    "oauth_states",
    "sessions",
    "user_identities",
    "users",
)

# --- What the runtime role may do. Grants follow what the services actually run.

READ_ONLY = ("alembic_version", "currencies")
# Insert and read only. The database triggers refuse changes as well: two independent locks.
APPEND_ONLY = (
    "audit_events",
    "expense_shares",
    "fx_rates",
    "journal_entries",
    "journal_lines",
    "savings_goal_events",
)
# The only tables with a code path that deletes rows.
DELETABLE = (
    "budgets",
    "categories",
    "financial_accounts",
    "ledger_accounts",
    "oauth_states",
    "quick_templates",
    "recurring_occurrences",
    "recurring_rules",
    "sessions",
    "trip_personal_budgets",
    "trip_plan_items",
)


def classified_tables() -> set[str]:
    return set(PERSONAL) | set(TRIP_SHARED) | set(NO_RLS)


def unclassified(tables: Iterable[str]) -> set[str]:
    """Tables nobody has decided about. Applying refuses while any exist."""
    return set(tables) - classified_tables()


def check_role_name(role: str) -> str:
    if not ROLE_NAME.fullmatch(role):
        raise ValueError(
            "Role and database names must be lowercase letters, digits and underscores"
        )
    return role


# --- SQL builders


def role_sql(runtime_role: str, verifier: str, *, connection_limit: int = 20) -> str:
    """Run once by an administrator. `verifier` is a SCRAM verifier, never a plain password."""
    check_role_name(runtime_role)
    return (
        f"CREATE ROLE {runtime_role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS NOINHERIT CONNECTION LIMIT {connection_limit} PASSWORD '{verifier}';"
    )


def owner_role_sql(owner_role: str, verifier: str, *, connection_limit: int = 5) -> str:
    """The schema owner: no server-wide rights. It owns one database, and nothing else."""
    check_role_name(owner_role)
    return (
        f"CREATE ROLE {owner_role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS CONNECTION LIMIT {connection_limit} PASSWORD '{verifier}';"
    )


def database_sql(database: str, owner_role: str) -> str:
    """A new, empty database owned by the schema owner. Nobody else may connect until `apply`."""
    db = check_role_name(database)
    owner = check_role_name(owner_role)
    return f"CREATE DATABASE {db} OWNER {owner} ENCODING 'UTF8' TEMPLATE template0"


def grants_sql(database: str, runtime_role: str, tables: Iterable[str]) -> list[str]:
    """Run by the schema owner. Replaces whatever the role had, so re-running is safe."""
    role = check_role_name(runtime_role)
    db = check_role_name(database)
    names = sorted(tables)
    statements = [
        f"REVOKE ALL ON DATABASE {db} FROM PUBLIC",
        f"GRANT CONNECT ON DATABASE {db} TO {role}",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"GRANT USAGE ON SCHEMA public TO {role}",
    ]
    for table in names:
        if table in READ_ONLY:
            grant = "SELECT"
        elif table in APPEND_ONLY:
            grant = "SELECT, INSERT"
        elif table in DELETABLE:
            grant = "SELECT, INSERT, UPDATE, DELETE"
        else:
            grant = "SELECT, INSERT, UPDATE"
        statements.append(f"GRANT {grant} ON {table} TO {role}")
    return statements


CURRENT_USER = "(SELECT finpilot_current_user())"
INVITE_HASH = "decode(NULLIF(current_setting('app.invite_token_hash', true), ''), 'hex')"
ANY_MEMBER = "finpilot_trip_member_status(trip_id) IS NOT NULL"
ACTIVE_MEMBER = "finpilot_trip_member_status(trip_id) = 'active'"

_HELPERS = f"""
CREATE OR REPLACE FUNCTION finpilot_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION finpilot_trip_member_status(target uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT status FROM trip_members
    WHERE trip_id = target AND user_id = finpilot_current_user()
$$;

CREATE OR REPLACE FUNCTION finpilot_trip_invited(target uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM trip_invitations
        WHERE trip_id = target AND token_hash = {INVITE_HASH}
    )
$$;

CREATE OR REPLACE FUNCTION finpilot_trip_created_by_me(target uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM trips WHERE id = target AND created_by = finpilot_current_user()
    )
$$;
"""  # noqa: S608 - built only from constants

_HELPER_SIGNATURES = (
    "finpilot_current_user()",
    "finpilot_trip_member_status(uuid)",
    "finpilot_trip_invited(uuid)",
    "finpilot_trip_created_by_me(uuid)",
)

# Table -> {command: (USING, WITH CHECK)}. Only what the grants above allow at all.
_MEMBER_READ = (ANY_MEMBER, None)
_TRIP_POLICIES: dict[str, dict[str, tuple[str | None, str | None]]] = {
    "trips": {
        "SELECT": (
            f"created_by = {CURRENT_USER} OR finpilot_trip_member_status(id) IS NOT NULL "
            "OR finpilot_trip_invited(id)",
            None,
        ),
        "INSERT": (None, f"created_by = {CURRENT_USER}"),
        # USING also governs row locks, which a former member's actions take. WITH CHECK is what
        # stops them changing the trip itself.
        "UPDATE": (
            "finpilot_trip_member_status(id) IS NOT NULL OR finpilot_trip_invited(id)",
            "finpilot_trip_member_status(id) = 'active' OR finpilot_trip_invited(id)",
        ),
    },
    "trip_members": {
        "SELECT": (f"{ANY_MEMBER} OR user_id = {CURRENT_USER}", None),
        # Only yourself, and only into a trip you created or hold an invitation to.
        "INSERT": (
            None,
            f"user_id = {CURRENT_USER} AND "
            "(finpilot_trip_created_by_me(trip_id) OR finpilot_trip_invited(trip_id))",
        ),
        "UPDATE": (f"{ACTIVE_MEMBER} OR user_id = {CURRENT_USER}", None),
    },
    "trip_invitations": {
        "SELECT": (f"{ACTIVE_MEMBER} OR token_hash = {INVITE_HASH}", None),
        "INSERT": (None, ACTIVE_MEMBER),
        "UPDATE": (f"{ACTIVE_MEMBER} OR token_hash = {INVITE_HASH}", None),
    },
    "trip_expenses": {
        "SELECT": _MEMBER_READ,
        "INSERT": (None, ACTIVE_MEMBER),
        # Row locks need this. The immutability trigger still refuses any real change.
        "UPDATE": (ANY_MEMBER, None),
    },
    "trip_expense_versions": {
        "SELECT": _MEMBER_READ,
        "INSERT": (None, ACTIVE_MEMBER),
        "UPDATE": (ANY_MEMBER, None),  # a former payer may still approve or reject
    },
    "expense_shares": {"SELECT": _MEMBER_READ, "INSERT": (None, ACTIVE_MEMBER)},
    "settlements": {
        "SELECT": _MEMBER_READ,
        "INSERT": (None, ANY_MEMBER),  # someone who left can still settle what they owe
        "UPDATE": (ANY_MEMBER, None),
    },
    "trip_disputes": {
        "SELECT": _MEMBER_READ,
        "INSERT": (None, ANY_MEMBER),
        "UPDATE": (ANY_MEMBER, None),
    },
    "trip_plan_items": {
        "SELECT": _MEMBER_READ,
        "INSERT": (None, ACTIVE_MEMBER),
        "UPDATE": (ACTIVE_MEMBER, None),
        "DELETE": (ACTIVE_MEMBER, None),
    },
}


def policy_name(table: str, command: str) -> str:
    return f"finpilot_{table}_{command.lower()}"


def _policy(table: str, command: str, using: str | None, check: str | None) -> list[str]:
    name = policy_name(table, command)
    clauses = f" USING ({using})" if using else ""
    clauses += f" WITH CHECK ({check})" if check else ""
    return [
        f"DROP POLICY IF EXISTS {name} ON {table}",
        f"CREATE POLICY {name} ON {table} FOR {command} TO PUBLIC{clauses}",
    ]


def rls_sql(runtime_role: str) -> list[str]:
    """Run by the schema owner. Idempotent. FORCE is left off so the owner remains maintenance."""
    role = check_role_name(runtime_role)
    statements = [_HELPERS]
    for signature in _HELPER_SIGNATURES:
        statements += [
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION {signature} TO {role}",
        ]
    for table, column in sorted(PERSONAL.items()):
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        rule = f"{column} = {CURRENT_USER}"
        statements += _policy(table, "ALL", rule, rule)
    for table in TRIP_SHARED:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for command, (using, check) in _TRIP_POLICIES[table].items():
            statements += _policy(table, command, using, check)
    return statements


def public_tables(connection: Connection) -> list[str]:
    return list(
        connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        ).scalars()
    )


def apply_hardening(engine: Engine, database: str, runtime_role: str) -> None:
    """Grants and policies, in one transaction, as the schema owner. All or nothing."""
    with engine.begin() as connection:
        tables = public_tables(connection)
        unknown = unclassified(tables)
        if unknown:
            raise ValueError(f"Tables with no security decision: {sorted(unknown)}")
        for statement in [*grants_sql(database, runtime_role, tables), *rls_sql(runtime_role)]:
            connection.execute(text(statement))


# --- Verification: try the forbidden things, as the runtime role, and undo whatever happens.


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool


_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("can't drop a financial table", "DROP TABLE journal_entries CASCADE"),
    ("can't drop a personal table", "DROP TABLE transactions CASCADE"),
    (
        "can't disable the ledger's triggers",
        "ALTER TABLE journal_lines DISABLE TRIGGER trg_journal_lines_append_only",
    ),
    ("can't disable every trigger", "ALTER TABLE transactions DISABLE TRIGGER ALL"),
    ("can't turn RLS off", "ALTER TABLE transactions DISABLE ROW LEVEL SECURITY"),
    ("can't change a constraint", "ALTER TABLE transactions DROP CONSTRAINT pk_transactions"),
    ("can't add a column", "ALTER TABLE transactions ADD COLUMN x int"),
    ("can't replace a policy", "DROP POLICY finpilot_transactions_all ON transactions"),
    ("can't create a table", "CREATE TABLE finpilot_probe (id int)"),
    ("can't create a schema", "CREATE SCHEMA finpilot_probe"),
    ("can't empty the journal", "TRUNCATE journal_lines"),
    ("can't delete from the journal", "DELETE FROM journal_lines"),
    ("can't rewrite the journal", "UPDATE journal_entries SET user_id = user_id"),
    ("can't delete audit history", "DELETE FROM audit_events"),
    ("can't delete transactions", "DELETE FROM transactions"),
    ("can't change currencies", "DELETE FROM currencies"),
    ("can't create a role", "CREATE ROLE finpilot_probe"),
    ("can't skip triggers", "SET session_replication_role = replica"),
    ("can't read server files", "COPY users TO PROGRAM 'cmd'"),
)


def _refused(connection: Connection, statement: str) -> bool:
    """True only for a real permission refusal. Any other error doesn't prove anything."""
    savepoint = connection.begin_nested()
    try:
        connection.execute(text(statement))
    except DBAPIError as exc:
        savepoint.rollback()
        return getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
    savepoint.rollback()
    return False


def verify_runtime(engine: Engine) -> list[Check]:
    """Everything runs in a transaction that is rolled back. Reports names and booleans only."""
    checks: list[Check] = []
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            attributes = connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            checks.append(Check("role has no server-wide privileges", not any(attributes)))
            owned = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tableowner = current_user"
                )
            )
            checks.append(Check("role owns no table", owned == 0))
            database_owner = connection.scalar(
                text(
                    "SELECT pg_get_userbyid(datdba) = current_user FROM pg_database "
                    "WHERE datname = current_database()"
                )
            )
            checks.append(Check("role doesn't own the database", not database_owner))
            inherited = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
                    "WHERE r.rolname = current_user"
                )
            )
            checks.append(Check("role is a member of no other role", inherited == 0))
            owner = connection.scalar(
                text("SELECT tableowner FROM pg_tables WHERE tablename = 'journal_entries'")
            )
            checks.append(
                Check(
                    "can't become the schema owner",
                    _refused(connection, f'SET ROLE "{owner}"'),
                )
            )
            checks.append(
                Check(
                    "can't give itself BYPASSRLS",
                    _refused(connection, "ALTER ROLE CURRENT_USER BYPASSRLS"),
                )
            )
            for name, statement in _FORBIDDEN:
                checks.append(Check(name, _refused(connection, statement)))
            # A user holding some rights gets only a warning from GRANT, so check the outcome.
            savepoint = connection.begin_nested()
            try:
                connection.execute(text("GRANT DELETE ON journal_lines TO CURRENT_USER"))
            except DBAPIError:
                pass
            finally:
                savepoint.rollback()
            checks.append(
                Check(
                    "can't grant itself more",
                    connection.scalar(
                        text("SELECT has_table_privilege(current_user, 'journal_lines', 'DELETE')")
                    )
                    is False,
                )
            )
            protected = [*PERSONAL, *TRIP_SHARED]
            enabled: set[str] = set(
                connection.execute(
                    text(
                        "SELECT relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relrowsecurity"
                    )
                ).scalars()
            )
            checks.append(Check("RLS is on for every protected table", set(protected) <= enabled))
            connection.execute(text("SELECT set_config('app.user_id', '', true)"))
            for table in protected:
                count = connection.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                checks.append(Check(f"no identity, no rows: {table}", count == 0))
        finally:
            transaction.rollback()
    return checks


def is_hardened(connection: Connection) -> bool:
    """For readiness in production: not a privileged role, and RLS is really on."""
    privileged = connection.scalar(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
    )
    owned = connection.scalar(
        text(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
            "AND tableowner = current_user"
        )
    )
    enabled: set[str] = set(
        connection.execute(
            text(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relrowsecurity"
            )
        ).scalars()
    )
    return not privileged and owned == 0 and {*PERSONAL, *TRIP_SHARED} <= enabled
