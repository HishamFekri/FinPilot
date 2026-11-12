"""Downloading your data and asking to delete your account, in a real browser."""

import json
import re
from pathlib import Path

from playwright.sync_api import expect

from tests_e2e.conftest import WEB, Server, Session
from tests_e2e.test_sign_in_flow import click_google, google_signs_in


def _fresh_sign_in(session: Session, server: Server, subject: str) -> str:
    """Its own account: asking to delete changes it, and other tests reuse the seeded people."""
    session.traffic.google = google_signs_in(server, subject, "Fresh Person")
    session.page.goto(f"{WEB}/en")
    click_google(session.page)
    session.page.wait_for_url(f"{WEB}/en/app**")
    return f"{subject}@example.test"


def test_downloading_my_data_gives_me_a_private_file(session: Session, server: Server) -> None:
    email = _fresh_sign_in(session, server, "download-check")
    page = session.page
    session.go("/app/settings/privacy")
    with page.expect_download() as caught:
        page.get_by_role("button", name="Download my data").click()
    download = caught.value
    assert re.fullmatch(r"finpilot-data-\d{4}-\d{2}-\d{2}\.json", download.suggested_filename)
    data = json.loads(Path(download.path()).read_text(encoding="utf-8"))
    assert data["profile"]["email"] == email and data["complete"] is True
    expect(page.get_by_text("Your download has started.")).to_be_visible()


def test_asking_to_delete_can_be_undone(session: Session, server: Server) -> None:
    email = _fresh_sign_in(session, server, "delete-check")
    page = session.page
    session.go("/app/settings/privacy")
    expect(page.get_by_role("heading", name="What gets deleted")).to_be_visible()
    expect(page.get_by_role("heading", name="What stays")).to_be_visible()

    delete = page.get_by_role("button", name="Delete my account")
    expect(delete).to_be_disabled()
    page.get_by_role("textbox").fill(email)
    page.get_by_role("checkbox").check()
    expect(delete).to_be_enabled()
    delete.click()

    keep = page.get_by_role("button", name="Keep my account")
    expect(keep).to_be_visible()
    expect(page.get_by_text("This account is waiting to be deleted")).to_be_visible()

    keep.click()
    expect(page.get_by_role("button", name="Delete my account")).to_be_visible()
    expect(page.get_by_text("This account is waiting to be deleted")).to_have_count(0)


def test_open_trip_items_block_deletion_and_say_why(session: Session) -> None:
    session.login("Alice")  # owns the seeded trip, with other people on it
    session.go("/app/settings/privacy")
    page = session.page
    expect(page.get_by_text("Your account can't be deleted yet")).to_be_visible()
    expect(page.get_by_role("button", name="Delete my account")).to_have_count(0)
    expect(page.get_by_text("ask the operator to erase", exact=False)).to_be_visible()


def test_the_page_reads_right_to_left_in_arabic(session: Session) -> None:
    session.login("Alice")
    session.go("/app/settings/privacy", "ar")
    page = session.page
    expect(page.get_by_role("heading", level=1)).to_have_text("الخصوصية وبياناتك")
    assert page.evaluate("() => document.documentElement.dir") == "rtl"
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1
