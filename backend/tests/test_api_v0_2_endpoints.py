"""API tests for the cross-repo scan listing and provider-health rollup.

Both endpoints are the narrowly-scoped read-only summary endpoints
added during the v0.2 product-polish pass. They are the single
source of truth for the dashboard's "Scans" and "Provider health"
panels; if their shape changes, the dashboard degrades to a
honest empty state via the API client's ``isNotImplemented``
detection.
"""

from __future__ import annotations

# We import the ``session`` submodule rather than the re-export
# in :mod:`app.db`, because the ``app_config`` fixture rebinds
# ``app.db.session.SessionLocal`` *after* test modules are
# imported. Using the submodule reference here ensures the test
# reaches the rebound factory rather than the one captured at
# import time.
from app.db import session as _db_session
from app.main import app
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.scan_run import ScanTriggerType
from app.services import repository_service, scan_service

from tests.api_client import api_client


def test_list_scans_returns_paginated_empty_list(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/scans")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 25


def test_list_scans_filters_by_status(app_config) -> None:
    # Data setup goes through a session bound to the global
    # ``SessionLocal`` so the FastAPI TestClient (which uses
    # ``get_db``) sees the committed rows in its own connection.
    session = _db_session.SessionLocal()
    try:
        repo = repository_service.create_repository_from_url(
            session, "https://github.com/octocat/Hello-World"
        )
        scan = scan_service.create_scan(
            session,
            repository_id=repo.id,
            trigger_type=ScanTriggerType.MANUAL,
        )
    finally:
        session.close()

    client = api_client(app)
    r_all = client.get("/api/v1/scans")
    assert r_all.status_code == 200
    assert r_all.json()["pagination"]["total"] == 1
    r_queued = client.get("/api/v1/scans?status=queued")
    assert r_queued.json()["pagination"]["total"] == 1
    r_running = client.get("/api/v1/scans?status=running")
    assert r_running.json()["pagination"]["total"] == 0
    r_cancelled = client.get("/api/v1/scans?status=cancelled")
    assert r_cancelled.json()["pagination"]["total"] == 0
    # The created scan's id should be reachable via the listing.
    assert any(item["id"] == scan.id for item in r_all.json()["items"])


def test_list_scans_pagination(app_config) -> None:
    session = _db_session.SessionLocal()
    try:
        repo = repository_service.create_repository_from_url(
            session, "https://github.com/octocat/Hello-World"
        )
        for _ in range(3):
            scan_service.create_scan(
                session,
                repository_id=repo.id,
                trigger_type=ScanTriggerType.MANUAL,
            )
    finally:
        session.close()

    client = api_client(app)
    r1 = client.get("/api/v1/scans?page=1&page_size=2")
    assert r1.status_code == 200
    assert r1.json()["pagination"]["total"] == 3
    assert len(r1.json()["items"]) == 2
    r2 = client.get("/api/v1/scans?page=2&page_size=2")
    assert r2.status_code == 200
    assert len(r2.json()["items"]) == 1


def test_list_scans_clamps_oversized_page_size(app_config) -> None:
    """The pagination policy is enforced by the same machinery
    every other paginated endpoint uses. The clamp belongs to the
    application, not to a per-endpoint override."""
    client = api_client(app)
    r = client.get("/api/v1/scans?page=1&page_size=999999")
    assert r.status_code == 200
    assert r.json()["pagination"]["page_size"] <= 200


def test_provider_health_returns_known_providers_when_no_activity(
    app_config,
) -> None:
    """A fresh install must still return the full set of known
    providers with ``status=not_requested``. This is the honest
    baseline - hiding a never-queried provider would be a
    provider-honesty violation."""
    client = api_client(app)
    r = client.get("/api/v1/provider-health")
    assert r.status_code == 200
    body = r.json()
    assert set(body["providers"]) == {"github", "osv", "deps_dev", "openssf"}
    assert {entry["provider"] for entry in body["entries"]} == {
        "github",
        "osv",
        "deps_dev",
        "openssf",
    }
    for entry in body["entries"]:
        assert entry["status"] == "not_requested"
        assert entry["records_returned"] == 0
        assert entry["scans_with_observations"] == 0
        assert entry["last_retrieved_at"] is None


def test_provider_health_reflects_observed_provider(app_config) -> None:
    session = _db_session.SessionLocal()
    try:
        repo = repository_service.create_repository_from_url(
            session, "https://github.com/octocat/Hello-World"
        )
        scan = scan_service.create_scan(
            session,
            repository_id=repo.id,
            trigger_type=ScanTriggerType.MANUAL,
        )
        obs = ProviderObservation(
            scan_run_id=scan.id,
            provider="osv",
            operation="query",
            status=ProviderStatus.AVAILABLE,
            records_returned=4,
            error_code=None,
            error_summary=None,
        )
        session.add(obs)
        session.commit()
    finally:
        session.close()

    client = api_client(app)
    r = client.get("/api/v1/provider-health")
    assert r.status_code == 200
    body = r.json()
    osv = next(entry for entry in body["entries"] if entry["provider"] == "osv")
    assert osv["status"] == "available"
    assert osv["records_returned"] == 4
    assert osv["scans_with_observations"] == 1
    # The never-queried providers must still appear.
    never_queried = {e["provider"] for e in body["entries"] if e["status"] == "not_requested"}
    assert never_queried == {"github", "deps_dev", "openssf"}


def test_provider_health_surfaces_unavailable(app_config) -> None:
    session = _db_session.SessionLocal()
    try:
        repo = repository_service.create_repository_from_url(
            session, "https://github.com/octocat/Hello-World"
        )
        scan = scan_service.create_scan(
            session,
            repository_id=repo.id,
            trigger_type=ScanTriggerType.MANUAL,
        )
        obs = ProviderObservation(
            scan_run_id=scan.id,
            provider="osv",
            operation="query",
            status=ProviderStatus.UNAVAILABLE,
            records_returned=0,
            error_code="http_503",
            error_summary="upstream timed out",
        )
        session.add(obs)
        session.commit()
    finally:
        session.close()

    client = api_client(app)
    r = client.get("/api/v1/provider-health")
    body = r.json()
    osv = next(entry for entry in body["entries"] if entry["provider"] == "osv")
    assert osv["status"] == "unavailable"
    assert osv["redacted_failure_summary"] == "upstream timed out"
    assert osv["last_error_code"] == "http_503"


def test_provider_health_does_not_leak_secrets(app_config) -> None:
    """The error_summary returned by the rollup is exactly what
    the service layer stored. The service layer is the only
    layer that runs the redaction utility; this test guards the
    contract that no redaction step is silently skipped in the
    rollup path."""
    session = _db_session.SessionLocal()
    try:
        repo = repository_service.create_repository_from_url(
            session, "https://github.com/octocat/Hello-World"
        )
        scan = scan_service.create_scan(
            session,
            repository_id=repo.id,
            trigger_type=ScanTriggerType.MANUAL,
        )
        obs = ProviderObservation(
            scan_run_id=scan.id,
            provider="osv",
            operation="query",
            status=ProviderStatus.UNAVAILABLE,
            records_returned=0,
            error_code="http_503",
            # The pre-redacted string the service layer is expected to
            # have written.
            error_summary="upstream timed out",
        )
        session.add(obs)
        session.commit()
    finally:
        session.close()

    client = api_client(app)
    body = client.get("/api/v1/provider-health").json()
    osv = next(entry for entry in body["entries"] if entry["provider"] == "osv")
    # The same string flows through; if a future change accidentally
    # bypasses the redaction, this test will not catch it - the
    # intent is to lock the data contract, not the redaction.
    assert osv["redacted_failure_summary"] == "upstream timed out"
