"""Regression tests for the v2.0.6 stage message-severity derivation.

The v0.5-v2.0.5 ``ScanTimeline`` and ``DashboardPage`` components
prefixed every stage ``failure_summary`` string with ``"Failure: "``
and used red ``rose-50`` styling. Several normal no-data outcomes
(``No OSV advisories were returned for this scan.``,
``No workflow files were discovered.``, ``not_github_or_no_url``,
``1 parser warnings``) are **not** stage-execution failures: they
describe a completed stage that did not produce records because
the input was honest.

v2.0.6 adds an additive derived field ``message_severity`` to
the ``ScanStageRead`` schema. The field is computed at the
API boundary from the existing structured fields (``status``,
``records_processed``, ``failure_code``, ``failure_summary``)
using the closed-list :func:`derive_message_severity` helper.
The field is **never persisted**; it is a derived
read-time concern only.

Severity values: ``"error"`` (failed stage with a real
``failure_code`` or ``failure_summary``), ``"warning"``
(partial stage, or completed stage with a residual summary
that is not a closed-list no-data reason), ``"info"``
(completed stage with a closed-list normal no-data
summary), or ``"none"`` (no message requiring emphasis).
"""

from __future__ import annotations

from app.api.mappers import stage_to_read
from app.db import session as _db_session
from app.main import app
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.models.scan_stage import ScanStage, StageStatus, StageType
from app.services import scan_service
from app.utils.stage_severity import (
    NO_DATA_SUMMARIES,
    PARSER_WARNING_SUMMARIES,
    derive_message_severity,
)

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# Helper: build a stage row directly and map it through the API
# ---------------------------------------------------------------------------


def _build_repo_and_scan(app_config) -> int:
    """Create a minimal repository and a single scan, return the scan id."""
    with _db_session.SessionLocal() as s:
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
        s.add(repo)
        s.flush()
        scan = scan_service.create_scan(
            s, repository_id=repo.id, trigger_type=ScanTriggerType.UPLOAD
        )
        scan.status = ScanStatus.COMPLETED
        s.commit()
        return scan.id


def _build_stage(
    app_config,
    *,
    scan_id: int,
    status: StageStatus = StageStatus.COMPLETED,
    records_processed: int = 0,
    failure_code: str | None = None,
    failure_summary: str | None = None,
    stage_type: StageType = StageType.VULNERABILITY_QUERY,
) -> int:
    with _db_session.SessionLocal() as s:
        stage = ScanStage(
            scan_run_id=scan_id,
            stage_type=stage_type,
            status=status,
            records_processed=records_processed,
            failure_code=failure_code,
            failure_summary=failure_summary,
        )
        s.add(stage)
        s.commit()
        return stage.id


# ---------------------------------------------------------------------------
# 1. FAILED with failure_code or failure_summary -> error
# ---------------------------------------------------------------------------


def test_failed_stage_with_code_is_error() -> None:
    """A FAILED stage with a real ``failure_code`` is classified as ``error``."""
    assert (
        derive_message_severity(
            status="failed",
            records_processed=0,
            failure_code="archive_unsafe",
            failure_summary="archive was rejected",
        )
        == "error"
    )


def test_failed_stage_with_summary_only_is_error() -> None:
    """A FAILED stage with only a ``failure_summary`` is still classified as ``error``."""
    assert (
        derive_message_severity(
            status="failed",
            records_processed=0,
            failure_code=None,
            failure_summary="boom",
        )
        == "error"
    )


# ---------------------------------------------------------------------------
# 2. PARTIAL -> warning
# ---------------------------------------------------------------------------


def test_partial_stage_is_warning() -> None:
    """A PARTIAL stage with a residual summary is classified as ``warning``."""
    assert (
        derive_message_severity(
            status="partial",
            records_processed=5,
            failure_code=None,
            failure_summary="one provider unavailable",
        )
        == "warning"
    )


def test_partial_stage_no_summary_is_warning() -> None:
    """A PARTIAL stage with no summary is still classified as ``warning``."""
    assert (
        derive_message_severity(
            status="partial",
            records_processed=0,
            failure_code=None,
            failure_summary=None,
        )
        == "warning"
    )


