"""Spending Intelligence and the sidebar's accent, in a real browser."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests_e2e.conftest import Session

TAB = "/app/analytics?tab=intelligence"
TITLES = (
    "Key insights",
    "Spending pace",
    "Period comparisons",
    "Category intelligence",
    "Fixed and variable spending",
    "Income and commitments",
    "Trip spending",
)


def open_tab(session: Session, locale: str = "en") -> Page:
    session.login("Alice")
    session.go(TAB, locale)
    return session.page


def test_the_tab_sits_inside_analytics_and_shows_real_figures(session: Session) -> None:
    page = open_tab(session)
    expect(page.get_by_role("heading", name="Spending Intelligence", level=2)).to_be_visible()
    for title in TITLES:
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    table = page.get_by_role("table", name="Period comparisons")
    expect(table.get_by_role("rowheader", name=re.compile("Month to date"))).to_be_visible()
    # Everything was recorded today, so history doesn't reach back: no invented percentage.
    expect(table.get_by_text(re.compile("records don't reach back")).first).to_be_visible()
    assert session.console_errors == [], session.console_errors[:3]


def test_a_period_change_refetches_and_existing_tabs_still_work(session: Session) -> None:
    page = open_tab(session)
    button = page.get_by_role("button", name="Year to date", exact=True)
    with page.expect_response(lambda r: "intelligence" in r.url and "period=ytd" in r.url):
        button.click()
    expect(button).to_have_attribute("aria-pressed", "true")
    page.get_by_role("tab", name="Reports & exports").click()
    expect(page.get_by_role("button", name=re.compile("PDF"))).to_be_visible()


def test_charts_have_tables_and_exact_values(session: Session) -> None:
    page = open_tab(session)
    card = page.get_by_role("region", name="Fixed and variable by month")
    card.get_by_role("button", name="Table", exact=True).click()
    expect(card.get_by_role("table")).to_be_visible()
    weekdays = page.get_by_role("region", name="Daily pattern")
    monday = weekdays.get_by_role("button", name=re.compile("Monday"))
    monday.click()
    expect(weekdays.locator("p[aria-live]")).to_contain_text("Monday")


def test_a_category_opens_its_transactions(session: Session) -> None:
    page = open_tab(session)
    section = page.get_by_role("region", name="Category intelligence")
    with page.expect_response(
        lambda r: "/transactions?" in r.url and "category_id" in r.url
    ) as hit:
        section.get_by_role("link", name="Open transactions").first.click()
    url = hit.value.url
    assert "from=" in url and "currency=EUR" in url
    expect(page).to_have_url(re.compile(r"/app/transactions\?"))


def test_a_filter_states_what_it_cannot_cover(session: Session) -> None:
    page = open_tab(session)
    page.get_by_role("combobox", name="Category").click()
    page.get_by_role("option", name="Groceries", exact=True).click()
    expect(page.get_by_text(re.compile("commitments can't be shown"))).to_be_visible()


@pytest.mark.parametrize("width", [320, 390, 768, 1366])
@pytest.mark.parametrize("locale", ["en", "ar", "tr"])
def test_no_sideways_scroll(session: Session, locale: str, width: int) -> None:
    session.page.set_viewport_size({"width": width, "height": 900})
    page = open_tab(session, locale)
    expect(page.get_by_role("heading", level=2).first).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0


def test_reduced_motion_is_respected(session: Session) -> None:
    session.page.emulate_media(reduced_motion="reduce")
    page = open_tab(session)
    names = page.evaluate(
        "() => [...document.querySelectorAll('main *, aside *')]"
        ".map((n) => getComputedStyle(n).animationName).filter((a) => a !== 'none')"
    )
    assert names == []


def test_the_active_page_is_not_shown_by_color_alone(session: Session) -> None:
    session.login("Alice")
    session.go("/app/analytics")
    active = session.page.locator("aside a[aria-current=page]")
    expect(active).to_have_text("Analytics")
    assert active.evaluate("(a) => getComputedStyle(a, '::before').width") != "0px"
    assert int(active.evaluate("(a) => getComputedStyle(a).fontWeight")) >= 600
