"""Regression tests from the security audit (docs/SECURITY_AUDIT.md), through the real API."""

import csv
import io
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment
from app.core.limits import MAX_BODY_BYTES
from app.core.rate_limit import RateLimiter
from app.main import create_app
from app.services import exports
from app.services.text_safety import unsafe
from tests.auth_harness import CSRF_COOKIE
from tests.conftest import make_settings
from tests.google_fakes import FakeGoogle
from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _balance, _category_id, _setup
from tests.test_security_sweep import body_for, call, operations
from tests.test_settlements import _pay
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures

UNSAFE = ("post", "put", "patch", "delete")
EVIL_ORIGIN = "https://evil.example"
PRIVATE = "ZZ-PERSONAL-SECRET"


def race(count: int, action: Callable[[], Any]) -> list[Any]:
    """Runs `action` from `count` threads released at the same moment."""
    results: list[Any] = []
    barrier = threading.Barrier(count)

    def run() -> None:
        barrier.wait()
        results.append(action())

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def keyed(person: Person, key: str) -> dict[str, str]:
    return {**person.h(False), "Idempotency-Key": key}


def spend(person: Person, account: dict[str, Any], amount: str, text: str = "x") -> Any:
    return _ok(person.post("/transactions", {
        "type": "expense", "account_id": account["id"], "description": text,
        "category_id": _category_id(person, "groceries"),
        "amount": {"amount": amount, "currency": account["currency"]},
    }), 201)  # fmt: skip


# --- CSRF, on every route that changes anything


@pytest.mark.parametrize(
    "case", ["no_token", "wrong_token", "cross_site_origin", "no_origin", "null_origin"]
)
def test_every_change_is_refused_without_proof_it_came_from_finpilot(
    people: list[Person], auth_app: FastAPI, case: str
) -> None:
    alice, *_ = people
    good = alice.h(False)
    origin = good["Origin"]
    headers = {
        "no_token": {"Origin": origin},
        "wrong_token": {"Origin": origin, "X-CSRF-Token": "forged"},
        "cross_site_origin": {"Origin": EVIL_ORIGIN, "X-CSRF-Token": good["X-CSRF-Token"]},
        "no_origin": {"X-CSRF-Token": good["X-CSRF-Token"]},
        "null_origin": {"Origin": "null", "X-CSRF-Token": good["X-CSRF-Token"]},
    }[case]
    checked = 0
    for method, path, _ in operations(auth_app):
        if method not in UNSAFE or path.endswith("/auth/logout"):
            continue
        auth_app.state.rate_limiter = RateLimiter()
        url = path
        for name in ("trip_id", "account_id", "transaction_id"):
            url = url.replace(f"{{{name}}}", str(uuid.uuid4()))
        while "{" in url:
            url = url[: url.index("{")] + str(uuid.uuid4()) + url[url.index("}") + 1 :]
        response = alice.c.request(method.upper(), url, json={}, headers=headers)
        assert response.status_code == 403, (method, path, response.status_code)
        assert response.json()["code"] == "csrf_failed", (method, path)
        checked += 1
    assert checked > 80


def test_a_cross_site_logout_with_a_live_session_ends_nothing(people: list[Person]) -> None:
    alice, *_ = people
    response = alice.c.post(f"{API}/auth/logout", headers={"Origin": EVIL_ORIGIN})
    assert response.status_code == 403
    assert alice.get("/auth/me").status_code == 200


# --- Fields nobody may set


@pytest.mark.parametrize("field", ["user_id", "owner_user_id", "is_admin"])
def test_a_body_cant_smuggle_ownership_fields(
    people: list[Person], auth_app: FastAPI, field: str
) -> None:
    alice, bob, *_ = people
    account = _setup(alice)
    ids = {"account_id": account["id"], "category_id": _category_id(alice, "groceries")}
    accepted = []
    for method, path, operation in operations(auth_app):
        if method not in UNSAFE or "{" in path:
            continue
        base = body_for(auth_app, operation, ids)
        if not isinstance(base, dict):
            continue
        response = call(alice, auth_app, method, path, ids, {**base, field: bob.id})
        assert response.status_code < 500, (path, response.text[:120])
        if response.status_code < 300:
            accepted.append(path)
    assert accepted == []


