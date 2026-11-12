"""CSV and PDF exports of a personal report (API_SPEC.md §13.3, §13.4; SECURITY.md §7).

Both are built from the same `reporting.report` figures and transaction rows the screen shows; no
figure is calculated here, only formatted.

**CSV:** UTF-8 with a byte-order mark, comma delimiter, CRLF line endings, RFC 4180 quoting, stable
English column names, amounts as plain decimals with a separate currency column, ISO dates. A text
cell that a spreadsheet would run as a formula (starting with = + - @, tab or CR, even after
invisible or bidirectional marks) is prefixed with an apostrophe. Numeric columns are generated
here, so they're never prefixed.

**PDF:** an HTML report (Jinja2, autoescaped) printed to A4 by headless Chromium, which shapes
Arabic correctly, embeds the fonts it uses, repeats table headers on every page, and numbers pages.
No user-provided URL or file is ever loaded: the page is rendered from a string with the network
blocked. WeasyPrint was the first choice (ARCHITECTURE.md §6) but needs a system GTK install;
Chromium is the documented fallback for Arabic (§14.11).
"""

import csv
import html as html_lib
import io
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.errors import ApiError

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
_ENV = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
# Invisible and bidirectional marks that could hide a formula's first character.
_HIDDEN = re.compile(r"^(?:[^\S\t\r]|[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff])*")
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")

GROUPING = {"en": (",", "."), "ar": (",", "."), "tr": (".", ",")}


def safe_cell(value: str | None) -> str:
    """A text cell that can't run as a spreadsheet formula."""
    if not value:
        return ""
    visible = _HIDDEN.sub("", value)
    return f"'{value}" if visible.startswith(_DANGEROUS) else value


def csv_bytes(header: list[str], rows: list[list[str]], text_columns: set[int]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(c) if i in text_columns else c for i, c in enumerate(row)])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def number(amount: str, locale: str) -> str:
    """A canonical decimal string with the language's separators: "1234.5" → "1.234,5" in Turkish.
    Text only; no arithmetic."""
    group, decimal = GROUPING.get(locale, GROUPING["en"])
    sign = "-" if amount.startswith("-") else ""
    whole, _, fraction = amount.lstrip("-").partition(".")
    grouped = re.sub(r"\B(?=(\d{3})+(?!\d))", group, whole)
    return f"{sign}{grouped}{decimal + fraction if fraction else ''}"


@dataclass(frozen=True)
class PdfSettings:
    channel: str | None
    executable: str | None
    timeout_ms: int
    max_concurrent: int = 2


_SLOTS: dict[int, threading.BoundedSemaphore] = {}
_SLOTS_LOCK = threading.Lock()


def _slots(size: int) -> threading.BoundedSemaphore:
    """One shared limit per configured size: at most `size` browsers at once in this process."""
    with _SLOTS_LOCK:
        if size not in _SLOTS:
            _SLOTS[size] = threading.BoundedSemaphore(size)
        return _SLOTS[size]


def busy() -> ApiError:
    return ApiError(503, "pdf_busy", "Too many PDFs are being made right now; try again shortly")


def render_html(context: dict[str, Any], template: str = "report.html") -> str:
    return _ENV.get_template(template).render(**context)


def render_pdf(html: str, settings: PdfSettings, footer: dict[str, str]) -> bytes:
    """Prints the report HTML to an A4 PDF with page numbers.

    Safety: Chromium's sandbox is on, JavaScript is off, every request (including file: and data:
    subresources) is aborted, the context is offline, every step has the timeout, at most
    `max_concurrent` browsers run at once (503 pdf_busy otherwise), and the browser is closed on
    every path, success or failure. The PDF is returned as bytes; nothing is written to disk.
    503 pdf_unavailable if no browser starts.
    """
    slots = _slots(settings.max_concurrent)
    if not slots.acquire(timeout=2):
        raise busy()
    try:
        return _render(html, settings, footer)
    finally:
        slots.release()


def launch_options(settings: PdfSettings) -> dict[str, Any]:
    """How every PDF browser is started; scripts/sandbox_probe.py starts it the same way.

    chromium_sandbox: Playwright otherwise passes --no-sandbox. With the sandbox on, a browser that
    can't start it fails, and the caller gets 503 pdf_unavailable.
    """
    launch: dict[str, Any] = {
        "headless": True,
        "timeout": settings.timeout_ms,
        "chromium_sandbox": True,
    }
    if settings.executable:
        launch["executable_path"] = settings.executable
    elif settings.channel:
        launch["channel"] = settings.channel
    return launch


def _render(html: str, settings: PdfSettings, footer: dict[str, str]) -> bytes:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - a dependency
        raise ApiError(503, "pdf_unavailable", "PDF export isn't available right now") from None
    e = {key: html_lib.escape(value) for key, value in footer.items()}
    direction = "rtl" if footer.get("dir") == "rtl" else "ltr"
    footer_html = (
        '<div style="width:100%;font-size:7.5px;color:#6b7280;padding:0 14mm;'
        "display:flex;justify-content:space-between;"
        "font-family:Segoe UI,Noto Sans,Noto Sans Arabic,Tahoma,Arial,sans-serif;"
        f'direction:{direction}">'
        f"<span>FinPilot · {e['generated']}</span>"
        f'<span>{e["page"]} <span class="pageNumber"></span> {e["of"]} '
        '<span class="totalPages"></span></span></div>'
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options(settings))
            try:
                context = browser.new_context(java_script_enabled=False, offline=True)
                context.set_default_timeout(settings.timeout_ms)
                page = context.new_page()
                # Nothing but the given HTML is ever loaded.
                page.route("**/*", lambda route: route.abort())
                page.set_content(html, wait_until="domcontentloaded", timeout=settings.timeout_ms)
                return page.pdf(
                    format="A4",
                    print_background=True,
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=footer_html,
                    margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                browser.close()
    except PlaywrightError:
        raise ApiError(503, "pdf_unavailable", "PDF export isn't available right now") from None


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")
