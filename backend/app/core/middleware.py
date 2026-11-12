"""Request IDs and access logs that record route templates, never raw URLs."""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.limits import MAX_BODY_BYTES
from app.core.logging import request_id_var

logger = logging.getLogger("finpilot.access")


def route_template(scope: Mapping[str, Any]) -> str:
    """The full path with parameter values replaced by names, so IDs never reach the logs.

    Included routers only expose their own suffix on `scope["route"].path`, so this starts
    from the request path. Unmatched paths are arbitrary client input, and aren't logged.
    """
    if scope.get("route") is None:
        return "unmatched"
    names = {str(value): name for name, value in scope.get("path_params", {}).items()}
    segments = str(scope["path"]).split("/")
    return "/".join(f"{{{names[s]}}}" if s in names else s for s in segments)


def _too_large(request: Request, request_id: str) -> Response | None:
    declared = request.headers.get("content-length", "")
    if not declared.isdigit() or int(declared) <= MAX_BODY_BYTES:
        return None
    return JSONResponse(
        {
            "type": "about:blank#request_too_large",
            "title": "Request too large",
            "status": 413,
            "code": "request_too_large",
            "request_id": request_id,
        },
        status_code=413,
        media_type="application/problem+json",
    )


def _security_headers(request: Request, response: Response) -> None:
    """On every API response, so nothing depends on which proxy sits in front."""
    # Routes set this through a dependency, which a directly returned file response doesn't get.
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.app.state.settings.secure_cookies:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # A fresh ID each time: an ID supplied by the client could be used to forge log lines.
    request_id = uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    status = 500
    try:
        response = _too_large(request, request_id) or await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        _security_headers(request, response)
        return response
    finally:
        logger.info(
            "request",
            extra={
                "route": route_template(request.scope),
                "method": request.method,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        request_id_var.reset(token)