# --- Trip privacy


def _private_world(alice: Person, carol: Person, shared: bool = True) -> str:
    """A trip with Carol in it, and Alice's private money that Carol must never see."""
    account = _setup(alice)
    _ok(alice.post("/accounts", {
        "name": PRIVATE, "type": "wallet", "currency": "EUR",
        "opening_balance": {"amount": "8888.88", "currency": "EUR"},
    }), 201)  # fmt: skip
    spend(alice, account, "12.00", PRIVATE)
    _ok(alice.post("/goals", {
        "name": PRIVATE, "target": {"amount": "100.00", "currency": "EUR"},
    }), 201)  # fmt: skip
    _setup(carol)
    trip = _trip(alice, members=[carol])
    _expense(alice, trip, alice, "90.00", [alice, carol] if shared else [alice])
    budget = {"amount": "7777.77"}
    _ok(alice.c.put(f"{API}/trips/{trip}/my-budget", json=budget, headers=alice.h(False)))
    return trip


def _assert_nothing_private(person: Person, app: FastAPI, trip: str, label: str) -> int:
    seen = 0
    ids = {"trip_id": trip}
    for method, path, _ in operations(app):
        if method != "get" or "{trip_id}" not in path or path.endswith(".pdf"):
            continue
        if path.count("{") > 1:
            continue
        response = call(person, app, method, path, ids)
        assert response.status_code < 500, (label, path)
        for secret in (PRIVATE, "7777.77", "8888.88", "alice@example.com"):
            assert secret not in response.text, (label, path, secret)
        seen += 1
    return seen


def test_a_member_never_sees_another_members_private_money(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol)
    assert _assert_nothing_private(carol, auth_app, trip, "member") > 10
    mine = _ok(carol.get(f"/trips/{trip}/my-budget"))
    assert mine["amount"] is None  # her own, unset: never Alice's


def test_a_former_member_sees_nothing_private_and_cant_add_expenses(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol, shared=False)  # she owes nothing, so she can leave
    _ok(carol.post(f"/trips/{trip}/leave", key=False), 204)
    _assert_nothing_private(carol, auth_app, trip, "former")
    body = {"payer_user_id": carol.id, "description": "x", "category_key": "food_and_drinks",
            "expense_date": TODAY.isoformat(), "amount": {"amount": "5.00", "currency": "EUR"},
            "split": {"method": "equal", "shares": [{"user_id": carol.id}]}}  # fmt: skip
    assert carol.post(f"/trips/{trip}/expenses", body).status_code in (403, 404)


def test_a_used_invitation_cant_be_replayed_and_a_forged_one_is_refused(
    people: list[Person],
) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice)
    link = _ok(alice.post(f"/trips/{trip}/invitations", {"label": "Bob"}, key=False), 201)
    token = link["token"]
    assert len(token) >= 40  # 256 random bits
    _ok(bob.post("/invitations/accept", {"token": token}, key=False))
    replay = carol.post("/invitations/accept", {"token": token}, key=False)
    assert replay.status_code in (404, 410)
    forged = carol.post("/invitations/accept", {"token": "x" * 43}, key=False)
    assert forged.status_code in (404, 410)
    assert token not in str(_ok(alice.get(f"/trips/{trip}/invitations")))


def test_concurrent_acceptance_of_one_invitation_admits_one_person(people: list[Person]) -> None:
    alice, bob, carol, dan = people
    trip = _trip(alice)
    token = _ok(alice.post(f"/trips/{trip}/invitations", {"label": "x"}, key=False), 201)["token"]
    who = iter([bob, carol, dan])
    lock = threading.Lock()

    def accept() -> int:
        with lock:
            person = next(who)
        return int(person.post("/invitations/accept", {"token": token}, key=False).status_code)

    results = race(3, accept)
    assert sorted(results)[0] == 200 and results.count(200) == 1


# --- Bidirectional spoofing in text other people read


