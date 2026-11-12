"""Two people in real browsers: each sees only their own money; shared trips show only trip data."""

from playwright.sync_api import expect

from tests_e2e.conftest import Session


def test_people_see_only_their_own_entries(session: Session) -> None:
    session.login("Alice")
    session.go("/app/transactions")
    expect(session.page.get_by_text("E2E purchase 1", exact=True)).to_be_visible()
    assert "Bob private purchase" not in session.page.content()

    session.login("Bob")
    session.go("/app/transactions")
    expect(session.page.get_by_text("Bob private purchase")).to_be_visible()
    assert "E2E purchase" not in session.page.content()
    assert "Alice's bank" not in session.page.content()


def test_another_persons_account_page_is_not_found(session: Session) -> None:
    alice_account = session.world.accounts["Alice"]
    session.login("Bob")
    session.go(f"/app/accounts/{alice_account}")
    session.page.wait_for_timeout(500)
    assert "Alice's bank" not in session.page.content()
    assert "E2E purchase" not in session.page.content()


def test_trip_members_share_the_trip_but_not_their_accounts(session: Session) -> None:
    session.login("Bob")
    session.go(f"/app/trips/{session.world.trip}")
    expect(session.page.get_by_text("E2E dinner")).to_be_visible()
    content = session.page.content()
    assert "Alice's bank" not in content
    assert "E2E purchase" not in content


def test_an_outsider_cannot_open_the_trip(session: Session) -> None:
    session.login("Carol")
    session.go(f"/app/trips/{session.world.trip}")
    session.page.wait_for_timeout(500)
    assert "E2E dinner" not in session.page.content()
