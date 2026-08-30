"""Regression tests for the v2.0.6 historical repository labels.

The v2.0.5 repository list surfaces an opaque canonical upload
identifier (e.g. ``upload/2ed7b06ed7d3d967``) as the primary
row label when ``Repository.original_filename`` is null.
v2.0.5 already added the column for new uploads; v0.x-v2.0.4
historical uploaded rows have ``original_filename = NULL`` and
still need a human-readable label.

v2.0.6 derives a per-repository historical archive filename
from the existing ``Workspace.archive_filename`` rows (the
rows already persist the original archive filename, basename-
only, sanitised at intake). The derivation is read-only: no
historical row is mutated; ``Repository.original_filename`` is
not backfilled; the helper is consumed at the API boundary.

Precedence in ``_display_name`` (v2.0.6):

1. GitHub rows: ``owner/name``.
2. Uploaded rows with ``original_filename``: the persisted
   filename.
3. Uploaded rows with a single non-null
   ``Workspace.archive_filename``: that historical filename.
4. Uploaded rows with a conflict (multiple distinct
   non-null filenames) or no filenames at all: the bounded
   opaque fallback ``Uploaded archive · upload/<short-key>``.

The helper issues a single batched query
(``get_repository_historical_filenames``); the list endpoint
does not produce an N+1 request pattern.

The search endpoint also matches a repository whose
``Workspace.archive_filename`` rows contain the term. A
search for ``test-09-mixed-monorepo`` (or any substring)
returns repository 13 even though
``Repository.original_filename`` is null.
"""

from __future__ import annotations

from app.api.repositories import _canonical_identity, _display_name
from app.db import session as _db_session
from app.main import app
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState
from app.repositories import repository_repo
from app.services import scan_service
from app.services.workspace_service import WorkspaceService
from app.utils.paths import basename_safely

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_repo(
    *,
    owner: str,
    name: str,
    source_type: RepositorySourceType,
    original_filename: str | None = None,
) -> int:
    """Create one repository row directly via the ORM and return its id."""
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
            default_branch=None,
            visibility=RepositoryVisibility.PUBLIC
            if source_type == RepositorySourceType.GITHUB
            else RepositoryVisibility.PRIVATE,
            original_filename=original_filename,
        )
        s.add(repo)
        s.commit()
        return repo.id


def _build_scan_with_workspace(
    *,
    repository_id: int,
    status: ScanStatus = ScanStatus.COMPLETED,
    archive_filename: str | None = None,
) -> int:
    """Create one scan + workspace row, with optional archive_filename."""
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repository_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = status
        workspace = WorkspaceService(s).create_for_scan(
            scan,
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            archive_filename=archive_filename or "upload.zip",
        )
        if archive_filename is not None:
            # Overwrite the value the workspace service
            # derived from the supplied name. This lets us
            # simulate a workspace with a null filename.
            workspace.archive_filename = archive_filename
        workspace.state = WorkspaceState.READY
        s.commit()
        return scan.id


# ---------------------------------------------------------------------------
# 1. Repository.original_filename takes precedence
# ---------------------------------------------------------------------------


def test_original_filename_takes_precedence_over_historical(app_config, workspace_root) -> None:
    """``original_filename`` (when set) wins over historical archive filenames."""
    repo_id = _build_repo(
        owner="upload",
        name="precedence-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="primary-name.zip",
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="historical-name.zip",
    )
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        # The historical helper still resolves the
        # historical filename; the precedence is in
        # ``_display_name``.
        assert hist.historical_archive_filename == "historical-name.zip"
        assert hist.historical_filename_conflict is False
        assert _display_name(repo) == "primary-name.zip"


# ---------------------------------------------------------------------------
# 2. Historical archive filename is used when original_filename is null
# ---------------------------------------------------------------------------


