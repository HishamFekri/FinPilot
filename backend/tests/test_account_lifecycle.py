"""Downloading your data and deleting your account, with synthetic users on a test database."""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.rate_limit import RateLimiter
from app.db import hardening
from app.db.models import AuditEvent, Trip, TripDispute, User, UserIdentity, UserSession
from app.services import account_data
from app.services.account_erasure import REMOVED, ErasureRefusedError, erase
from app.services.trip_expenses import nets
from tests.auth_harness import sign_in
from tests.google_fakes import FakeGoogle
from tests.test_fx import providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _setup
from tests.test_security_hardening import PRIVATE, _private_world, race, spend
from tests.test_settlements import _act, _pay
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures

Factory = sessionmaker[Session]


def ask(person: Person, *, email: str | None = None, ack: Any = True) -> Any:
    body = {
        "confirm_email": email or f"{person.name.lower()}@example.com",
        "acknowledge_retention": ack,
    }
    return person.c.post(f"{API}/me/deletion-request", json=body, headers=person.h(False))


def status_of(person: Person) -> Any:
    return _ok(person.get("/me/deletion-request"))


def cancel(person: Person) -> Any:
    return person.c.delete(f"{API}/me/deletion-request", headers=person.h(False))


def actions(factory: Factory, user_id: str) -> list[str]:
    with factory() as db:
        return list(
            db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.owner_user_id == user_id)
                .order_by(AuditEvent.occurred_at)
            )
        )


def age_sessions(factory: Factory, user_id: str, minutes: int) -> None:
    with factory() as db:
        db.execute(
            text(
                "UPDATE sessions SET created_at = now() - make_interval(mins => :m) "
                "WHERE user_id = :u"
            ),
            {"m": minutes, "u": user_id},
        )
        db.commit()


def row(factory: Factory, user_id: str) -> User:
    with factory() as db:
        user = db.get(User, user_id)
        assert user is not None
        return user


def do_erase(factory: Factory, user_id: str, grace: timedelta = timedelta(0)) -> None:
    with factory() as db:
        erase(db, user_id, grace=grace)  # type: ignore[arg-type]
        db.commit()


# --- Export


def test_the_download_holds_my_data_as_a_private_attachment(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice)
    spend(alice, account, "12.34", PRIVATE)
    _plan(alice, account)
    response = alice.get("/me/data-export")
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert re.fullmatch(
        r'attachment; filename="finpilot-data-\d{4}-\d{2}-\d{2}\.json"',
        response.headers["content-disposition"],
    )
    assert response.headers["x-export-complete"] == "true"
    data = json.loads(response.content)
    assert data["format"] == account_data.FORMAT_NAME and data["complete"] is True
    assert data["profile"]["email"] == "alice@example.com"
    for section, _ in account_data.SECTIONS:
        assert section in data, section
    assert PRIVATE in {t["description"] for t in data["transactions"]}
    assert data["accounts"] and data["categories"] and data["installment_plans"]


def test_the_download_never_names_who_it_doesnt_belong_to(people: list[Person]) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol)
    text_out = carol.get("/me/data-export").text
    for secret in (PRIVATE, "7777.77", "8888.88", "777777", "alice@example.com", "Alice", alice.id):
        assert secret not in text_out, secret
    data = json.loads(text_out)
    assert data["trips"][0]["name"] == "Lisbon"
    assert data["trips"][0]["my_budget_minor"] is None
    assert data["trips"][0]["my_shares"][0]["my_share_minor"] == 4500
    assert trip


def test_my_own_private_trip_budget_is_in_my_download(people: list[Person]) -> None:
    alice, _, carol, _ = people
    _private_world(alice, carol)
    data = _ok(alice.get("/me/data-export"))
    assert data["trips"][0]["my_budget_minor"] == 777777


def test_someone_elses_note_stays_out_of_my_download(people: list[Person]) -> None:
    alice, _, carol, _ = people
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "20.00", [alice, carol])
    _pay(carol, trip, alice, "10.00", note="carol-wrote-this")
    alices = alice.get("/me/data-export").text
    carols = carol.get("/me/data-export").text
    assert "carol-wrote-this" not in alices
    assert "carol-wrote-this" in carols


def test_a_capped_download_says_exactly_what_was_cut(
    people: list[Person], factory: Factory
) -> None:
    alice, *_ = people
    account = _setup(alice)
    for amount in ("1.00", "2.00", "3.00"):
        spend(alice, account, amount)
    with factory() as db:
        user = db.get(User, alice.id)
        assert user is not None
        data = account_data.build(db, user, limit=1)
    assert data["complete"] is False
    assert "transactions" in data["truncated_sections"]  # type: ignore[operator]
    assert len(data["transactions"]) == 1  # type: ignore[arg-type]


