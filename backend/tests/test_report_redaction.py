"""The report-scrubbing hook in tests/conftest.py: passwords out, report shapes intact.

Regression (CI, commit 175fc0f): the hook turned every longrepr into a string, including a
skip's (path, line, reason) tuple. With `-ra`, pytest's summary folds skips and asserts that
tuple, so the whole run crashed whenever a test skipped while a password was known.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import redact_longrepr

SECRET = "ci_only_not_a_secret"
SESSION = """
import pytest

def test_skips_in_its_call():
    pytest.skip("not the provisioned role")

def test_skip_reason_mentions_the_password():
    pytest.skip("connected with {secret}")

def test_passes():
    assert True
"""
FAILING = """
def test_fails_with_the_password_in_its_locals():
    password = "{secret}"
    assert password == "something else"
"""


def test_a_skip_keeps_its_tuple_with_the_reason_scrubbed() -> None:
    redacted = redact_longrepr(("/x/test_a.py", 4, f"Skipped: uses {SECRET}"), {SECRET})
    assert redacted == ("/x/test_a.py", 4, "Skipped: uses ***")


def test_text_and_tracebacks_become_scrubbed_text() -> None:
    assert redact_longrepr(f"boom {SECRET}", {SECRET}) == "boom ***"

    class Traceback:
        def __str__(self) -> str:
            return f"E   assert '{SECRET}' == 'x'"

    assert redact_longrepr(Traceback(), {SECRET}) == "E   assert '***' == 'x'"


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_runtest_makereport  # noqa: F401\n", encoding="utf-8"
    )
    (tmp_path / "test_session.py").write_text(body.format(secret=SECRET), encoding="utf-8")
    backend = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(backend),
        "TEST_DATABASE_URL": f"postgresql+psycopg://u:{SECRET}@localhost:5433/finpilot_test",
    }
    return subprocess.run(  # noqa: S603 - this interpreter running pytest on a generated file
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-ra",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )


def test_a_run_with_skips_reports_them_and_exits_cleanly(tmp_path: Path) -> None:
    result = _run(tmp_path, SESSION)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed, 2 skipped" in output
    assert "SKIPPED" in output and "not the provisioned role" in output
    assert "INTERNALERROR" not in output and "AssertionError" not in output
    assert SECRET not in output


def test_a_real_failure_still_fails_the_run_and_hides_the_password(tmp_path: Path) -> None:
    result = _run(tmp_path, FAILING)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "1 failed" in output and "INTERNALERROR" not in output
    assert SECRET not in output and "***" in output
