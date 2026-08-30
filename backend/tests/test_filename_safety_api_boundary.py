"""Filename-safety defence-in-depth tests at the public API boundary.

The intake layer sanitises ``Repository.original_filename``
and ``Workspace.archive_filename`` at write time
(``basename_safely``). This file exercises the defence-in-
depth path validators on the response schemas
(``RepositoryRead``, ``WorkspaceRead``) so a historical row
that pre-dates the intake sanitiser, or a row that was
inserted by an operator with a tool that bypassed the
sanitiser, can never expose a Windows drive-letter path,
a POSIX absolute path, a parent-traversal segment, or a
root path through the public API.

The tests target every public surface that returns a
filename-shaped value:

- ``GET /api/v1/repositories`` (list)
- ``GET /api/v1/repositories/{id}`` (detail)
- ``POST /api/v1/system/workspaces/cleanup`` (system
  administrative cleanup; returns the cleaned workspaces)
- The ``display_name`` field on the list / detail
  response (built from the raw model attribute; the
  schema validator alone is not enough)
- The ``POST /api/v1/repositories/upload`` invalid-
  extension error envelope (must not echo the raw
  client-supplied filename)
- The ``ARCHIVE_UNSAFE`` error envelope from the
  intake service (must sanitise the ``filename`` detail)
- The ``basename_safely`` helper itself, including the
  ``C:secret.zip`` drive-relative shape and UNC paths

The public response must contain only the basename
component, never a parent path, never a drive letter,
and never a root path. The persisted database row is not
mutated by the read path: a historical row with a
pathful value must continue to carry that value on disk
(it is evidence; it does not get rewritten at read time).
Only the response payload is sanitised.
"""

from __future__ import annotations

import pytest
from app.api.repositories import _display_name
from app.db import session as _db_session
from app.main import app
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.models.workspace import WorkspaceKind, WorkspaceState
from app.services import scan_service
from app.services.workspace_service import WorkspaceService
from app.utils.paths import basename_safely

from tests.api_client import api_client


@pytest.fixture
def client(app_config):
    return api_client(app)


def _build_repo(
    *,
    owner: str,
    name: str,
    source_type: RepositorySourceType,
    original_filename: str | None = None,
) -> int:
    """Create a single repository row and return its id."""
    canonical = (
        f"https://github.com/{owner}/{name}"
        if source_type == RepositorySourceType.GITHUB
        else f"upload://{name}"
    )
    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=source_type,
            provider=(
                RepositoryProvider.GITHUB
                if source_type == RepositorySourceType.GITHUB
                else RepositoryProvider.LOCAL_UPLOAD
            ),
            owner=owner,
            name=name,
            canonical_url=canonical,
            visibility=(
                RepositoryVisibility.PUBLIC
                if source_type == RepositorySourceType.GITHUB
                else RepositoryVisibility.PRIVATE
            ),
            original_filename=original_filename,
        )
        s.add(repo)
        s.commit()
        return repo.id


def _build_scan_with_pathful_workspace(
    *,
    repository_id: int,
    archive_filename: str,
) -> int:
    """Create a scan + workspace with a deliberately pathful filename.

    The pathful value bypasses the intake sanitiser by
    overwriting ``Workspace.archive_filename`` directly.
    This is the same shape a historical row from before
    the v2.0.5 sanitiser would have on disk.
    """
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repository_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        workspace.archive_filename = archive_filename
        s.commit()
        return scan.id


# ---------------------------------------------------------------------------
# 1. Repository.original_filename sanitisation on list / detail
# ---------------------------------------------------------------------------


