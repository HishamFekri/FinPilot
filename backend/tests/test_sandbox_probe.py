"""scripts/sandbox_probe.py's judgement, on a fake /proc (the real one runs in the container)."""

from pathlib import Path

from app.services.exports import PdfSettings, launch_options
from scripts.sandbox_probe import Process, chromium_processes, verdict

SHELL = "/opt/ms-playwright/chromium_headless_shell-1200/chrome-linux/headless_shell"


def _process(proc: Path, pid: int, argv: list[str], filters: int, nspid: str) -> None:
    folder = proc / str(pid)
    folder.mkdir()
    (folder / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (folder / "status").write_text(
        f"Name:\theadless_shell\nSeccomp:\t2\nSeccomp_filters:\t{filters}\nNSpid:\t{nspid}\n",
        encoding="utf-8",
    )


def test_a_sandboxed_browser_is_recognised(tmp_path: Path) -> None:
    _process(tmp_path, 40, [SHELL, "--headless", "--remote-debugging-pipe"], 1, "40")
    _process(tmp_path, 41, [SHELL, "--type=zygote"], 1, "41 1")
    _process(tmp_path, 52, [SHELL, "--type=renderer", "--lang=en"], 2, "52 3")
    _process(tmp_path, 7, ["/usr/bin/python", "-m", "scripts.sandbox_probe"], 1, "7")
    (tmp_path / "self").mkdir()
    processes = chromium_processes(tmp_path)
    assert sorted(p.kind for p in processes) == ["browser", "renderer", "zygote"]
    assert verdict(processes) == (True, [])


def test_no_sandbox_or_a_shared_namespace_is_refused() -> None:
    browser = Process(40, "browser", no_sandbox=True, seccomp_filters=1, pid_namespace_depth=1)
    renderer = Process(52, "renderer", no_sandbox=True, seccomp_filters=1, pid_namespace_depth=1)
    ok, problems = verdict([browser, renderer])
    assert not ok
    assert "a Chromium process runs with --no-sandbox" in problems
    assert any("shares the browser's PID namespace" in p for p in problems)
    assert any("no seccomp filter of its own" in p for p in problems)
    assert verdict([]) == (False, ["no Chromium process was found"])
    assert verdict([browser])[0] is False


def test_the_probe_launches_chromium_exactly_like_pdf_exports() -> None:
    options = launch_options(PdfSettings(channel=None, executable=None, timeout_ms=30_000))
    assert options == {"headless": True, "timeout": 30_000, "chromium_sandbox": True}
    with_channel = launch_options(PdfSettings(channel="chrome", executable=None, timeout_ms=1))
    assert with_channel["channel"] == "chrome" and with_channel["chromium_sandbox"] is True
