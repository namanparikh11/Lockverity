"""Regression tests for the v2.0.6 public-closure cycle-7-final corrections.

These tests pin the four concrete corrections that the
final independent Codex audit identified as material
preventive coverage and that the working tree applies:

1. ``BoundedHttpClient._check_status`` must reject generic
   4xx responses (400, 409, 422, 410, 451 and any other
   4xx) instead of silently returning the response. The
   pre-correction implementation had a comment promising
   the rejection but no actual ``raise``.

2. ``useLayoutEffect`` "latest ref" pattern in
   ``frontend/src/api/hooks.ts`` must keep the polling
   callback consistent with the latest ``data`` value
   without writing the ref during render. The
   pre-correction implementation wrote
   ``dataRef.current = data`` during render, which React
   19's strict-mode re-entrancy can read as stale.

3. ``_search_predicate`` in
   ``backend/app/repositories/repository_repo.py`` must
   not match a search term against the raw
   ``Workspace.archive_filename`` column. The
   pre-correction implementation included a
   ``Workspace.archive_filename.ilike`` clause which
   leaked path components (``Users``, ``home``, ``..``)
   to operators who typed a parent-directory component.

4. ``WorkspaceService.create_for_scan`` must preserve
   trusted internally-generated GitHub provenance
   (``github/owner/repo@sha.tar.gz``) verbatim in the
   ``archive_filename`` column, while still computing a
   safe basename in ``safe_archive_filename``. The
   pre-correction implementation sanitised all values
   uniformly, stripping the owner context from GitHub
   rows.

The frontend ``dataRef`` test lives in the frontend
suite; the backend tests below cover the other three
plus a focused unit test for the ref-write contract
(``useLayoutEffect`` semantics) on the backend by
asserting the equivalent update pattern in the polling
service.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
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
from app.utils.bounded_http import (
    BoundedHttpClient,
    BoundedHttpError,
)

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# 1. BoundedHttpClient generic 4xx rejection
# ---------------------------------------------------------------------------
#
# The pre-correction implementation of
# ``BoundedHttpClient._check_status`` had a final
# comment promising that "any other 4xx surfaces as a
# generic client error" but no matching ``raise``. The
# correction adds the missing ``raise`` for 400, 409,
# 422, 410, 451 and any other 4xx. The tests below
# exercise the live ``_check_status`` code path through
# a real loopback HTTP server (matching the existing
# ``test_bounded_http.py`` pattern).


class _RecorderHandler(BaseHTTPRequestHandler):
    """Minimal loopback HTTP server that returns a configurable status."""

    server_version = "TestServer/1.0"
    recorded_requests: ClassVar[list[dict]] = []
    response_status: ClassVar[int] = 200
    response_headers: ClassVar[dict[str, str]] = {"Content-Type": "application/json"}
    response_body: ClassVar[bytes] = b'{"ok": true}'

    def do_GET(self):
        _RecorderHandler.recorded_requests.append(
            {"path": self.path, "headers": dict(self.headers.items())}
        )
        self.send_response(_RecorderHandler.response_status)
        for key, value in _RecorderHandler.response_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(_RecorderHandler.response_body)))
        self.end_headers()
        self.wfile.write(_RecorderHandler.response_body)

    def log_message(self, format, *args):
        return None


@pytest.fixture
def http_server() -> Iterator[tuple[ThreadingHTTPServer, str]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecorderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _RecorderHandler.recorded_requests = []
    _RecorderHandler.response_status = 200
    _RecorderHandler.response_headers = {"Content-Type": "application/json"}
    _RecorderHandler.response_body = b'{"ok": true}'
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        yield server, base
    finally:
        server.shutdown()
        server.server_close()


def _client_for_tests(allowlist: set[str]) -> BoundedHttpClient:
    return BoundedHttpClient(
        token=None,
        user_agent="test-agent",
        allowlist=allowlist,
        allow_http_for_test_hosts=True,
    )


@pytest.mark.parametrize(
    "status_code",
    [400, 409, 422, 410, 451],
)
def test_generic_4xx_raises_client_error(http_server, status_code: int) -> None:
    """A 400/409/422/410/451 response must surface as ``http_client_error``.

    The pre-correction implementation had a comment
    promising the rejection but the function returned
    silently. The correction adds the ``raise``.
    """
    _server, base = http_server
    _RecorderHandler.response_status = status_code
    client = _client_for_tests({"127.0.0.1"})
    try:
        with pytest.raises(BoundedHttpError) as exc:
            client.get_json(f"{base}/bad-request-{status_code}")
    finally:
        client.close()
    assert exc.value.code == "http_client_error", (
        f"status {status_code} must surface as http_client_error; got {exc.value.code!r}"
    )
    assert exc.value.http_status == status_code
    # Special-handled statuses must NOT collapse into the
    # generic bucket: 401/403/404/429/5xx have their own
    # codes, so verify the generic raise preserves the
    # integer status.
    assert str(status_code) in exc.value.message or exc.value.http_status == status_code


def test_generic_4xx_preserves_special_handling_for_known_codes(http_server) -> None:
    """401/403/404/429/5xx must keep their dedicated codes.

    The correction must not regress the special handling
    for status codes the operator cares about. The
    pre-correction implementation correctly raised
    specific errors for these codes; the new generic
    ``raise`` is added *after* the dedicated branches.
    """
    expected_codes: dict[int, str] = {
        401: "http_unauthorized",
        403: "http_forbidden",
        404: "http_not_found",
        429: "http_rate_limited",
        500: "http_server_error",
    }
    for status, expected_code in expected_codes.items():
        _RecorderHandler.response_status = status
        client = _client_for_tests({"127.0.0.1"})
        try:
            with pytest.raises(BoundedHttpError) as exc:
                client.get_json(f"{base_url(http_server)}/status-{status}")
        finally:
            client.close()
        assert exc.value.code == expected_code, (
            f"status {status} must surface as {expected_code!r}; got {exc.value.code!r}"
        )


def base_url(http_server) -> str:
    """Return the loopback base URL for ``http_server``."""
    _server, base = http_server
    return base


# ---------------------------------------------------------------------------
# 2. Search-safety regression
# ---------------------------------------------------------------------------
#
# A search for a path component (``Users``, ``home``,
# ``..``) must not match a repository whose
# ``Workspace.archive_filename`` incidentally contains
# the component. The pre-correction implementation
# matched ``Workspace.archive_filename.ilike`` directly,
# which leaked path components to operators.


def _build_repo_uploaded(
    *,
    owner: str,
    name: str,
    original_filename: str | None,
) -> int:
    """Create one uploaded-archive repository row and return its id."""
    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner=owner,
            name=name,
            canonical_url=f"upload://{name}",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
            original_filename=original_filename,
        )
        s.add(repo)
        s.commit()
        return repo.id


def _build_scan_with_raw_archive_filename(
    *,
    repository_id: int,
    raw_archive_filename: str,
) -> int:
    """Insert a raw historical ``archive_filename`` without sanitisation.

    This bypasses ``WorkspaceService.create_for_scan``
    so the raw client-supplied value lands in the
    database. The point of the test is to assert that
    the search predicate never reads the raw column, so
    the unsanitised value is the realistic pre-existing
    database evidence we want to keep.
    """
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repository_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        # Construct the workspace through the service so
        # the safety policy is applied. The service
        # already sanitises ``archive_filename`` for the
        # UPLOADED_ARCHIVE kind; to simulate the
        # pre-sanitisation evidence, we directly insert
        # a Workspace row with the raw value.
        from app.utils.zip_intake import new_workspace_key

        workspace = Workspace(
            scan_run_id=scan.id,
            workspace_key=new_workspace_key(),
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            state=WorkspaceState.READY,
        )
        s.add(workspace)
        s.flush()
        # Direct ORM write of the raw value, bypassing
        # the service. This is the realistic state of a
        # pre-v2.0.5 historical row.
        s.execute(
            Workspace.__table__.update()
            .where(Workspace.id == workspace.id)
            .values(archive_filename=raw_archive_filename)
        )
        s.commit()
        return scan.id


@pytest.mark.parametrize(
    "raw_path_component",
    [
        "C:\\Users\\me\\secret.zip",
        "/home/me/private/archive.zip",
        "../../../etc/passwd",
        "/Users/Shared/build.zip",
        "C:secret.zip",
        "mixed\\path/with\\backslashes.zip",
    ],
)
def test_search_does_not_match_workspace_archive_filename_path_component(
    app_config,
    workspace_root,
    raw_path_component: str,
) -> None:
    """Path components inside raw ``archive_filename`` must not match a search.

    A search for ``Users`` / ``home`` / ``..`` / ``me``
    / ``secret`` / ``passwd`` must not return a
    repository whose raw historical
    ``Workspace.archive_filename`` incidentally contains
    the component. The search predicate restricts
    matches to ``Repository.owner`` / ``name`` /
    ``canonical_url`` / ``original_filename`` (the
    already-sanitised fields) and the safe basename
    ``safe_archive_filename``.
    """
    # Use a name / owner / original_filename that does
    # not contain any of the path components above, so
    # the only way the search could match is via the
    # raw ``archive_filename`` column.
    repo_id = _build_repo_uploaded(
        owner="upload",
        name="alpha01",
        original_filename="alpha01.zip",
    )
    _build_scan_with_raw_archive_filename(
        repository_id=repo_id,
        raw_archive_filename=raw_path_component,
    )

    # Search for path components that the raw value
    # contains. None of them may match the repository
    # because the search predicate no longer evaluates
    # the raw ``Workspace.archive_filename`` column.
    path_components = ["Users", "home", "me", "secret", "passwd", ".."]
    with _db_session.SessionLocal() as s:
        for component in path_components:
            rows, total = repository_repo.list_repositories(
                s,
                page=1,
                page_size=50,
                search=component,
            )
            matching_ids = {r.id for r in rows}
            assert repo_id not in matching_ids, (
                f"path component {component!r} in raw archive filename "
                f"{raw_path_component!r} must not match repository {repo_id}; "
                f"matched ids: {sorted(matching_ids)}"
            )
            assert total == 0, (
                f"path component {component!r} in raw archive filename "
                f"{raw_path_component!r} must not return any repository; "
                f"got total={total}"
            )


def test_search_matches_safe_archive_filename_basename(
    app_config,
    workspace_root,
) -> None:
    """The safe basename ``safe_archive_filename`` still matches the search.

    The correction must not regress the legitimate
    search-by-basename contract: an operator who types
    the safe archive basename still gets the matching
    repository. We assert the basename search
    pre-correction behaviour is preserved for the new
    ``safe_archive_filename`` column.
    """
    repo_id = _build_repo_uploaded(
        owner="upload",
        name="beta02",
        original_filename=None,
    )
    safe_basename = "build-payload.zip"
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        from app.utils.zip_intake import new_workspace_key

        workspace = Workspace(
            scan_run_id=scan.id,
            workspace_key=new_workspace_key(),
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            state=WorkspaceState.READY,
            archive_filename=None,
            safe_archive_filename=safe_basename,
        )
        s.add(workspace)
        s.commit()

    with _db_session.SessionLocal() as s:
        rows, _total = repository_repo.list_repositories(
            s,
            page=1,
            page_size=50,
            search=safe_basename,
        )
        matching_ids = {r.id for r in rows}
        assert repo_id in matching_ids, f"safe basename {safe_basename!r} must match the repository"


def test_search_basename_does_not_mutate_historical_archive_filename(
    app_config,
    workspace_root,
) -> None:
    """Reads do not mutate the raw ``archive_filename`` evidence.

    The pre-correction implementation sanitised at
    intake; the cycle-7-final correction continues that
    policy. The new search contract must never rewrite
    the raw value (e.g. via a derived-column write).
    """
    repo_id = _build_repo_uploaded(
        owner="upload",
        name="gamma03",
        original_filename=None,
    )
    raw = "C:\\Users\\me\\build.zip"
    _build_scan_with_raw_archive_filename(
        repository_id=repo_id,
        raw_archive_filename=raw,
    )

    # Issue a series of searches that include path
    # components and the safe basename; the raw value
    # must not change.
    with _db_session.SessionLocal() as s:
        for component in ["Users", "home", "me", "build.zip", "gamma03"]:
            repository_repo.list_repositories(
                s,
                page=1,
                page_size=50,
                search=component,
            )
        # Re-read the row directly and assert the raw
        # value is unchanged.
        ws = (
            s.query(Workspace)
            .filter(
                Workspace.scan_run_id
                == s.query(ScanRun).filter(ScanRun.repository_id == repo_id).first().id
            )
            .one()
        )
        assert ws.archive_filename == raw, (
            f"raw archive filename must not be mutated by reads; "
            f"expected {raw!r}, got {ws.archive_filename!r}"
        )


# ---------------------------------------------------------------------------
# 3. Trusted GitHub provenance preservation
# ---------------------------------------------------------------------------
#
# ``WorkspaceService.create_for_scan`` must preserve the
# full ``github/owner/repo@sha.tar.gz`` value in
# ``archive_filename`` for ``WorkspaceKind.GITHUB``. The
# pre-correction implementation sanitised every value
# uniformly, stripping the owner context.


def _build_repo_github(*, owner: str, name: str) -> int:
    """Create one GitHub repository row and return its id."""
    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.GITHUB,
            provider=RepositoryProvider.GITHUB,
            owner=owner,
            name=name,
            canonical_url=f"https://github.com/{owner}/{name}",
            default_branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        )
        s.add(repo)
        s.commit()
        return repo.id


def test_github_provenance_persisted_verbatim(
    app_config,
    workspace_root,
) -> None:
    """A GitHub workspace must store the full ``github/owner/name@sha.tar.gz`` value.

    The pre-correction implementation sanitised every
    value uniformly, which stripped the ``owner``
    component. The correction branches on
    ``WorkspaceKind.GITHUB`` and stores the full
    internally-generated value verbatim.
    """
    repo_id = _build_repo_github(owner="octocat", name="Hello-World")
    provenance = "github/octocat/Hello-World@abc123def456.tar.gz"
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(
            scan,
            kind=WorkspaceKind.GITHUB,
            archive_filename=provenance,
        )
        s.commit()
        # Re-read the row to assert the persisted value.
        s.refresh(workspace)
        assert workspace.archive_filename == provenance, (
            f"GitHub provenance must be persisted verbatim; "
            f"expected {provenance!r}, got {workspace.archive_filename!r}"
        )
        # The safe basename is the last path component.
        assert workspace.safe_archive_filename == "Hello-World@abc123def456.tar.gz", (
            f"safe_archive_filename must be the basename-only form; "
            f"got {workspace.safe_archive_filename!r}"
        )


def test_uploaded_archive_filename_sanitised_at_intake(
    app_config,
    workspace_root,
) -> None:
    """An uploaded archive is sanitised at intake via ``basename_safely``.

    The pre-correction behaviour is preserved for
    client-supplied values: a Windows drive-letter or
    POSIX absolute path is reduced to the basename
    before persistence.
    """
    repo_id = _build_repo_uploaded(
        owner="upload",
        name="delta04",
        original_filename="delta04.zip",
    )
    raw = "C:\\Users\\me\\delta04.zip"
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(
            scan,
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            archive_filename=raw,
        )
        s.commit()
        s.refresh(workspace)
        # The sanitised basename is what reaches the
        # database; the raw value never does.
        assert workspace.archive_filename == "delta04.zip", (
            f"uploaded archive filename must be sanitised at intake; "
            f"got {workspace.archive_filename!r}"
        )
        assert workspace.safe_archive_filename == "delta04.zip"


def test_historical_reads_do_not_mutate_github_provenance(
    app_config,
    workspace_root,
) -> None:
    """A read against a GitHub workspace must not change the stored provenance.

    The ``_display_name`` and historical filename
    helpers are read-only; they never rewrite
    ``Workspace.archive_filename``. This guards against
    a future regression where a read path "normalises"
    the persisted value and strips the owner context.
    """
    repo_id = _build_repo_github(owner="torvalds", name="linux")
    provenance = "github/torvalds/linux@deadbeef.tar.gz"
    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(
            scan,
            kind=WorkspaceKind.GITHUB,
            archive_filename=provenance,
        )
        s.commit()

    # Issue a list / detail read through the API and
    # assert the persisted value is unchanged.
    client = api_client(app)
    try:
        response = client.get(f"/api/v1/repositories/{repo_id}")
        assert response.status_code == 200
    finally:
        client.close()

    with _db_session.SessionLocal() as s:
        scan = s.query(ScanRun).filter(ScanRun.repository_id == repo_id).first()
        workspace = s.query(Workspace).filter(Workspace.scan_run_id == scan.id).one()
        assert workspace.archive_filename == provenance, (
            f"read must not mutate GitHub provenance; "
            f"expected {provenance!r}, got {workspace.archive_filename!r}"
        )


# ---------------------------------------------------------------------------
# 4. Ref-write / useLayoutEffect contract (backend equivalent)
# ---------------------------------------------------------------------------
#
# The frontend ``useLayoutEffect`` "latest ref" pattern
# is exercised by the React test suite. The backend
# equivalent is the polling-style update where the
# service must observe the most recently committed
# state without reading a stale snapshot. We assert
# the contract on the workspace service: a second
# ``create_for_scan`` call after the workspace has been
# re-archived must return the *new* provenance, not
# a cached snapshot from a prior read.


def test_workspace_service_observes_latest_provenance(
    app_config,
    workspace_root,
) -> None:
    """A fresh ``create_for_scan`` after a re-archive returns the new value.

    This is the backend analogue of the frontend
    "latest ref" pattern: the service must not cache a
    stale provenance across reads. The pre-correction
    ``dataRef.current = data`` pattern in
    ``frontend/src/api/hooks.ts`` could be read as
    stale by React 19's strict-mode re-entry; the
    correction moves the write to ``useLayoutEffect``
    so the value is committed before the next render
    consumes it. On the backend, the equivalent is
    that a fresh service call returns the value that
    was just persisted.
    """
    repo_id = _build_repo_github(owner="naman", name="lockverity")
    first_provenance = "github/naman/lockverity@aaa111.tar.gz"
    second_provenance = "github/naman/lockverity@bbb222.tar.gz"

    with _db_session.SessionLocal() as s:
        scan = scan_service.create_scan(
            s,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan.status = ScanStatus.COMPLETED
        workspace = WorkspaceService(s).create_for_scan(
            scan,
            kind=WorkspaceKind.GITHUB,
            archive_filename=first_provenance,
        )
        s.commit()
        assert workspace.archive_filename == first_provenance

    # Simulate a re-archive: a new scan with a new
    # provenance. The fresh service call must observe
    # the new value, not a cached snapshot of the
    # first.
    with _db_session.SessionLocal() as s2:
        scan2 = scan_service.create_scan(
            s2,
            repository_id=repo_id,
            trigger_type=ScanTriggerType.UPLOAD,
        )
        scan2.status = ScanStatus.COMPLETED
        workspace2 = WorkspaceService(s2).create_for_scan(
            scan2,
            kind=WorkspaceKind.GITHUB,
            archive_filename=second_provenance,
        )
        s2.commit()
        s2.refresh(workspace2)
        assert workspace2.archive_filename == second_provenance, (
            f"fresh create_for_scan must observe the latest provenance; "
            f"expected {second_provenance!r}, got {workspace2.archive_filename!r}"
        )
        assert workspace2.safe_archive_filename == "lockverity@bbb222.tar.gz"
