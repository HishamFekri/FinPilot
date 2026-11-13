from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.v1.auth import register_sign_in_errors
from app.api.v1.router import api_router
from app.core.config import Environment, Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import request_context
from app.core.rate_limit import RateLimiter
from app.db import session as db_session
from app.services.fx import FxGateway
from app.services.fx_providers import Coinbase, Frankfurter
from app.services.google_oidc import build_google_provider

API_V1_PREFIX = "/api/v1"


def build_fx_gateway(settings: Settings) -> FxGateway:
    timeout = settings.fx_http_timeout_seconds
    return FxGateway(
        frankfurter=Frankfurter(timeout),
        coinbase=Coinbase(timeout),
        stale_days=settings.fx_stale_days,
        outbound_per_minute=settings.fx_outbound_per_minute,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # The API's map is for developers. Published, it only helps someone probing the service.
    local = settings.app_env in (Environment.DEVELOPMENT, Environment.TEST)
    app = FastAPI(
        title="FinPilot API",
        version="0.1.0",
        openapi_url=f"{API_V1_PREFIX}/openapi.json" if local else None,
        docs_url=f"{API_V1_PREFIX}/docs" if local else None,
        redoc_url=None,
    )
    app.state.settings = settings
    # The database uses these same settings, never a second copy read from the environment.
    db_session.use_settings(settings)
    # The real Google client or None. Tests replace it only through dependency overrides.
    app.state.google_provider = build_google_provider(settings)
    # Per process. Not shared between workers or kept across restarts (SECURITY.md §13.1).
    app.state.rate_limiter = RateLimiter()
    # Exchange rates and prices, with their caches and outbound budget. Tests swap the transport.
    app.state.fx = build_fx_gateway(settings)
    app.middleware("http")(request_context)
    register_error_handlers(app)
    register_sign_in_errors(app)
    app.include_router(api_router, prefix=API_V1_PREFIX)
    if settings.trusted_proxy_list:
        # Outermost, so everything after it, including rate limits, sees the visitor's address.
        # Only the named proxy is believed; anyone else's X-Forwarded-For is ignored. Uvicorn's own
        # handling is turned off in the image (--no-proxy-headers), so this is the only place.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxy_list)
    return app
