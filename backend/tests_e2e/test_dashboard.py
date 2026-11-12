"""Home's customizable sections and Total Balance, and a blocked deletion, in a real browser.

Carol arranges her Home here, so Alice's (used by the page sweep) stays the default.
"""

import re

from playwright.sync_api import Locator, Page, expect

from tests_e2e.conftest import Session


def row_of(page: Page, widget: str) -> str | None:
    row: str | None = page.evaluate(
        "(w) => document.querySelector(`[data-widget='${w}']`)?.getAttribute('data-row') ?? null",
        widget,
    )
    return row


def rows(page: Page) -> list[list[str]]:
    found: list[list[str]] = page.evaluate(
        """() => {
          const out = [];
          for (const n of document.querySelectorAll('[data-widget]')) {
            (out[Number(n.dataset.row)] ??= []).push(n.dataset.widget);
          }
          return out;
        }"""
    )
    return found


DEFAULT = [["summary"], ["recent", "categories"], ["upcoming", "budgets", "goals"]]


def saved(page: Page, word: str = "Saved") -> None:
    expect(page.get_by_role("status").filter(has_text=word)).to_be_visible()


def drag(page: Page, handle: Locator, x: float, y: float) -> None:
    box = handle.bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(x, y, steps=12)
    page.mouse.up()


def still(session: Session) -> Page:
    """Moves apply at once, without the glide, so each step can be checked as it happens."""
    session.page.emulate_media(reduced_motion="reduce")
    return session.page


def test_sections_move_by_keyboard_and_drag_and_the_layout_is_kept(session: Session) -> None:
    session.page.set_viewport_size({"width": 1366, "height": 1700})  # everything within reach
    session.login("Carol")
    session.go("/app")
    page = still(session)
    expect(page.get_by_role("region", name="Total Balance")).to_be_visible()
    assert "Net worth" not in page.get_by_role("main").inner_text()
    assert rows(page) == DEFAULT

    page.get_by_role("button", name="Customize dashboard").click()
    expect(page.get_by_role("button", name="New transaction")).to_be_visible()
    # Keyboard: up takes Goals out of its row, onto its own; Home sends it to the top.
    handle = page.get_by_role("button", name="Move Goals")
    handle.focus()
    page.keyboard.press("ArrowUp")
    expect(handle).to_be_focused()
    assert rows(page)[2] == ["goals"]
    page.keyboard.press("Home")
    assert rows(page)[0] == ["goals"]
    # Left and right trade places in a row.
    categories = page.get_by_role("button", name="Move Spending by Category")
    categories.focus()
    page.keyboard.press("ArrowLeft")
    assert rows(page)[2] == ["categories", "recent"]

    # Drag: Budgets to a row of its own at the very top.
    top = page.locator("[data-widget='goals']").bounding_box()
    assert top
    drag(page, page.get_by_role("button", name="Move Budgets"), top["x"] + 40, top["y"] + 2)
    assert rows(page)[0] == ["budgets"]
    # Drag beside: into the middle of the Total Balance row, past its centre.
    target = page.locator("[data-widget='summary']").bounding_box()
    assert target
    drag(
        page,
        page.get_by_role("button", name="Move Upcoming"),
        target["x"] + target["width"] * 0.8,
        target["y"] + target["height"] / 2,
    )
    assert ["summary", "upcoming"] in rows(page)

    # Hide a section; it can be shown again from the list.
    page.locator("[data-widget='goals']").get_by_role("button", name="Hide").click()
    expect(page.get_by_role("button", name="Show Goals")).to_be_visible()
    saved(page)
    arranged = rows(page)
    page.get_by_role("button", name="Done").click()

    # Kept after a reload and in a fresh browser session.
    session.go("/app")
    page.wait_for_function("() => document.querySelector('[data-widget=goals]') === null")
    assert rows(page) == arranged
    session.context.clear_cookies()
    session.login("Carol")
    session.go("/app")
    page.wait_for_function("() => document.querySelector('[data-widget=goals]') === null")
    assert rows(page) == arranged

    # Reset to default.
    page.get_by_role("button", name="Customize dashboard").click()
    page.get_by_role("button", name="Reset to default").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Reset to default").click()
    saved(page)
    session.go("/app")
    assert rows(page) == DEFAULT


def test_arabic_arrows_follow_the_reading_direction(session: Session) -> None:
    session.login("Carol")
    session.go("/app", "ar")
    page = still(session)
    page.get_by_role("button", name="تخصيص لوحة المعلومات").click()
    handle = page.get_by_role("button", name="نقل الإنفاق حسب الفئة")
    handle.focus()
    # In Arabic the row starts on the right: the right arrow moves toward its start.
    page.keyboard.press("ArrowRight")
    assert rows(page)[1] == ["categories", "recent"]
    page.keyboard.press("ArrowLeft")
    assert rows(page)[1] == ["recent", "categories"]
    saved(page, "حُفظ")


def test_phone_stacks_the_rows_and_keeps_the_order(session: Session) -> None:
    session.page.set_viewport_size({"width": 390, "height": 844})
    session.login("Carol")
    session.go("/app")
    page = still(session)
    first = page.locator("[data-widget='recent']").bounding_box()
    second = page.locator("[data-widget='categories']").bounding_box()
    assert first and second and second["y"] >= first["y"] + first["height"] - 1
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
    page.get_by_role("button", name="Customize dashboard").click()
    page.get_by_role("button", name="Move Activity").focus()
    page.keyboard.press("ArrowUp")
    assert row_of(page, "recent") == "1" and rows(page)[2] == ["categories"]
    page.keyboard.press("ArrowDown")
    assert rows(page)[1] == ["recent", "categories"]
    saved(page)


def test_a_blocked_deletion_explains_why_and_posts_once(session: Session) -> None:
    session.login("Alice")
    session.go(f"/app/accounts/{session.world.accounts['Alice']}")
    page = session.page
    page.get_by_role("button", name="Delete", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog.get_by_text(re.compile("Archiving hides it"))).to_be_visible()
    dialog.get_by_role("button", name="Delete", exact=True).dblclick()
    expect(dialog.get_by_text(re.compile("Money was recorded on this account"))).to_be_visible()
    deletes = [r for r in session.traffic.requests if r[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][2] == 409
    # Nothing was deleted.
    session.go("/app/accounts")
    expect(page.get_by_text("Alice's bank").first).to_be_visible()