@pytest.mark.parametrize("text", ["Dinner ‮00.09", "a⁦b", "bell\x07"])
def test_shared_trip_text_cant_reorder_what_others_read(people: list[Person], text: str) -> None:
    alice, *_ = people
    assert alice.post("/trips", {"name": text, "base_currency": "EUR"}).status_code == 422
    trip = _trip(alice)
    body = {"payer_user_id": alice.id, "description": text, "category_key": "food_and_drinks",
            "expense_date": TODAY.isoformat(), "amount": {"amount": "5.00", "currency": "EUR"},
            "split": {"method": "equal", "shares": [{"user_id": alice.id}]}}  # fmt: skip
    assert alice.post(f"/trips/{trip}/expenses", body).status_code == 422


@pytest.mark.parametrize("text", ["مطعم على النيل", "گل‌ها", "👨‍👩‍👧", "Çiğköfte İzmir"])  # noqa: RUF001
def test_real_arabic_turkish_and_emoji_text_is_kept(people: list[Person], text: str) -> None:
    assert not unsafe(text)
    alice, *_ = people
    created = alice.post("/trips", {"name": text, "base_currency": "EUR"})
    assert created.status_code == 201 and created.json()["name"] == text


# --- Export injection


def test_csv_cells_that_would_run_as_formulas_are_neutralised(people: list[Person]) -> None:
    alice, bob, *_ = people
    account = _setup(alice)
    _setup(bob)
    for payload in ("=HYPERLINK(1)", "+1+1", "@SUM(A1)", "-2+3"):
        spend(alice, account, "1.00", payload)
    first = TODAY.replace(day=1).isoformat()
    response = alice.get("/reports/export.csv", **{"from": first, "to": TODAY.isoformat()})
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-store")
    assert "attachment" in response.headers["content-disposition"]
    cells = [c for row in csv.reader(io.StringIO(response.text)) for c in row]
    for payload in ("=HYPERLINK(1)", "+1+1", "@SUM(A1)"):
        assert payload not in cells, payload
        assert f"'{payload}" in cells, payload


def test_trip_csv_neutralises_formulas_from_other_members(people: list[Person]) -> None:
    alice, _, carol, _ = people
    _setup(alice)
    _setup(carol)
    trip = _trip(alice, members=[carol])
    _expense(carol, trip, carol, "30.00", [alice, carol], description="=cmd|calc")
    response = alice.get(f"/trips/{trip}/reports/expenses.csv")
    assert response.status_code == 200
    assert "'=cmd|calc" in response.text


def test_another_persons_export_is_not_found(people: list[Person]) -> None:
    alice, bob, *_ = people
    _setup(alice)
    _setup(bob)
    trip = _trip(alice)
    for suffix in ("expenses.csv", "balances.csv", "settlements.csv"):
        assert bob.get(f"/trips/{trip}/reports/{suffix}").status_code in (403, 404)
    assert bob.get("/reports/trip-shares.csv").status_code in (200, 422)


# --- Money can only move once


def test_one_key_sent_at_once_pays_an_installment_once(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice, opening="2000.00")
    plan = _plan(alice, account, item_price="400.00", down_payment="0.00", installment_count=4,
                 first_due_date=(TODAY + timedelta(days=5)).isoformat())  # fmt: skip
    before = _balance(alice, account)
    key = str(uuid.uuid4())
    body = {"account_id": account["id"], "principal": "100.00"}
    url = f"{API}/installments/{plan['id']}/payments"
    results = race(4, lambda: alice.c.post(url, json=body, headers=keyed(alice, key)).status_code)
    assert all(s in (201, 409) for s in results), results
    assert results.count(201) >= 1
    assert float(before) - float(_balance(alice, account)) == 100.0


def test_racing_payments_cant_overpay_a_plan(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice, opening="5000.00")
    plan = _plan(alice, account, item_price="400.00", down_payment="0.00", installment_count=4,
                 first_due_date=(TODAY + timedelta(days=5)).isoformat())  # fmt: skip
    before = float(_balance(alice, account))
    body = {"account_id": account["id"], "principal": "400.00"}
    results = race(4, lambda: alice.post(f"/installments/{plan['id']}/payments", body).status_code)
    assert results.count(201) == 1, results
    assert all(s == 201 or 400 <= s < 500 for s in results), results
    assert before - float(_balance(alice, account)) == 400.0