def test_historical_filename_used_when_original_filename_is_null(
    app_config, workspace_root
) -> None:
    """An uploaded row with null ``original_filename`` uses the historical archive filename."""
    repo_id = _build_repo(
        owner="upload",
        name="historical-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "test-09-mixed-monorepo.zip"
        assert hist.historical_filename_conflict is False
        assert (
            _display_name(repo, historical_archive_filename=hist.historical_archive_filename)
            == "test-09-mixed-monorepo.zip"
        )


# ---------------------------------------------------------------------------
# 3. Multiple agreeing historical filenames produce one display name
# ---------------------------------------------------------------------------


def test_multiple_agreeing_workspaces_produce_one_filename(app_config, workspace_root) -> None:
    """Two workspaces with the same archive filename resolve to one name."""
    repo_id = _build_repo(
        owner="upload",
        name="agreeing-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="agreeing.zip")
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="agreeing.zip")
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "agreeing.zip"
        assert hist.historical_filename_conflict is False
        assert hist.historical_archive_filename_count == 1


# ---------------------------------------------------------------------------
# 4. Conflicting historical filenames use the bounded fallback
# ---------------------------------------------------------------------------


def test_conflicting_workspaces_use_fallback(app_config, workspace_root) -> None:
    """Two workspaces with different filenames flag a conflict."""
    repo_id = _build_repo(
        owner="upload",
        name="conflict-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="one.zip")
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="two.zip")
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename is None
        assert hist.historical_filename_conflict is True
        assert hist.historical_archive_filename_count == 2
        # Display name falls back to the bounded opaque label.
        assert _display_name(
            repo, historical_archive_filename=hist.historical_archive_filename
        ).startswith("Uploaded archive")


# ---------------------------------------------------------------------------
# 5. Null workspace filenames use the bounded fallback
# ---------------------------------------------------------------------------


def test_null_workspace_filenames_use_fallback(app_config, workspace_root) -> None:
    """A workspace with null ``archive_filename`` does not pollute the historical result."""
    repo_id = _build_repo(
        owner="upload",
        name="null-filename-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    # Create a workspace through the service (so the
    # workspace_key is generated correctly), then set the
    # archive_filename to null.
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        workspace.archive_filename = None
        s.commit()
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename is None
        assert hist.historical_filename_conflict is False
        assert hist.historical_archive_filename_count == 0


# ---------------------------------------------------------------------------
# 6. Path safety at the basename_safely boundary
# ---------------------------------------------------------------------------


def test_basename_safely_strips_absolute_paths() -> None:
    """The intake boundary uses ``basename_safely`` to strip absolute paths.

    The historical helper operates on values that the
    database already trusts; it does not re-sanitise. The
    intake boundary is covered here.
    """
    assert basename_safely("C:\\Users\\me\\secret.zip") == "secret.zip"
    assert basename_safely("C:/Users/me/secret.zip") == "secret.zip"
    assert basename_safely("/etc/passwd") == "passwd"


# ---------------------------------------------------------------------------
# 6b. Read-time sanitization defends against historical
#     rows that pre-date the v2.0.5 intake sanitiser.
# ---------------------------------------------------------------------------


def test_historical_pathful_archive_filename_is_sanitised(app_config, workspace_root) -> None:
    """A historical ``Workspace.archive_filename`` value that
    contains a Windows drive-letter path must be reduced to a
    basename by the read-time sanitisation; the API must
    never expose ``C:\\Users\\me\\secret.zip`` even when the
    row was inserted by an operator with a tool that bypassed
    the intake sanitiser.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    # Insert a workspace whose ``archive_filename`` is a raw
    # Windows drive-letter path. This simulates a
    # historical row from before the v2.0.5 sanitiser was
    # added; the read-time sanitiser must catch it. We use
    # the ``WorkspaceService`` to create the row so the
    # ``workspace_key`` invariant is honoured, then
    # overwrite the sanitised ``archive_filename`` with a
    # deliberately-pathful value to bypass the
    # intake-layer sanitiser.
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        workspace.archive_filename = "C:\\Users\\me\\secret.zip"
        s.commit()

    # The historical helper must surface the basename only.
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "secret.zip"
        assert "C:\\Users" not in (hist.historical_archive_filename or "")
        assert hist.historical_filename_conflict is False

    # The list API must not surface the original pathful value.
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert rows, "repository not in list response"
    row = rows[0]
    assert row["display_name"] == "secret.zip"
    assert "C:\\" not in row["display_name"]
    assert "C:/" not in row["display_name"]
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text

    # The detail API must not surface the original pathful
    # value either.
    detail = client.get(f"/api/v1/repositories/{repo_id}")
    assert detail.status_code == 200
    assert "C:\\" not in detail.text
    assert "C:/" not in detail.text


def test_historical_posix_path_archive_filename_is_sanitised(app_config, workspace_root) -> None:
    """A historical ``Workspace.archive_filename`` value that
    contains a POSIX absolute path is reduced to a basename
    at the read boundary; the API never exposes
    ``/var/data/secret.zip`` even when the row was inserted
    by an operator with a tool that bypassed the intake
    sanitiser.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-2",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        workspace.archive_filename = "/var/data/secret.zip"
        s.commit()
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "secret.zip"
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert rows[0]["display_name"] == "secret.zip"
    assert "/var/data" not in response.text


def test_historical_traversal_archive_filename_is_sanitised(app_config, workspace_root) -> None:
    """A historical ``Workspace.archive_filename`` value that
    contains a parent-traversal segment is reduced to the
    trailing basename at the read boundary.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-3",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        workspace.archive_filename = "../../../etc/passwd"
        s.commit()
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "passwd"
        assert ".." not in (hist.historical_archive_filename or "")


def test_historical_empty_archive_filename_is_dropped(app_config, workspace_root) -> None:
    """A historical ``Workspace.archive_filename`` value that
    sanitises to ``None`` (root, dot-only, empty) is dropped
    from the historical helper result; the API falls back to
    the bounded opaque label.
    """
    repo_id = _build_repo(
        owner="upload",
        name="pathful-4",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        # An empty value. ``basename_safely("")`` is ``None``;
        # the historical helper must drop the value.
        workspace.archive_filename = ""
        s.commit()
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename is None
        assert hist.historical_filename_conflict is False
        assert hist.historical_archive_filename_count == 0
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert rows[0]["display_name"].startswith("Uploaded archive · upload/")
    assert basename_safely("/var/data/archive.zip") == "archive.zip"
    assert basename_safely("../../etc/passwd") == "passwd"


# ---------------------------------------------------------------------------
# 7. Unicode filename remains safe
# ---------------------------------------------------------------------------


def test_unicode_filename_is_preserved(app_config, workspace_root) -> None:
    """Unicode filenames round-trip through the helper unchanged."""
    repo_id = _build_repo(
        owner="upload",
        name="unicode-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="café.zip")
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename == "café.zip"


# ---------------------------------------------------------------------------
# 8. Local absolute path never appears in API output
# ---------------------------------------------------------------------------


def test_local_path_never_in_api_output(app_config, workspace_root) -> None:
    """A repository created with a basename-only ``original_filename`` never
    exposes an absolute path through the list endpoint.
    """
    repo_id = _build_repo(
        owner="upload",
        name="path-safety-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=basename_safely("C:\\Users\\me\\secret.zip"),
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="secret.zip")
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    rows = [r for r in body["items"] if r["id"] == repo_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["display_name"] == "secret.zip"
    assert "C:\\" not in row["display_name"]
    assert "C:/" not in row["display_name"]
    assert "C:\\" not in row["canonical_identity"]
    assert "C:/" not in row["canonical_identity"]
    assert "C:\\Users" not in response.text
    assert "C:/Users" not in response.text


# ---------------------------------------------------------------------------
# 9. Repository list returns the historical display name
# ---------------------------------------------------------------------------


def test_list_returns_historical_display_name(app_config, workspace_root) -> None:
    """The list endpoint returns the historical archive filename as the display name."""
    repo_id = _build_repo(
        owner="upload",
        name="historical-list-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    row = next(r for r in body["items"] if r["id"] == repo_id)
    assert row["display_name"] == "test-09-mixed-monorepo.zip"
    assert row["canonical_identity"].startswith("upload/")


# ---------------------------------------------------------------------------
# 10. Search by historical filename
# ---------------------------------------------------------------------------


def test_search_by_historical_filename(app_config, workspace_root) -> None:
    """A search for the historical filename returns the repository."""
    repo_id = _build_repo(
        owner="upload",
        name="historical-search-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    client = api_client(app)
    response = client.get(
        "/api/v1/repositories",
        params={"search": "test-09-mixed-monorepo"},
    )
    assert response.status_code == 200
    body = response.json()
    ids = [r["id"] for r in body["items"]]
    assert repo_id in ids


# ---------------------------------------------------------------------------
# 11. Search by partial historical filename
# ---------------------------------------------------------------------------


def test_search_by_partial_historical_filename(app_config, workspace_root) -> None:
    """A partial-substring search on the historical filename returns the repository."""
    repo_id = _build_repo(
        owner="upload",
        name="historical-search-2",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    client = api_client(app)
    response = client.get(
        "/api/v1/repositories",
        params={"search": "mixed-monorepo"},
    )
    assert response.status_code == 200
    body = response.json()
    ids = [r["id"] for r in body["items"]]
    assert repo_id in ids


# ---------------------------------------------------------------------------
# 12. Historical filename search returns no duplicate repository rows
# ---------------------------------------------------------------------------


def test_historical_filename_search_returns_no_duplicates(app_config, workspace_root) -> None:
    """A repository with two workspaces that share the same historical filename
    appears in the search result exactly once.
    """
    repo_id = _build_repo(
        owner="upload",
        name="historical-search-3",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="duplicate-test.zip")
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="duplicate-test.zip")
    client = api_client(app)
    response = client.get(
        "/api/v1/repositories",
        params={"search": "duplicate-test"},
    )
    assert response.status_code == 200
    body = response.json()
    matching = [r for r in body["items"] if r["id"] == repo_id]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# 13. Search by scan ID still works
# ---------------------------------------------------------------------------


def test_scan_id_search_still_works(app_config, workspace_root) -> None:
    """A pure-digit search returns the parent repository of the scan."""
    repo_id = _build_repo(
        owner="upload",
        name="scan-search-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    scan_id = _build_scan_with_workspace(repository_id=repo_id, archive_filename="x.zip")
    client = api_client(app)
    # Pure-integer token
    response = client.get("/api/v1/repositories", params={"search": str(scan_id)})
    assert response.status_code == 200
    body = response.json()
    assert any(r["id"] == repo_id for r in body["items"])
    # Hash-prefixed token
    response = client.get("/api/v1/repositories", params={"search": f"#{scan_id}"})
    assert response.status_code == 200
    body = response.json()
    assert any(r["id"] == repo_id for r in body["items"])


# ---------------------------------------------------------------------------
# 14. GitHub labels remain unchanged
# ---------------------------------------------------------------------------


def test_github_labels_unchanged(app_config, workspace_root) -> None:
    """GitHub rows still render as ``owner/name`` regardless of historical helper."""
    repo_id = _build_repo(
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    # GitHub workspaces carry the tarball name in archive_filename;
    # the display name still prefers ``owner/name``.
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="github/octocat/Hello-World@abc.tar.gz",
    )
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        # The historical helper returns whatever the
        # workspace carries; for GitHub, the
        # ``_display_name`` helper still prefers
        # ``owner/name``.
        assert (
            _display_name(repo, historical_archive_filename=hist.historical_archive_filename)
            == "octocat/Hello-World"
        )
        assert _canonical_identity(repo) == "https://github.com/octocat/Hello-World"


# ---------------------------------------------------------------------------
# 15. New-upload original_filename behaviour remains unchanged
# ---------------------------------------------------------------------------


def test_new_upload_filename_takes_precedence(app_config, workspace_root) -> None:
    """A new upload with ``original_filename`` continues to use that value."""
    repo_id = _build_repo(
        owner="upload",
        name="new-upload-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="fresh-upload.zip",
    )
    _build_scan_with_workspace(repository_id=repo_id, archive_filename="fresh-upload.zip")
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    row = next(r for r in body["items"] if r["id"] == repo_id)
    assert row["display_name"] == "fresh-upload.zip"


# ---------------------------------------------------------------------------
# 16. Query count remains bounded
# ---------------------------------------------------------------------------


def test_query_count_is_bounded(app_config, workspace_root) -> None:
    """The list endpoint does not produce an N+1 historical-filename query.

    The endpoint issues:

    1. One paginated list query.
    2. One batched summary aggregate.
    3. One batched historical-filename aggregate.

    Total statements are bounded by a small constant. With
    N repositories and 5 < N, the count must remain below
    a generous threshold that proves no per-row N+1 lookup.
    """
    # Create 5 uploaded repos with workspaces.
    for i in range(5):
        repo_id = _build_repo(
            owner="upload",
            name=f"perf-{i}",
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            original_filename=f"archive-{i}.zip",
        )
        _build_scan_with_workspace(repository_id=repo_id, archive_filename=f"archive-{i}.zip")

    # Instrument the global engine (the same one the
    # endpoint uses after ``app_config`` rebinds it).
    from sqlalchemy import event

    engine = _db_session.engine
    counter = {"n": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        client = api_client(app)
        response = client.get("/api/v1/repositories")
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
    assert response.status_code == 200
    # Bound is generous: list + summary + historical +
    # any incidental queries (eg session bootstrap).
    # The point is to fail loudly if a per-row pattern
    # sneaks in (which would push the count into the
    # tens).
    assert counter["n"] < 20, f"expected bounded query count, got {counter['n']}"


# ---------------------------------------------------------------------------
# 17. Repository #13 field-test fixture (the repro)
# ---------------------------------------------------------------------------


def test_field_test_repo_13_resolves_to_test_09(app_config, workspace_root) -> None:
    """The exact field-test repro: repository with two scans that both carry
    ``archive_filename='test-09-mixed-monorepo.zip'`` resolves to
    ``test-09-mixed-monorepo.zip`` as the display name.
    """
    repo_id = _build_repo(
        owner="upload",
        name="7e12fbd201665dd4",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    # Two scans of the same archive, the rescan pattern.
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    _build_scan_with_workspace(
        repository_id=repo_id,
        archive_filename="test-09-mixed-monorepo.zip",
    )
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        # Both workspaces carry the same filename; the
        # helper collapses to one filename with no
        # conflict flag.
        assert hist.historical_archive_filename == "test-09-mixed-monorepo.zip"
        assert hist.historical_filename_conflict is False
        assert hist.historical_archive_filename_count == 1
        # Display name uses the historical filename.
        assert (
            _display_name(repo, historical_archive_filename=hist.historical_archive_filename)
            == "test-09-mixed-monorepo.zip"
        )


# ---------------------------------------------------------------------------
# 18. No scan or workspace evidence is mutated
# ---------------------------------------------------------------------------


def test_no_evidence_mutation(app_config, workspace_root) -> None:
    """The historical helper is read-only; it does not mutate any persisted row."""
    repo_id = _build_repo(
        owner="upload",
        name="no-mutate-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    scan_id = _build_scan_with_workspace(repository_id=repo_id, archive_filename="stable.zip")
    with _db_session.SessionLocal() as s:
        before_filename = s.get(Workspace, 1)
        # Find the actual workspace
        from sqlalchemy import select

        ws = s.execute(select(Workspace).where(Workspace.scan_run_id == scan_id)).scalar_one()
        before_filename = ws.archive_filename
        before_scan_id = scan_id
        # Trigger the read path several times.
        for _ in range(3):
            repository_repo.get_repository_historical_filenames(s, [repo_id])
        s.expire_all()
        ws_after = s.get(Workspace, ws.id)
        repo_after = s.get(Repository, repo_id)
        scan_after = s.get(ScanRun, scan_id)
        assert ws_after.archive_filename == before_filename
        assert scan_after.id == before_scan_id
        assert repo_after.original_filename is None  # was None; helper does not write


# ---------------------------------------------------------------------------
# 19. Two upload repos with same historical filename are distinct
# ---------------------------------------------------------------------------


def test_multiple_upload_repos_with_same_filename_are_distinct(app_config, workspace_root) -> None:
    """Two repositories that share a historical filename remain distinct rows."""
    repo_a_id = _build_repo(
        owner="upload",
        name="shared-a",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    repo_b_id = _build_repo(
        owner="upload",
        name="shared-b",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    _build_scan_with_workspace(repository_id=repo_a_id, archive_filename="shared.zip")
    _build_scan_with_workspace(repository_id=repo_b_id, archive_filename="shared.zip")
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_a_id, repo_b_id])
        assert result[repo_a_id].historical_archive_filename == "shared.zip"
        assert result[repo_b_id].historical_archive_filename == "shared.zip"
        assert result[repo_a_id].historical_filename_conflict is False
        assert result[repo_b_id].historical_filename_conflict is False


# ---------------------------------------------------------------------------
# 20. Empty repository_ids returns empty result
# ---------------------------------------------------------------------------


def test_empty_repository_ids_returns_empty() -> None:
    """An empty ``repository_ids`` argument returns an empty result without a query."""
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [])
        assert result == {}


# ---------------------------------------------------------------------------
# 21. Repository with no workspaces returns zero-summary
# ---------------------------------------------------------------------------


def test_repo_with_no_workspaces_returns_zero_summary(app_config, workspace_root) -> None:
    """A repository that has no scans and no workspaces returns a zero row."""
    repo_id = _build_repo(
        owner="never-scanned",
        name="never-scanned",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
    )
    with _db_session.SessionLocal() as s:
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert hist.historical_archive_filename is None
        assert hist.historical_filename_conflict is False
        assert hist.historical_archive_filename_count == 0


# ---------------------------------------------------------------------------
# 22. List endpoint zero row case is rendered with bounded fallback
# ---------------------------------------------------------------------------


def test_list_renders_fallback_for_historical_zero_row(app_config, workspace_root) -> None:
    """A row with no historical filename and no original_filename renders the fallback."""
    repo_id = _build_repo(
        owner="upload",
        name="fallback-1",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename=None,
    )
    with _db_session.SessionLocal() as s:
        repo = s.get(Repository, repo_id)
        result = repository_repo.get_repository_historical_filenames(s, [repo_id])
        hist = result[repo_id]
        assert _display_name(
            repo, historical_archive_filename=hist.historical_archive_filename
        ).startswith("Uploaded archive")
