"""Tests for the v1.6.1 rescan service.

The v1.6 frontend called ``POST /api/v1/repositories/{id}/scans``
to retry or rescan a terminal scan. The previous
implementation only created a queued scan row; the
scan was broken because no workspace was associated
with it and the orchestrator failed the archive
validation stage with ``failure_code="not_found"``.

The v1.6.1 repair:
- creates a fresh scan row;
- creates a distinct fresh workspace;
- re-materialises the source evidence into the new
  workspace before the route returns;
- preserves the historical scan and workspace
  unchanged;
- refuses to leave a queued orphan scan when source
  evidence cannot be reconstructed (the route
  returns a bounded ``rescan_source_unavailable``
  error before any queued row is persisted).

External-network isolation
==========================

Two tests in this module drive the orchestrator
end-to-end via ``POST /api/v1/scans/{id}/run``:

- :func:`test_github_rescan_creates_distinct_scan_and_workspace`
- :func:`test_github_rescan_can_be_started_via_run_endpoint`

If those runs reached the real OSV, deps.dev, or
OpenSSF Scorecard endpoints, the
:func:`conftest._block_external_network` autouse
fixture would raise :exc:`NetworkAccessBlocked` and
the test would fail. The
:func:`conftest._fake_providers_for_scan_tests`
autouse fixture replaces the real provider factories
with in-process fakes that return
``ProviderUnavailable`` for every external call. The
fixture is global, so this module no longer needs to
import it; a test that needs a specific provider
outcome (e.g. the rescan fixtures that patch
``fetch_repository_metadata`` and ``download_tarball``
for the GitHub intake side) keeps its own patches;
the autouse fixture only applies when no override is
set.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from app.db import session as _db_session
from app.main import app
from app.models.scan_run import ScanStatus
from app.models.workspace import WorkspaceKind, WorkspaceState
from app.services import scan_service
from app.services.workspace_service import WorkspaceService
from app.utils.errors import ApiError, ApiErrorCode

from tests.api_client import api_client


def _build_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", '{"name":"rescan","version":"0.0.1"}')
        zf.writestr("src/index.js", "console.log('rescan');")
    return buf.getvalue()


def _build_tar_gz_bytes() -> bytes:
    """Build a real tar.gz archive with one file for the GitHub rescan path.

    The intake_tar_gz helper expects a gzip-compressed
    tar archive; a plain ZIP is rejected with
    ``archive_invalid``. The rescan service uses the
    same intake helper, so the test must use a
    real tar.gz.
    """
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b'{"name":"rescan-tar","version":"0.0.1"}'
        info = tarfile.TarInfo(name="package.json")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture
def client(app_config):
    return api_client(app)


def _ingest_upload(client) -> tuple[int, int]:
    body = _build_zip_bytes()
    r = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("rescan-fixture.zip", body, "application/zip")},
    )
    assert r.status_code == 201
    payload = r.json()
    return payload["repository"]["id"], payload["scan"]["id"]


def _seed_github_repository_and_scan(client) -> tuple[int, int]:
    """Create a GitHub repository, a queued scan, and a READY workspace."""
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    repo_id = r.json()["id"]
    r_scan = client.post(f"/api/v1/repositories/{repo_id}/scans")
    assert r_scan.status_code == 201
    first_scan_id = r_scan.json()["id"]
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        # The /scans route only creates a scan row;
        # the rescan service requires a READY
        # workspace as the source of truth. Seed one
        # manually.
        for ws in s.query(Workspace).filter_by(scan_run_id=first_scan_id).all():
            s.delete(ws)
        s.commit()
        scan = scan_service.get_scan_or_404(s, first_scan_id)
        workspaces = WorkspaceService(s)
        workspace = workspaces.create_for_scan(
            scan,
            kind=WorkspaceKind.GITHUB,
            archive_filename="octocat/Hello-World.tar.gz",
        )
        paths = workspaces.paths_for(workspace.workspace_key)
        paths.contents_dir.mkdir(parents=True, exist_ok=True)
        (paths.contents_dir / "README.md").write_text("first scan", encoding="utf-8")
        workspaces.transition(workspace, target=WorkspaceState.VALIDATING)
        workspaces.transition(
            workspace,
            target=WorkspaceState.READY,
            archive_sha256="a" * 64,
            archive_size=20,
            file_count=1,
            uncompressed_size=20,
        )
        s.commit()
    return repo_id, first_scan_id


def _seed_upload_repository_and_scan(client) -> tuple[int, int]:
    """Create an upload repository, a queued scan, and a READY workspace.

    The public /repositories/upload endpoint already
    creates a READY workspace, so this helper just
    returns the ids. The test then runs the rescan.
    """
    return _ingest_upload(client)


def _fake_github_provider(_client, *, owner, name, canonical_url, requested_ref=None):
    from app.providers.github_provider import GitHubRepositoryMetadata

    return GitHubRepositoryMetadata(
        owner=owner,
        name=name,
        default_branch="main",
        description="Test",
        visibility="public",
        archived=False,
        resolved_commit_sha="0" * 40,
        canonical_url=canonical_url,
    )


def _fake_github_tarball(_client, *, owner, name, commit_sha, **kwargs):
    from app.providers.github_provider import GitHubTarball

    body = _build_tar_gz_bytes()
    return GitHubTarball(
        body=body,
        content_sha256="0" * 64,
        content_length=len(body),
        resolved_commit_sha=commit_sha,
        etag=None,
        last_modified=None,
    )


def test_github_rescan_creates_distinct_scan_and_workspace(client) -> None:
    """GitHub rescan materialises a new workspace and scan."""
    repo_id, first_scan_id = _seed_github_repository_and_scan(client)

    with (
        patch(
            "app.services.rescan_service.github_provider.fetch_repository_metadata",
            side_effect=_fake_github_provider,
        ),
        patch(
            "app.services.rescan_service.github_provider.download_tarball",
            side_effect=_fake_github_tarball,
        ),
    ):
        r2 = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    assert r2.status_code == 201
    new_scan_id = r2.json()["id"]
    assert new_scan_id != first_scan_id

    with _db_session.SessionLocal() as s:
        from app.models.scan_run import ScanRun
        from app.models.workspace import Workspace

        old_scan = s.get(ScanRun, first_scan_id)
        new_scan = s.get(ScanRun, new_scan_id)
        assert new_scan.status == ScanStatus.QUEUED
        old_workspace = s.query(Workspace).filter_by(scan_run_id=first_scan_id).one()
        new_workspace = s.query(Workspace).filter_by(scan_run_id=new_scan_id).one()
        assert old_workspace.id != new_workspace.id
        assert old_workspace.scan_run_id == first_scan_id
        assert new_workspace.scan_run_id == new_scan_id
        assert new_workspace.state == WorkspaceState.READY
        assert old_scan.repository_id == repo_id
        assert new_scan.repository_id == repo_id
        # Both scans are bound to the same repository.
        assert old_scan.id != new_scan.id
        # Historical scan + workspace unchanged in id
        # and repository binding.
        assert old_workspace.workspace_key != new_workspace.workspace_key


def test_github_rescan_can_be_started_via_run_endpoint(client) -> None:
    """A new GitHub rescan can be passed to /scans/{id}/run."""
    repo_id, _first_scan_id = _seed_github_repository_and_scan(client)
    with (
        patch(
            "app.services.rescan_service.github_provider.fetch_repository_metadata",
            side_effect=_fake_github_provider,
        ),
        patch(
            "app.services.rescan_service.github_provider.download_tarball",
            side_effect=_fake_github_tarball,
        ),
    ):
        r2 = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    new_scan_id = r2.json()["id"]
    r3 = client.post(f"/api/v1/scans/{new_scan_id}/run")
    # The run endpoint must accept the new scan id;
    # the previous v1.6 defect returned
    # ``failure_code="not_found"`` because the scan
    # had no workspace. We do not assert the final
    # state because the inline executor proceeds
    # into the orchestrator; we only assert that
    # the call does not immediately 500.
    assert r3.status_code in (200, 409)


def test_upload_rescan_creates_new_workspace_from_previous(client) -> None:
    """Upload rescan copies the previous workspace into a new one."""
    repo_id, scan_id = _seed_upload_repository_and_scan(client)

    r2 = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    assert r2.status_code == 201
    new_scan_id = r2.json()["id"]

    with _db_session.SessionLocal() as s:
        from app.models.scan_run import ScanRun
        from app.models.workspace import Workspace

        old_scan = s.get(ScanRun, scan_id)
        new_scan = s.get(ScanRun, new_scan_id)
        old_workspace = s.query(Workspace).filter_by(scan_run_id=scan_id).one()
        new_workspace = s.query(Workspace).filter_by(scan_run_id=new_scan_id).one()
        assert old_scan.id != new_scan.id
        assert old_workspace.id != new_workspace.id
        assert old_scan.status == ScanStatus.QUEUED
        assert old_workspace.state == WorkspaceState.READY
        assert new_workspace.state == WorkspaceState.READY
        contents_root = WorkspaceService(s).paths_for(new_workspace.workspace_key).contents_dir
        # The package.json from the original archive
        # is now in the new workspace.
        assert (contents_root / "package.json").is_file()
        assert (contents_root / "src" / "index.js").is_file()
        # Source provenance is preserved.
        assert new_workspace.archive_sha256 == old_workspace.archive_sha256


def test_upload_rescan_rejects_when_previous_workspace_missing(client) -> None:
    """Missing previous workspace returns bounded error before any queued row.

    The rescan service seeds the fresh scan only
    after the source has been confirmed available.
    When the previous workspace is gone (or never
    produced READY contents), the route returns
    ``rescan_source_unavailable`` and the original
    repository is left untouched.
    """
    repo_id, scan_id = _ingest_upload(client)
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        workspace = s.query(Workspace).filter_by(scan_run_id=scan_id).one()
        s.delete(workspace)
        s.commit()

    r2 = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    assert r2.status_code == 422
    body = r2.json()
    assert body["error"]["code"] == "rescan_source_unavailable"
    assert "no longer available" in body["error"]["message"].lower()

    with _db_session.SessionLocal() as s:
        from app.models.scan_run import ScanRun

        new_queued = [
            scan
            for scan in s.query(ScanRun).filter_by(repository_id=repo_id).all()
            if scan.id != scan_id and scan.status == ScanStatus.QUEUED
        ]
        assert new_queued == []


def test_rescan_invalid_repository_returns_404(client) -> None:
    r = client.post("/api/v1/repositories/99999/rescan")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"


def test_rescan_stable_error_envelope_preserved(client) -> None:
    """The bounded source-unavailable error uses the stable envelope."""
    repo_id, scan_id = _ingest_upload(client)
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        workspace = s.query(Workspace).filter_by(scan_run_id=scan_id).one()
        s.delete(workspace)
        s.commit()

    r = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert "request_id" in body["error"]


def test_rescan_creates_workspace_with_separate_workspace_root(client) -> None:
    """The new workspace has a different on-disk key from the previous one."""
    repo_id, scan_id = _ingest_upload(client)
    r = client.post(f"/api/v1/repositories/{repo_id}/rescan")
    new_scan_id = r.json()["id"]

    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        old = s.query(Workspace).filter_by(scan_run_id=scan_id).one()
        new = s.query(Workspace).filter_by(scan_run_id=new_scan_id).one()
        # Distinct on-disk workspace keys ensure the
        # previous workspace is never mutated by the
        # orchestrator when it processes the new scan.
        assert old.workspace_key != new.workspace_key
        # Both workspaces are within the configured
        # root, so the orchestrator's bounded
        # workspace_service can resolve both.
        workspaces = WorkspaceService(s)
        old_paths = workspaces.paths_for(old.workspace_key)
        new_paths = workspaces.paths_for(new.workspace_key)
        assert str(old_paths.workspace_dir).startswith(str(workspaces.root))
        assert str(new_paths.workspace_dir).startswith(str(workspaces.root))


def test_rescan_unknown_source_type_returns_bounded_error() -> None:
    """A future source type that the service does not understand is rejected.

    The defensive branch in the rescan service is
    hit by constructing a service instance directly
    with a mock repository whose ``source_type`` is
    a string that the service does not know about.
    The API surface only accepts the two known
    source types, so the defensive branch is best
    exercised at the service layer.
    """
    from app.services.rescan_service import RescanService

    class _StubSourceType:
        value = "future_thing"

    class _StubRepo:
        def __init__(self) -> None:
            self.id = 12345
            self.source_type = _StubSourceType()
            self.canonical_url = "https://example.com/future"

    class _StubSession:
        def get(self, _model, _id):
            return _StubRepo()

    with pytest.raises(ApiError) as exc_info:
        RescanService(_StubSession()).rescan_repository(12345)
    assert exc_info.value.code == ApiErrorCode.RESCAN_SOURCE_UNAVAILABLE


def test_rescan_github_codes_map_to_provider_unavailable(client, monkeypatch) -> None:
    """v2.0 defect fix.

    Previously the rescan route only mapped the literal
    string ``"github_error"`` to PROVIDER_UNAVAILABLE;
    every other ``github_*`` code (notably
    ``github_not_found``) wrongly landed on
    RESCAN_SOURCE_UNAVAILABLE. The fix is a prefix
    match on the ``github_`` family so every upstream
    materialisation failure surfaces as
    provider_unavailable.
    """
    from app.services import rescan_service
    from app.services.rescan_service import _RescanError

    class _StubRepo:
        def __init__(self) -> None:
            self.id = 1
            self.source_type = "github"
            self.canonical_url = "https://github.com/x/y"

    class _StubRescanService:
        def __init__(self, _session) -> None:
            pass

        def rescan_repository(self, repository_id, **_kwargs):
            raise _RescanError(
                code="github_not_found",
                message="Upstream returned 404 Not Found.",
            )

    monkeypatch.setattr(rescan_service, "RescanService", _StubRescanService)
    # The route imports RescanService at module load
    # time, so we also have to patch the symbol the
    # route already bound to.
    import app.api.scans as _scans_api

    monkeypatch.setattr(_scans_api, "RescanService", _StubRescanService)
    with _db_session.SessionLocal() as s:
        from app.models.repository import (
            Repository,
            RepositoryProvider,
            RepositorySourceType,
            RepositoryVisibility,
        )

        s.add(
            Repository(
                id=1,
                source_type=RepositorySourceType.GITHUB,
                provider=RepositoryProvider.GITHUB,
                owner="x",
                name="y",
                canonical_url="https://github.com/x/y",
                default_branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            )
        )
        s.commit()
    try:
        r = client.post("/api/v1/repositories/1/rescan")
        # PROVIDER_UNAVAILABLE maps to HTTP 502 per the
        # project's error-envelope table; the source
        # error is still the GitHub 404, not a generic
        # 500.
        assert r.status_code == 502
        body = r.json()
        assert body["error"]["code"] == "provider_unavailable"
        assert body["error"]["details"]["rescan_code"] == "github_not_found"
    finally:
        with _db_session.SessionLocal() as s:
            from app.models.repository import Repository

            s.query(Repository).filter_by(id=1).delete()
            s.commit()


def test_rescan_non_github_codes_map_to_source_unavailable(client, monkeypatch) -> None:
    """v2.0 defect fix.

    Non-GitHub codes (e.g. ``rescan_source_unavailable`` or
    any future provider-specific code that does not start
    with ``github_``) must continue to land on
    RESCAN_SOURCE_UNAVAILABLE. The widening of the
    upstream-failure branch must not eat the genuine
    source-unavailable branch.
    """
    from app.services import rescan_service
    from app.services.rescan_service import _RescanError

    class _StubRescanService:
        def __init__(self, _session) -> None:
            pass

        def rescan_repository(self, repository_id, **_kwargs):
            raise _RescanError(
                code="rescan_source_unavailable",
                message="The original uploaded source is no longer available.",
            )

    monkeypatch.setattr(rescan_service, "RescanService", _StubRescanService)
    import app.api.scans as _scans_api

    monkeypatch.setattr(_scans_api, "RescanService", _StubRescanService)
    with _db_session.SessionLocal() as s:
        from app.models.repository import (
            Repository,
            RepositoryProvider,
            RepositorySourceType,
            RepositoryVisibility,
        )

        s.add(
            Repository(
                id=1,
                source_type=RepositorySourceType.GITHUB,
                provider=RepositoryProvider.GITHUB,
                owner="x",
                name="y",
                canonical_url="https://github.com/x/y",
                default_branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            )
        )
        s.commit()
    try:
        r = client.post("/api/v1/repositories/1/rescan")
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "rescan_source_unavailable"
        assert body["error"]["details"]["rescan_code"] == "rescan_source_unavailable"
    finally:
        with _db_session.SessionLocal() as s:
            from app.models.repository import Repository

            s.query(Repository).filter_by(id=1).delete()
            s.commit()
