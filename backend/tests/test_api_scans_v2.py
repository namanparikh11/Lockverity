"""Tests for the v0.2 scan API endpoints (run, cancel, providers, stages).

External-network isolation
==========================

These tests run the full scan orchestrator inline. The
orchestrator drives the GitHub, OSV, deps.dev and OpenSSF
Scorecard providers; if any of those made a real outbound
call, the :func:`conftest._block_external_network`
autouse fixture would raise :exc:`NetworkAccessBlocked`
and the test would fail.

The :func:`conftest._fake_providers_for_scan_tests`
autouse fixture replaces the real provider factories with
in-process fakes so the orchestrator records honest
``not_requested`` / ``provider_unavailable`` observations
without opening a socket. The fixture is global, so this
module no longer needs to import it; a test that
overrides the factory via its own ``monkeypatch`` still
takes precedence within its scope.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from app.db import session as _db_session
from app.main import app
from app.models.scan_run import ScanStatus
from app.services import scan_service
from app.singletons import get_executor, reset_executor_for_tests

from tests.api_client import api_client


@pytest.fixture(autouse=True)
def _reset_executor():
    """Always run the orchestrator inline in API tests."""
    reset_executor_for_tests()
    yield
    get_executor().shutdown(wait=True)


@pytest.fixture
def client(app_config):
    return api_client(app)


def _build_zip_with_manifest() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", '{"name":"sample","version":"1.0.0"}')
        zf.writestr("src/index.js", "console.log('hi')")
    return buf.getvalue()


def _create_scan_with_workspace(client) -> int:
    """Create a repository, a scan, and a ready workspace with a manifest."""
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    scan_id = r2.json()["id"]
    with _db_session.SessionLocal() as s:
        from app.models.workspace import WorkspaceKind, WorkspaceState
        from app.services.workspace_service import WorkspaceService

        workspaces = WorkspaceService(s)
        scan = scan_service.get_scan_or_404(s, scan_id)
        workspace = workspaces.create_for_scan(
            scan, kind=WorkspaceKind.GITHUB, archive_filename="x.tar.gz"
        )
        paths = workspaces.paths_for(workspace.workspace_key)
        paths.contents_dir.mkdir(parents=True, exist_ok=True)
        (paths.contents_dir / "package.json").write_text(
            '{"name":"x","version":"1"}', encoding="utf-8"
        )
        workspaces.transition(workspace, target=WorkspaceState.VALIDATING)
        workspaces.transition(
            workspace,
            target=WorkspaceState.READY,
            archive_sha256="a" * 64,
            archive_size=10,
            file_count=1,
            uncompressed_size=10,
        )
        s.commit()
    return scan_id


def test_create_scan_still_works(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    assert r2.status_code == 201


def test_run_endpoint_advances_scan_to_completed(client) -> None:
    scan_id = _create_scan_with_workspace(client)
    r3 = client.post(f"/api/v1/scans/{scan_id}/run")
    assert r3.status_code == 200
    r4 = client.get(f"/api/v1/scans/{scan_id}")
    body = r4.json()
    # The external providers (OSV, deps.dev, OpenSSF
    # Scorecard) are faked as unavailable; the orchestrator
    # records honest ``provider_unavailable`` observations
    # and marks the scan ``partial`` rather than
    # ``completed``. Local-only work still completes
    # successfully; the terminal status reflects the
    # honest provider availability.
    assert body["status"] in {"completed", "partial"}
    r5 = client.get(f"/api/v1/scans/{scan_id}/stages")
    assert len(r5.json()["items"]) == 10


def test_run_endpoint_rejects_running_scan(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    scan_id = r2.json()["id"]
    with _db_session.SessionLocal() as s:
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
    r3 = client.post(f"/api/v1/scans/{scan_id}/run")
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "illegal_transition"


def test_cancel_endpoint_cancels_queued_scan(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    scan_id = r2.json()["id"]
    r3 = client.post(f"/api/v1/scans/{scan_id}/cancel")
    assert r3.status_code == 200
    r4 = client.get(f"/api/v1/scans/{scan_id}")
    assert r4.json()["status"] == "cancelled"


def test_cancel_endpoint_rejects_terminal_scan(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    scan_id = r2.json()["id"]
    with _db_session.SessionLocal() as s:
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
    r3 = client.post(f"/api/v1/scans/{scan_id}/cancel")
    assert r3.status_code == 409


def test_providers_endpoint_records_observations(client) -> None:
    scan_id = _create_scan_with_workspace(client)
    client.post(f"/api/v1/scans/{scan_id}/run")
    r3 = client.get(f"/api/v1/scans/{scan_id}/providers")
    body = r3.json()
    # v0.4 records one observation per real provider call
    # plus the structural stage observations. A scan with no
    # components still has at least 6 structural rows
    # (intake, archive_validation, manifest_discovery,
    # dependency_parsing, rule_engine, export_generation)
    # plus 2 from the workflow and scorecard providers.
    assert body["pagination"]["total"] >= 8
    statuses = [item["status"] for item in body["items"]]
    assert "not_requested" in statuses
    providers = {item["provider"] for item in body["items"]}
    # The v0.4 wiring ensures the structural providers are
    # always present, even when the scan has no components.
    assert "filesystem" in providers
    assert "rule_engine" in providers


def test_stages_endpoint_returns_pipeline(client) -> None:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r2 = client.post(f"/api/v1/repositories/{repo_id}/scans")
    scan_id = r2.json()["id"]
    r3 = client.get(f"/api/v1/scans/{scan_id}/stages")
    assert r3.status_code == 200
    types = [s["stage_type"] for s in r3.json()["items"]]
    assert types[0] == "repository_intake"
    assert types[-1] == "export_generation"


def test_system_provider_limits_endpoint(client) -> None:
    r = client.get("/api/v1/system/provider-limits")
    assert r.status_code == 200
    body = r.json()
    assert "github" in body
    assert isinstance(body["github"], list)


def test_system_workspaces_cleanup_endpoint(client) -> None:
    r = client.post("/api/v1/system/workspaces/cleanup")
    assert r.status_code == 200
    body = r.json()
    assert "removed" in body
    assert "removed_workspaces" in body
