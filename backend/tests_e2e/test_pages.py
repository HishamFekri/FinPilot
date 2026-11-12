"""Every signed-in page in a real browser: loads, one heading, no errors, no sideways scroll."""

import pytest
from playwright.sync_api import expect

from tests_e2e.conftest import Session

PAGES = [
    "/app",
    "/app/accounts",
    "/app/transactions",
    "/app/budgets",
    "/app/recurring",
    "/app/subscriptions",
    "/app/calendar",
    "/app/goals",
    "/app/insights",  # forwards to /app/analytics?tab=spending
    "/app/reports",  # forwards to /app/analytics?tab=reports
    "/app/analytics",
    "/app/analytics?tab=intelligence",
    "/app/installments",
    "/app/installments/{plan}",
    "/app/trips",
    "/app/trips/{trip}",
    "/app/trips/{trip}/report",
    "/app/settings",
    "/app/settings/categories",
    "/app/settings/templates",
    "/app/settings/sessions",
]
VIEWPORTS = [
    (320, 700),
    (360, 800),
    (390, 844),
    (768, 1024),
    (1024, 768),
    (1366, 768),
    (1440, 900),
    (1920, 1080),
    (2560, 1440),
]

# The dev server's own hot-reload and React DevTools notices aren't app errors. React's
# development-only performance tracks also log a timing error when a server page redirects (the
# old /insights and /reports); performance tracks exist only in development builds.
NOISE = ("Download the React DevTools", "[HMR]", "[Fast Refresh]")
DEV_ONLY = "cannot have a negative time stamp"


def overflow(session: Session) -> int:
    return int(
        session.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
    )


@pytest.mark.parametrize("locale", ["en", "ar", "tr"])
def test_pages_load_cleanly_at_every_width(session: Session, locale: str) -> None:
    session.login("Alice")
    problems: list[str] = []
    for width, height in VIEWPORTS:
        session.page.set_viewport_size({"width": width, "height": height})
        for template in PAGES:
            path = template.format(trip=session.world.trip, plan=session.world.plan)
            session.console_errors.clear()
            session.go(path, locale)
            expect(session.page.get_by_role("heading", level=1)).to_have_count(1)
            if (extra := overflow(session)) > 0:
                problems.append(f"{locale} {width}px {path}: {extra}px sideways scroll")
            errors = [
                e for e in session.console_errors if not e.startswith(NOISE) and DEV_ONLY not in e
            ]
            if errors:
                problems.append(f"{locale} {width}px {path}: console {errors[:2]}")
    assert not problems, "\n".join(problems)
    assert session.traffic.failures() == []


def test_arabic_pages_are_right_to_left(session: Session) -> None:
    session.login("Alice")
    session.go("/app", "ar")
    assert session.page.evaluate("() => document.documentElement.dir") == "rtl"
    assert session.page.evaluate("() => document.documentElement.lang") == "ar"


def test_signed_out_pages_show_no_private_data(session: Session) -> None:
    # No sign-in: the app must not render any of Alice's entries.
    session.go("/app/transactions")
    session.page.wait_for_timeout(800)
    assert "E2E purchase" not in session.page.content()