def test_a_void_raced_four_times_reverses_once(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice)
    start = float(_balance(alice, account))
    txn = spend(alice, account, "70.00")
    headers = {**alice.h(False), "If-Match": f'"{txn["version"]}"'}
    url = f"{API}/transactions/{txn['id']}/void"
    results = race(4, lambda: alice.c.post(url, headers=headers).status_code)
    assert results.count(200) == 1, results
    assert all(s == 200 or 400 <= s < 500 for s in results), results
    assert float(_balance(alice, account)) == start


def test_one_key_records_a_trip_expense_once(people: list[Person]) -> None:
    alice, *_ = people
    _setup(alice)
    trip = _trip(alice)
    key = str(uuid.uuid4())
    body = {"payer_user_id": alice.id, "description": "Once", "category_key": "food_and_drinks",
            "expense_date": TODAY.isoformat(), "amount": {"amount": "5.00", "currency": "EUR"},
            "split": {"method": "equal", "shares": [{"user_id": alice.id}]}}  # fmt: skip
    url = f"{API}/trips/{trip}/expenses"
    results = race(4, lambda: alice.c.post(url, json=body, headers=keyed(alice, key)).status_code)
    assert all(s in (201, 409) for s in results), results
    listed = _ok(alice.get(f"/trips/{trip}/expenses"))
    items = listed["items"] if isinstance(listed, dict) else listed
    assert len(items) == 1


def test_a_settlement_reversal_raced_four_times_counts_once(people: list[Person]) -> None:
    alice, bob, *_ = people
    _setup(alice)
    _setup(bob)
    trip = _trip(alice, members=[bob])
    _expense(alice, trip, alice, "90.00", [alice, bob])
    paid = _pay(bob, trip, alice, "45.00")
    _ok(alice.post(f"/trips/{trip}/settlements/{paid['id']}/confirm", None))
    url = f"/trips/{trip}/settlements/{paid['id']}/reverse"
    results = race(4, lambda: alice.post(url, None).status_code)
    assert results.count(201) + results.count(200) == 1, results
    assert all(s < 300 or 400 <= s < 500 for s in results), results


# --- Headers, limits, what the service gives away


def _hosted_settings(url: str = "") -> Any:
    extra = {"database_url": url} if url else {}
    return make_settings(app_env=Environment.DEMO, web_origin="https://finpilot.example", **extra)


def test_api_docs_exist_only_in_development_and_test() -> None:
    with TestClient(create_app(_hosted_settings())) as hosted:
        assert hosted.get("/api/v1/openapi.json").status_code == 404
        assert hosted.get("/api/v1/docs").status_code == 404
    with TestClient(create_app(make_settings())) as local:
        assert local.get("/api/v1/openapi.json").status_code == 200


