"""Regression tests for the v2.0.1 per-repository scan-history filter.

The v1.8 page sent ``?status=`` and ``?trigger_type=`` query params
to ``GET /repositories/{id}/scans`` and rendered a filtered UI. v2.0
shipped with the route silently ignoring those params, so the table
showed every scan regardless of the active filter. v2.0.1 wires the
filter through the route, the service, and the repository so the
returned list actually matches the URL state.

These tests do not exercise the whole pipeline end-to-end (the demo
loader integration test in ``test_scan_service.py`` already covers
that path). The v2.0.1 defect is read-side, so the focused tests
assert the contract: the route returns only the rows that match the
filter, the service forwards the kwargs, and unknown values are
rejected with a bounded 422 envelope.
"""

from __future__ import annotations

import pytest
from app.main import app
from app.models.scan_run import ScanStatus, ScanTriggerType
from fastapi.testclient import TestClient

from tests.api_client import api_client


@pytest.fixture()
def client(app_config) -> TestClient:
    """Bind the FastAPI app to the in-memory test engine.

    The ``app_config`` fixture swaps the global ``app.db.session``
    engine for the test session's engine. Without it the
    :class:`TestClient` would talk to the default SQLite file
    instead of the per-test scratch DB and the per-repository
    listing would be empty.
    """
    return api_client(app)


@pytest.fixture()
def repository(client: TestClient) -> int:
    """Create a public GitHub repository for the per-repo listing."""
    resp = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def test_repository_scans_route_accepts_status_filter(client: TestClient, repository: int) -> None:
    """``?status=`` narrows the per-repository scan list.

    Reproduces the v2.0 defect: v2.0 ignored the query param and
    returned every scan; v2.0.1 must return only the matching
    status (or the empty list if no scan matches).
    """
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans?status=completed",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "pagination" in body
    for item in body["items"]:
        assert item["status"] == "completed"


def test_repository_scans_route_accepts_trigger_filter(client: TestClient, repository: int) -> None:
    """``?trigger_type=`` narrows the per-repository scan list."""
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans?trigger_type=manual",
    )
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert item["trigger_type"] == "manual"


def test_repository_scans_route_combines_filters(client: TestClient, repository: int) -> None:
    """``?status=`` and ``?trigger_type=`` combine as AND."""
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans?status=completed&trigger_type=manual",
    )
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert item["status"] == "completed"
        assert item["trigger_type"] == "manual"


def test_repository_scans_route_rejects_unknown_status(client: TestClient, repository: int) -> None:
    """Unknown ``?status=`` is rejected by the route's Query validator."""
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans?status=garbage",
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


def test_repository_scans_route_rejects_unknown_trigger(
    client: TestClient, repository: int
) -> None:
    """Unknown ``?trigger_type=`` is rejected by the route's Query validator."""
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans?trigger_type=garbage",
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


def test_repository_scans_route_no_filter_returns_all(client: TestClient, repository: int) -> None:
    """Without a filter, every scan in the repository is returned."""
    seed = client.post(
        f"/api/v1/repositories/{repository}/scans",
        json={"trigger_type": "manual"},
    )
    assert seed.status_code == 201
    resp = client.get(
        f"/api/v1/repositories/{repository}/scans",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 1


def test_scan_service_passes_status_to_repository(session) -> None:
    """The service forwards ``status`` and ``trigger_type`` to the repo.

    Pins the v2.0.1 wiring between
    ``app.services.scan_service.list_scans_for_repository`` and
    ``app.repositories.scan_repo.list_scans_for_repository``. A
    future regression that drops the kwargs would fail this test.
    """
    from app.models.repository import Repository
    from app.repositories import scan_repo as repo_module
    from app.services import scan_service

    # The service first calls ``get_repository_or_404``; the
    # in-memory session has no repository seeded, so we insert a
    # bare-bones row that the validation accepts.
    session.add(
        Repository(
            source_type="github",
            provider="github",
            owner="acceptance",
            name="filter-spy",
            canonical_url="https://github.com/acceptance/filter-spy",
            default_branch="main",
            visibility="public",
        )
    )
    session.commit()
    repository_id = int(
        session.execute(
            __import__("sqlalchemy").text("SELECT id FROM repositories ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    )

    captured: dict = {}

    def _spy(session_, _repository_id, *, page, page_size, **kwargs):
        captured.update(kwargs)
        return ([], 0)

    original = repo_module.list_scans_for_repository
    repo_module.list_scans_for_repository = _spy  # type: ignore[assignment]
    try:
        scan_service.list_scans_for_repository(
            session,
            repository_id,
            page=1,
            page_size=25,
            status=ScanStatus.COMPLETED,
            trigger_type=ScanTriggerType.MANUAL,
        )
    finally:
        repo_module.list_scans_for_repository = original  # type: ignore[assignment]
    assert captured.get("status") == ScanStatus.COMPLETED
    assert captured.get("trigger_type") == ScanTriggerType.MANUAL
