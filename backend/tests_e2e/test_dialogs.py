"""The entry dialog in a real browser: opening, switching or cancelling never writes; Save once.

These guard the bug found in the everyday milestone: React reused the "Record refund" button as
the refund form's submit button, so the click that opened the form also submitted it. jsdom
doesn't reproduce that; a real browser does.
"""

import re

from playwright.sync_api import Locator, expect

from tests_e2e.conftest import Session

REFUNDS = "/refunds"
VERSIONS = "/versions"  # a correction is a new version of the entry


def open_entry(s: Session, description: str) -> Locator:
    s.login("Alice")
    s.go("/app/transactions")
    s.page.get_by_role("button", name=re.compile(description)).first.click()
    dialog = s.page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    return dialog


def type_amount(field: Locator, text: str) -> None:
    """The refund amount starts filled with what's left; replace it the way a person would."""
    field.click()
    field.press("ControlOrMeta+a")
    field.press_sequentially(text)


def settle(s: Session) -> None:
    """Lets any late request leave the browser before counting."""
    s.page.wait_for_timeout(600)
    s.page.wait_for_load_state("networkidle")


def test_opening_the_refund_form_writes_nothing(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 1")
    dialog.get_by_role("button", name="Record refund").click()
    expect(dialog.get_by_label("Refund amount (EUR)")).to_be_visible()
    settle(session)
    assert session.traffic.writes() == []


def test_opening_the_correction_form_writes_nothing(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 2")
    dialog.get_by_role("button", name="Correct", exact=True).click()
    expect(dialog.get_by_role("button", name="Save correction")).to_be_visible()
    settle(session)
    assert session.traffic.writes() == []


def test_cancelling_writes_nothing(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 3")
    dialog.get_by_role("button", name="Record refund").click()
    type_amount(dialog.get_by_label("Refund amount (EUR)"), "5")
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("button", name="Correct", exact=True).click()
    dialog.get_by_role("button", name="Back").click()
    session.page.keyboard.press("Escape")
    expect(session.page.get_by_role("dialog")).to_have_count(0)
    settle(session)
    assert session.traffic.writes() == []


def test_save_posts_exactly_once(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 4")
    dialog.get_by_role("button", name="Record refund").click()
    type_amount(dialog.get_by_label("Refund amount (EUR)"), "4.50")
    dialog.get_by_role("button", name="Save refund").click()
    settle(session)
    writes = session.traffic.writes()
    assert [w[0] for w in writes] == ["POST"], (writes, session.traffic.errors)
    assert REFUNDS in writes[0][1] and writes[0][2] < 300, session.traffic.errors


def test_double_clicking_save_posts_once(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 5")
    dialog.get_by_role("button", name="Record refund").click()
    type_amount(dialog.get_by_label("Refund amount (EUR)"), "3")
    dialog.get_by_role("button", name="Save refund").dblclick()
    expect(session.page.get_by_text("Refund recorded.")).to_be_visible()
    settle(session)
    assert len(session.traffic.writes(REFUNDS)) == 1, session.traffic.writes()


def test_keyboard_opens_and_submits(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 6")
    dialog.get_by_role("button", name="Record refund").focus()
    session.page.keyboard.press("Enter")
    amount = dialog.get_by_label("Refund amount (EUR)")
    expect(amount).to_be_visible()
    settle(session)
    assert session.traffic.writes() == [], "Enter on 'Record refund' must only open the form"
    type_amount(amount, "2")
    amount.press("Enter")
    expect(session.page.get_by_text("Refund recorded.")).to_be_visible()
    settle(session)
    assert len(session.traffic.writes(REFUNDS)) == 1, session.traffic.writes()


def test_switching_views_never_reuses_the_submit_button(session: Session) -> None:
    dialog = open_entry(session, "E2E purchase 7")
    # A fast double click on the button that opens a form lands its second click on whatever
    # now sits in that spot, which is the form's submit button.
    dialog.get_by_role("button", name="Record refund").dblclick()
    expect(dialog.get_by_label("Refund amount (EUR)")).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("button", name="Correct", exact=True).dblclick()
    expect(dialog.get_by_role("button", name="Save correction")).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("button", name="Record refund").press("Enter")
    dialog.get_by_role("button", name="Back").press("Enter")
    settle(session)
    assert session.traffic.writes() == []
    assert not any(VERSIONS in r[1] or REFUNDS in r[1] for r in session.traffic.writes())
