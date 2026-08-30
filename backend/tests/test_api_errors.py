"""API tests for structured error handling."""

from __future__ import annotations

from app.main import app

from tests.api_client import api_client


def test_validation_error_envelope(app_config) -> None:
    client = api_client(app)
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "not-a-url"},
    )
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "validation_error"
    assert "message" in body["error"]
    assert "details" in body["error"]
    assert "request_id" in body["error"]


def test_pydantic_validation_error_envelope(app_config) -> None:
    """Pydantic-raised validation errors also use the error envelope."""
    client = api_client(app)
    r = client.post("/api/v1/repositories", json={})  # missing field
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert "errors" in body["error"]["details"]


def test_not_found_envelope(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/repositories/9999")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "repository_id" in body["error"]["details"]


def test_request_id_round_trips(app_config) -> None:
    client = api_client(app)
    r = client.get(
        "/api/v1/repositories/9999",
        headers={"x-request-id": "abc-123"},
    )
    assert r.headers.get("x-request-id") == "abc-123"
    assert r.json()["error"]["request_id"] == "abc-123"


def test_request_id_minted_when_absent(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/repositories/9999")
    assert "x-request-id" in r.headers
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]


def test_no_stack_trace_leak(app_config) -> None:
    client = api_client(app)
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://example.com/foo/bar"},
    )
    body_text = r.text
    assert "Traceback" not in body_text
    assert 'File "' not in body_text
