"""The running production stack: proxy, web, API and database together."""

import ssl
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import Engine, text

from app.db import hardening
from tests_stack.conftest import (
    API,
    PERSISTENT_EMAIL,
    Person,
    needs_proxy,
    ok,
    setting,
    signed_in,
)


def _world(person: Person) -> tuple[dict[str, str], dict[str, str]]:
    """Default accounts and categories, set up through the API."""
    ok(person.browser.post(f"{API}/setup", json={"currency": "TRY"}, headers=person.headers()))
    accounts = {a["system_key"]: a for a in ok(person.browser.get(f"{API}/accounts"))}
    categories = {c["system_key"]: c["id"] for c in ok(person.browser.get(f"{API}/categories"))}
    return accounts["bank"], categories


def _yesterday() -> str:
    """A date that isn't in the future in any time zone, whatever this machine's clock says."""
    return (datetime.now(UTC) - timedelta(days=1)).date().isoformat()


def _spend(person: Person, amount: str = "125.50", description: str = "Stack test") -> str:
    bank, categories = _world(person)
    posted = ok(
        person.browser.post(
            f"{API}/transactions",
            json={
                "type": "expense",
                "account_id": bank["id"],
                "amount": {"amount": amount, "currency": "TRY"},
                "category_id": categories["education"],
                "effective_date": _yesterday(),
                "description": description,
            },
            headers=person.keyed(),
        )
    )
    return str(posted["id"])


# --- The proxy, the routing, and the headers


def test_health_and_readiness_answer_through_the_proxy(anonymous: httpx.Client) -> None:
    assert ok(anonymous.get(f"{API}/healthz")) == {"status": "ok"}
    # In production, ready means migrated, restricted role, and RLS on.
    assert ok(anonymous.get(f"{API}/readyz")) == {"status": "ready"}


def test_api_responses_carry_the_security_headers(anonymous: httpx.Client) -> None:
    response = anonymous.get(f"{API}/healthz")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    assert len(response.headers["x-request-id"]) == 32
    assert "server" not in response.headers


def test_pages_come_from_the_web_server_with_its_policy(anonymous: httpx.Client) -> None:
    page = anonymous.get("/en")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    policy = page.headers["content-security-policy"]
    assert "'nonce-" in policy and "frame-ancestors 'none'" in policy
    assert "'unsafe-eval'" not in policy  # a production build, not development
    assert page.headers["strict-transport-security"].startswith("max-age=")
    assert "server" not in page.headers and "x-powered-by" not in page.headers


def test_api_documentation_isnt_published(anonymous: httpx.Client) -> None:
    for path in (f"{API}/docs", f"{API}/openapi.json"):
        assert anonymous.get(path).status_code == 404


@needs_proxy
def test_large_bodies_are_refused_before_they_reach_the_api(anonymous: httpx.Client) -> None:
    # Chunked, so no Content-Length: the API couldn't measure it in advance, the proxy must.
    chunks = (b"x" * 65536 for _ in range(40))  # 2.5 MiB
    response = anonymous.post(f"{API}/auth/logout", content=chunks)
    assert response.status_code == 413


@needs_proxy
@pytest.mark.phase("main")  # behind the existing proxy, Cloudflare redirects instead
def test_plain_http_only_redirects_to_https(tls: ssl.SSLContext | None, origin: str) -> None:
    http_url = setting("STACK_HTTP_URL", required=False)
    if not http_url:
        pytest.skip("STACK_HTTP_URL isn't set")
    with httpx.Client(base_url=http_url, follow_redirects=False) as plain:
        response = plain.get("/en/app?x=1")
    assert response.status_code == 308
    assert response.headers["location"] == f"{origin}/en/app?x=1"


# --- Sessions, cookies and CSRF in production mode


def test_signed_out_requests_are_refused(anonymous: httpx.Client) -> None:
    assert anonymous.get(f"{API}/auth/me").status_code == 401
    assert anonymous.get(f"{API}/accounts").status_code == 401


def test_changes_need_both_the_csrf_token_and_this_origin(person: Person) -> None:
    assert ok(person.browser.get(f"{API}/auth/me"))["email"] == person.email
    me = f"{API}/auth/me"
    body = {"locale": "tr"}
    assert person.browser.patch(me, json=body, headers={"Origin": person.origin}).status_code == 403
    wrong_origin = {"X-CSRF-Token": person.csrf, "Origin": "https://evil.example"}
    assert person.browser.patch(me, json=body, headers=wrong_origin).status_code == 403
    no_origin = {"X-CSRF-Token": person.csrf}
    assert person.browser.patch(me, json=body, headers=no_origin).status_code == 403
    assert ok(person.browser.patch(me, json=body, headers=person.headers()))["locale"] == "tr"


