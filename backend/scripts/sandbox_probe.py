"""Is Chromium's own sandbox really on? Run inside the API container or image:

    python -m scripts.sandbox_probe

Starts the browser exactly as PDF exports do (app.services.exports.launch_options), opens a
synthetic page offline and, while it's open, reads /proc for the browser's processes:
- no process may carry --no-sandbox;
- at least one renderer must exist;
- every renderer must be in its own PID namespace (the namespace sandbox), and
- under more seccomp filters than the browser process (Chromium's seccomp-bpf sandbox on top of
  the container's own filter).
Then it prints the page to PDF. One JSON line is printed; the exit code is 0 only when all of the
above hold. Needs no settings, secrets, database or network.
"""

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.exports import PdfSettings, launch_options

PAGE = "<!doctype html><html><body><h1>FinPilot sandbox probe</h1><p>Synthetic.</p></body></html>"


@dataclass(frozen=True)
class Process:
    pid: int
    kind: str  # "browser", or the value of --type= (renderer, zygote, gpu-process, utility)
    no_sandbox: bool
    seccomp_filters: int  # -1 when the kernel doesn't report it
    pid_namespace_depth: int


def _status(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def chromium_processes(proc: Path = Path("/proc")) -> list[Process]:
    """Every Chromium process visible in this PID namespace."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [
                a.decode(errors="replace")
                for a in (entry / "cmdline").read_bytes().split(b"\0")
                if a
            ]
            status = _status((entry / "status").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue  # the process ended, or isn't readable
        # Chromium retitles its child processes: their cmdline becomes one space-joined string.
        argv = [token for a in argv for token in a.split(" ") if token]
        if not argv:
            continue
        program = argv[0].rsplit("/", 1)[-1]
        if not any(name in program for name in ("headless_shell", "chrome", "chromium")):
            continue
        if "crashpad" in program:
            continue  # the crash reporter: a helper of its own, not a browser or a sandboxed child
        kind = next((a.split("=", 1)[1] for a in argv[1:] if a.startswith("--type=")), "browser")
        filters = status.get("Seccomp_filters", "")
        found.append(
            Process(
                pid=int(entry.name),
                kind=kind,
                no_sandbox="--no-sandbox" in argv,
                seccomp_filters=int(filters) if filters.isdigit() else -1,
                pid_namespace_depth=len(status.get("NSpid", "").split()),
            )
        )
    return found


def verdict(processes: list[Process]) -> tuple[bool, list[str]]:
    """Whether these processes show a working sandbox, and every reason they don't."""
    problems = []
    if not processes:
        return False, ["no Chromium process was found"]
    if any(p.no_sandbox for p in processes):
        problems.append("a Chromium process runs with --no-sandbox")
    browsers = [p for p in processes if p.kind == "browser"]
    renderers = [p for p in processes if p.kind == "renderer"]
    if len(browsers) != 1:
        problems.append(f"expected one browser process, found {len(browsers)}")
    if not renderers:
        problems.append("no renderer process was found")
    if browsers and renderers:
        browser = browsers[0]
        for renderer in renderers:
            if renderer.pid_namespace_depth <= browser.pid_namespace_depth:
                problems.append(f"renderer {renderer.pid} shares the browser's PID namespace")
            if renderer.seccomp_filters <= browser.seccomp_filters:
                problems.append(f"renderer {renderer.pid} has no seccomp filter of its own")
    return not problems, problems


def probe() -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    settings = PdfSettings(
        channel=os.environ.get("PDF_BROWSER_CHANNEL") or None,
        executable=os.environ.get("PDF_BROWSER_PATH") or None,
        timeout_ms=30_000,
    )
    result: dict[str, Any] = {"launch": launch_options(settings) | {"timeout": 30_000}}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options(settings))
            try:
                context = browser.new_context(java_script_enabled=False, offline=True)
                page = context.new_page()
                page.route("**/*", lambda route: route.abort())
                page.set_content(PAGE, wait_until="domcontentloaded")
                processes = chromium_processes()
                pdf = page.pdf(format="A4")
            finally:
                browser.close()
    except Exception as error:
        message = str(error).strip().splitlines()
        result.update(sandboxed=False, error=" | ".join(line for line in message[:12] if line))
        return result
    sandboxed, problems = verdict(processes)
    result.update(
        sandboxed=sandboxed and pdf.startswith(b"%PDF"),
        pdf_bytes=len(pdf),
        processes=[asdict(p) for p in processes],
        problems=problems,
    )
    return result


def main() -> int:
    result = probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("sandboxed") else 1


if __name__ == "__main__":
    sys.exit(main())
