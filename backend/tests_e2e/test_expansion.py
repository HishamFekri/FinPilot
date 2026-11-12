"""The financial expansion in a real browser: Quick Add's account choice, installments, analytics,
Total Balance, and private trip budgets."""

import re

from playwright.sync_api import expect

from tests_e2e.conftest import Session


def test_a_usd_amount_goes_to_the_usd_account_unless_one_was_chosen(session: Session) -> None:
    session.login("Alice")
    session.go("/app")
    session.page.get_by_role("button", name="New transaction").click()
    dialog = session.page.get_by_role("dialog")
    form = dialog
    form.get_by_role("combobox", name="Currency").click()
    session.page.get_by_role("option", name="USD", exact=True).click()
    expect(form.get_by_role("combobox", name="Paid from")).to_contain_text("Alice's dollars")
    form.get_by_label("Amount", exact=True).fill("22")
    form.locator("label").filter(has_text="Groceries").click()  # the chip, as a person taps it
    form.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).to_have_count(0)
    posted = session.traffic.writes("/transactions")
    assert len(posted) == 1 and posted[0][2] == 201
    # The USD account paid: no conversion was asked for or sent.
    session.go(f"/app/accounts/{session.world.accounts['Alice USD']}")
    expect(session.page.get_by_text("278.00", exact=False).first).to_be_visible()


def test_recording_an_installment_payment_marks_it_paid(session: Session) -> None:
    session.login("Alice")
    session.go(f"/app/installments/{session.world.plan}")
    page = session.page
    expect(page.get_by_role("heading", level=1, name="E2E car")).to_be_visible()
    expect(page.get_by_text("0 of 10")).to_be_visible()
    page.get_by_role("button", name="Record a payment").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_label("Principal")).to_have_value("100.00")
    dialog.get_by_role("button", name="Record payment").click()
    expect(page.get_by_text("Payment recorded.")).to_be_visible()
    expect(page.get_by_text("1 of 10")).to_be_visible()
    payments = session.traffic.writes("/payments")
    assert len(payments) == 1 and payments[0][2] == 201


def test_old_insights_and_reports_addresses_open_analytics(session: Session) -> None:
    session.login("Alice")
    session.go("/app/insights")
    assert session.page.url.endswith("/en/app/analytics?tab=spending")
    expect(session.page.get_by_role("tab", name="Spending", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    session.go("/app/reports")
    assert session.page.url.endswith("/en/app/analytics?tab=reports")
    # The exports are still there.
    expect(session.page.get_by_role("button", name=re.compile("PDF"))).to_be_visible()
    # Tabs work from the keyboard.
    session.page.get_by_role("tab", name="Reports & exports").focus()
    session.page.keyboard.press("ArrowRight")
    session.page.keyboard.press("Enter")
    expect(session.page.get_by_role("tab", name="Accounts")).to_have_attribute(
        "aria-selected", "true"
    )


def test_accounts_show_total_balance_and_nothing_about_net_worth(session: Session) -> None:
    session.login("Alice")
    session.go("/app/accounts")
    total = session.page.get_by_role("region", name="Total Balance")
    expect(total.get_by_text(re.compile("No exchange rate for USD"))).to_be_visible()
    text = session.page.get_by_role("main").inner_text()
    assert "Liabilities" not in text and "Net worth" not in text


def test_a_personal_trip_budget_is_only_visible_to_its_owner(session: Session) -> None:
    session.login("Bob")
    trip = session.world.trip
    session.go(f"/app/trips/{trip}")
    budgets = session.page.get_by_role("region", name="Budgets")
    expect(budgets.get_by_text("Only you can see this.")).to_be_visible()
    budgets.get_by_role("button", name="Set").last.click()
    dialog = session.page.get_by_role("dialog")
    dialog.get_by_label("Amount").fill("987")
    dialog.get_by_role("button", name="Save").click()
    expect(session.page.get_by_role("dialog")).to_have_count(0)
    expect(session.page.get_by_text(re.compile("987"))).not_to_have_count(0)

    session.context.clear_cookies()
    session.login("Alice")
    session.go(f"/app/trips/{trip}")
    expect(session.page.get_by_role("heading", name="Budgets")).to_be_visible()
    assert "987" not in session.page.content()
