"""The shell in a real browser: FinPilot's dropdown, the profile menu, signing out, the wordmark.

jsdom has no layout, so placement (RTL alignment, layering above dialogs) is checked here.
"""

import re

from playwright._impl._api_structures import FloatRect
from playwright.sync_api import Locator, expect

from tests_e2e.conftest import WEB, Session

PRIVATE = "Dan's bank"  # Dan's account name: never visible once he has signed out


def box(locator: Locator) -> FloatRect:
    found = locator.bounding_box()
    assert found is not None
    return found


def type_filter(s: Session, locale: str = "en") -> Locator:
    s.go("/app/transactions", locale)
    return s.page.locator("[role=combobox]").first


def test_dropdown_works_from_the_keyboard(session: Session) -> None:
    session.login("Alice")
    trigger = type_filter(session)
    expect(trigger).to_have_text(re.compile("All types"))
    trigger.focus()
    session.page.keyboard.press("ArrowDown")
    listbox = session.page.get_by_role("listbox")
    expect(listbox).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    # The selected option is highlighted first; End, Home and the arrows move from there.
    selected = listbox.get_by_role("option", name="All types")
    expect(selected).to_have_attribute("aria-selected", "true")
    session.page.keyboard.press("End")
    session.page.keyboard.press("Home")
    session.page.keyboard.press("ArrowDown")
    session.page.keyboard.press("Enter")
    expect(listbox).to_have_count(0)
    expect(trigger).to_be_focused()
    expect(trigger).not_to_have_text(re.compile("All types"))
    # Typeahead: typing a label's first letters picks it.
    session.page.keyboard.press("ArrowDown")
    expect(session.page.get_by_role("listbox")).to_be_visible()
    session.page.keyboard.type("inc")
    session.page.keyboard.press("Enter")
    expect(trigger).to_have_text(re.compile("Income"))


def test_escape_closes_without_changing_and_returns_focus(session: Session) -> None:
    session.login("Alice")
    trigger = type_filter(session)
    trigger.click()
    listbox = session.page.get_by_role("listbox")
    expect(listbox).to_be_visible()
    session.page.keyboard.press("ArrowDown")
    session.page.keyboard.press("Escape")
    expect(listbox).to_have_count(0)
    expect(trigger).to_be_focused()
    expect(trigger).to_have_text(re.compile("All types"))


def test_dropdown_opens_under_its_button_on_the_correct_side(session: Session) -> None:
    session.login("Alice")
    for locale, edge in (("en", "left"), ("ar", "right")):
        trigger = type_filter(session, locale)
        trigger.click()
        listbox = session.page.get_by_role("listbox")
        expect(listbox).to_be_visible()
        popup = listbox.locator("xpath=..")  # the panel around the list
        session.page.wait_for_timeout(300)  # the opening transition
        t, p = box(trigger), box(popup)
        assert p["y"] >= t["y"] + t["height"], (locale, t, p)
        if edge == "left":
            assert abs(p["x"] - t["x"]) <= 2, (locale, t, p)
        else:
            assert abs((p["x"] + p["width"]) - (t["x"] + t["width"])) <= 2, (locale, t, p)
        session.page.keyboard.press("Escape")


def test_dropdown_inside_a_dialog_is_on_top_and_keeps_the_dialog_open(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    session.page.get_by_role("button", name="New transaction").first.click()
    dialog = session.page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    trigger = dialog.get_by_role("combobox", name="Paid from")
    trigger.click()
    options = session.page.get_by_role("option")
    expect(options.first).to_be_visible()
    last = options.last
    b = box(last)
    on_top = session.page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y)?.closest('[role=option]')?.textContent",
        [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2],
    )
    assert on_top == last.text_content(), "the option is covered by something else"
    label = last.text_content() or ""
    last.click()
    expect(session.page.get_by_role("listbox")).to_have_count(0)
    expect(dialog).to_be_visible()
    expect(trigger).to_have_text(label)
    assert session.traffic.writes() == []


def test_profile_menu_shows_the_person_and_reaches_settings(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    profile = session.page.get_by_role("button", name="Account menu: Alice")
    expect(profile).to_be_visible()
    expect(profile).to_contain_text("Alice")
    # No separate Settings link in the navigation.
    nav = session.page.get_by_role("navigation", name="Main navigation").first
    expect(nav.get_by_role("link", name="Settings")).to_have_count(0)
    profile.click()
    menu = session.page.get_by_role("menu")
    expect(menu).to_be_visible()
    expect(menu.get_by_text("alice@example.test")).to_be_visible()
    menu.get_by_role("menuitem", name="Settings").click()
    session.page.wait_for_url(f"{WEB}/en/app/settings")
    profile.click()
    session.page.get_by_role("menu").get_by_role("menuitem", name="Signed-in devices").click()
    session.page.wait_for_url(f"{WEB}/en/app/settings/sessions")


def test_phone_reaches_settings_from_the_more_sheet(session: Session) -> None:
    session.page.set_viewport_size({"width": 390, "height": 844})
    session.login("Alice")
    session.go("/app")
    session.page.get_by_role("button", name="More", exact=True).click()
    sheet = session.page.get_by_role("dialog")
    expect(sheet.get_by_text("Alice", exact=True)).to_be_visible()
    sheet.get_by_role("link", name="Settings").click()
    session.page.wait_for_url(f"{WEB}/en/app/settings")


def test_wordmark_is_text_only(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    for brand in session.page.get_by_role("link", name="FinPilot", exact=True).all():
        assert brand.locator("svg, img").count() == 0


def sign_out(s: Session, locale: str) -> None:
    s.page.locator("[aria-haspopup=menu][data-placement=sidebar]").click()
    s.page.get_by_role("menu").get_by_role("menuitem").last.click()


def test_signing_out_goes_to_the_landing_page_and_back_shows_nothing(session: Session) -> None:
    session.login("Dan")
    session.go("/app")
    session.go("/app/accounts")
    expect(session.page.get_by_text(PRIVATE).first).to_be_visible()
    sign_out(session, "en")
    session.page.wait_for_url(f"{WEB}/en")
    assert any("/auth/logout" in r[1] and r[2] == 204 for r in session.traffic.requests)
    names = {c["name"] for c in session.context.cookies()}
    assert not names & {"fp_session", "__Host-fp_session"}, "the API cleared the session cookie"
    # Back returns to the app page, which asks the API and finds no session.
    session.page.go_back()
    session.page.wait_for_url(f"{WEB}/en")
    session.page.wait_for_load_state("networkidle")
    assert PRIVATE not in session.page.content()
    expect(session.page.get_by_role("button", name=re.compile("Account menu"))).to_have_count(0)


def test_signing_out_keeps_the_language(session: Session) -> None:
    session.login("Eve")
    session.go("/app", "ar")
    sign_out(session, "ar")
    session.page.wait_for_url(f"{WEB}/ar")
    assert session.page.evaluate("() => document.documentElement.lang") == "ar"


def test_failed_sign_out_stays_signed_in_and_says_so(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    session.page.route(f"{WEB}/api/v1/auth/logout", lambda route: route.abort())
    sign_out(session, "en")
    expect(session.page.get_by_text("You're still signed in")).to_be_visible()
    assert session.page.url == f"{WEB}/en/app"
    session.page.unroute(f"{WEB}/api/v1/auth/logout")
    # Still signed in for real: the app keeps working.
    session.go("/app/transactions")
    expect(session.page.get_by_text("E2E purchase 1").first).to_be_visible()
