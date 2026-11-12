"""Browser-level security: what only a real browser can show. Test accounts and fake Google only."""

import re

from playwright.sync_api import expect

from tests_e2e.conftest import API, WEB, Server, Session
from tests_e2e.test_sign_in_flow import click_google, google_signs_in

XSS = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'


def test_real_sign_in_sets_cookies_scripts_cant_read(session: Session, server: Server) -> None:
    page = session.page
    session.traffic.google = google_signs_in(server, "cookie-check", "Cookie Check")
    page.goto(f"{WEB}/en")
    click_google(page)
    page.wait_for_url(f"{WEB}/en/app**")
    cookies = {c["name"]: c for c in session.context.cookies()}
    assert cookies["fp_session"]["httpOnly"] is True
    assert cookies["fp_session"]["sameSite"] == "Lax"
    assert cookies["fp_csrf"]["httpOnly"] is False
    assert "fp_oauth" not in cookies  # cleared once sign-in finishes
    assert "fp_session" not in page.evaluate("() => document.cookie")


def test_private_pages_send_protective_headers(session: Session) -> None:
    session.login("Alice")
    response = session.page.goto(f"{WEB}/en/app")
    assert response is not None
    headers = response.headers
    csp = headers["content-security-policy"]
    assert "'nonce-" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    script = re.search(r"script-src ([^;]*)", csp)
    assert script and "'unsafe-inline'" not in script.group(1)
    assert "no-store" in headers["cache-control"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_stored_markup_is_shown_as_text_and_never_runs(session: Session, server: Server) -> None:
    alice = server.world.people["Alice"]
    alice.post("/transactions", {
        "type": "expense", "account_id": server.world.accounts["Alice"],
        "amount": {"amount": "1.00", "currency": "EUR"},
        "category_id": alice.category("groceries"), "description": XSS[:100],
    })  # fmt: skip
    session.login("Alice")
    for path in ("/app", "/app/transactions"):
        session.go(path)
        session.page.wait_for_timeout(300)
        assert session.page.evaluate("() => window.__pwned") is None, path
        assert session.page.locator("main img[src='x']").count() == 0, path
    expect(session.page.get_by_text("onerror", exact=False).first).to_be_visible()


def test_a_next_address_can_only_be_inside_the_app(session: Session) -> None:
    page = session.page
    for evil in (
        "https://evil.example",
        "//evil.example",
        "/en/app/../../evil",
        "javascript:alert(1)",
    ):
        page.goto(f"{WEB}/en?next={evil}")
        page.wait_for_load_state("networkidle")
        href = page.locator("a[href*='/auth/google/start']").first.get_attribute("href") or ""
        assert "return_to=%2Fen%2Fapp" in href and href.endswith("%2Fapp"), (evil, href)


def test_nothing_financial_is_kept_in_browser_storage(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    session.go("/app/transactions")
    keys = session.page.evaluate(
        "() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)]"
    )
    assert set(keys) <= {"fp_theme", "fp_display_currency", "theme"}, keys


def test_an_invitation_token_stays_out_of_every_request(session: Session, server: Server) -> None:
    alice = server.world.people["Alice"]
    token = alice.post(f"/trips/{server.world.trip}/invitations", {"label": "Guest"})["token"]
    page = session.page
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url))
    session.login("Carol")
    page.goto(f"{WEB}/en/invite#{token}")
    page.wait_for_load_state("networkidle")
    assert all(token not in url for url in seen if f"{API}/invitations" not in url)
    assert token not in page.url


def test_after_sign_out_the_api_refuses_the_old_session(session: Session, server: Server) -> None:
    # Its own account: signing out revokes the session, and other tests reuse the seeded people.
    page = session.page
    session.traffic.google = google_signs_in(server, "sign-out-check", "Sign Out Check")
    page.goto(f"{WEB}/en")
    click_google(page)
    page.wait_for_url(f"{WEB}/en/app**")
    page.locator("[aria-haspopup=menu][data-placement=sidebar]").click()
    page.get_by_role("menu").get_by_role("menuitem").last.click()
    page.wait_for_url(f"{WEB}/en")
    status = page.evaluate(f"async () => (await fetch('{API}/auth/me')).status")
    assert status == 401