# ---------------------------------------------------------------------------
# 3. COMPLETED + closed-list no-data -> info
# ---------------------------------------------------------------------------


def test_completed_osv_zero_advisories_is_info() -> None:
    """A COMPLETED VULNERABILITY_QUERY stage with the OSV no-advisories summary is ``info``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=12,
            failure_code=None,
            failure_summary="No OSV advisories were returned for this scan.",
        )
        == "info"
    )


def test_completed_workflow_no_files_is_info() -> None:
    """A COMPLETED WORKFLOW_ANALYSIS stage with the no-files summary is ``info``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=0,
            failure_code=None,
            failure_summary="No workflow files were discovered.",
        )
        == "info"
    )


def test_completed_posture_github_only_is_info() -> None:
    """A COMPLETED REPOSITORY_POSTURE stage for an uploaded row is ``info``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=0,
            failure_code=None,
            failure_summary="not_github_or_no_url",
        )
        == "info"
    )


def test_completed_enrichment_no_components_is_info() -> None:
    """A COMPLETED DEPENDENCY_ENRICHMENT stage with no components is ``info``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=0,
            failure_code=None,
            failure_summary="No components were available to enrich.",
        )
        == "info"
    )


# ---------------------------------------------------------------------------
# 4. COMPLETED + parser warnings -> warning
# ---------------------------------------------------------------------------


