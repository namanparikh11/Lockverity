"""Regression tests for the v2.0.5 repository identification and list summary.

The v2.0.4 repository list surfaces an opaque canonical upload
identifier (e.g. ``upload/2ed7b06ed7d3d967``) as the primary
row label and provides no scan count, no latest-scan summary,
and no per-row "Open latest scan" / "Compare" action. The
v2.0.5 field-test repro required operators to open two clicks
to identify each row when several uploaded archives were
present, and to manually count scans to know whether a
comparison was available.

v2.0.5 adds:

1. A nullable ``original_filename`` column on ``repositories``
   (basename only, sanitised at intake).
2. A bounded human-readable label: ``owner/repository`` for
   GitHub rows; ``original_filename`` for uploaded rows (or
   the bounded fallback when ``original_filename`` is null);
   ``upload/<short-key>`` as the secondary technical
   identifier.
3. A per-row summary (``scan_count``,
   ``eligible_comparison_scan_count``, ``latest_scan``) returned
   by ``GET /api/v1/repositories`` without an N+1 query.
4. A bounded search predicate that matches the original
   filename, ``owner``, ``name``, canonical URL, and exact
   repository / scan IDs.
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
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.models.workspace import WorkspaceKind, WorkspaceState
from app.repositories import repository_repo
from app.services import scan_service
from app.services.workspace_service import WorkspaceService
from app.utils.paths import basename_safely

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# basename_safely unit tests
# ---------------------------------------------------------------------------


def test_basename_safely_returns_plain_basename() -> None:
    """A simple filename passes through unchanged."""
    assert basename_safely("package.zip") == "package.zip"


def test_basename_safely_strips_absolute_windows_path() -> None:
    """A client-supplied absolute path returns only the basename.

    This is the v2.0.5 path-leak defence: a malicious or
    careless client that submits ``C:\\Users\\me\\secret.zip``
    sees ``secret.zip`` stored, never the absolute path.
    """
    assert basename_safely("C:\\Users\\me\\secret.zip") == "secret.zip"
    assert basename_safely("C:/Users/me/secret.zip") == "secret.zip"


def test_basename_safely_strips_unix_absolute_path() -> None:
    """A client-supplied /etc/passwd-style path returns only the basename."""
    assert basename_safely("/etc/passwd") == "passwd"


def test_basename_safely_strips_parent_traversal() -> None:
    """Parent traversal segments are stripped to the last segment."""
    assert basename_safely("../../etc/passwd") == "passwd"


def test_basename_safely_rejects_root() -> None:
    """A bare root path returns ``None``."""
    assert basename_safely("/") is None
    assert basename_safely("C:\\") is None


def test_basename_safely_rejects_empty() -> None:
    """An empty or whitespace-only path returns ``None``."""
    assert basename_safely("") is None
    assert basename_safely("   ") is None
    assert basename_safely(None) is None


def test_basename_safely_preserves_unicode() -> None:
    """Unicode filenames are NFC-normalised and preserved."""
    # é can be encoded as one codepoint (U+00E9) or two
    # (U+0065 U+0301). NFC collapses them.
    decomposed = "café.zip".replace("é", "e\u0301")
    assert basename_safely(decomposed) == "café.zip"


def test_basename_safely_truncates_very_long_names() -> None:
    """A name longer than the column size is truncated gracefully."""
    long = "a" * 1000 + ".zip"
    out = basename_safely(long)
    assert out is not None
    assert len(out) <= 512
    # The extension is preserved so the truncated label is
    # still recognisable.
    assert out.endswith(".zip")


def test_basename_safely_rejects_just_dots() -> None:
    """A path that is only a dot-segment returns ``None``."""
    assert basename_safely("..") is None
    assert basename_safely(".") is None
    assert basename_safely("../") is None


# ---------------------------------------------------------------------------
# Display name and canonical identity unit tests
# ---------------------------------------------------------------------------


def test_display_name_github_uses_owner_name() -> None:
    """GitHub rows render as ``owner/name``."""
    repo = Repository(
        id=1,
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="octocat",
        name="Hello-World",
        canonical_url="https://github.com/octocat/Hello-World",
        default_branch="master",
        visibility=RepositoryVisibility.PUBLIC,
        archived=False,
    )
    assert _display_name(repo) == "octocat/Hello-World"


def test_display_name_uploaded_uses_original_filename() -> None:
    """Uploaded rows render as the original filename when known."""
    repo = Repository(
        id=1,
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        provider=RepositoryProvider.LOCAL_UPLOAD,
        owner="upload",
        name="abc12345",
        canonical_url="upload://abc12345",
        default_branch=None,
        visibility=RepositoryVisibility.PRIVATE,
        archived=False,
        original_filename="my-test-archive.zip",
    )
    assert _display_name(repo) == "my-test-archive.zip"


def test_display_name_uploaded_no_filename_uses_fallback() -> None:
    """Uploaded rows without ``original_filename`` use the bounded fallback."""
    repo = Repository(
        id=1,
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        provider=RepositoryProvider.LOCAL_UPLOAD,
        owner="upload",
        name="def67890",
        canonical_url="upload://def67890",
        default_branch=None,
        visibility=RepositoryVisibility.PRIVATE,
        archived=False,
        original_filename=None,
    )
    assert _display_name(repo) == "Uploaded archive · upload/def67890"


def test_canonical_identity_github_is_url() -> None:
    """GitHub canonical identity is the canonical URL."""
    repo = Repository(
        id=1,
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="octocat",
        name="Hello-World",
        canonical_url="https://github.com/octocat/Hello-World",
        default_branch="master",
        visibility=RepositoryVisibility.PUBLIC,
        archived=False,
    )
    assert _canonical_identity(repo) == "https://github.com/octocat/Hello-World"


def test_canonical_identity_uploaded_is_short_key() -> None:
    """Uploaded canonical identity is ``upload/<short-key>``."""
    repo = Repository(
        id=1,
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        provider=RepositoryProvider.LOCAL_UPLOAD,
        owner="upload",
        name="abc12345",
        canonical_url="upload://abc12345",
        default_branch=None,
        visibility=RepositoryVisibility.PRIVATE,
        archived=False,
    )
    assert _canonical_identity(repo) == "upload/abc12345"


# ---------------------------------------------------------------------------
# API list + summary tests
# ---------------------------------------------------------------------------


def _build_repo(
    app_config, *, owner: str, name: str, source_type, original_filename: str | None = None
) -> int:
    """Create one repository row directly via the ORM."""
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


def _build_scan(app_config, workspace_root, *, repository_id: int, status: ScanStatus) -> int:
    """Create one repository, one scan, and a workspace, in the right state."""
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s, repository_id=repository_id, trigger_type=ScanTriggerType.MANUAL
        )
        scan.status = status
        workspace = WorkspaceService(s).create_for_scan(scan, kind=WorkspaceKind.UPLOADED_ARCHIVE)
        paths = WorkspaceService(s).paths_for(workspace.workspace_key)
        paths.contents_dir.mkdir(parents=True, exist_ok=True)
        (paths.contents_dir / "package.json").write_text(
            '{"name":"x","version":"0.0.0"}', encoding="utf-8"
        )
        WorkspaceService(s).transition(workspace, target=WorkspaceState.VALIDATING)
        WorkspaceService(s).transition(
            workspace,
            target=WorkspaceState.READY,
            archive_sha256="a" * 64,
            archive_size=42,
            file_count=1,
            uncompressed_size=42,
        )
        s.commit()
        return scan.id


def test_list_repositories_returns_scan_count(app_config, workspace_root) -> None:
    """The list response includes the scan count for each row."""
    repo_id = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED)
    _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.PARTIAL)
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    body = response.json()
    target = next(item for item in body["items"] if item["id"] == repo_id)
    assert target["summary"]["scan_count"] == 2
    assert target["summary"]["eligible_comparison_scan_count"] == 2
    assert target["summary"]["latest_scan"] is not None
    assert target["summary"]["latest_scan"]["status"] in ("completed", "partial")


def test_list_repositories_latest_scan_uses_largest_id(app_config, workspace_root) -> None:
    """The latest scan is the one with the largest ``id``.

    SQLite monotonicity makes the ``MAX(id)`` aggregate
    deterministic even when two scans share the same
    ``created_at`` timestamp.
    """
    repo_id = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    first = _build_scan(
        app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED
    )
    second = _build_scan(
        app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    target = next(item for item in response.json()["items"] if item["id"] == repo_id)
    assert target["summary"]["latest_scan"]["id"] == second
    assert target["summary"]["latest_scan"]["id"] != first


def test_list_repositories_no_scans_returns_zero_summary(app_config, workspace_root) -> None:
    """A repository with no scans renders a zero-summary with ``None`` latest."""
    repo_id = _build_repo(
        app_config,
        owner="upload",
        name="never-scanned",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="never-scanned.zip",
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    target = next(item for item in response.json()["items"] if item["id"] == repo_id)
    assert target["summary"]["scan_count"] == 0
    assert target["summary"]["eligible_comparison_scan_count"] == 0
    assert target["summary"]["latest_scan"] is None


def test_list_repositories_search_by_filename(app_config, workspace_root) -> None:
    """The search predicate matches the original filename."""
    target_id = _build_repo(
        app_config,
        owner="upload",
        name="abcdef",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="test-09-mixed-monorepo.zip",
    )
    _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories?search=test-09-mixed-monorepo")
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert target_id in ids


def test_list_repositories_search_by_github_owner_name(app_config, workspace_root) -> None:
    """The search predicate matches ``owner`` and ``name``."""
    target_id = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    _build_repo(
        app_config,
        owner="upload",
        name="abc",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="x.zip",
    )
    client = api_client(app)
    # Search by the unique ``name`` segment; the ``ilike``
    # predicate matches both ``Hello-World`` and the GitHub
    # canonical URL fragment.
    response = client.get("/api/v1/repositories?search=Hello-World")
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert target_id in ids


def test_list_repositories_search_by_canonical_upload_key(app_config, workspace_root) -> None:
    """The search predicate matches the canonical upload identifier."""
    target_id = _build_repo(
        app_config,
        owner="upload",
        name="zzz123",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="x.zip",
    )
    _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories?search=zzz123")
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert target_id in ids


def test_list_repositories_search_by_scan_id(app_config, workspace_root) -> None:
    """A bare scan ID resolves to the parent repository."""
    target_repo = _build_repo(
        app_config,
        owner="upload",
        name="abc",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="x.zip",
    )
    other_repo = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    target_scan = _build_scan(
        app_config, workspace_root, repository_id=target_repo, status=ScanStatus.COMPLETED
    )
    client = api_client(app)
    response = client.get(f"/api/v1/repositories?search={target_scan}")
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert target_repo in ids
    assert other_repo not in ids


def test_list_repositories_search_by_hash_scan_id(app_config, workspace_root) -> None:
    """``#15`` resolves to scan id 15."""
    target_repo = _build_repo(
        app_config,
        owner="upload",
        name="hash-test",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="x.zip",
    )
    other_repo = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    target_scan = _build_scan(
        app_config, workspace_root, repository_id=target_repo, status=ScanStatus.COMPLETED
    )
    client = api_client(app)
    response = client.get(f"/api/v1/repositories?search=%23{target_scan}")
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert target_repo in ids
    assert other_repo not in ids


