"""The landing page is the only way in: there is no standalone sign-in page.

Google is faked at its own address: the browser really goes through the start route, a redirect to
accounts.google.com, and the real callback, which checks state, browser binding, nonce, and PKCE.
"""

import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.google_fakes import make_id_token
from tests_e2e.conftest import API, WEB, Server, Session

CALLBACK = f"{WEB}{API}/auth/google/callback"
# In each language: the button, then the text of the cancelled, unreachable and expired messages.
TEXT = {
    "en": (
        "Continue with Google",
        "Sign-in was cancelled",
        "can't be reached",
        "session has ended",
    ),
    "ar": ("Google", "أُلغي تسجيل الدخول", "تعذّر الوصول", "انتهت جلستك"),
    "tr": ("Google", "Giriş iptal edildi", "ulaşılamıyor", "Oturumunuz sona erdi"),  # noqa: RUF001
}
LOCALES = ("en", "ar", "tr")
OLD_TITLES = ("Sign in to FinPilot", "Back to the home page")


def watch_for_old_page(page: Page) -> list[str]:
    """Every address the tab visits, to prove the old page is never on the way."""
    visited: list[str] = []
    page.on("framenavigated", lambda frame: visited.append(frame.url))
    return visited


def assert_never_on_old_page(page: Page, visited: list[str]) -> None:
    assert not [url for url in visited if "/sign-in" in url], visited
    body = page.locator("body").inner_text()
    assert not any(title in body for title in OLD_TITLES)
    assert page.locator("h1").count() == 1


def click_google(page: Page) -> None:
    page.locator("a[href*='/auth/google/start']").first.click()


def google_signs_in(server: Server, subject: str, name: str) -> Callable[[dict[str, str]], str]:
    """Google's consent screen saying yes: a real code, checked by the real callback."""

    def answer(query: dict[str, str]) -> str:
        token = make_id_token(
            nonce=query["nonce"], sub=subject, email=f"{subject}@example.test", name=name
        )
        code = server.google.issue_code(challenge=query["code_challenge"], id_token=token)
        return f"state={query['state']}&code={code}"

    return answer


def google_refuses(error: str) -> Callable[[dict[str, str]], str]:
    return lambda query: f"state={query['state']}&error={error}"


def banner(page: Page, tone: str = "error") -> Locator:
    """The landing page's message (Next.js keeps its own empty alert region on every page)."""
    return page.locator(f"[data-tone={tone}]")


@pytest.mark.parametrize("locale", LOCALES)
def test_landing_to_google_to_the_dashboard(session: Session, server: Server, locale: str) -> None:
    page = session.page
    visited = watch_for_old_page(page)
    session.traffic.google = google_signs_in(server, f"new-{locale}", "New Person")
    page.goto(f"{WEB}/{locale}")
    click_google(page)
    page.wait_for_url(f"{WEB}/{locale}/app**")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("() => document.documentElement.lang") == locale
    expect(page.locator("main")).to_be_visible()
    assert_never_on_old_page(page, visited)


@pytest.mark.parametrize("locale", LOCALES)
def test_a_cancelled_google_sign_in_returns_to_the_landing_page(
    session: Session, locale: str
) -> None:
    page = session.page
    visited = watch_for_old_page(page)
    session.traffic.google = google_refuses("access_denied")
    page.goto(f"{WEB}/{locale}")
    click_google(page)
    page.wait_for_url(f"{WEB}/{locale}")  # the error code has already left the address bar
    expect(banner(page)).to_contain_text(TEXT[locale][1])
    assert_never_on_old_page(page, visited)
    # A reload doesn't repeat it.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(banner(page)).to_have_count(0)


def test_a_failed_sign_in_can_be_retried_and_shows_no_details(
    session: Session, server: Server
) -> None:
    page = session.page
    session.traffic.google = google_refuses("server_error")
    page.goto(f"{WEB}/en")
    click_google(page)
    alert = banner(page)
    expect(alert).to_contain_text("Sign-in didn't complete")
    assert "server_error" not in alert.inner_text()

    # The retry link in the message goes round again, and this time Google says yes.
    session.traffic.google = google_signs_in(server, "retrier", "Retry Person")
    alert.get_by_role("link", name="Try again").click()
    page.wait_for_url(f"{WEB}/en/app**")


def test_a_tampered_callback_is_a_failure_and_never_a_redirect_loop(session: Session) -> None:
    page = session.page
    visited = watch_for_old_page(page)
    page.goto(f"{CALLBACK}?state=forged&code=forged")
    expect(banner(page)).to_contain_text("Sign-in didn't complete")
    assert page.url == f"{WEB}/en"
    assert_never_on_old_page(page, visited)
    assert len(visited) <= 3


