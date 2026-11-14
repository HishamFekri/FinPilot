"""Environment-based configuration. Invalid configuration stops the app at startup."""

import ipaddress
import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# The only callback path. Google must be registered with exactly this, per environment.
GOOGLE_CALLBACK_PATH = "/api/v1/auth/google/callback"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Containers read secrets from files here (Docker secrets), never from environment variables.
SECRETS_DIR_VARIABLE = "FINPILOT_SECRETS_DIR"


def read_secret(name: str) -> str | None:
    """A secret for tools outside Settings (migrations, provisioning scripts).

    In order: the variable itself, a file named by `{name}_FILE`, or a file named `name.lower()` in
    the secrets directory. Blank counts as unset. The value is never printed or logged.
    """
    value = os.environ.get(name)
    if not value:
        path = os.environ.get(f"{name}_FILE")
        if not path and os.environ.get(SECRETS_DIR_VARIABLE):
            candidate = Path(os.environ[SECRETS_DIR_VARIABLE]) / name.lower()
            path = str(candidate) if candidate.is_file() else None
        value = Path(path).read_text(encoding="utf-8") if path else None
    value = value.strip() if value else None
    return value or None


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    DEMO = "demo"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors would otherwise echo the input, including any password in a URL.
        hide_input_in_errors=True,
    )

    app_env: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Required, with no default: a default could point the app at the wrong database.
    database_url: PostgresDsn = Field(repr=False)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: int = Field(default=5, ge=1, le=60)
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    # Google sign-in. All three or none. Values only ever come from the environment.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = Field(default=None, repr=False)
    google_redirect_uri: str | None = None
    google_http_timeout_seconds: float = Field(default=5, gt=0, le=30)
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=900)

    # Exchange rates and prices (FINANCIAL_RULES.md §7). Hosts are fixed in code, never configured.
    fx_http_timeout_seconds: float = Field(default=3, gt=0, le=10)
    # A reference older than this, for the transaction's date, needs explicit acknowledgement.
    fx_stale_days: int = Field(default=4, ge=1, le=30)
    # Outbound calls to all providers together, per process and minute.
    fx_outbound_per_minute: int = Field(default=60, ge=1, le=600)

    # PDF exports: headless Chromium (Playwright). A browser channel installed on the machine
    # ("chrome", "msedge"), or an explicit executable path, which wins when set.
    pdf_browser_channel: str | None = "chrome"
    pdf_browser_path: str | None = None
    pdf_timeout_seconds: int = Field(default=30, ge=5, le=120)
    # Transactions in one PDF; more are in the CSV, which has no such limit below this ceiling.
    pdf_max_rows: int = Field(default=2000, ge=100, le=20000)
    # Browsers running at once in this process; more requests get 503 pdf_busy.
    pdf_max_concurrent: int = Field(default=2, ge=1, le=8)
    # Chromium's own sandbox is always on (exports.py). There is deliberately no setting to turn it
    # off: where the sandbox can't start, PDF export answers 503 instead of running unsandboxed.
    csv_max_rows: int = Field(default=50000, ge=1000, le=200000)
    # Rows per section of the personal data download. Anything cut is named in the file.
    data_export_max_rows: int = Field(default=50000, ge=1000, le=200000)

    # The web app's origin. Sign-in returns here, and only this origin may make cookie-authenticated
    # changes (CSRF). In production it must be https and share the API's host.
    web_origin: str = "http://localhost:3000"
    session_idle_hours: int = Field(default=7 * 24, ge=1, le=30 * 24)
    session_absolute_hours: int = Field(default=30 * 24, ge=1, le=90 * 24)

    # The reverse proxy in front of the API: addresses or networks, comma separated. Only a request
    # arriving from one of these has its X-Forwarded-For believed, so rate limits count the real
    # visitor rather than the proxy. Required in production; never a catch-all.
    trusted_proxies: str | None = None

    # Who may create a NEW account (existing accounts always sign in, subject to their status):
    #   open       anyone with a verified Google address: public registration
    #   allowlist  only the addresses in BETA_ALLOWLIST (comma or newline separated; in production
    #              a secrets file, `beta_allowlist`, so addresses never appear in the environment)
    #   closed     nobody: the switch that pauses sign-ups (an environment change and a restart,
    #              no deploy)
    # Unset: "open", except the demo, which has no Google sign-in at all ("allowlist").
    registration_mode: Literal["closed", "allowlist", "open"] | None = None
    beta_allowlist: str | None = None

    @field_validator("database_url")
    @classmethod
    def require_psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+psycopg":
            raise ValueError("database_url must use the postgresql+psycopg:// scheme")
        return value

    @field_validator(
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "pdf_browser_channel",
        "pdf_browser_path",
        "trusted_proxies",
        "registration_mode",
        "beta_allowlist",
        mode="before",
    )
    @classmethod
    def blank_means_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_google_sign_in(self) -> Self:
        values = (self.google_client_id, self.google_client_secret, self.google_redirect_uri)
        if any(value is not None for value in values) and None in values:
            raise ValueError(
                "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI go together"
            )
        if self.app_env is Environment.PRODUCTION and not self.google_configured:
            raise ValueError("Production needs Google sign-in, the only way to authenticate")
        if self.app_env is Environment.DEMO and self.google_configured:
            raise ValueError("The demo never uses Google sign-in (SECURITY.md §14)")
        if self.google_client_id is not None and not self.google_client_id.endswith(
            ".apps.googleusercontent.com"
        ):
            raise ValueError("GOOGLE_CLIENT_ID doesn't look like a Google OAuth client ID")
        if self.google_redirect_uri is not None:
            self._check_redirect_uri(self.google_redirect_uri)
        self._check_web_origin()
        if self.session_idle_hours > self.session_absolute_hours:
            raise ValueError("SESSION_IDLE_HOURS can't exceed SESSION_ABSOLUTE_HOURS")
        self._check_trusted_proxies()
        self._check_registration()
        return self

    def _check_registration(self) -> None:
        for address in self.beta_allowlist_addresses:
            if address.count("@") != 1 or address.startswith("@") or address.endswith("@"):
                raise ValueError("BETA_ALLOWLIST must list email addresses")
        if self.app_env is not Environment.PRODUCTION:
            return
        if self.effective_registration_mode == "allowlist" and not self.beta_allowlist_addresses:
            raise ValueError(
                "REGISTRATION_MODE=allowlist needs BETA_ALLOWLIST (the owner's address at least); "
                "use REGISTRATION_MODE=closed to admit no new accounts"
            )

    @property
    def effective_registration_mode(self) -> str:
        if self.registration_mode is not None:
            return self.registration_mode
        return "allowlist" if self.app_env is Environment.DEMO else "open"

    @property
    def beta_allowlist_addresses(self) -> frozenset[str]:
        if not self.beta_allowlist:
            return frozenset()
        parts = self.beta_allowlist.replace("\n", ",").split(",")
        return frozenset(p.strip().lower() for p in parts if p.strip())

    def admits_new_account(self, email: str) -> bool:
        """Whether a verified Google address with no FinPilot account may create one now."""
        mode = self.effective_registration_mode
        if mode == "open":
            return True
        if mode == "allowlist":
            return email.strip().lower() in self.beta_allowlist_addresses
        return False

    def _check_trusted_proxies(self) -> None:
        for entry in self.trusted_proxy_list:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(
                    "TRUSTED_PROXIES must list IP addresses or networks, comma separated"
                ) from None
            if network.prefixlen < 8:
                # Anything this wide would let any client choose the address it's counted under.
                raise ValueError("TRUSTED_PROXIES must name the proxy, not a catch-all network")
        if self.app_env is Environment.PRODUCTION and not self.trusted_proxy_list:
            raise ValueError(
                "Production needs TRUSTED_PROXIES: the reverse proxy's address, so rate limits "
                "count visitors and not the proxy"
            )

    @property
    def trusted_proxy_list(self) -> list[str]:
        if not self.trusted_proxies:
            return []
        return [entry.strip() for entry in self.trusted_proxies.split(",") if entry.strip()]

    def _check_web_origin(self) -> None:
        parts = urlsplit(self.web_origin)
        local = self.app_env in (Environment.DEVELOPMENT, Environment.TEST)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("WEB_ORIGIN must be an absolute http(s) origin")
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("WEB_ORIGIN must be an origin only, such as https://finpilot.example")
        if parts.scheme == "http" and not (local and parts.hostname in LOOPBACK_HOSTS):
            raise ValueError("WEB_ORIGIN must use https, except on loopback in development")
        if self.google_redirect_uri is not None:
            # Cookies are scoped by host. If these differ, the session set at the callback would
            # never reach the web app, and a localhost/127.0.0.1 mix is the usual cause.
            if urlsplit(self.google_redirect_uri).hostname != parts.hostname:
                raise ValueError("WEB_ORIGIN and GOOGLE_REDIRECT_URI must use the same host")
            if not local and self.api_origin != self.normalized_web_origin:
                raise ValueError("Outside development, the web app and API must share one origin")

    @property
    def normalized_web_origin(self) -> str:
        parts = urlsplit(self.web_origin)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def api_origin(self) -> str | None:
        if self.google_redirect_uri is None:
            return None
        parts = urlsplit(self.google_redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Origins allowed to make cookie-authenticated changes."""
        return frozenset(o for o in (self.normalized_web_origin, self.api_origin) if o)

    def _check_redirect_uri(self, uri: str) -> None:
        parts = urlsplit(uri)
        local = self.app_env in (Environment.DEVELOPMENT, Environment.TEST)
        if parts.scheme == "http" and not (local and parts.hostname in LOOPBACK_HOSTS):
            raise ValueError(
                "GOOGLE_REDIRECT_URI must use https, except on loopback in development"
            )
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("GOOGLE_REDIRECT_URI must be an absolute http(s) URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(
                "GOOGLE_REDIRECT_URI must not contain credentials, a query, or a fragment"
            )
        if parts.path != GOOGLE_CALLBACK_PATH:
            raise ValueError(f"GOOGLE_REDIRECT_URI must end in {GOOGLE_CALLBACK_PATH}")

    @property
    def google_configured(self) -> bool:
        return self.google_client_id is not None

    @property
    def secure_cookies(self) -> bool:
        """Local development runs over plain http, where browsers refuse Secure cookies.

        Derived from the environment and never configurable, so production can't turn it off.
        """
        return self.app_env not in (Environment.DEVELOPMENT, Environment.TEST)


@lru_cache
def get_settings() -> Settings:
    # Values come from the environment, .env, or secret files, which mypy can't see. Each secret
    # file is named after its setting (`database_url`, `google_client_secret`).
    secrets_dir = os.environ.get(SECRETS_DIR_VARIABLE) or None
    return Settings(_secrets_dir=secrets_dir)  # type: ignore[call-arg]
