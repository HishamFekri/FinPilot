"""The restricted runtime role and row-level security, on a private throwaway PostgreSQL server.

The app really connects as the runtime role here, through the real routes, so a policy that's too
tight fails as visibly as one that's too loose.
"""

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

import app.db.models  # noqa: F401 - registers the tables
from app.core.config import BACKEND_DIR
from app.db import hardening
from app.db.base import Base
from scripts import db_hardening
from tests.pg_cluster import DATABASE, RUNTIME, Cluster, find_binaries
from tests.test_fx import providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _setup
from tests.test_security_hardening import PRIVATE, _private_world, spend
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures

RLS_REFUSED = "42501"


@pytest.fixture(scope="module")
def cluster() -> Iterator[Cluster]:
    binaries = find_binaries()
    if binaries is None:
        pytest.skip("PostgreSQL's initdb isn't installed, so no private server can start")
    server = Cluster(binaries)
    saved = {k: os.environ.get(k) for k in ("TEST_RUNTIME_DATABASE_URL", "TEST_RUNTIME_ROLE")}
    try:
        server.start()
        server.provision()
        os.environ["TEST_RUNTIME_DATABASE_URL"] = server.runtime_url
        os.environ["TEST_RUNTIME_ROLE"] = RUNTIME
        yield server
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.stop()


@pytest.fixture(scope="module")
def test_database_url(cluster: Cluster) -> str:
    """Replaces the shared test database for this module only."""
    return cluster.owner_url


@pytest.fixture(scope="module")
def runtime(cluster: Cluster, auth_engine: Engine) -> Iterator[Engine]:
    engine = create_engine(cluster.runtime_url, poolclass=NullPool)
    yield engine
    engine.dispose()


def as_person(engine: Engine, user_id: str | None, sql: str, **params: Any) -> list[Any]:
    with engine.begin() as connection:
        if user_id:
            connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": user_id})
        return list(connection.execute(text(sql), params))


def count(engine: Engine, user_id: str | None, table: str, where: str = "true", **p: Any) -> int:
    return int(as_person(engine, user_id, f"SELECT count(*) FROM {table} WHERE {where}", **p)[0][0])


def refused_by_rls(engine: Engine, user_id: str, sql: str, **params: Any) -> bool:
    try:
        as_person(engine, user_id, sql, **params)
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None) == RLS_REFUSED
    return False


# --- The classification itself, which needs no server


def test_every_table_has_a_security_decision() -> None:
    assert hardening.unclassified(Base.metadata.tables) == set()


def test_no_table_is_in_two_groups() -> None:
    groups = [set(hardening.PERSONAL), set(hardening.TRIP_SHARED), set(hardening.NO_RLS)]
    assert sum(len(g) for g in groups) == len(set.union(*groups))


def test_each_personal_table_really_has_its_owner_column() -> None:
    for table, column in hardening.PERSONAL.items():
        assert column in Base.metadata.tables[table].c, table


def test_every_trip_shared_table_has_a_policy_for_each_thing_the_role_may_do() -> None:
    sql = "\n".join(hardening.rls_sql("finpilot_app"))
    for table in hardening.TRIP_SHARED:
        assert f"{hardening.policy_name(table, 'SELECT')} ON {table}" in sql, table


def test_the_append_only_tables_get_no_update_or_delete() -> None:
    statements = hardening.grants_sql("x_test", "finpilot_app", hardening.APPEND_ONLY)
    grants = [s for s in statements if s.startswith("GRANT SELECT")]
    assert len(grants) == len(hardening.APPEND_ONLY)
    assert all("UPDATE" not in s and "DELETE" not in s for s in grants)


@pytest.mark.parametrize("bad", ["x; DROP ROLE y", "Robert", 'a"b', ""])
def test_role_names_cant_carry_sql(bad: str) -> None:
    with pytest.raises(ValueError, match="lowercase"):
        hardening.role_sql(bad, "v")


# --- The script's safety catches


