"""API tests for the repository endpoints."""

from __future__ import annotations

import re

import pytest
from app.main import app

from tests.api_client import api_client


@pytest.fixture
def client(app_config):
    return api_client(app)


def test_create_repository_201(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["owner"] == "octocat"
    assert body["name"] == "Hello-World"
    assert body["canonical_url"] == "https://github.com/octocat/Hello-World"
    assert body["provider"] == "github"
    assert body["source_type"] == "github"


def test_create_repository_idempotent(client) -> None:
    payload = {"canonical_url": "https://github.com/octocat/Hello-World"}
    a = client.post("/api/v1/repositories", json=payload)
    b = client.post("/api/v1/repositories", json=payload)
    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["id"] == b.json()["id"]


def test_create_repository_rejects_non_github(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://example.com/foo/bar"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_create_repository_rejects_extra_path(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World/tree/main"},
    )
    assert r.status_code == 422


def test_create_repository_rejects_credentials(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://user:pass@github.com/octocat/Hello-World"},
    )
    assert r.status_code == 422


def test_create_repository_rejects_empty_body(client) -> None:
    r = client.post("/api/v1/repositories", json={})
    assert r.status_code == 422


def test_list_repositories_pagination(client) -> None:
    for i in range(3):
        client.post(
            "/api/v1/repositories",
            json={"canonical_url": f"https://github.com/o{i}/r{i}"},
        )
    r = client.get("/api/v1/repositories?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 3
    assert len(body["items"]) == 2


def test_list_repositories_clamps_oversized_page_size(client) -> None:
    r = client.get("/api/v1/repositories?page=1&page_size=999999")
    assert r.status_code == 200
    assert r.json()["pagination"]["page_size"] <= 200


def test_get_repository_404(client) -> None:
    r = client.get("/api/v1/repositories/9999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_get_repository_ok(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    rid = r.json()["id"]
    r2 = client.get(f"/api/v1/repositories/{rid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == rid


# ---------------------------------------------------------------------------
# v2.1.1: defence-in-depth safe wrapper for the legacy
# ``POST /repositories`` endpoint. The primary bundled-UI path
# is ``POST /repositories/github``; the legacy endpoint is
# retained for backwards compatibility and is wrapped with the
# same safe-error boundary so external clients (curl, scripts,
# the prior ``api.createRepository`` helper) never see a raw
# traceback or a half-committed row.
# ---------------------------------------------------------------------------


def test_legacy_endpoint_returns_internal_unexpected_with_correlation_id(
    client, monkeypatch
) -> None:
    """End-to-end: a simulated database write failure on
    the legacy ``POST /repositories`` route returns the
    documented ``INTERNAL_UNEXPECTED`` envelope with a
    16-character lowercase hex ``correlation_id``.
    """

    def _raise_db_error(*_args, **_kwargs):
        raise RuntimeError("simulated db write failure from the legacy route")

    # Patch the INNER service call so the failure happens
    # AFTER the wrapper's try/except is entered but BEFORE
    # the inner call commits. This mirrors the
    # packaged-runtime acceptance failure mode: a write
    # to a read-only database. The safe wrapper must
    # sanitise the RuntimeError into the documented
    # ``INTERNAL_UNEXPECTED`` envelope.
    monkeypatch.setattr(
        "app.services.repository_service.create_repository_from_url",
        _raise_db_error,
    )

    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_unexpected"
    cid = body["error"]["details"]["correlation_id"]
    assert re.fullmatch(r"[0-9a-f]{16}", cid) is not None
    assert body["error"]["details"]["kind"] == "repository"
    # The response carries no path, no token, no raw exception.
    msg = body["error"]["message"]
    assert "RuntimeError" not in msg
    assert "simulated" not in msg
    assert "Traceback" not in msg


def test_legacy_endpoint_classified_errors_preserved(client) -> None:
    """End-to-end: a non-GitHub URL on the legacy
    ``POST /repositories`` route still returns the
    classified ``validation_error`` envelope (the safe
    wrapper does not collapse classified errors into
    ``INTERNAL_UNEXPECTED``).
    """
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://example.com/octocat/Hello-World"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert "is not a valid public GitHub URL" in body["error"]["message"]