def test_the_download_is_rate_limited_per_person(people: list[Person], auth_app: FastAPI) -> None:
    alice, bob, *_ = people
    auth_app.state.rate_limiter = RateLimiter()
    assert [alice.get("/me/data-export").status_code for _ in range(6)] == [200] * 5 + [429]
    assert bob.get("/me/data-export").status_code == 200


def test_the_filename_cant_be_steered_by_anything_a_person_typed() -> None:
    assert account_data.filename(datetime(2026, 1, 2, tzinfo=UTC).date()) == (
        "finpilot-data-2026-01-02.json"
    )


# --- Asking to delete


def test_a_clear_account_can_ask_and_nothing_is_erased(
    people: list[Person], factory: Factory
) -> None:
    alice, *_ = people
    account = _setup(alice)
    spend(alice, account, "5.00", PRIVATE)
    before = status_of(alice)
    assert before["can_request"] is True and before["recent_sign_in"] is True

    after = _ok(ask(alice))
    assert after["status"] == "deletion_pending" and after["requested_at"]
    assert "user.deletion.requested" in actions(factory, alice.id)
    # Still all there: this step erases nothing.
    assert PRIVATE in alice.get("/me/data-export").text


@pytest.mark.parametrize("email", ["bob@example.com", "alice@example.co", "alice"])
def test_the_wrong_email_is_refused(people: list[Person], email: str) -> None:
    alice, *_ = people
    response = ask(alice, email=email)
    assert response.status_code == 422 and response.json()["code"] == "confirmation_mismatch"
    assert status_of(alice)["status"] == "active"


def test_the_email_check_ignores_only_case_and_spaces(people: list[Person]) -> None:
    alice, *_ = people
    assert ask(alice, email="  ALICE@Example.com ").status_code == 200


@pytest.mark.parametrize("ack", [False, "yes", None])
def test_retention_has_to_be_acknowledged(people: list[Person], ack: Any) -> None:
    alice, *_ = people
    assert ask(alice, ack=ack).status_code == 422
    assert status_of(alice)["status"] == "active"