def test_list_repositories_search_does_not_duplicate_when_multiple_scans_match(
    app_config, workspace_root
) -> None:
    """A scan-ID search must not return duplicate rows.

    Even if a single repository has many scans, the search
    must collapse to one row per repository, never one row
    per matching scan.
    """
    repo_id = _build_repo(
        app_config,
        owner="upload",
        name="dup-test",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="x.zip",
    )
    scan_id = _build_scan(
        app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED
    )
    _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED)
    _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED)
    client = api_client(app)
    response = client.get(f"/api/v1/repositories?search={scan_id}")
    body = response.json()
    matches = [item for item in body["items"] if item["id"] == repo_id]
    assert len(matches) == 1


def test_list_repositories_pagination_remains_correct(app_config, workspace_root) -> None:
    """Pagination remains deterministic with the new fields."""
    for i in range(5):
        _build_repo(
            app_config,
            owner="upload",
            name=f"page-{i:02d}",
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            original_filename=f"page-{i:02d}.zip",
        )
    client = api_client(app)
    page1 = client.get("/api/v1/repositories?page=1&page_size=2")
    page2 = client.get("/api/v1/repositories?page=2&page_size=2")
    page3 = client.get("/api/v1/repositories?page=3&page_size=2")
    assert page1.json()["pagination"]["total"] >= 5
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 2
    assert len(page3.json()["items"]) >= 1