@pytest.mark.parametrize("locale", LOCALES)
def test_old_sign_in_url_goes_to_the_landing_page(session: Session, locale: str) -> None:
    page = session.page
    response = page.goto(f"{WEB}/{locale}/sign-in")
    assert response is not None and response.status == 200
    assert page.url == f"{WEB}/{locale}"
    assert banner(page).count() == 0
    assert_never_on_old_page(page, [])


def test_old_sign_in_url_keeps_a_valid_error_and_drops_anything_else(session: Session) -> None:
    page = session.page
    page.goto(f"{WEB}/ar/sign-in?error=cancelled")
    expect(banner(page)).to_contain_text(TEXT["ar"][1])
    assert page.url == f"{WEB}/ar"
    page.goto(f"{WEB}/en/sign-in?next=https%3A%2F%2Fevil.example")
    assert page.url == f"{WEB}/en"
    page.goto(f"{WEB}/sign-in")  # no language in the address
    assert re.fullmatch(rf"{WEB}/(en|ar|tr)", page.url)


def test_signed_out_visit_to_the_app_lands_and_returns_there_after_google(
    session: Session, server: Server
) -> None:
    page = session.page
    session.traffic.google = google_signs_in(server, "returner", "Returning Person")
    page.goto(f"{WEB}/tr/app/goals")
    assert page.url == f"{WEB}/tr?next=%2Ftr%2Fapp%2Fgoals"
    click_google(page)
    page.wait_for_url(f"{WEB}/tr/app/goals")


@pytest.mark.parametrize("locale", LOCALES)
def test_an_ended_session_returns_to_the_landing_page_and_says_why(
    session: Session, locale: str
) -> None:
    page = session.page
    visited = watch_for_old_page(page)
    # Fay's sessions are ended on the server per language, so the browser's cookie is stale.
    name = f"Fay{locale}"
    session.login(name)
    session.world.people[name].post("/auth/logout")
    page.goto(f"{WEB}/{locale}/app/accounts")
    expect(banner(page, "notice").filter(has_text=TEXT[locale][3])).to_be_visible()
    assert page.url == f"{WEB}/{locale}?next=%2F{locale}%2Fapp%2Faccounts"
    assert not {c["name"] for c in session.context.cookies()} & {"fp_session", "__Host-fp_session"}
    assert_never_on_old_page(page, visited)


@pytest.mark.parametrize("locale", LOCALES)
def test_api_down_stays_on_the_landing_page_and_says_it_is_not_your_sign_in(
    session: Session, locale: str
) -> None:
    page = session.page
    visited = watch_for_old_page(page)
    page.goto(f"{WEB}/{locale}")
    page.wait_for_load_state("networkidle")
    session.context.route(f"{WEB}{API}/healthz", lambda route: route.abort())
    click_google(page)
    expect(banner(page)).to_contain_text(TEXT[locale][2])
    assert page.url == f"{WEB}/{locale}"
    assert_never_on_old_page(page, visited)
    # Once it's back, the same button works.
    session.context.unroute(f"{WEB}{API}/healthz")
    session.traffic.google = google_refuses("access_denied")
    click_google(page)
    expect(banner(page)).to_contain_text(TEXT[locale][1])


@pytest.mark.parametrize("locale", LOCALES)
def test_sign_out_goes_straight_to_the_landing_page_and_back_shows_nothing(
    session: Session, server: Server, locale: str
) -> None:
    page = session.page
    session.traffic.google = google_signs_in(server, f"leaver-{locale}", "Private Name")
    page.goto(f"{WEB}/{locale}")
    click_google(page)
    page.wait_for_url(f"{WEB}/{locale}/app**")
    page.wait_for_load_state("networkidle")
    visited = watch_for_old_page(page)
    page.goto(f"{WEB}/{locale}/app/accounts")
    page.wait_for_load_state("networkidle")
    page.locator("[aria-haspopup=menu][data-placement=sidebar]").click()
    page.get_by_role("menu").get_by_role("menuitem").last.click()
    page.wait_for_url(f"{WEB}/{locale}")
    assert any("/auth/logout" in r[1] and r[2] == 204 for r in session.traffic.requests)
    assert not {c["name"] for c in session.context.cookies()} & {"fp_session", "__Host-fp_session"}
    expect(banner(page)).to_have_count(0)

    page.go_back()
    page.wait_for_load_state("networkidle")
    assert re.match(rf"{WEB}/{locale}(\?|$)", page.url)
    assert "Private Name" not in page.content()
    assert_never_on_old_page(page, visited)