def test_signing_out_clears_secure_host_only_cookies(person: Person) -> None:
    response = person.browser.post(f"{API}/auth/logout", headers=person.headers())
    assert response.status_code == 204
    cookies = response.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("__Host-fp_session="))
    lowered = session.lower()
    assert "secure" in lowered and "httponly" in lowered and "samesite=lax" in lowered
    assert "path=/" in lowered and "domain=" not in lowered
    assert person.browser.get(f"{API}/auth/me").status_code == 401


# --- Isolation, exports, and the database role


def test_one_person_cant_reach_anothers_records(
    owner_engine: Engine, tls: ssl.SSLContext | None, origin: str, person: Person
) -> None:
    transaction = _spend(person)
    accounts = ok(person.browser.get(f"{API}/accounts"))
    stranger = signed_in(owner_engine, tls, origin)
    try:
        _world(stranger)
        for account in accounts:
            assert stranger.browser.get(f"{API}/accounts/{account['id']}").status_code == 404
        listed = ok(stranger.browser.get(f"{API}/transactions"))
        assert transaction not in str(listed)
    finally:
        stranger.browser.close()


def test_csv_and_pdf_exports_work_and_are_never_cached(person: Person) -> None:
    _spend(person, description="Export check")
    csv = person.browser.get(f"{API}/reports/export.csv")
    assert csv.status_code == 200, csv.text[:300]
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["cache-control"] == "no-store"
    assert csv.headers["content-disposition"].startswith("attachment")
    assert "Export check" in csv.content.decode("utf-8-sig")
    # Chromium runs with its sandbox on. If the sandbox can't start, this is 503, never unsafe.
    pdf = person.browser.get(f"{API}/reports/export.pdf", params={"locale": "ar"})
    assert pdf.status_code == 200, pdf.text[:300]
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and pdf.headers["cache-control"] == "no-store"


def test_the_runtime_role_sees_nothing_without_an_identity(
    person: Person, runtime_engine: Engine, owner_engine: Engine
) -> None:
    _spend(person)
    with owner_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transactions")) > 0
    with runtime_engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transactions")) == 0
        connection.execute(
            text("SELECT set_config('app.user_id', :id, true)"), {"id": str(person.id)}
        )
        mine = connection.scalar(text("SELECT count(*) FROM transactions"))
        others = connection.scalar(
            text("SELECT count(*) FROM transactions WHERE user_id <> :id"), {"id": person.id}
        )
    assert mine > 0 and others == 0


def test_the_runtime_role_cant_change_the_schema_or_its_own_rights(runtime_engine: Engine) -> None:
    failed = [check.name for check in hardening.verify_runtime(runtime_engine) if not check.passed]
    assert failed == []


# --- Rate limits behind the proxy (last: it uses up this client's sign-in allowance)


@needs_proxy
@pytest.mark.phase("main", "behind")
def test_z_a_forged_forwarded_address_buys_no_new_sign_in_attempts(
    anonymous: httpx.Client,
) -> None:
    """The proxy replaces whatever X-Forwarded-For a visitor sends. An invalid return path is
    refused (422) without contacting Google, after the limit is counted; over the limit, the
    sign-in page redirects with error=failed."""
    for n in range(20):
        response = anonymous.get(
            f"{API}/auth/google/start",
            params={"return_to": "https://elsewhere.example"},
            headers={"X-Forwarded-For": f"192.0.2.{n}"},
        )
        assert response.status_code == 422, response.text[:200]
    limited = anonymous.get(
        f"{API}/auth/google/start",
        params={"return_to": "https://elsewhere.example"},
        headers={"X-Forwarded-For": "192.0.2.250"},
    )
    assert limited.status_code == 303 and "error=failed" in limited.headers["location"]


# --- Behind the server's existing proxy (a stand-in for it and Cloudflare)
#
# The stand-in trusts only the test runner, in cloudflared's role, for Cf-Connecting-Ip, and
# forwards that one address. The FinPilot proxy trusts only the stand-in. Each test uses its own
# documentation addresses (203.0.113.0/24), because the sign-in limit counts per address.


def _start_sign_in(browser: httpx.Client, **headers: str) -> httpx.Response:
    return browser.get(
        f"{API}/auth/google/start",
        params={"return_to": "https://elsewhere.example"},
        headers=headers,
    )


def _limited(response: httpx.Response) -> bool:
    return response.status_code == 303 and "error=failed" in response.headers["location"]