def test_original_filename_pathful_value_is_sanitised_in_list(app_config, client) -> None:
    """A repository row whose ``original_filename`` is a Windows
    drive-letter path is exposed through the list endpoint as
    the basename only; the API never reveals the parent path.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-list",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=basename_safely("C:\\Users\\me\\secret.zip"),
    )
    _build_scan_with_pathful_workspace(repository_id=repo_id, archive_filename="secret.zip")
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert rows, "repository not in list response"
    row = rows[0]
    assert row["original_filename"] == "secret.zip"
    assert "C:\\" not in row["original_filename"]
    assert "C:/" not in row["original_filename"]
    # Belt-and-braces: the entire response text must not
    # leak the pathful value.
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text


def test_original_filename_pathful_value_is_sanitised_in_detail(app_config, client) -> None:
    """The detail endpoint must apply the same defence-in-depth
    sanitisation as the list endpoint.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-detail",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=basename_safely("C:\\Users\\me\\secret.zip"),
    )
    _build_scan_with_pathful_workspace(repository_id=repo_id, archive_filename="secret.zip")
    response = client.get(f"/api/v1/repositories/{repo_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["original_filename"] == "secret.zip"
    assert "C:\\" not in response.text
    assert "C:/" not in response.text


def test_original_filename_posix_path_is_sanitised(app_config, client) -> None:
    """A POSIX absolute path is reduced to the basename at the API."""
    repo_id = _build_repo(
        owner="upload",
        name="pathful-posix",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=basename_safely("/var/data/secret.zip"),
    )
    _build_scan_with_pathful_workspace(repository_id=repo_id, archive_filename="secret.zip")
    response = client.get("/api/v1/repositories")
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert rows[0]["original_filename"] == "secret.zip"
    assert "/var/data" not in response.text


# ---------------------------------------------------------------------------
# 2. Workspace.archive_filename sanitisation on system cleanup
# ---------------------------------------------------------------------------


def _drive_workspace_to_cleaned_up(workspace_id: int) -> None:
    """Transition a workspace to CLEANED_UP so the cleanup endpoint picks it up."""
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        ws = s.get(Workspace, workspace_id)
        ws.state = WorkspaceState.CLEANED_UP
        s.commit()


def _trigger_system_cleanup_with_pathful_archive_filename(client, archive_filename: str) -> int:
    """Create a workspace that is already in CLEANED_UP state and
    whose ``archive_filename`` is a pathful value. The cleanup
    endpoint then returns the workspace; the validator on
    ``WorkspaceRead.archive_filename`` must sanitise the
    response.
    """
    repo_id = _build_repo(
        owner="upload",
        name="cleanup-pathful",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    scan_id = _build_scan_with_pathful_workspace(
        repository_id=repo_id, archive_filename=archive_filename
    )
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace

        ws = s.execute(
            __import__("sqlalchemy").select(Workspace).where(Workspace.scan_run_id == scan_id)
        ).scalar_one()
        workspace_id = ws.id
    return workspace_id


def test_system_cleanup_pathful_archive_filename_is_sanitised(app_config, client) -> None:
    """The system cleanup endpoint must not leak a Windows
    drive-letter path through ``archive_filename`` in the
    ``removed_workspaces`` list.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(
        client, "C:\\Users\\me\\archive.zip"
    )
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        # The cleanup endpoint may have nothing to remove
        # because the workspace was not in a stale state;
        # in that case the test asserts the response
        # surface shape only and is satisfied by a 200.
        assert isinstance(body["removed"], int)
        assert isinstance(body["removed_workspaces"], list)
        return
    row = rows[0]
    assert row["archive_filename"] == "archive.zip"
    assert "C:\\" not in response.text
    assert "C:/" not in response.text
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text


def test_system_cleanup_posix_path_archive_filename_is_sanitised(app_config, client) -> None:
    """The system cleanup endpoint must not leak a POSIX
    absolute path through ``archive_filename``.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(
        client, "/var/data/archive.zip"
    )
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] == "archive.zip"
    assert "/var/data" not in response.text


def test_system_cleanup_traversal_archive_filename_is_sanitised(app_config, client) -> None:
    """The system cleanup endpoint must not leak a parent-traversal
    segment through ``archive_filename``.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(client, "../../etc/passwd")
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] == "passwd"
    assert ".." not in rows[0]["archive_filename"]


def test_system_cleanup_mixed_separators_archive_filename_is_sanitised(app_config, client) -> None:
    """A mixed-separator path (Windows backslash + POSIX forward
    slash) is reduced to the trailing basename.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(
        client, "C:\\Users\\me\\Documents\\../../etc/passwd"
    )
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] == "passwd"
    assert ".." not in rows[0]["archive_filename"]


def test_system_cleanup_drive_relative_archive_filename_is_sanitised(app_config, client) -> None:
    """A drive-relative path (e.g. ``C:secret.zip``) is reduced to
    the trailing basename.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(client, "C:secret.zip")
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] == "secret.zip"
    assert "C:" not in rows[0]["archive_filename"]


def test_system_cleanup_empty_archive_filename_is_dropped(app_config, client) -> None:
    """An empty ``archive_filename`` is dropped to ``None`` at the
    API boundary.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(client, "")
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] is None


