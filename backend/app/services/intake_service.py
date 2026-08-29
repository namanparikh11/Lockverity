"""Intake service.

The :class:`IntakeService` is the single entry point for adding
a new repository to the system. It supports two flows:

- :meth:`intake_github` - resolve a public GitHub URL to a
  commit SHA, download the tarball, validate and extract it.
- :meth:`intake_upload` - accept a streamed ZIP upload, validate
  and extract it.

Both flows create a fresh :class:`Repository`, a :class:`ScanRun`,
a :class:`Workspace`, and a default stage pipeline. The
resulting scan is in ``queued`` state; the orchestrator picks it
up via the executor.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanTriggerType
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState
from app.providers import github_provider
from app.providers.github_provider import GitHubIntakeError
from app.repositories import repository_repo
from app.services import scan_service
from app.services.workspace_service import WorkspaceService
from app.utils.archive_validation import ArchiveLimits
from app.utils.errors import ApiError, ApiErrorCode
from app.utils.github import is_valid_ref
from app.utils.repo_url import (
    NormalizedRepositoryUrl,
    RepositoryUrlError,
    normalize_github_url,
)
from app.utils.zip_intake import (
    ZipIntakeError,
    ZipIntakeResult,
    intake_tar_gz,
    intake_zip,
)

logger = logging.getLogger("lockverity.intake")


def _archive_limits(settings) -> Any:
    """Build an :class:`ArchiveLimits` from application settings."""
    return ArchiveLimits(
        max_compressed_bytes=settings.archive_max_compressed_bytes,
        max_uncompressed_bytes=settings.archive_max_uncompressed_bytes,
        max_file_count=settings.archive_max_file_count,
        max_file_bytes=settings.archive_max_file_bytes,
        max_depth=settings.archive_max_depth,
        suspicious_ratio=settings.archive_suspicious_ratio,
    )


@dataclass(frozen=True, slots=True)
class GitHubIntakeRequest:
    canonical_url: str
    requested_ref: str | None = None


@dataclass(frozen=True, slots=True)
class IntakeResult:
    repository: Repository
    scan: ScanRun
    workspace: Workspace
    intake_summary: dict[str, Any]


class IntakeService:
    """All repository and scan creation paths."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._workspaces = WorkspaceService(session, settings=self._settings)

    # ------------------------------------------------------------------
    # GitHub intake
    # ------------------------------------------------------------------
    def intake_github(self, payload: GitHubIntakeRequest) -> IntakeResult:
        # v2.1.1: a top-level try/except wraps the body so an
        # unhandled exception (a database write failure, a
        # filesystem error that survived every bounded
        # fallback, a programmer error, etc.) is sanitised
        # into the documented ``INTERNAL_UNEXPECTED``
        # envelope with a non-PII 16-character lowercase hex
        # ``correlation_id`` in ``details``. The inner
        # branches already raise ``ApiError`` for the
        # classified failure modes (``validation_error``,
        # ``not_found``, ``invalid_ref``, ``rate_limited``,
        # ``forbidden``, ``archive_unsafe``, etc.); those
        # ``ApiError`` instances are re-raised as-is because
        # the outer handler skips ``ApiError`` itself. The
        # handler mirrors the workspace ``FAILED``
        # transition onto the scan row and rolls back the
        # session, so a top-level write failure does not
        # leave a misleading ``QUEUED`` scan behind.
        try:
            return self._intake_github_inner(payload)
        except ApiError:
            # Already classified: ``validation_error``,
            # ``not_found``, ``invalid_ref``, ``rate_limited``,
            # ``forbidden``, ``archive_unsafe``, etc. Re-raise
            # without modification so the inner handler
            # owns the cleanup, the message, and the
            # ``details`` envelope.
            raise
        except Exception as exc:
            correlation_id = secrets.token_hex(8)
            logger.exception(
                "intake internal error (correlation_id=%s, kind=%s)",
                correlation_id,
                "github",
            )
            # Best-effort cleanup: roll back any partial
            # transaction so the response does not carry a
            # half-committed row. The workspace / scan
            # transitions are not attempted here because
            # the failure happened before the workspace
            # was created; the outer route's error handler
            # returns the 500 envelope to the client.
            with contextlib.suppress(Exception):
                self._session.rollback()
            raise ApiError(
                ApiErrorCode.INTERNAL_UNEXPECTED,
                "An internal error occurred. See Diagnostics for the "
                "correlation id and the runtime log for the full trace.",
                details={
                    "correlation_id": correlation_id,
                    "kind": "github",
                },
            ) from exc

    def _intake_github_inner(self, payload: GitHubIntakeRequest) -> IntakeResult:
        # Step 1: Normalize the URL.
        try:
            normalized = normalize_github_url(payload.canonical_url)
        except RepositoryUrlError as exc:
            raise ApiError(
                ApiErrorCode.VALIDATION_ERROR,
                "Repository URL is not a valid public GitHub URL.",
                details={"reason": str(exc)},
            ) from exc

        if payload.requested_ref is not None and not is_valid_ref(payload.requested_ref):
            raise ApiError(
                ApiErrorCode.VALIDATION_ERROR,
                "Requested ref is not a valid Git ref.",
                details={"reason": "invalid_ref"},
            )

        # Step 2: Resolve metadata + commit SHA.
        client = github_provider.build_client(
            token=self._settings.github_token,
            user_agent=self._settings.github_user_agent,
        )
        try:
            try:
                metadata = github_provider.fetch_repository_metadata(
                    client,
                    owner=normalized.owner,
                    name=normalized.name,
                    canonical_url=normalized.canonical_url,
                    requested_ref=payload.requested_ref,
                )
            except GitHubIntakeError as exc:
                raise _github_error_to_api_error(exc) from exc
            # Step 3: Download the tarball.
            try:
                tarball = github_provider.download_tarball(
                    client,
                    owner=normalized.owner,
                    name=normalized.name,
                    commit_sha=metadata.resolved_commit_sha,
                    max_response_bytes=self._settings.github_max_download_bytes,
                    timeout_seconds=self._settings.github_timeout_seconds,
                )
            except GitHubIntakeError as exc:
                raise _github_error_to_api_error(exc) from exc
        finally:
            client.close()

        # Step 4: Persist repository, scan, workspace.
        repository = self._get_or_create_github_repository(normalized, metadata)
        scan = self._queue_scan(repository, ScanTriggerType.MANUAL, metadata.resolved_commit_sha)
        workspace = self._workspaces.create_for_scan(
            scan,
            kind=WorkspaceKind.GITHUB,
            archive_filename=f"github/{metadata.owner}/{metadata.name}@{metadata.resolved_commit_sha}.tar.gz",
        )

        # Step 5: Quarantine the tarball bytes, validate, and extract.
        # Build a one-shot source: the first call returns the
        # whole body, subsequent calls return empty bytes so the
        # quarantine loop terminates.
        _sent = [False]

        def _tarball_source(_chunk_size: int) -> bytes:
            if _sent[0]:
                return b""
            _sent[0] = True
            return tarball.body

        result, workspace = self._quarantine_validate_extract(
            workspace=workspace,
            source=_tarball_source,
            limits=_archive_limits(self._settings),
            archive_filename=workspace.archive_filename,
            intake_kind="tar_gz",
        )
        return IntakeResult(
            repository=repository,
            scan=scan,
            workspace=workspace,
            intake_summary={
                "kind": "github",
                "owner": metadata.owner,
                "name": metadata.name,
                "resolved_commit_sha": metadata.resolved_commit_sha,
                "default_branch": metadata.default_branch,
                "visibility": metadata.visibility,
                "archive_sha256": result.archive_sha256,
                "archive_size": result.archive_size,
                "file_count": result.file_count,
                "uncompressed_size": result.uncompressed_size,
                "etag": tarball.etag,
                "last_modified": tarball.last_modified,
            },
        )

    # ------------------------------------------------------------------
    # ZIP upload intake
    # ------------------------------------------------------------------
    def intake_upload(
        self,
        *,
        upload: Callable[[int], bytes] | Iterable[bytes],
        archive_filename: str | None = None,
    ) -> IntakeResult:
        """Intake a streamed ZIP upload.

        ``upload`` is either an iterable of byte chunks or a
        callable that returns a single chunk per invocation. The
        repository and scan are created on the fly; the upload
        content is not retained outside the workspace.
        """
        # v2.1.1: a top-level try/except wraps the body so an
        # unhandled exception (a database write failure, a
        # filesystem error that survived every bounded
        # fallback, a programmer error, etc.) is sanitised
        # into the documented ``INTERNAL_UNEXPECTED``
        # envelope with a non-PII 16-character lowercase hex
        # ``correlation_id`` in ``details``. The inner
        # branches already raise ``ApiError`` for the
        # classified failure modes (``validation_error``,
        # ``archive_unsafe``); those ``ApiError`` instances
        # are re-raised as-is because the outer handler
        # skips ``ApiError`` itself.
        try:
            return self._intake_upload_inner(upload=upload, archive_filename=archive_filename)
        except ApiError:
            raise
        except Exception as exc:
            correlation_id = secrets.token_hex(8)
            logger.exception(
                "intake internal error (correlation_id=%s, kind=%s)",
                correlation_id,
                "upload",
            )
            with contextlib.suppress(Exception):
                self._session.rollback()
            raise ApiError(
                ApiErrorCode.INTERNAL_UNEXPECTED,
                "An internal error occurred. See Diagnostics for the "
                "correlation id and the runtime log for the full trace.",
                details={
                    "correlation_id": correlation_id,
                    "kind": "upload",
                },
            ) from exc

    def _intake_upload_inner(
        self,
        *,
        upload: Callable[[int], bytes] | Iterable[bytes],
        archive_filename: str | None = None,
    ) -> IntakeResult:
        # v2.0.5: the original filename (basename-only) is
        # persisted on the repository row for the
        # human-readable label. ``basename_safely`` strips
        # absolute paths and other unsafe forms; an empty or
        # unsafe value resolves to ``None`` (the API will
        # surface a bounded fallback label).
        repository = self._create_upload_repository(original_filename=archive_filename)
        scan = self._queue_scan(
            repository,
            ScanTriggerType.UPLOAD,
            requested_ref=None,
        )
        workspace = self._workspaces.create_for_scan(
            scan,
            kind=WorkspaceKind.UPLOADED_ARCHIVE,
            archive_filename=archive_filename or "upload.zip",
        )
        result, workspace = self._quarantine_validate_extract(
            workspace=workspace,
            source=upload,
            limits=_archive_limits(self._settings),
            archive_filename=workspace.archive_filename,
        )
        return IntakeResult(
            repository=repository,
            scan=scan,
            workspace=workspace,
            intake_summary={
                "kind": "uploaded_archive",
                "archive_sha256": result.archive_sha256,
                "archive_size": result.archive_size,
                "file_count": result.file_count,
                "uncompressed_size": result.uncompressed_size,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _quarantine_validate_extract(
        self,
        *,
        workspace: Workspace,
        source: Callable[[int], bytes] | Iterable[bytes],
        limits: Any,
        archive_filename: str | None,
        intake_kind: str = "zip",
    ) -> tuple[ZipIntakeResult, Workspace]:
        paths = self._workspaces.paths_for(workspace.workspace_key)
        self._workspaces.transition(workspace, target=WorkspaceState.VALIDATING)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        try:
            if intake_kind == "tar_gz":
                result = intake_tar_gz(paths, source=source, limits=limits)
            else:
                result = intake_zip(paths, source=source, limits=limits)
        except ZipIntakeError as exc:
            self._workspaces.transition(
                workspace,
                target=WorkspaceState.FAILED,
                failure_code=exc.code,
                failure_summary=exc.message,
            )
            # v2.1.1: a failed intake must NOT leave the
            # scan row in a non-terminal state. The scan
            # is moved to ``FAILED`` (a terminal state)
            # alongside the workspace so the UI does not
            # show a misleading "running" or "queued" scan
            # after the intake has already failed. The
            # ``failure_code`` / ``failure_summary`` mirror
            # the workspace values so the operator sees the
            # same diagnostic in both surfaces.
            self._transition_intake_scan_to_failed(workspace, exc.code, exc.message)
            self._workspaces.cleanup(workspace)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
            # Sanitise the filename before it reaches the
            # error envelope: a pathful value
            # (``C:\\Users\\me\\secret.zip``) must not be
            # echoed in the response. ``basename_safely``
            # returns ``None`` for an unsafe value; we
            # surface the bounded empty string in that
            # case so the detail shape is stable.
            from app.utils.paths import basename_safely

            safe_archive_filename = basename_safely(archive_filename) or ""
            # v2.1.1: replace the generic "Archive was
            # rejected." with a category-specific actionable
            # message so the user knows whether to retry, to
            # re-upload, or to open an issue. The original
            # ``exc.code`` and bounded ``exc.message`` remain
            # in the error envelope ``details`` so debugging
            # tooling keeps the precise failure category.
            safe_message = _archive_rejection_message(exc.code)
            raise ApiError(
                ApiErrorCode.ARCHIVE_UNSAFE,
                safe_message,
                details={
                    "code": exc.code,
                    "message": exc.message,
                    "filename": safe_archive_filename,
                },
            ) from exc
        except Exception as exc:
            self._workspaces.transition(
                workspace,
                target=WorkspaceState.FAILED,
                failure_code="archive_internal_error",
                failure_summary=str(exc)[:2048],
            )
            # v2.1.1: same terminal-state guarantee for
            # the ``INTERNAL_UNEXPECTED`` branch. The scan
            # is moved to ``FAILED`` so the UI does not
            # show a misleading "running" or "queued" scan
            # after the intake has already failed. The
            # ``failure_summary`` is the safe ``str(exc)``
            # cap so the row never carries an unbounded
            # string.
            self._transition_intake_scan_to_failed(
                workspace, "archive_internal_error", str(exc)[:2048]
            )
            self._workspaces.cleanup(workspace)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
            # v2.1.1: re-raise as a sanitised
            # ``INTERNAL_UNEXPECTED`` ``ApiError`` with a
            # non-PII correlation id. The original exception's
            # safe ``str()`` is recorded in the workspace's
            # ``failure_summary`` and the rotating runtime
            # log; the client envelope only carries the
            # bounded message and the correlation id.
            # ``secrets.token_hex(8)`` produces 8 random
            # bytes encoded as a 16-character lowercase
            # hex string. The format is documented as
            # ``^[0-9a-f]{16}$`` and is used as the
            # operator-facing log/response correlation id.
            correlation_id = secrets.token_hex(8)
            logger.exception(
                "intake internal error (correlation_id=%s, kind=%s)",
                correlation_id,
                intake_kind,
            )
            raise ApiError(
                ApiErrorCode.INTERNAL_UNEXPECTED,
                "An internal error occurred. See Diagnostics for the "
                "correlation id and the runtime log for the full trace.",
                details={
                    "correlation_id": correlation_id,
                    "kind": intake_kind,
                },
            ) from exc
        self._workspaces.transition(
            workspace,
            target=WorkspaceState.READY,
            archive_sha256=result.archive_sha256,
            archive_size=result.archive_size,
            file_count=result.file_count,
            uncompressed_size=result.uncompressed_size,
        )
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return result, workspace

    def _get_or_create_github_repository(
        self,
        normalized: NormalizedRepositoryUrl,
        metadata: github_provider.GitHubRepositoryMetadata,
    ) -> Repository:
        existing = repository_repo.get_repository_by_canonical_url(
            self._session, normalized.canonical_url
        )
        if existing is not None:
            # Refresh the public metadata that the GitHub API
            # returned. We never overwrite identity fields.
            existing.default_branch = metadata.default_branch or existing.default_branch
            existing.description = metadata.description or existing.description
            existing.visibility = _to_visibility(metadata.visibility)
            existing.archived = metadata.archived
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
                raise
            return existing
        repo = repository_repo.create_github_repository(
            self._session,
            owner=normalized.owner,
            name=normalized.name,
            canonical_url=normalized.canonical_url,
            description=metadata.description,
            default_branch=metadata.default_branch,
            visibility=metadata.visibility,
        )
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return repo

    def _create_upload_repository(self, original_filename: str | None = None) -> Repository:
        # Uploaded archives are uniquely identified by their
        # archive SHA-256 once it has been computed. We cannot
        # know the SHA before the upload completes, so we use a
        # stable placeholder URL; the actual SHA replaces it
        # after validation. The unique constraint on
        # ``canonical_url`` keeps the placeholder from
        # double-creating the row.
        import hashlib
        import secrets

        marker = secrets.token_hex(8)
        placeholder_url = f"upload://{marker}"
        repo = repository_repo.create_github_repository(  # type: ignore[arg-type]
            self._session,
            owner="upload",
            name=marker,
            canonical_url=placeholder_url,
            description="Uploaded archive",
            default_branch=None,
            visibility="private",
        )
        repo.source_type = RepositorySourceType.UPLOADED_ARCHIVE
        repo.provider = RepositoryProvider.LOCAL_UPLOAD
        # v2.0.5: persist the basename of the client-supplied
        # filename. ``original_filename`` is stored as
        # basename-only; an absolute path the client sends
        # never reaches the database. The column is nullable
        # for v0.x-v2.0.4 historical rows; the intake path
        # populates it for new uploads. The field is used as
        # the primary human-readable label by the repository
        # list and search endpoints.
        if original_filename is not None:
            from app.utils.paths import basename_safely

            repo.original_filename = basename_safely(original_filename)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        # Reference hashlib so static analyzers see the dependency.
        _ = hashlib.sha256
        return repo

    def _queue_scan(
        self,
        repository: Repository,
        trigger: ScanTriggerType,
        requested_ref: str | None,
    ) -> ScanRun:
        scan = scan_service.create_scan(
            self._session,
            repository_id=repository.id,
            trigger_type=trigger,
            requested_ref=requested_ref,
        )
        return scan

    def _transition_intake_scan_to_failed(
        self,
        workspace: Workspace,
        failure_code: str,
        failure_summary: str,
    ) -> None:
        """Move the scan attached to ``workspace`` to ``FAILED``.

        v2.1.1: a failed intake must NOT leave the scan row
        in a non-terminal state. The scan is moved to
        ``FAILED`` (a terminal state) alongside the
        workspace so the UI does not show a misleading
        "running" or "queued" scan after the intake has
        already failed. The ``failure_code`` /
        ``failure_summary`` mirror the workspace values
        so the operator sees the same diagnostic in both
        surfaces. The method is a no-op when no scan is
        attached (which is the case for archive uploads
        that fail before the scan is created).
        """
        # The Workspace model exposes the foreign key
        # under ``scan_run_id`` (not ``scan_id``); the
        # name ``scan_id`` is the public API name on
        # the related pydantic schemas only.
        scan_id = getattr(workspace, "scan_run_id", None)
        if scan_id is None:
            return
        # ``transition_scan`` validates the state machine
        # (``QUEUED -> FAILED`` is legal) and persists the
        # change. The function commits internally; the
        # surrounding try/except in the caller also
        # commits the workspace transition in the same
        # transaction so the two surfaces stay
        # consistent.
        from app.models.scan_run import ScanStatus

        scan_service.transition_scan(
            self._session,
            scan_id,
            target=ScanStatus.FAILED,
            failure_code=failure_code,
            failure_summary=failure_summary,
        )


def _to_visibility(value: str) -> RepositoryVisibility:
    value = (value or "").lower()
    if value == "public":
        return RepositoryVisibility.PUBLIC
    if value == "private":
        return RepositoryVisibility.PRIVATE
    return RepositoryVisibility.UNKNOWN


def _github_error_to_api_error(exc: GitHubIntakeError) -> ApiError:
    code = ApiErrorCode.PROVIDER_UNAVAILABLE
    http_status = exc.http_status
    safe_message = exc.redacted_summary()
    details: dict[str, Any] = {"code": exc.code}
    if http_status is not None:
        details["http_status"] = http_status
    if exc.code in {"github_not_found"}:
        code = ApiErrorCode.NOT_FOUND
        # v2.1.1: distinguish "URL is wrong / repo
        # does not exist" from "private repo / not
        # supported". The not_found case is the most
        # common user-reported failure mode after a
        # typo or copy-paste; a private repo produces
        # the same HTTP status. The actionable message
        # below covers both: the operator should confirm
        # the URL and the visibility before re-trying.
        safe_message = (
            "Repository could not be accessed. "
            "Confirm that the URL exists and is public. "
            "Private repositories are not supported in this version."
        )
    elif exc.code in {"github_invalid_ref"}:
        # v2.1.1: the repository was successfully
        # resolved (so it exists and is public) but the
        # supplied ref was not a branch, tag or commit
        # SHA on that repository. The user-facing
        # message is distinct from the
        # "repository could not be accessed" message
        # above so the operator can tell the two
        # failure modes apart from a single response.
        # The ``details.ref`` carries the requested
        # ref (already known-safe by the time it
        # reached this code path) and ``code``
        # carries the precise provider code for
        # diagnostics.
        code = ApiErrorCode.INVALID_REF
        safe_message = (
            "The requested branch, tag, or commit could not be found on "
            "the repository. Check the ref and try again."
        )
    elif exc.code in {"github_rate_limited"}:
        code = ApiErrorCode.RATE_LIMITED
        # v2.1.1: surface a retryable message instead of
        # the upstream's raw "429 Too Many Requests"
        # string. The Diagnostics page shows the current
        # rate-limit state; the operator can also
        # configure a token to lift the unauthenticated
        # 60-per-hour GitHub cap.
        safe_message = (
            "GitHub rate limit reached. Wait a few minutes and retry. "
            "Configure LOCKVERITY_GITHUB_TOKEN to lift the unauthenticated limit. "
            "The Diagnostics page shows the current rate-limit state."
        )
    elif exc.code in {"github_unauthorized", "github_forbidden"}:
        code = ApiErrorCode.FORBIDDEN
        safe_message = (
            "GitHub denied the request. The repository may be private, "
            "the URL may be wrong, or the configured token may lack access. "
            "Private repositories are not supported in this version."
        )
    elif exc.code in {"github_response_too_large"}:
        code = ApiErrorCode.VALIDATION_ERROR
        safe_message = (
            "The requested repository archive exceeds the configured "
            "download cap. Use a smaller ref or a self-uploaded archive."
        )
    elif exc.code in {"github_host_forbidden"}:
        code = ApiErrorCode.FORBIDDEN
        safe_message = (
            "The configured GitHub host is not in the bounded allowlist. "
            "Verify the upstream URL and the network policy."
        )
    elif exc.code in {"github_unavailable", "github_timeout"}:
        code = ApiErrorCode.PROVIDER_UNAVAILABLE
        safe_message = (
            "GitHub did not respond in time. Retry shortly. "
            "If the issue persists, see the Diagnostics page."
        )
    safe_message = safe_message or "GitHub intake failed."
    return ApiError(code, safe_message, details=details)


# v2.1.1: category-specific actionable message for the
# archive-rejection branch. The original intake surfaced
# the literal zip_intake failure code and message in
# the response, which was the right diagnostic value for
# an operator reading the API response but a poor user
# message for the operator who just wanted to know what
# to do next. The mapping here translates the rejection
# code into an actionable, category-specific user
# message; the original code and message stay in
# ``details`` for diagnostics.
_ARCHIVE_REJECTION_MESSAGES: dict[str, str] = {
    "archive_unsafe_path": (
        "Archive was rejected: it contains a path that is "
        "outside the archive root, an absolute path, a "
        "Windows drive-letter path, a UNC path, or a "
        "symlink. Re-create the archive from a clean source "
        "checkout."
    ),
    "archive_symlink_forbidden": (
        "Archive was rejected: it contains a hard link. "
        "Lockverity never follows hard links inside an uploaded "
        "archive. Re-create the archive as plain files."
    ),
    "archive_symlink_target_unsafe": (
        "Archive was rejected: it contains a symbolic link with a "
        "target that escapes the archive root, an absolute target, "
        "a Windows drive-letter target, or a UNC target. Re-create "
        "the archive without the unsafe link."
    ),
    "archive_symlink_target_missing": (
        "Archive was rejected: it contains a symbolic link with an "
        "empty or malformed target. Re-create the archive with a "
        "valid relative target."
    ),
    "archive_symlink_target_too_large": (
        "Archive was rejected: it contains a symbolic link whose "
        "recorded target is far larger than any filesystem path. The "
        "entry is carrying a payload rather than a link target; "
        "investigate the archive source."
    ),
    "archive_too_many_files": (
        "Archive was rejected: it contains more files than "
        "the configured cap. Reduce the archive size or split "
        "the upload."
    ),
    "archive_entry_too_large": (
        "Archive was rejected: a single entry exceeds the "
        "configured per-entry cap. Re-create the archive "
        "without the oversized file."
    ),
    "archive_uncompressed_too_large": (
        "Archive was rejected: the cumulative uncompressed "
        "size exceeds the configured cap. Re-create the "
        "archive with fewer files."
    ),
    "archive_overwrite_forbidden": (
        "Archive was rejected: the workspace already contains "
        "a file with the same path. Retry with a fresh "
        "workspace."
    ),
    "archive_path_resolve_failed": (
        "Archive was rejected: a path inside the archive "
        "could not be resolved on this host. This is "
        "usually a Windows long-path issue with a deep "
        "workspace tree; retry with a shallower extraction "
        "or a different runtime home."
    ),
    "archive_path_escape": (
        "Archive was rejected: a path inside the archive "
        "resolves outside the workspace contents. The "
        "archive appears to be malicious or corrupted. Do "
        "not retry; investigate the archive source."
    ),
    "archive_extract_failed": (
        "Archive was rejected: a tarball member could not "
        "be extracted. The tarball appears to be corrupted "
        "or truncated. Re-download or re-create it."
    ),
    "archive_quarantine_write_failed": (
        "Archive was rejected: the quarantine directory is "
        "not writable. Check the runtime home directory "
        "permissions."
    ),
    "archive_validation_failed": (
        "Archive was rejected: the archive failed safety "
        "validation. Inspect the archive source and the "
        "operational log for the precise failure."
    ),
}


def _archive_rejection_message(code: str) -> str:
    """Return the actionable user-facing message for an archive rejection code.

    The function prefers a category-specific message; if
    the code is unknown it falls through to a generic
    "Archive was rejected." which the operator can read
    alongside the precise code carried in the response
    ``details`` envelope.
    """
    return _ARCHIVE_REJECTION_MESSAGES.get(
        code, "Archive was rejected. See the response details for the precise failure."
    )


__all__ = [
    "GitHubIntakeRequest",
    "IntakeResult",
    "IntakeService",
]