def test_an_old_session_has_to_sign_in_again(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    age_sessions(factory, alice.id, 16)
    assert status_of(alice)["recent_sign_in"] is False
    response = ask(alice)
    assert response.status_code == 403 and response.json()["code"] == "recent_sign_in_required"
    assert status_of(alice)["status"] == "active"


def test_an_unknown_field_is_refused(people: list[Person]) -> None:
    alice, *_ = people
    response = alice.c.post(
        f"{API}/me/deletion-request",
        json={
            "confirm_email": "alice@example.com",
            "acknowledge_retention": True,
            "user_id": people[1].id,
        },
        headers=alice.h(False),
    )
    assert response.status_code == 422


def test_asking_twice_is_refused_and_recorded_once(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    _ok(ask(alice))
    again = ask(alice)
    assert again.status_code == 409 and again.json()["code"] == "deletion_already_requested"
    assert actions(factory, alice.id).count("user.deletion.requested") == 1


def test_two_requests_at_once_succeed_exactly_once(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    codes = race(4, lambda: ask(alice).status_code)
    assert sorted(codes) == [200, 409, 409, 409]
    assert actions(factory, alice.id).count("user.deletion.requested") == 1


def test_asking_signs_out_every_other_browser(
    people: list[Person], auth_app: FastAPI, google: FakeGoogle
) -> None:
    from fastapi.testclient import TestClient

    alice, *_ = people
    with TestClient(auth_app, raise_server_exceptions=False) as other:
        sign_in(other, google, subject="google-Alice", email="alice@example.com", name="Alice")
        assert other.get(f"{API}/auth/me").status_code == 200
        _ok(ask(alice))
        assert other.get(f"{API}/auth/me").status_code == 401
    assert alice.get("/auth/me").status_code == 200


# --- While it's pending


def test_while_pending_money_stops_moving_but_reading_doesnt(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice)
    _ok(ask(alice))
    response = alice.post("/transactions", {
        "type": "expense", "account_id": account["id"], "description": "x",
        "category_id": None, "amount": {"amount": "1.00", "currency": account["currency"]},
    })  # fmt: skip
    assert response.status_code == 409 and response.json()["code"] == "deletion_pending"
    assert alice.get("/accounts").status_code == 200
    assert alice.get("/me/data-export").status_code == 200


def test_cancelling_restores_the_account_and_is_recorded(
    people: list[Person], factory: Factory
) -> None:
    alice, *_ = people
    account = _setup(alice)
    _ok(ask(alice))
    assert _ok(cancel(alice))["status"] == "active"
    assert row(factory, alice.id).deletion_requested_at is None
    assert "user.deletion.cancelled" in actions(factory, alice.id)
    spend(alice, account, "1.00")  # works again


def test_there_is_nothing_to_cancel_when_nothing_was_asked(people: list[Person]) -> None:
    alice, *_ = people
    response = cancel(alice)
    assert response.status_code == 409 and response.json()["code"] == "deletion_not_requested"


# --- What blocks it


def _kinds(person: Person) -> set[str]:
    return {b["kind"] for b in status_of(person)["blockers"]}


def test_an_open_trip_balance_blocks_both_sides(people: list[Person]) -> None:
    alice, _, carol, _ = people
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "90.00", [alice, carol])
    assert "trip_balance" in _kinds(carol)
    response = ask(carol)
    assert response.status_code == 409 and response.json()["code"] == "deletion_blocked"
    blockers = response.json()["params"]["blockers"]
    assert blockers[0]["trip_name"] == "Lisbon" and blockers[0]["amount_minor"] == -4500
    assert status_of(carol)["status"] == "active"
    assert "trip_balance" in _kinds(alice)  # owed money counts too


def test_settling_up_clears_the_balance_block(people: list[Person]) -> None:
    alice, _, carol, _ = people
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "90.00", [alice, carol])
    payment = _pay(carol, trip, alice, "45.00")
    assert "trip_pending" in _kinds(carol)  # waiting for Alice to confirm
    _act(alice, trip, payment["id"], "confirm")
    assert _kinds(carol) == set()
    _ok(ask(carol))


def test_owning_a_trip_others_are_on_blocks_it(people: list[Person]) -> None:
    alice, _, carol, _ = people
    _trip(alice, members=[carol])
    assert _kinds(alice) == {"trip_owner"}
    assert _kinds(carol) == set()


def test_an_open_dispute_blocks_it(people: list[Person], factory: Factory) -> None:
    alice, _, carol, _ = people
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "20.00", [alice, carol])
    with factory() as db:
        version, expense = db.execute(
            text("SELECT id, expense_id FROM trip_expense_versions WHERE trip_id = :t"), {"t": trip}
        ).one()
        db.add(
            TripDispute(
                trip_id=trip, expense_id=expense, version_id=version,
                raised_by=carol.id, reason="not mine",
            )
        )  # fmt: skip
        db.commit()
    assert "trip_dispute" in _kinds(carol)


def test_personal_debts_warn_but_dont_block(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice)
    _plan(alice, account)
    state = status_of(alice)
    assert state["blockers"] == [] and state["can_request"] is True
    assert {w["kind"] for w in state["warnings"]} == {"personal_debts", "installment_plans"}
    _ok(ask(alice))


def test_the_status_never_reveals_another_members_money(people: list[Person]) -> None:
    alice, _, carol, _ = people
    _private_world(alice, carol)
    body = carol.get("/me/deletion-request").text
    for secret in (PRIVATE, "7777", "8888", "alice@example.com"):
        assert secret not in body, secret


# --- Erasure (a maintenance step, on synthetic users only)


