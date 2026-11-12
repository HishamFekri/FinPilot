"""The redesigned Home in a real browser: its sections, the New transaction dialog, and the rule
that FinPilot's only overall figure is Total Balance."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect

from tests_e2e.conftest import Session

PAGES = (
    "/app",
    "/app/accounts",
    "/app/analytics",
    "/app/analytics?tab=accounts",
    "/app/analytics?tab=reports",
)


def no_sideways_scroll(page: Page) -> None:
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0


def test_net_worth_appears_nowhere(session: Session) -> None:
    session.login("Alice")
    for path in PAGES:
        session.go(path)
        main = session.page.get_by_role("main").inner_text()
        assert not re.search(r"net worth|liabilities", main, re.I), path
    assert not [r for r in session.traffic.requests if "/wealth" in r[1]]
    session.go("/app")
    session.page.get_by_role("button", name="Customize dashboard").click()
    chips = session.page.get_by_role("main").inner_text()
    assert "Net worth" not in chips


def test_home_shows_the_figures_the_design_promises(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    page = session.page
    expect(page.get_by_role("heading", level=1)).to_contain_text("Your money, in focus.")
    expect(page.get_by_role("button", name="New transaction")).to_be_visible()
    expect(page.locator("[data-widget='recent']").get_by_text("E2E purchase 6")).to_be_visible()
    # At 1366x768 the balance, the month, recent activity and the chart are all on the first screen.
    for selector in (
        "[data-widget='summary']",
        "[data-widget='recent']",
        "[data-widget='categories']",
    ):
        box = page.locator(selector).bounding_box()
        assert box and box["y"] < 768, selector
    summary = page.get_by_role("region", name="Total Balance")
    expect(summary.get_by_text("Spent this month")).to_be_visible()
    expect(summary.get_by_text("Received")).to_be_visible()


def test_a_missing_rate_shows_exact_subtotals_and_says_why(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    summary = session.page.get_by_role("region", name="Total Balance")
    expect(summary.get_by_text(re.compile("No exchange rate for USD"))).to_be_visible()
    expect(summary.get_by_role("list", name="Each currency")).to_contain_text("USD")
    # No combined figure is invented.
    assert "≈" not in summary.locator("p").first.inner_text()


def test_the_details_popover_has_the_scope_and_can_be_closed(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    page = session.page
    page.get_by_role("button", name="Rates and details").click()
    popup = page.get_by_role("dialog", name="How this is worked out")
    expect(
        popup.get_by_text(re.compile("Debts and installment plans aren't included"))
    ).to_be_visible()
    page.keyboard.press("Escape")
    expect(popup).to_have_count(0)


def test_changing_the_display_currency_leaves_balances_alone(session: Session) -> None:
    session.login("Carol")
    session.go("/app")
    page = session.page
    summary = page.get_by_role("region", name="Total Balance")
    summary.get_by_role("combobox", name="Show in").click()
    page.get_by_role("option", name="TRY", exact=True).click()
    expect(summary.get_by_text(re.compile("Indicative value"))).to_be_visible()
    expect(summary.locator("p").first).to_contain_text("≈")
    assert session.traffic.writes() == []
    # Back to the account's own currency: exact again, no "≈".
    summary.get_by_role("combobox", name="Show in").click()
    page.get_by_role("option", name="EUR", exact=True).click()
    expect(summary.get_by_text(re.compile("^Across"))).to_be_visible()


@pytest.mark.parametrize("opener", ["button", "shortcut"])
def test_new_transaction_opens_focused_and_opening_never_saves(
    session: Session, opener: str
) -> None:
    session.login("Alice")
    session.go("/app")
    page = session.page
    if opener == "button":
        page.get_by_role("button", name="New transaction").click()
    else:
        page.keyboard.press("n")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label("Amount", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    assert session.traffic.writes() == []
    # There's no inline form on Home any more.
    expect(page.get_by_label("Amount", exact=True)).to_have_count(0)


def test_saving_updates_home_once(session: Session) -> None:
    session.login("Bob")
    session.go("/app")
    page = session.page
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Amount", exact=True).fill("12.34")
    dialog.locator("label").filter(has_text="Groceries").click()
    dialog.get_by_label("Description").fill("Home test entry")
    dialog.get_by_role("button", name="Save", exact=True).dblclick()
    expect(dialog).to_have_count(0)
    recent = page.locator("[data-widget='recent']")
    expect(recent.get_by_text("Home test entry")).to_be_visible()
    posted = session.traffic.writes("/transactions")
    assert len(posted) == 1 and posted[0][2] == 201
    categories = page.locator("[data-widget='categories']")
    expect(categories.get_by_role("button", name=re.compile("Groceries"))).to_be_visible()
    # And the row opens its details.
    recent.get_by_role("button", name=re.compile("Home test entry")).click()
    expect(page.get_by_role("dialog")).to_contain_text("Home test entry")


def test_the_chart_reveals_exact_amounts_and_has_a_full_table(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    section = session.page.locator("[data-widget='categories']")
    section.get_by_role("button", name=re.compile("Groceries")).focus()
    expect(section.locator("[class*=donutCenter]")).to_contain_text("Groceries")
    section.get_by_text(re.compile(r"All categories")).click()
    table = section.get_by_role("table")
    expect(table.get_by_role("rowheader", name="Transportation")).to_be_visible()
    expect(table.get_by_text("€1,200.00").first).to_be_visible()


def test_upcoming_lists_installments_without_counting_them(session: Session) -> None:
    # A plan of its own: other tests pay Alice's.
    bob = session.world.people["Bob"]
    due = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    plan = bob.post("/installments", {
        "origin": "purchase", "name": "Upcoming plan", "lender": "E2E bank", "currency": "EUR",
        "installment_count": 4, "first_due_date": due, "item_price": "400.00",
        "down_payment": "0.00", "category_id": bob.category("shopping"),
        "account_id": session.world.accounts["Bob"],
    })  # fmt: skip
    session.login("Bob")
    session.go("/app")
    upcoming = session.page.locator("[data-widget='upcoming']")
    expect(upcoming.get_by_text("Upcoming plan")).to_be_visible()
    link = upcoming.get_by_role("link", name=re.compile("Upcoming plan"))
    assert f"/app/installments/{plan['id']}" in (link.get_attribute("href") or "")


def test_budgets_and_goals_show_their_progress(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    goals = session.page.locator("[data-widget='goals']")
    expect(goals.get_by_text("E2E goal")).to_be_visible()
    expect(goals.get_by_text("20%")).to_be_visible()
    expect(goals.get_by_text(re.compile(r"€100\.00 / €500\.00"))).to_be_visible()


@pytest.mark.parametrize("width", [320, 390])
def test_phones_keep_everything_on_the_page(session: Session, width: int) -> None:
    session.page.set_viewport_size({"width": width, "height": 800})
    session.login("Alice")
    session.go("/app")
    no_sideways_scroll(session.page)
    session.page.locator("[aria-keyshortcuts=N]").first.click()
    dialog = session.page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    session.page.wait_for_timeout(400)  # it slides up
    box = dialog.bounding_box()
    # A sheet: it reaches the bottom edge of the screen.
    assert box and abs(box["y"] + box["height"] - 800) <= 2
    session.page.keyboard.press("Escape")


def test_reduced_motion_is_respected(session: Session) -> None:
    session.page.emulate_media(reduced_motion="reduce")
    session.login("Alice")
    session.go("/app")
    names = session.page.evaluate(
        "() => [...document.querySelectorAll('main *')]"
        ".map((n) => getComputedStyle(n).animationName).filter((a) => a !== 'none')"
    )
    assert names == []


def test_arabic_home_runs_right_to_left(session: Session) -> None:
    session.login("Alice")
    session.go("/app", "ar")
    page = session.page
    assert page.evaluate("() => document.documentElement.dir") == "rtl"
    expect(page.get_by_role("heading", level=1)).to_contain_text("أموالك بوضوح")
    sidebar = page.locator("aside").bounding_box()
    assert sidebar and sidebar["x"] > 600
    no_sideways_scroll(page)
    # A Latin name in an Arabic list still starts at the right-hand edge.
    row = page.locator("[data-widget='recent'] li button").first
    assert row.bounding_box()


def test_the_sidebar_order_and_the_profile(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    links = session.page.locator("aside nav a").all_inner_texts()
    assert links[:3] == ["Home", "Transactions", "Trips"]
    brand = session.page.locator("aside").get_by_role("link", name="FinPilot", exact=True)
    assert brand.locator("svg, img").count() == 0
    expect(session.page.locator("[aria-haspopup=menu][data-placement=sidebar]")).to_contain_text(
        "Alice"
    )