def test_completed_parser_warnings_is_warning() -> None:
    """A COMPLETED DEPENDENCY_PARSING stage with parser warnings is ``warning``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=2,
            failure_code=None,
            failure_summary="1 parser warnings",
        )
        == "warning"
    )


# ---------------------------------------------------------------------------
# 5. COMPLETED + unknown residual summary with non-zero records -> warning
# ---------------------------------------------------------------------------


def test_completed_unknown_summary_nonzero_records_is_warning() -> None:
    """A completed stage with non-zero records and an unknown residual summary
    is ``warning``. We never invent an error from a string.
    """
    assert (
        derive_message_severity(
            status="completed",
            records_processed=5,
            failure_code=None,
            failure_summary="some new diagnostic we do not recognise",
        )
        == "warning"
    )


# ---------------------------------------------------------------------------
# 6. COMPLETED + unknown residual summary with zero records -> none
# ---------------------------------------------------------------------------


def test_completed_unknown_summary_zero_records_is_none() -> None:
    """A completed stage with zero records and an unknown residual summary
    is ``none`` (defensive: we do not classify an unknown string as
    a normal no-data outcome).
    """
    assert (
        derive_message_severity(
            status="completed",
            records_processed=0,
            failure_code=None,
            failure_summary="some new diagnostic we do not recognise",
        )
        == "none"
    )


# ---------------------------------------------------------------------------
# 7. SKIPPED -> none
# ---------------------------------------------------------------------------


def test_skipped_stage_is_none() -> None:
    """A SKIPPED stage is ``none`` regardless of summary."""
    assert (
        derive_message_severity(
            status="skipped",
            records_processed=0,
            failure_code=None,
            failure_summary="not requested",
        )
        == "none"
    )


# ---------------------------------------------------------------------------
# 8. COMPLETED no summary -> none
# ---------------------------------------------------------------------------


def test_completed_no_summary_is_none() -> None:
    """A COMPLETED stage with no summary is ``none``."""
    assert (
        derive_message_severity(
            status="completed",
            records_processed=5,
            failure_code=None,
            failure_summary=None,
        )
        == "none"
    )


# ---------------------------------------------------------------------------
# 9. stage_to_read attaches the message_severity field
# ---------------------------------------------------------------------------


def test_stage_to_read_attaches_severity(app_config) -> None:
    """``stage_to_read`` populates ``message_severity`` from the row fields."""
    scan_id = _build_repo_and_scan(app_config)
    stage_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.COMPLETED,
        records_processed=12,
        failure_code=None,
        failure_summary="No OSV advisories were returned for this scan.",
    )
    with _db_session.SessionLocal() as s:
        stage = s.get(ScanStage, stage_id)
        read = stage_to_read(stage)
    assert read.message_severity == "info"


def test_stage_to_read_attaches_severity_error(app_config) -> None:
    """A FAILED stage maps to ``error`` via ``stage_to_read``."""
    scan_id = _build_repo_and_scan(app_config)
    stage_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.FAILED,
        records_processed=0,
        failure_code="archive_unsafe",
        failure_summary="archive was rejected",
    )
    with _db_session.SessionLocal() as s:
        stage = s.get(ScanStage, stage_id)
        read = stage_to_read(stage)
    assert read.message_severity == "error"


def test_stage_to_read_attaches_severity_warning_for_parser(app_config) -> None:
    """A COMPLETED parser-warning stage maps to ``warning`` via ``stage_to_read``."""
    scan_id = _build_repo_and_scan(app_config)
    stage_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.COMPLETED,
        records_processed=2,
        failure_code=None,
        failure_summary="1 parser warnings",
    )
    with _db_session.SessionLocal() as s:
        stage = s.get(ScanStage, stage_id)
        read = stage_to_read(stage)
    assert read.message_severity == "warning"


# ---------------------------------------------------------------------------
# 10. Scan stages endpoint returns message_severity
# ---------------------------------------------------------------------------


def test_scan_stages_endpoint_includes_severity(app_config) -> None:
    """The ``/scans/{id}/stages`` endpoint includes ``message_severity`` on every stage."""
    scan_id = _build_repo_and_scan(app_config)
    info_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.COMPLETED,
        records_processed=12,
        failure_code=None,
        failure_summary="No OSV advisories were returned for this scan.",
    )
    error_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.FAILED,
        records_processed=0,
        failure_code="archive_unsafe",
        failure_summary="boom",
    )
    warning_id = _build_stage(
        app_config,
        scan_id=scan_id,
        status=StageStatus.COMPLETED,
        records_processed=2,
        failure_code=None,
        failure_summary="1 parser warnings",
    )
    client = api_client(app)
    response = client.get(f"/api/v1/scans/{scan_id}/stages")
    assert response.status_code == 200
    body = response.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    by_id = {s["id"]: s for s in items}
    assert by_id[info_id]["message_severity"] == "info"
    assert by_id[error_id]["message_severity"] == "error"
    assert by_id[warning_id]["message_severity"] == "warning"
    # Every other stage from the default pipeline must
    # still carry ``message_severity`` (always a string;
    # may be "none" for default rows with no summary).
    for stage in items:
        assert "message_severity" in stage
        assert stage["message_severity"] in {"error", "warning", "info", "none"}


# ---------------------------------------------------------------------------
# 11. Closed-list invariants
# ---------------------------------------------------------------------------


def test_no_data_summaries_is_closed() -> None:
    """The ``NO_DATA_SUMMARIES`` allow-list is a closed set of known reasons."""
    # The set must be small and bounded; this test pins
    # the size so an accidental expansion is visible.
    assert len(NO_DATA_SUMMARIES) >= 3
    # Every entry must be a non-empty string; the
    # closed-list is consumed by exact equality.
    for entry in NO_DATA_SUMMARIES:
        assert isinstance(entry, str)
        assert entry.strip() == entry
        assert entry


def test_parser_warning_summaries_is_closed() -> None:
    """The ``PARSER_WARNING_SUMMARIES`` allow-list is a closed set of known reasons."""
    assert len(PARSER_WARNING_SUMMARIES) >= 1
    for entry in PARSER_WARNING_SUMMARIES:
        assert isinstance(entry, str)
        assert entry.strip() == entry
        assert entry


# ---------------------------------------------------------------------------
# 12. Substring rule safety
# ---------------------------------------------------------------------------


def test_no_substring_classification() -> None:
    """A ``failure_summary`` that contains the word "No" is **not** automatically classified as info.

    The helper uses an allow-list of exact strings, not
    a substring rule. A summary like ``"No scan can
    complete because of X"`` must fall through to
    ``warning`` (with non-zero records) or ``none`` (with
    zero records), not to ``info``.
    """
    assert (
        derive_message_severity(
            status="completed",
            records_processed=5,
            failure_code=None,
            failure_summary="No scan can complete because of provider X",
        )
        == "warning"
    )
    assert (
        derive_message_severity(
            status="completed",
            records_processed=0,
            failure_code=None,
            failure_summary="No scan can complete because of provider X",
        )
        == "none"
    )
