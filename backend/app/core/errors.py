"""RFC 9457 problem+json responses, with a stable machine-readable code (API_SPEC.md §1.8)."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class InvalidText(ValueError):
    """Text FinPilot won't store. A ValueError, so request models can raise it too."""


class ApiError(Exception):
    """A deliberate API failure. Clients translate `code` and `params`; `title` is for logs."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.params = params
        self.headers = headers


def _problem(
    status: int,
    code: str,
    title: str,
    params: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"about:blank#{code}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": request_id_var.get(),
    }
    if params:
        body["params"] = params
    if errors:
        body["errors"] = errors
    return JSONResponse(jsonable_encoder(body), status_code=status, media_type=PROBLEM_JSON)


def problem_response(exc: ApiError) -> JSONResponse:
    """For routes that must adjust the response, such as clearing a cookie on failure."""
    response = _problem(exc.status, exc.code, exc.title, exc.params)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _handle_api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - narrowing only; registered for ApiError
    return problem_response(exc)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - narrowing only
    phrase = HTTPStatus(exc.status_code).phrase
    code = phrase.lower().replace(" ", "_").replace("-", "_")
    return _problem(exc.status_code, code, phrase)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - narrowing only
    # Input values are left out on purpose, so a response never echoes submitted data.
    errors = [
        {"field": ".".join(str(part) for part in err["loc"]), "code": err["type"]}
        for err in exc.errors()
    ]
    return _problem(422, "validation_error", "Request validation failed", errors=errors)


async def _handle_bad_value(_: Request, exc: Exception) -> JSONResponse:
    # Such as a NUL character, which PostgreSQL text can't hold. The value itself isn't logged.
    logger.warning("Database refused a value", extra={"error_type": type(exc).__name__})
    return _problem(422, "invalid_value", "A value couldn't be accepted")


async def _handle_invalid_text(_: Request, exc: Exception) -> JSONResponse:
    # Raised from services (and for text no encoding can hold). The text itself isn't logged.
    return _problem(422, "validation_error", "That text contains characters that aren't allowed")


async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    # Details stay in the logs. The client only gets a request ID to quote.
    logger.error("Unhandled exception", exc_info=exc, extra={"error_type": type(exc).__name__})
    return _problem(500, "internal_error", "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(DataError, _handle_bad_value)
    app.add_exception_handler(InvalidText, _handle_invalid_text)
    app.add_exception_handler(UnicodeEncodeError, _handle_invalid_text)
    app.add_exception_handler(Exception, _handle_unexpected)
