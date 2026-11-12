import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.core.errors import ApiError
from app.core.logging import JsonFormatter
from app.db.session import get_engine
from tests.conftest import UNREACHABLE_URL


def test_app_exposes_versioned_routes_only(app: FastAPI) -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/v1/healthz" in paths
    assert "/api/v1/readyz" in paths
    assert all(path.startswith("/api/v1/") for path in paths)


def test_the_only_authentication_routes_are_google_and_session_management(app: FastAPI) -> None:
    """No password, registration, reset, or verification route exists, or may be added."""
    auth = {p for p in app.openapi()["paths"] if "/auth" in p or p.endswith("/me")}
    assert auth == {
        "/api/v1/auth/google/start",
        "/api/v1/auth/google/callback",
        "/api/v1/auth/me",
        # A profile preference (Home's layout), not a way to sign in.
        "/api/v1/auth/me/home-layout",
        "/api/v1/auth/logout",
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/{session_id}",
    }
    for path in app.openapi()["paths"]:
        assert not any(word in path for word in ("password", "register", "reset", "verify"))


def test_the_app_holds_no_google_client_unless_configured(app: FastAPI) -> None:
    assert app.state.google_provider is None


def test_no_test_or_mock_sign_in_route_exists(app: FastAPI) -> None:
    for path in app.openapi()["paths"]:
        assert not any(
            word in path for word in ("mock", "fake", "test", "dev-login", "impersonate")
        )


def test_healthz_needs_no_database(client: TestClient) -> None:
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_readyz_reports_503_without_details_when_database_is_down(
    app: FastAPI, client: TestClient
) -> None:
    # A real driver connection attempt against a closed port, with nothing mocked.
    app.dependency_overrides[get_engine] = lambda: create_engine(
        UNREACHABLE_URL, poolclass=NullPool, connect_args={"connect_timeout": 1}
    )
    response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "not_ready"
    assert "127.0.0.1" not in response.text
    assert "psycopg" not in response.text


def test_unknown_route_returns_problem_json(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "not_found"


def test_api_error_includes_code_params_and_request_id(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/api-error")
    def boom() -> None:
        raise ApiError(409, "invalid_state", "Invalid state", params={"state": "closed"})

    response = client.get("/api/v1/_probe/api-error")
    body = response.json()
    assert response.status_code == 409
    assert body["code"] == "invalid_state"
    assert body["params"] == {"state": "closed"}
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_validation_errors_do_not_echo_submitted_values(app: FastAPI, client: TestClient) -> None:
    class Payload(BaseModel):
        amount: int

    @app.post("/api/v1/_probe/validate")
    def validate(payload: Payload) -> None:
        return None

    response = client.post("/api/v1/_probe/validate", json={"amount": "private-value"})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["field"] == "body.amount"
    assert "private-value" not in response.text


def test_unexpected_errors_do_not_leak_details(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail")

    response = client.get("/api/v1/_probe/crash")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret internal detail" not in response.text


def test_access_log_uses_route_template_not_query_string(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="finpilot.access"):
        client.get("/api/v1/healthz?token=should-not-be-logged")
    formatted = [JsonFormatter().format(r) for r in caplog.records if r.name == "finpilot.access"]
    assert formatted, "expected an access log line"
    assert "should-not-be-logged" not in " ".join(formatted)
    entry = json.loads(formatted[-1])
    assert entry["route"] == "/api/v1/healthz"
    assert entry["status"] == 200


def test_access_log_replaces_path_parameters_and_hides_unmatched_paths(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/api/v1/_probe/items/{item_id}")
    def item(item_id: str) -> None:
        return None

    with caplog.at_level(logging.INFO, logger="finpilot.access"):
        client.get("/api/v1/_probe/items/private-id-123")
        client.get("/api/v1/no-such-path-private-456")
    routes = [
        json.loads(JsonFormatter().format(r))["route"]
        for r in caplog.records
        if r.name == "finpilot.access"
    ]
    assert routes == ["/api/v1/_probe/items/{item_id}", "unmatched"]


def test_logging_setup_silences_url_logging_libraries(app: FastAPI) -> None:
    # httpx logs full request URLs at INFO, including query strings.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("uvicorn.access").disabled


def test_log_formatter_ignores_unlisted_extra_fields() -> None:
    record = logging.LogRecord("x", logging.INFO, "f", 1, "hello", None, None)
    record.email = "person@example.com"
    assert "person@example.com" not in JsonFormatter().format(record)