def test_list_repositories_provider_isolation_remains_correct(app_config, workspace_root) -> None:
    """The list endpoint does not leak cross-repository scan data."""
    a_id = _build_repo(
        app_config,
        owner="octocat",
        name="Hello-World",
        source_type=RepositorySourceType.GITHUB,
    )
    b_id = _build_repo(
        app_config,
        owner="upload",
        name="other",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="other.zip",
    )
    a_scan = _build_scan(
        app_config, workspace_root, repository_id=a_id, status=ScanStatus.COMPLETED
    )
    b_scan = _build_scan(
        app_config, workspace_root, repository_id=b_id, status=ScanStatus.COMPLETED
    )
    client = api_client(app)
    response = client.get("/api/v1/repositories")
    body = response.json()
    a_row = next(item for item in body["items"] if item["id"] == a_id)
    b_row = next(item for item in body["items"] if item["id"] == b_id)
    assert a_row["summary"]["latest_scan"]["id"] == a_scan
    assert b_row["summary"]["latest_scan"]["id"] == b_scan
    assert a_row["summary"]["latest_scan"]["id"] != b_row["summary"]["latest_scan"]["id"]


# ---------------------------------------------------------------------------
# RepositoryRepo search predicate unit tests
# ---------------------------------------------------------------------------


