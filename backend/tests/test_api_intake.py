"""Tests for the intake API endpoints (GitHub + upload)."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from typing import Any

import pytest
from app.main import app
from app.providers import github_provider

from tests.api_client import api_client


class _FakeResponse:
    def __init__(
        self, status_code: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, list[_FakeResponse]] = {}

    def queue(self, path: str, response: _FakeResponse) -> None:
        self.responses.setdefault(path, []).append(response)

    def _consume(self, url: str) -> _FakeResponse:
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        self.calls.append(path)
        queue = self.responses.get(path, [])
        if not queue:
            return _FakeResponse(500, b"", {})
        return queue.pop(0)

    def get_json(self, url: str, **_: Any) -> _FakeResponse:
        from app.utils.bounded_http import BoundedHttpError

        response = self._consume(url)
        if response.status_code == 404:
            raise BoundedHttpError("http_not_found", "Upstream returned 404 Not Found.")
        if 500 <= response.status_code < 600:
            raise BoundedHttpError(
                "http_server_error",
                f"Upstream returned {response.status_code}.",
                http_status=response.status_code,
            )
        return response

    def download(self, url: str, **_: Any) -> _FakeResponse:
        from app.utils.bounded_http import BoundedHttpError

        response = self._consume(url)
        if 500 <= response.status_code < 600:
            raise BoundedHttpError(
                "http_server_error",
                f"Upstream returned {response.status_code}.",
                http_status=response.status_code,
            )
        return response

    def close(self) -> None:
        return None


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:
    client = _FakeClient()

    def _build(**kwargs):
        return client

    monkeypatch.setattr(github_provider, "build_client", _build)
    return client


@pytest.fixture
def client(app_config):
    return api_client(app)


def _build_tarball_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"# hi"
        info = tarfile.TarInfo("README.md")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_github_endpoint_creates_repository_scan_and_workspace(client, fake_client) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    fake_client.queue(
        "/repos/octocat/Hello-World",
        _FakeResponse(
            200,
            json.dumps(
                {
                    "default_branch": "main",
                    "visibility": "public",
                    "archived": False,
                }
            ).encode("utf-8"),
            {"Content-Type": "application/json"},
        ),
    )
    fake_client.queue(
        "/repos/octocat/Hello-World/branches/main",
        _FakeResponse(
            200,
            json.dumps({"name": "main", "commit": {"sha": sha}}).encode("utf-8"),
            {"Content-Type": "application/json"},
        ),
    )
    fake_client.queue(
        f"/octocat/Hello-World/tar.gz/{sha}",
        _FakeResponse(
            200,
            _build_tarball_bytes(),
            {"Content-Type": "application/x-gzip"},
        ),
    )
    response = client.post(
        "/api/v1/repositories/github",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["repository"]["provider"] == "github"
    assert body["scan"]["status"] == "queued"
    assert body["workspace"]["kind"] == "github"
    assert body["workspace"]["state"] == "ready"
    assert body["intake_summary"]["resolved_commit_sha"] == sha


def test_github_endpoint_rejects_non_github_url(client) -> None:
    response = client.post(
        "/api/v1/repositories/github",
        json={"canonical_url": "https://example.com/octocat/Hello-World"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_github_endpoint_rejects_unsafe_request_ref(client) -> None:
    response = client.post(
        "/api/v1/repositories/github",
        json={
            "canonical_url": "https://github.com/octocat/Hello-World",
            "requested_ref": "../etc",
        },
    )
    assert response.status_code == 422


def test_upload_endpoint_creates_scan_and_workspace(client) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", b"hi")
    body = buf.getvalue()
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("hello.zip", body, "application/zip")},
    )
    assert response.status_code == 201
    result = response.json()
    assert result["workspace"]["kind"] == "uploaded_archive"
    assert result["workspace"]["state"] == "ready"
    assert result["workspace"]["file_count"] == 1


def test_upload_endpoint_rejects_non_zip_extension(client) -> None:
    body = b"not a zip"
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("hello.txt", body, "application/zip")},
    )
    assert response.status_code == 422


def test_upload_endpoint_rejects_traversal(client) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", b"evil")
    body = buf.getvalue()
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("evil.zip", body, "application/zip")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "archive_unsafe"


def test_upload_endpoint_rejects_oversized_declared_length(client, monkeypatch) -> None:
    """A declared ``Content-Length`` that exceeds the cap must be
    rejected before any body byte is read; the handler does not
    read the request body in that case.
    """
    from app.api import intake as intake_module
    from app.core.config import Settings

    # Tighten the cap to a value we can prove.
    small_settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        workspace_root="./var/workspace",
        archive_max_compressed_bytes=512,
    )
    monkeypatch.setattr(intake_module, "get_settings", lambda: small_settings)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", b"hi" * 1024)
    body = buf.getvalue()
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("big.zip", body, "application/zip")},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["declared_bytes"] > 512
    assert error["details"]["max_compressed_bytes"] == 512


def test_upload_endpoint_uses_configured_limit_not_hardcoded(client, monkeypatch) -> None:
    """The endpoint must honour ``archive_max_compressed_bytes`` from
    settings rather than a hard-coded 100 MiB value. We verify
    this by tightening the cap and observing a rejection that
    would not happen against a 100 MiB cap.
    """
    from app.api import intake as intake_module
    from app.core.config import Settings

    # Cap of 1 byte: every well-formed zip upload is rejected.
    tiny_settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        workspace_root="./var/workspace",
        archive_max_compressed_bytes=1,
    )
    monkeypatch.setattr(intake_module, "get_settings", lambda: tiny_settings)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", b"hi")
    body = buf.getvalue()
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("hello.zip", body, "application/zip")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_upload_endpoint_streams_body_to_quarantine(client, monkeypatch) -> None:
    """The upload endpoint must hand the upload to the intake
    service as a streaming source, not as a fully-buffered
    ``bytes`` payload.

    We confirm this by intercepting
    ``IntakeService.intake_upload`` and recording the source
    object it was called with. The source must be a callable
    (the streaming contract) rather than a ``bytes`` /
    ``list`` (the legacy read-all-into-memory contract).
    """
    from app.api import intake as intake_module

    captured: dict = {}

    def _capture_intake_upload(self, *, upload, archive_filename):
        # Record the source type and the fact that the
        # endpoint handed us a streaming callable.
        captured["type"] = type(upload).__name__
        captured["callable"] = callable(upload)
        # Drain a single chunk to prove the source is
        # callable; we do not run the full quarantine here
        # because the real intake_upload writes to disk.
        try:
            first = upload(64)
            captured["first_chunk_type"] = type(first).__name__
            captured["first_chunk_len"] = len(first) if first else 0
        except Exception as exc:  # pragma: no cover
            captured["first_chunk_error"] = repr(exc)
        # Return a minimal IntakeResult by bypassing the
        # real pipeline. The test only cares that the
        # endpoint handed us a streaming source.
        from app.models.repository import (
            Repository,
            RepositoryProvider,
            RepositorySourceType,
            RepositoryVisibility,
        )
        from app.models.scan_run import ScanRun, ScanStatus
        from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState

        # Persist a minimal repository / scan / workspace
        # triplet so the API response can render.
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="placeholder",
            canonical_url="upload://placeholder",
            description="Uploaded archive",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
        )
        self._session.add(repo)
        self._session.flush()
        scan = ScanRun(
            repository_id=repo.id,
            status=ScanStatus.COMPLETED,
            trigger_type="upload",
        )
        self._session.add(scan)
        self._session.flush()
        workspace = Workspace(
            scan_run_id=scan.id,
            workspace_key="placeholder-key-min-16",
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            state=WorkspaceState.READY,
            archive_filename=archive_filename,
        )
        self._session.add(workspace)
        self._session.commit()
        from app.services.intake_service import IntakeResult

        return IntakeResult(
            repository=repo,
            scan=scan,
            workspace=workspace,
            intake_summary={"kind": "uploaded_archive"},
        )

    monkeypatch.setattr(
        intake_module.intake_service.IntakeService,
        "intake_upload",
        _capture_intake_upload,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", b"hi" * 4096)
    body = buf.getvalue()
    response = client.post(
        "/api/v1/repositories/upload",
        files={"file": ("streamed.zip", body, "application/zip")},
    )
    assert response.status_code == 201
    # The endpoint must hand the upload to the intake
    # service as a streaming callable, not as a fully
    # buffered ``list[bytes]`` or ``bytes`` object.
    assert captured["callable"] is True
    # The source must yield a ``bytes`` chunk; the size
    # is bounded by the upload read chunk.
    assert captured.get("first_chunk_type") == "bytes"
    assert captured["first_chunk_len"] <= intake_module.UPLOAD_READ_CHUNK