def test_erasure_refuses_someone_who_never_asked(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    with pytest.raises(ErasureRefusedError, match="not_pending_deletion"):
        do_erase(factory, alice.id)
    assert row(factory, alice.id).email == "alice@example.com"


def test_erasure_waits_for_the_grace_period(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    _ok(ask(alice))
    with pytest.raises(ErasureRefusedError, match="grace_period_not_over"):
        do_erase(factory, alice.id, grace=timedelta(days=14))
    assert row(factory, alice.id).status == "deletion_pending"


def test_erasure_checks_again_for_new_shared_debts(people: list[Person], factory: Factory) -> None:
    alice, _, carol, _ = people
    trip = _trip(alice, members=[carol])
    _ok(ask(carol))
    _expense(alice, trip, alice, "20.00", [alice, carol])  # added during the grace period
    with pytest.raises(ErasureRefusedError, match="still_blocked"):
        do_erase(factory, carol.id)
    assert row(factory, carol.id).email == "carol@example.com"


def _settled_trip(alice: Person, carol: Person) -> str:
    _setup(carol)
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "90.00", [alice, carol], description="Dinner")
    payment = _pay(carol, trip, alice, "45.00", note="carol-private-note")
    _act(alice, trip, payment["id"], "confirm")
    return trip


def test_erasure_removes_personal_data_and_keeps_the_others_books(
    people: list[Person], factory: Factory
) -> None:
    alice, _, carol, _ = people
    trip = _settled_trip(alice, carol)
    _ok(ask(carol))
    with factory() as db:
        found = db.get(Trip, trip)
        assert found is not None
        before = nets(db, found)
    do_erase(factory, carol.id)

    with factory() as db:
        for table, column in hardening.PERSONAL.items():
            left = db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :u"),
                {"u": carol.id},
            )
            assert left == 0, table
        assert db.scalar(select(func.count()).where(UserSession.user_id == carol.id)) == 0
        assert db.scalar(select(func.count()).where(UserIdentity.user_id == carol.id)) == 0
        assert (
            db.scalar(
                select(func.count()).where(
                    AuditEvent.scope == "user", AuditEvent.owner_user_id == carol.id
                )
            )
            == 0
        )
        tombstone = db.get(User, carol.id)
        assert tombstone is not None
        assert tombstone.status == "deleted"
        assert tombstone.email is None and tombstone.display_name is None
        erased = db.scalar(select(func.count()).where(AuditEvent.action == "user.erased"))
        assert erased == 1
        found = db.get(Trip, trip)
        assert found is not None
        after = nets(db, found)
    assert after == before and sum(after.values()) == 0  # everyone else's books still balance
    shown = alice.get(f"/trips/{trip}/settlements").text
    assert "carol-private-note" not in shown
    assert "carol@example.com" not in alice.get(f"/trips/{trip}").text


def test_erasure_cant_run_twice(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    _ok(ask(alice))
    do_erase(factory, alice.id)
    with pytest.raises(ErasureRefusedError, match="not_pending_deletion"):
        do_erase(factory, alice.id)


def test_the_old_session_is_dead_after_erasure(people: list[Person], factory: Factory) -> None:
    alice, *_ = people
    _ok(ask(alice))
    do_erase(factory, alice.id)
    assert alice.get("/auth/me").status_code == 401


def test_the_ledgers_protection_is_back_on_after_erasure(
    people: list[Person], factory: Factory
) -> None:
    alice, bob, *_ = people
    account = _setup(bob)
    spend(bob, account, "5.00")
    _ok(ask(alice))
    do_erase(factory, alice.id)
    with factory() as db, pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM journal_lines WHERE user_id = :u"), {"u": bob.id})


def test_erasure_leaves_another_persons_data_alone(people: list[Person], factory: Factory) -> None:
    alice, bob, *_ = people
    account = _setup(bob)
    spend(bob, account, "5.00", "Bob-keeps-this")
    before = bob.get("/transactions").text
    _ok(ask(alice))
    do_erase(factory, alice.id)
    assert bob.get("/transactions").text == before


def test_signing_in_after_erasure_is_a_new_empty_account(
    people: list[Person], factory: Factory, auth_app: FastAPI, google: FakeGoogle
) -> None:
    from fastapi.testclient import TestClient

    alice, *_ = people
    _setup(alice)
    _ok(ask(alice))
    do_erase(factory, alice.id)
    with TestClient(auth_app, raise_server_exceptions=False) as client:
        sign_in(client, google, subject="google-Alice", email="alice@example.com", name="Alice")
        me = client.get(f"{API}/auth/me").json()
    assert me["id"] != alice.id
    assert me["status"] == "active"


def test_a_tombstone_shows_as_a_neutral_member(people: list[Person], factory: Factory) -> None:
    alice, _, carol, _ = people
    trip = _settled_trip(alice, carol)
    _ok(ask(carol))
    do_erase(factory, carol.id)
    assert "Carol" not in alice.get(f"/trips/{trip}/members").text


def test_what_they_wrote_in_a_dispute_is_removed(people: list[Person], factory: Factory) -> None:
    alice, _, carol, _ = people
    trip = _settled_trip(alice, carol)
    with factory() as db:
        version, expense = db.execute(
            text("SELECT id, expense_id FROM trip_expense_versions WHERE trip_id = :t"), {"t": trip}
        ).one()
        db.add(
            TripDispute(
                trip_id=trip, expense_id=expense, version_id=version, raised_by=carol.id,
                reason="carol-private-reason", status="withdrawn", decided_at=datetime.now(UTC),
            )
        )  # fmt: skip
        db.commit()
    _ok(ask(carol))
    do_erase(factory, carol.id)
    with factory() as db:
        assert db.scalar(select(TripDispute.reason).where(TripDispute.trip_id == trip)) == REMOVED