def test_every_response_carries_the_security_headers() -> None:
    with TestClient(create_app(_hosted_settings())) as hosted:
        response = hosted.get("/api/v1/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in response.headers["strict-transport-security"]


def test_no_strict_transport_header_on_local_http() -> None:
    with TestClient(create_app(make_settings())) as local:
        assert "strict-transport-security" not in local.get("/api/v1/healthz").headers


def test_an_oversized_request_is_refused_before_it_is_read() -> None:
    with TestClient(create_app(make_settings())) as client:
        response = client.post(
            "/api/v1/transactions",
            content=b"{}",
            headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_private_responses_are_never_cached(people: list[Person], auth_app: FastAPI) -> None:
    alice, *_ = people
    _setup(alice)
    checked = 0
    for method, path, _ in operations(auth_app):
        if method != "get" or "{" in path or path.endswith((".pdf", "/healthz", "/readyz")):
            continue
        if "/auth/google/" in path or path.endswith("openapi.json"):
            continue
        response = call(alice, auth_app, method, path, {})
        if response.status_code == 200:
            assert "no-store" in response.headers.get("cache-control", ""), path
            checked += 1
    assert checked > 20


def test_people_behind_one_address_dont_share_a_limit(people: list[Person]) -> None:
    alice, bob, *_ = people
    alice.c.app.state.rate_limiter = RateLimiter()  # type: ignore[attr-defined]
    _setup(alice)
    _setup(bob)
    url = f"{API}/transactions"
    for _ in range(125):
        last = alice.c.post(url, json={}, headers=alice.h())
    assert last.status_code == 429
    assert bob.c.post(url, json={}, headers=bob.h()).status_code == 422


def test_invented_session_cookies_dont_buy_new_rate_limit_buckets(auth_app: FastAPI) -> None:
    auth_app.state.rate_limiter = RateLimiter()
    with TestClient(auth_app, raise_server_exceptions=False) as visitor:
        seen = set()
        for i in range(25):
            visitor.cookies.set("fp_session", f"invented-{i}")
            seen.add(visitor.get(f"{API}/auth/google/start", follow_redirects=False).status_code)
    assert 303 in seen  # the sign-in limit (20) still applied


def test_a_malformed_csrf_cookie_changes_nothing(google: FakeGoogle, people: list[Person]) -> None:
    alice, *_ = people
    alice.c.cookies.set(CSRF_COOKIE, "\t bad \t")
    headers = {"Origin": alice.h(False)["Origin"], "X-CSRF-Token": "\t bad"}
    assert alice.c.post(f"{API}/accounts", json={}, headers=headers).status_code == 403


def test_errors_dont_reveal_whether_someone_elses_record_exists(people: list[Person]) -> None:
    alice, bob, *_ = people
    account = _setup(alice)
    _setup(bob)
    theirs = bob.get(f"/accounts/{account['id']}")
    missing = bob.get(f"/accounts/{uuid.uuid4()}")
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["code"] == missing.json()["code"]
    assert theirs.json()["title"] == missing.json()["title"]


def test_session_tokens_never_reach_the_logs(
    people: list[Person], caplog: pytest.LogCaptureFixture
) -> None:
    alice, *_ = people
    token = alice.c.cookies["fp_session"]
    with caplog.at_level(logging.DEBUG):
        alice.get("/auth/me")
        alice.post("/accounts", {"name": "x"})
        alice.get("/this-does-not-exist")
    text = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    assert token not in text
    assert alice.csrf not in text


def test_a_trip_pdf_carries_nothing_private(
    people: list[Person], auth_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, _, carol, _ = people
    trip = _private_world(alice, carol)
    # The HTML that would be printed: no browser needed to read what it contains.
    monkeypatch.setattr(exports, "render_pdf", lambda html, *_a, **_k: html.encode())
    response = carol.get(f"/trips/{trip}/reports/export.pdf")
    assert response.status_code == 200, response.text[:200]
    assert response.headers["cache-control"].startswith("no-store")
    for secret in (PRIVATE, "7777.77", "8888.88", "alice@example.com"):
        assert secret not in response.text, secret


ARABIC = "".join(map(chr, (0x645, 0x637, 0x639, 0x645)))
TURKISH = "".join(map(chr, (0xC7, 0x69, 0x11F, 0x6B, 0xF6, 0x66, 0x74, 0x65)))
HIDDEN = ("", " ", chr(0x3000), chr(0x200B), chr(0x202E), chr(0xFEFF))


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", chr(9), chr(13)])
@pytest.mark.parametrize("text", ["SUM(A1)", ARABIC, TURKISH])
@pytest.mark.parametrize("hidden", HIDDEN)
def test_every_formula_trigger_is_neutralised_in_any_language(
    trigger: str, text: str, hidden: str
) -> None:
    value = f"{hidden}{trigger}{text}"
    assert exports.safe_cell(value) == chr(39) + value


@pytest.mark.parametrize("text", [ARABIC, TURKISH, "Lunch = good"])
def test_ordinary_text_in_any_language_is_left_alone(text: str) -> None:
    assert exports.safe_cell(text) == text


@pytest.mark.parametrize("markup", ["<script>alert(1)</script>", '<img src="file:///etc/passwd">'])
def test_markup_in_a_report_is_literal_text(markup: str) -> None:
    html = exports.render_html(
        {
            "lang": "en",
            "dir": "ltr",
            "t": {},
            "currencies": [],
            "complete": True,
            "completeness": "",
            "rows": [{"description": markup, "date": "2026-11-07"}],
        }
    )
    assert markup not in html
    assert "&lt;" in html