def test_parse_scan_id_token_pure_digits() -> None:
    """A bare integer string parses to the integer."""
    from app.repositories.repository_repo import _parse_scan_id_token

    assert _parse_scan_id_token("15") == 15
    assert _parse_scan_id_token("#15") == 15
    assert _parse_scan_id_token("  42  ") == 42


def test_parse_scan_id_token_rejects_mixed_text() -> None:
    """Mixed text is treated as free-text, never as a scan ID."""
    from app.repositories.repository_repo import _parse_scan_id_token

    assert _parse_scan_id_token("15abc") is None
    assert _parse_scan_id_token("Hello-World") is None
    assert _parse_scan_id_token("test-09-mixed-monorepo") is None
    assert _parse_scan_id_token("") is None
    assert _parse_scan_id_token(None) is None


def test_repository_search_summary_fills_zero_for_no_scans(app_config, workspace_root) -> None:
    """A repository with no scans appears with a zero-summary."""
    repo_id = _build_repo(
        app_config,
        owner="upload",
        name="no-scans",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="no-scans.zip",
    )
    with _db_session.SessionLocal() as s:
        summary = repository_repo.get_repository_summaries(s, [repo_id])[repo_id]
    assert summary.scan_count == 0
    assert summary.latest_scan_id is None
    assert summary.eligible_comparison_scan_count == 0


def test_repository_search_summary_uses_latest_scan_id(app_config, workspace_root) -> None:
    """The summary's latest scan id matches the largest scan id."""
    repo_id = _build_repo(
        app_config,
        owner="upload",
        name="latest-test",
        source_type=RepositorySourceType.UPLOADED_ARCHIVE,
        original_filename="latest-test.zip",
    )
    a = _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.COMPLETED)
    b = _build_scan(app_config, workspace_root, repository_id=repo_id, status=ScanStatus.PARTIAL)
    with _db_session.SessionLocal() as s:
        summary = repository_repo.get_repository_summaries(s, [repo_id])[repo_id]
    assert summary.latest_scan_id == b
    assert summary.latest_scan_id != a
    assert summary.eligible_comparison_scan_count == 2


def test_repository_search_does_not_emit_n_plus_1(app_config, workspace_root) -> None:
    """The list endpoint must not issue a per-row scan-history query.

    We seed many repositories with scans, capture the
    session's query log, and assert the list endpoint plus
    the summary batch query is a small constant number of
    queries (not one per row). The test uses SQLAlchemy's
    ``before_cursor_execute`` event listener to count
    statements.
    """
    from sqlalchemy import event

    n_repos = 6
    repo_ids = []
    for i in range(n_repos):
        rid = _build_repo(
            app_config,
            owner="upload",
            name=f"n-plus-one-{i:02d}",
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            original_filename=f"n-plus-one-{i:02d}.zip",
        )
        _build_scan(app_config, workspace_root, repository_id=rid, status=ScanStatus.COMPLETED)
        repo_ids.append(rid)

    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    client = api_client(app)
    with _db_session.SessionLocal() as s:
        event.listen(s.bind, "before_cursor_execute", _count)
        try:
            response = client.get(f"/api/v1/repositories?page_size={n_repos}")
        finally:
            event.remove(s.bind, "before_cursor_execute", _count)
    assert response.status_code == 200
    # The constant-per-request budget is: list query (1) +
    # count query (1) + summary batch query (1) = 3, plus
    # a handful of pre-fetch / commit / autoload queries.
    # We assert "definitely less than n_repos".
    assert counter["n"] < n_repos