@pytest.mark.phase("behind")
def test_each_real_client_gets_its_own_allowance_through_both_proxies(
    anonymous: httpx.Client,
) -> None:
    """The client address reported at the edge reaches the API intact, through two proxies."""
    for _ in range(20):
        response = _start_sign_in(anonymous, **{"Cf-Connecting-Ip": "203.0.113.10"})
        assert response.status_code == 422, response.text[:200]
    assert _limited(_start_sign_in(anonymous, **{"Cf-Connecting-Ip": "203.0.113.10"}))
    # A forged X-Forwarded-For changes nothing: the edge reads only Cf-Connecting-Ip.
    forged = {"Cf-Connecting-Ip": "203.0.113.10", "X-Forwarded-For": "198.51.100.7"}
    assert _limited(_start_sign_in(anonymous, **forged))
    # Someone else, at another address, isn't limited by the first person's attempts.
    assert _start_sign_in(anonymous, **{"Cf-Connecting-Ip": "203.0.113.11"}).status_code == 422


@pytest.mark.phase("behind")
def test_the_finpilot_proxy_serves_only_its_own_site() -> None:
    with httpx.Client(base_url=setting("STACK_PROXY_URL"), timeout=30) as plain:
        assert plain.get(f"{API}/healthz", headers={"Host": setting("STACK_HOST")}).json() == {
            "status": "ok"
        }
        other = plain.get(f"{API}/healthz", headers={"Host": "other.example"})
    assert other.status_code != 200 or other.content != b'{"status":"ok"}'


@pytest.mark.phase("behind")
def test_zz_reaching_the_finpilot_proxy_directly_buys_no_new_attempts() -> None:
    """Anyone on the edge network but the edge itself is untrusted: forged headers are ignored,
    so the connecting address keeps one allowance however the headers vary."""
    with httpx.Client(base_url=setting("STACK_PROXY_URL"), timeout=30) as plain:
        responses = [
            _start_sign_in(
                plain,
                Host=setting("STACK_HOST"),
                **{"X-Forwarded-For": f"203.0.113.{100 + n}", "Cf-Connecting-Ip": f"203.0.113.{n}"},
            )
            for n in range(22)
        ]
    assert all(r.status_code in (422, 303) for r in responses), [r.status_code for r in responses]
    assert sum(r.status_code == 422 for r in responses) <= 20
    assert _limited(responses[-1])


# --- Load: PDF exports while the stack-test script samples CPU and memory


@pytest.mark.phase("load")
def test_pdf_exports_under_load(person: Person) -> None:
    _spend(person, description="Load check")
    durations: list[float] = []

    def export(locale: str) -> None:
        started = time.monotonic()
        pdf = person.browser.get(f"{API}/reports/export.pdf", params={"locale": locale})
        assert pdf.status_code == 200, pdf.text[:300]
        assert pdf.content.startswith(b"%PDF")
        durations.append(time.monotonic() - started)

    for locale in ("en", "ar", "tr", "en", "ar", "tr", "en", "ar"):
        export(locale)
    # Two at a time: PDF_MAX_CONCURRENT is 2, so neither is refused.
    for _ in range(3):
        pair = [threading.Thread(target=export, args=(locale,)) for locale in ("en", "ar")]
        for thread in pair:
            thread.start()
        for thread in pair:
            thread.join()
    assert len(durations) == 14
    assert max(durations) < 30, durations
    print(f"PDF durations (s): {', '.join(f'{d:.2f}' for d in durations)}")


# --- Persistence across restarts


@pytest.mark.phase("main")
def test_a_persistent_record_is_written(
    owner_engine: Engine, tls: ssl.SSLContext | None, origin: str
) -> None:
    keeper = signed_in(owner_engine, tls, origin, email=PERSISTENT_EMAIL)
    try:
        if not ok(keeper.browser.get(f"{API}/transactions"))["items"]:
            _spend(keeper, amount="77.70", description="Survives a restart")
    finally:
        keeper.browser.close()


@pytest.mark.phase("after_restart")
def test_records_survive_restarting_every_container(
    owner_engine: Engine, tls: ssl.SSLContext | None, origin: str, anonymous: httpx.Client
) -> None:
    assert ok(anonymous.get(f"{API}/readyz")) == {"status": "ready"}
    keeper = signed_in(owner_engine, tls, origin, email=PERSISTENT_EMAIL)
    try:
        items = ok(keeper.browser.get(f"{API}/transactions"))["items"]
        assert any(item.get("description") == "Survives a restart" for item in items)
    finally:
        keeper.browser.close()