def test_the_script_needs_the_database_named_exactly() -> None:
    with pytest.raises(SystemExit, match="Repeat that name"):
        db_hardening._allow("finpilot_test", confirm="finpilot_other", owner_approved=False)


@pytest.mark.parametrize("name", ["finpilot_dev", "finpilot", "production"])
def test_the_script_wont_touch_a_non_test_database_without_approval(name: str) -> None:
    with pytest.raises(SystemExit, match="owner-approved"):
        db_hardening._allow(name, confirm=name, owner_approved=False)


def test_printing_the_sql_connects_to_nothing_and_holds_no_secret(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        db_hardening, "create_engine", lambda *a, **k: pytest.fail("connected to a database")
    )
    monkeypatch.setattr(sys, "argv", ["x", "print-sql", "--database", "x_test"])
    assert db_hardening.main() == 0
    out = capsys.readouterr().out
    assert "CREATE POLICY" in out and "<SCRAM verifier" in out and "SCRAM-SHA-256$" not in out


def _script(module: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own interpreter and arguments
        [sys.executable, "-m", module, *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "PYTHONUTF8": "1", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# --- What the runtime role can't do


def test_the_verification_script_passes_and_prints_no_secret(
    cluster: Cluster, people: list[Person]
) -> None:
    alice, _, carol, _ = people
    _private_world(alice, carol)
    result = _script("scripts.db_hardening", "verify", RUNTIME_DATABASE_URL=cluster.runtime_url)
    assert result.returncode == 0, result.stdout[-1500:]
    assert " 0 failed" in result.stdout
    for secret in (cluster.runtime_password, cluster.owner_password, cluster.admin_password):
        assert secret not in result.stdout + result.stderr


def test_applying_twice_changes_nothing_and_still_verifies(
    cluster: Cluster, auth_engine: Engine, runtime: Engine
) -> None:
    hardening.apply_hardening(auth_engine, DATABASE, RUNTIME)
    hardening.apply_hardening(auth_engine, DATABASE, RUNTIME)
    assert [c.name for c in hardening.verify_runtime(runtime) if not c.passed] == []


def test_verification_notices_a_role_with_too_much_power(cluster: Cluster) -> None:
    """The check itself must be able to fail: the schema owner fails it."""
    owner = create_engine(cluster.owner_url, poolclass=NullPool)
    failed = {c.name for c in hardening.verify_runtime(owner) if not c.passed}
    owner.dispose()
    assert "role owns no table" in failed
    assert "can't drop a financial table" in failed
    assert "can't disable the ledger's triggers" in failed


def test_applying_refuses_a_table_nobody_classified(auth_engine: Engine) -> None:
    with auth_engine.begin() as connection:
        connection.execute(text("CREATE TABLE new_undecided_table (id int)"))
    try:
        with pytest.raises(ValueError, match="no security decision"):
            hardening.apply_hardening(auth_engine, DATABASE, RUNTIME)
    finally:
        with auth_engine.begin() as connection:
            connection.execute(text("DROP TABLE new_undecided_table"))


def test_readiness_recognises_the_hardened_role_and_refuses_the_owner(
    runtime: Engine, auth_engine: Engine
) -> None:
    with runtime.connect() as connection:
        assert hardening.is_hardened(connection)
    with auth_engine.connect() as connection:
        assert not hardening.is_hardened(connection)


def test_rls_cant_be_switched_off_for_a_session(people: list[Person], runtime: Engine) -> None:
    alice, *_ = people
    _setup(alice)
    with pytest.raises(DBAPIError) as caught, runtime.begin() as connection:
        connection.execute(text("SET LOCAL row_security = off"))
        connection.execute(text("SELECT count(*) FROM financial_accounts"))
    assert getattr(caught.value.orig, "sqlstate", None) == RLS_REFUSED


def test_a_nonsense_identity_is_an_error_and_never_a_match(runtime: Engine) -> None:
    with pytest.raises(DBAPIError):
        as_person(runtime, "' OR 1=1 --", "SELECT count(*) FROM transactions")


def test_identity_doesnt_outlive_its_transaction(people: list[Person], runtime: Engine) -> None:
    alice, *_ = people
    with runtime.connect() as connection:
        with connection.begin():
            connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": alice.id})
        with connection.begin():
            value = connection.scalar(text("SELECT current_setting('app.user_id', true)"))
    assert not value


# --- Personal data


def test_a_person_sees_only_their_own_rows_in_every_personal_table(
    people: list[Person], runtime: Engine, auth_engine: Engine
) -> None:
    alice, bob, carol, _ = people
    trip = _private_world(alice, carol)
    account = _setup(bob)
    spend(bob, account, "5.00", "Bob's")
    assert trip
    populated = 0
    for table, column in hardening.PERSONAL.items():
        everyone = count(auth_engine, None, table)
        alices = count(auth_engine, None, table, f"{column} = :u", u=alice.id)
        populated += bool(alices and everyone > alices)
        assert count(runtime, None, table) == 0, f"{table}: visible with no identity"
        assert count(runtime, alice.id, table) == alices, table
        assert count(runtime, alice.id, table, f"{column} <> :u", u=alice.id) == 0, table
        assert count(runtime, bob.id, table, f"{column} = :u", u=alice.id) == 0, table
    assert populated >= 5  # otherwise this test proved nothing


def test_nobody_can_write_into_someone_elses_records(
    people: list[Person], runtime: Engine, auth_engine: Engine
) -> None:
    alice, bob, *_ = people
    _setup(alice)
    with runtime.begin() as connection:
        connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": bob.id})
        changed = connection.execute(
            text("UPDATE financial_accounts SET name = 'stolen' WHERE user_id = :u"),
            {"u": alice.id},
        )
        assert changed.rowcount == 0
    assert count(auth_engine, None, "financial_accounts", "name = 'stolen'") == 0
    assert refused_by_rls(
        runtime,
        bob.id,
        "INSERT INTO category_groups (user_id, name) VALUES (:u, 'x')",
        u=alice.id,
    )


# --- Trips


def test_a_member_sees_the_trip_and_a_stranger_sees_none_of_it(
    people: list[Person], runtime: Engine, auth_engine: Engine
) -> None:
    alice, bob, carol, _ = people
    trip = _private_world(alice, carol)
    for table in hardening.TRIP_SHARED:
        column = "id" if table == "trips" else "trip_id"
        where = f"{column} = :t"
        assert count(runtime, bob.id, table, where, t=trip) == 0, table
        assert count(runtime, None, table, where, t=trip) == 0, table
        if table != "trip_invitations":  # used invitations exist, and are for active members
            total = count(auth_engine, None, table, where, t=trip)
            assert count(runtime, carol.id, table, where, t=trip) == total, table


def test_private_budgets_and_personal_links_stay_private_inside_a_trip(
    people: list[Person], runtime: Engine
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol)
    assert count(runtime, alice.id, "trip_personal_budgets", "trip_id = :t", t=trip) == 1
    assert count(runtime, carol.id, "trip_personal_budgets", "trip_id = :t", t=trip) == 0
    assert count(runtime, carol.id, "personal_links", "owner_user_id = :u", u=alice.id) == 0


def test_a_former_member_can_read_history_but_cant_add_to_the_trip(
    people: list[Person], runtime: Engine
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol, shared=False)
    _ok(carol.post(f"/trips/{trip}/leave", key=False), 204)
    assert count(runtime, carol.id, "trips", "id = :t", t=trip) == 1
    assert count(runtime, carol.id, "trip_invitations", "trip_id = :t", t=trip) == 0
    assert refused_by_rls(
        runtime,
        carol.id,
        "INSERT INTO trip_expenses (trip_id, payer_user_id, created_by) VALUES (:t, :u, :u)",
        t=trip,
        u=carol.id,
    )
    assert refused_by_rls(
        runtime,
        carol.id,
        "INSERT INTO trip_invitations (trip_id, token_hash, invited_by, expires_at) "
        "VALUES (:t, decode(repeat('ab', 32), 'hex'), :u, now() + interval '1 day')",
        t=trip,
        u=carol.id,
    )
    response = alice.get(f"/trips/{trip}")
    assert response.status_code == 200


def test_nobody_can_add_themselves_to_a_trip_without_an_invitation(
    people: list[Person], runtime: Engine
) -> None:
    alice, bob, carol, _ = people
    trip = _private_world(alice, carol)
    assert refused_by_rls(
        runtime,
        bob.id,
        "INSERT INTO trip_members (trip_id, user_id, role) VALUES (:t, :u, 'owner')",
        t=trip,
        u=bob.id,
    )


def test_an_invitation_opens_only_with_its_own_token(people: list[Person], runtime: Engine) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, members=[carol])
    token = _ok(alice.post(f"/trips/{trip}/invitations", {"label": "Bob"}, key=False), 201)["token"]
    digest = hashlib.sha256(token.encode()).hexdigest()

    def visible(hash_value: str) -> tuple[int, int]:
        with runtime.begin() as connection:
            for name, value in (("app.user_id", bob.id), ("app.invite_token_hash", hash_value)):
                connection.execute(text("SELECT set_config(:n, :v, true)"), {"n": name, "v": value})
            return (
                int(connection.scalar(text("SELECT count(*) FROM trip_invitations")) or 0),
                int(connection.scalar(text("SELECT count(*) FROM trips")) or 0),
            )

    assert visible("") == (0, 0)
    assert visible("ab" * 32) == (0, 0)
    assert visible(digest) == (1, 1)
    # And the real route, as the real restricted role, end to end.
    _ok(bob.post("/invitations/accept", {"token": token}, key=False))
    assert count(runtime, bob.id, "trips", "id = :t", t=trip) == 1


# --- Everything still works, as the restricted role


def test_ordinary_money_flows_work_as_the_restricted_role(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, bob, carol, _ = people
    trip = _private_world(alice, carol)
    assert _plan(bob, _setup(bob))  # installments create a purchase, a debt and a schedule
    _expense(alice, trip, alice, "10.00", [alice, carol])
    for path, params in (
        ("/accounts", {}),
        ("/transactions", {}),
        ("/reports", {"from": "2026-01-01", "to": "2026-12-31"}),
        ("/analytics/intelligence", {}),
        ("/installments", {}),
        ("/reports/export.csv", {"from": "2026-01-01", "to": "2026-12-31"}),
        (f"/trips/{trip}", {}),
        (f"/trips/{trip}/balances", {}),
        (f"/trips/{trip}/reports/export.csv", {}),
        ("/me/data-export", {}),
        ("/me/deletion-request", {}),
    ):
        response = alice.c.get(f"{API}{path}", params=params)
        assert response.status_code < 500, (path, response.text[:200])
        assert response.status_code != 403, (path, response.text[:200])
    assert PRIVATE in alice.c.get(f"{API}/me/data-export").text


def test_reports_really_contain_data_as_the_restricted_role(people: list[Person]) -> None:
    """Reports open their own session. Without the identity, RLS would quietly make them empty."""
    alice, _, carol, _ = people
    trip = _private_world(alice, carol)
    period = {"from": "2026-01-01", "to": "2026-12-31"}
    report = _ok(alice.c.get(f"{API}/reports", params=period))
    assert report["by_currency"], "the report came back empty"
    assert PRIVATE in alice.c.get(f"{API}/reports/export.csv", params=period).text
    assert "Dinner" in alice.c.get(f"{API}/trips/{trip}/reports/summary").text


def test_a_former_member_can_lock_but_not_change_the_trip(
    people: list[Person], runtime: Engine
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol, shared=False)
    _ok(carol.post(f"/trips/{trip}/leave", key=False), 204)
    assert refused_by_rls(runtime, carol.id, "UPDATE trips SET name = 'x' WHERE id = :t", t=trip)
    assert _ok(carol.get(f"/trips/{trip}/reports/summary"))


def test_the_owner_keeps_the_maintenance_path(people: list[Person], auth_engine: Engine) -> None:
    """Migrations and maintenance run as the owner, which RLS deliberately doesn't apply to."""
    alice, *_ = people
    _setup(alice)
    assert count(auth_engine, None, "financial_accounts") > 0


# --- Erasure's script


def test_the_erase_script_refuses_anything_but_a_test_database(cluster: Cluster) -> None:
    result = _script(
        "scripts.erase_account",
        "--user-id", "00000000-0000-4000-8000-000000000000", "--grace-days", "0",
        OWNER_DATABASE_URL=cluster.admin_url,  # the "postgres" database: not a test database
    )  # fmt: skip
    assert result.returncode == 2
    assert "Refusing" in result.stdout
    assert cluster.admin_password not in result.stdout + result.stderr


# --- Provisioning a new production database


def test_create_owner_makes_a_powerless_owner_and_its_database_once(
    cluster: Cluster, tmp_path: Any
) -> None:
    password = "o" * 12 + os.urandom(12).hex()
    secret = tmp_path / "owner_role_password"
    secret.write_text(password, encoding="utf-8")
    args = ("create-owner", "--database", "finpilot_provision_test", "--owner-role", "fp_new_owner")
    env = {"ADMIN_DATABASE_URL": cluster.admin_url, "OWNER_ROLE_PASSWORD_FILE": str(secret)}

    first = _script("scripts.db_hardening", *args, **env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Created role fp_new_owner" in first.stdout
    again = _script("scripts.db_hardening", *args, **env)
    assert again.returncode == 0 and "already exists" in again.stdout
    for output in (first, again):
        assert password not in output.stdout + output.stderr
        assert cluster.admin_password not in output.stdout + output.stderr
        assert "SCRAM-SHA-256$" not in output.stdout + output.stderr

    admin = create_engine(cluster.admin_url, poolclass=NullPool)
    with admin.connect() as connection:
        powers = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = 'fp_new_owner'"
            )
        ).one()
        owner = connection.scalar(
            text(
                "SELECT pg_get_userbyid(datdba) FROM pg_database "
                "WHERE datname = 'finpilot_provision_test'"
            )
        )
    admin.dispose()
    assert not any(powers) and owner == "fp_new_owner"
    url = make_url(cluster.admin_url).set(
        username="fp_new_owner", password=password, database="finpilot_provision_test"
    )
    signed_in = create_engine(url, poolclass=NullPool)
    with signed_in.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "fp_new_owner"
    signed_in.dispose()


def test_create_owner_never_takes_over_someone_elses_database(cluster: Cluster) -> None:
    result = _script(
        "scripts.db_hardening",
        "create-owner", "--database", DATABASE, "--owner-role", "fp_intruder",
        ADMIN_DATABASE_URL=cluster.admin_url, OWNER_ROLE_PASSWORD="x" * 30,
    )  # fmt: skip
    assert result.returncode != 0
    assert "owned by another role" in result.stdout + result.stderr


def test_create_owner_needs_approval_outside_test_databases(cluster: Cluster) -> None:
    result = _script(
        "scripts.db_hardening",
        "create-owner", "--database", "finpilot",
        ADMIN_DATABASE_URL=cluster.admin_url, OWNER_ROLE_PASSWORD="x" * 30,
    )  # fmt: skip
    assert result.returncode != 0 and "owner-approved" in result.stdout + result.stderr


def test_create_owner_refuses_a_short_password(cluster: Cluster) -> None:
    result = _script(
        "scripts.db_hardening",
        "create-owner", "--database", "finpilot_short_test",
        ADMIN_DATABASE_URL=cluster.admin_url, OWNER_ROLE_PASSWORD="short",
    )  # fmt: skip
    assert result.returncode != 0 and "at least 24" in result.stdout + result.stderr