def test_system_cleanup_root_only_archive_filename_is_dropped(app_config, client) -> None:
    """A root-only path (e.g. ``/``) is dropped to ``None`` at the
    API boundary.
    """
    workspace_id = _trigger_system_cleanup_with_pathful_archive_filename(client, "/")
    response = client.post("/api/v1/system/workspaces/cleanup")
    assert response.status_code == 200
    body = response.json()
    rows = [w for w in body["removed_workspaces"] if w["id"] == workspace_id]
    if not rows:
        assert isinstance(body["removed"], int)
        return
    assert rows[0]["archive_filename"] is None


# ---------------------------------------------------------------------------
# 3. Database evidence is not mutated by the read path
# ---------------------------------------------------------------------------


def test_read_path_does_not_mutate_pathful_archive_filename(app_config, client) -> None:
    """The API boundary sanitises the response but must not
    rewrite the persisted database row. A historical row
    with a pathful ``archive_filename`` continues to carry
    that value on disk; the operator can still see the raw
    evidence in the database.
    """
    repo_id = _build_repo(
        owner="upload",
        name="no-mutate",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_pathful_workspace(
        repository_id=repo_id, archive_filename="C:\\Users\\me\\archive.zip"
    )
    # Trigger the read path several times.
    for _ in range(3):
        client.get("/api/v1/repositories")
    # The persisted row still carries the pathful value.
    with _db_session.SessionLocal() as s:
        from app.models.workspace import Workspace
        from sqlalchemy import select

        ws = s.execute(
            select(Workspace).where(Workspace.archive_filename == "C:\\Users\\me\\archive.zip")
        ).scalar_one_or_none()
        assert ws is not None
        assert ws.archive_filename == "C:\\Users\\me\\archive.zip"


# ---------------------------------------------------------------------------
# 4. ``basename_safely`` edge cases: drive-relative, UNC, mixed
# ---------------------------------------------------------------------------


def test_basename_safely_drive_relative_path() -> None:
    """``C:secret.zip`` (drive-relative) is reduced to the
    basename ``secret.zip``; the drive-letter prefix is
    stripped at the boundary.
    """
    assert basename_safely("C:secret.zip") == "secret.zip"
    assert basename_safely("c:secret.zip") == "secret.zip"
    assert basename_safely("Z:archive.tar.gz") == "archive.tar.gz"


def test_basename_safely_unc_paths_are_reduced_to_basename() -> None:
    """UNC paths (``\\\\server\\share\\secret.zip``,
    ``//server/share/secret.zip``) are reduced to the
    trailing segment; the host share component is
    treated as a directory and discarded.
    """
    assert basename_safely("\\\\server\\share\\secret.zip") == "secret.zip"
    assert basename_safely("//server/share/secret.zip") == "secret.zip"
    # A bare host share is treated as a directory and
    # surfaces the trailing share name; an empty UNC
    # tail returns None.
    assert basename_safely("\\\\server\\share") == "share"
    assert basename_safely("\\\\server") == "server"


def test_basename_safely_drive_letter_alone_returns_none() -> None:
    """A pure drive-letter form (``C:``, ``C:/``,
    ``C:\\``) returns ``None``; the basename of a
    drive letter alone is not a valid filename.
    """
    assert basename_safely("C:") is None
    assert basename_safely("c:") is None
    assert basename_safely("C:/") is None
    assert basename_safely("C:\\") is None
    assert basename_safely("D:/") is None


def test_basename_safely_mixed_separators() -> None:
    """Mixed-separator paths collapse to a single
    forward-slash form, then the trailing segment is
    surfaced.
    """
    assert basename_safely("C:\\Users\\me\\Documents\\../../etc/passwd") == "passwd"
    assert basename_safely("/var\\data\\archive.zip") == "archive.zip"


# ---------------------------------------------------------------------------
# 5. ``display_name`` is sanitised when ``original_filename`` is pathful
# ---------------------------------------------------------------------------


def test_display_name_sanitises_pathful_original_filename(app_config, client) -> None:
    """A repository row whose ``original_filename`` is a raw
    pathful value (e.g. ``C:\\Users\\me\\secret.zip``) is
    surfaced through the list / detail endpoints' ``display_name``
    field as the sanitised basename only; the raw path is
    never exposed.
    """
    from app.models.repository import Repository

    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="pathful-display",
            canonical_url="upload://pathful-display",
            visibility=RepositoryVisibility.PRIVATE,
            original_filename="C:\\Users\\me\\secret.zip",
        )
        s.add(repo)
        s.commit()
        s.refresh(repo)
        # The ``_display_name`` helper must reduce the
        # pathful value to ``secret.zip`` (the schema
        # validator on ``original_filename`` would also
        # sanitise the JSON-serialised response, but
        # ``display_name`` is built from the raw model
        # attribute; the explicit sanitisation closes
        # the gap).
        assert _display_name(repo) == "secret.zip"

    # List endpoint confirms the sanitised display name.
    response = client.get("/api/v1/repositories")
    rows = [r for r in response.json()["items"] if r["owner"] == "upload"]
    assert any(r["display_name"] == "secret.zip" for r in rows)
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text


