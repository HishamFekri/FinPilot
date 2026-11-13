"""One instant for the whole test run, imported once and shared.

The app's calendar clock (app/core/clock.py) is pinned to it by tests/conftest.py, and the
tests' TODAY is its UTC date, so a run that crosses midnight can't see two days. It lives here,
not in conftest.py: pytest imports conftest under its own module name, so importing a value from
it can give a second copy.
"""

from datetime import UTC, datetime

SESSION_NOW = datetime.now(UTC)