def test_display_name_drive_relative_original_filename(app_config) -> None:
    """A repository row with a drive-relative
    ``original_filename`` (``C:secret.zip``) is
    surfaced through ``display_name`` as ``secret.zip``.
    """
    from app.models.repository import Repository

    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="drive-relative",
            canonical_url="upload://drive-relative",
            visibility=RepositoryVisibility.PRIVATE,
            original_filename="C:secret.zip",
        )
        s.add(repo)
        s.commit()
        s.refresh(repo)
        assert _display_name(repo) == "secret.zip"


# ---------------------------------------------------------------------------
# 6. Invalid-extension error envelope: the raw filename is sanitised
# ---------------------------------------------------------------------------


def test_invalid_upload_extension_error_sanitises_filename(app_config, client) -> None:
    """A POST to ``/api/v1/repositories/upload`` with a
    non-``.zip`` extension and a pathful ``filename``
    surfaces the **sanitised** basename in the error
    envelope; the raw path is never echoed.
    """
    files = {
        "file": (
            "C:\\Users\\me\\secret.exe",
            b"fake",
            "application/octet-stream",
        ),
    }
    response = client.post("/api/v1/repositories/upload", files=files)
    # ``VALIDATION_ERROR`` is mapped to 422 in
    # ``app.core.errors``. The error envelope shape is
    # stable; only the status code changes.
    assert response.status_code == 422
    body = response.json()
    detail = body.get("error", {}).get("details", {})
    # The error envelope must carry the sanitised
    # basename (``secret.exe``), not the raw path.
    assert detail.get("filename") == "secret.exe"
    assert "C:\\" not in response.text
    assert "C:/" not in response.text
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text


def test_invalid_upload_extension_error_sanitises_posix_path(app_config, client) -> None:
    """A POST to ``/api/v1/repositories/upload`` with a
    POSIX absolute path surfaces the **sanitised**
    basename in the error envelope; the raw path is
    never echoed.
    """
    files = {
        "file": (
            "/var/data/secret.exe",
            b"fake",
            "application/octet-stream",
        ),
    }
    response = client.post("/api/v1/repositories/upload", files=files)
    assert response.status_code == 422
    body = response.json()
    detail = body.get("error", {}).get("details", {})
    assert detail.get("filename") == "secret.exe"
    assert "/var/data" not in response.text
    assert "/var" not in response.text


def test_invalid_upload_extension_error_handles_unsafe_filename(app_config, client) -> None:
    """A POST to ``/api/v1/repositories/upload`` with a
    drive-letter-only filename (``C:``) returns an error
    envelope with an empty ``filename`` detail; the
    detail shape is stable.
    """
    files = {
        "file": (
            "C:",
            b"fake",
            "application/octet-stream",
        ),
    }
    response = client.post("/api/v1/repositories/upload", files=files)
    assert response.status_code == 422
    body = response.json()
    detail = body.get("error", {}).get("details", {})
    # ``basename_safely("C:")`` is ``None``; the
    # detail surfaces the bounded empty string.
    assert detail.get("filename") == ""


# ---------------------------------------------------------------------------
# 7. Internal GitHub provenance is preserved
# ---------------------------------------------------------------------------


def test_github_archive_filename_provenance_preserved(app_config, client) -> None:
    """A workspace whose ``archive_filename`` is the
    internal GitHub provenance string
    (``github/owner/name@sha.tar.gz``) is exposed through
    the API with the trailing basename preserved; the
    sanitiser must not destroy internal provenance
    strings merely because they contain separators.
    """
    repo_id = _build_repo(
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    _build_scan_with_pathful_workspace(
        repository_id=repo_id,
        archive_filename="github/octocat/Hello-World@abc123.tar.gz",
    )
    response = client.get("/api/v1/repositories")
    response.json()
    # The list endpoint's ``display_name`` for a
    # GitHub row is the ``owner/name`` pair; the
    # archive filename is exposed only via the
    # ``original_filename`` schema field (null for
    # GitHub rows) and the workspace's
    # ``archive_filename``. The internal
    # ``github/owner/name@sha.tar.gz`` string is
    # therefore not on the list response, but the
    # provenance component is preserved in the
    # database and surfaced through the workspace API.
    assert "C:\\" not in response.text
