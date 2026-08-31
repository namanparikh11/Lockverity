"""Scan lifecycle service.

A scan moves through a strict set of states. The state machine here
is the authoritative implementation; the database stores the current
state but does not enforce transitions. Terminal states are
immutable: a completed, partial, failed, or cancelled scan cannot be
silently reopened.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.scan_run import (
    TERMINAL_SCAN_STATUSES,
    ScanRun,
    ScanStatus,
    ScanTriggerType,
)
from app.models.scan_stage import (
    TERMINAL_STAGE_STATUSES,
    ScanStage,
    StageStatus,
    StageType,
)
from app.repositories import scan_repo, stage_repo
from app.services import repository_service
from app.utils.datetime import utcnow
from app.utils.errors import ApiError, ApiErrorCode

# Legal scan transitions: ``from -> set of legal ``to`` statuses.
# v2.1.1: a queued scan can transition directly to
# ``FAILED`` when the intake itself fails (e.g. the
# repository cannot be resolved, the archive cannot be
# extracted, or an unhandled exception is sanitised
# into ``INTERNAL_UNEXPECTED``). Without this
# transition the scan would be left in a non-terminal
# ``QUEUED`` state, which surfaces to the UI as a
# misleading "running" or "queued" scan after the
# intake has already failed. The transition is
# deliberately a one-way move to a terminal state so
# the scan row never reverts to a non-terminal
# status.
_SCAN_TRANSITIONS: dict[ScanStatus, frozenset[ScanStatus]] = {
    ScanStatus.QUEUED: frozenset({ScanStatus.RUNNING, ScanStatus.CANCELLED, ScanStatus.FAILED}),
    ScanStatus.RUNNING: frozenset(
        {ScanStatus.COMPLETED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED}
    ),
    ScanStatus.COMPLETED: frozenset(),
    ScanStatus.PARTIAL: frozenset(),
    ScanStatus.FAILED: frozenset(),
    ScanStatus.CANCELLED: frozenset(),
}

# Legal stage transitions.
_STAGE_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.PENDING: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED}),
    StageStatus.RUNNING: frozenset(
        {StageStatus.COMPLETED, StageStatus.PARTIAL, StageStatus.FAILED}
    ),
    StageStatus.COMPLETED: frozenset(),
    StageStatus.PARTIAL: frozenset(),
    StageStatus.FAILED: frozenset(),
    StageStatus.SKIPPED: frozenset(),
}

# Default stage order when a scan is queued. v0.1 does not execute
# any of these stages; they are recorded as ``pending`` to make the
# scan lifecycle observable.
_DEFAULT_STAGE_PIPELINE: tuple[StageType, ...] = (
    StageType.REPOSITORY_INTAKE,
    StageType.ARCHIVE_VALIDATION,
    StageType.MANIFEST_DISCOVERY,
    StageType.DEPENDENCY_PARSING,
    StageType.DEPENDENCY_ENRICHMENT,
    StageType.VULNERABILITY_QUERY,
    StageType.WORKFLOW_ANALYSIS,
    StageType.REPOSITORY_POSTURE,
    StageType.FINDING_RECONCILIATION,
    StageType.EXPORT_GENERATION,
)


def _is_terminal(status: ScanStatus) -> bool:
    return status in TERMINAL_SCAN_STATUSES


def _is_terminal_stage(status: StageStatus) -> bool:
    return status in TERMINAL_STAGE_STATUSES


def assert_legal_scan_transition(current: ScanStatus, target: ScanStatus) -> None:
    """Raise :class:`ApiError` if ``current -> target`` is illegal."""
    if _is_terminal(current):
        raise ApiError(
            ApiErrorCode.ILLEGAL_TRANSITION,
            "Scan is in a terminal state and cannot be modified.",
            details={
                "current_status": current.value,
                "target_status": target.value,
            },
        )
    legal = _SCAN_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise ApiError(
            ApiErrorCode.ILLEGAL_TRANSITION,
            "Illegal scan status transition.",
            details={
                "current_status": current.value,
                "target_status": target.value,
                "allowed": sorted(s.value for s in legal),
            },
        )


def assert_legal_stage_transition(current: StageStatus, target: StageStatus) -> None:
    """Raise :class:`ApiError` if ``current -> target`` is illegal."""
    if _is_terminal_stage(current):
        raise ApiError(
            ApiErrorCode.ILLEGAL_TRANSITION,
            "Stage is in a terminal state and cannot be modified.",
            details={
                "current_status": current.value,
                "target_status": target.value,
            },
        )
    legal = _STAGE_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise ApiError(
            ApiErrorCode.ILLEGAL_TRANSITION,
            "Illegal stage status transition.",
            details={
                "current_status": current.value,
                "target_status": target.value,
                "allowed": sorted(s.value for s in legal),
            },
        )


def create_scan(
    session: Session,
    *,
    repository_id: int,
    trigger_type: ScanTriggerType,
    requested_ref: str | None = None,
) -> ScanRun:
    """Create a queued scan and seed its default stage pipeline.

    The scan is created in the ``queued`` state with all default
    stages in the ``pending`` state. The scan is *not* marked as
    completed - v0.1 has no executor, so no scan will run
    automatically. Operators move the scan to ``running`` via a
    background job (deferred to a later milestone).
    """
    repository_service.get_repository_or_404(session, repository_id)
    scan = scan_repo.create_scan(
        session,
        repository_id=repository_id,
        trigger_type=trigger_type,
        requested_ref=requested_ref,
    )
    for stage_type in _DEFAULT_STAGE_PIPELINE:
        stage_repo.create_stage(session, scan_run_id=scan.id, stage_type=stage_type)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(
            ApiErrorCode.DUPLICATE,
            "Could not create scan due to a constraint violation.",
        ) from exc
    session.refresh(scan)
    return scan


def get_scan_or_404(session: Session, scan_id: int) -> ScanRun:
    scan = scan_repo.get_scan_by_id(session, scan_id)
    if scan is None:
        raise ApiError(
            ApiErrorCode.NOT_FOUND,
            "Scan not found.",
            details={"scan_id": scan_id},
        )
    return scan


def list_scans_for_repository(
    session: Session,
    repository_id: int,
    *,
    page: int,
    page_size: int,
    status: ScanStatus | None = None,
    trigger_type: ScanTriggerType | None = None,
) -> tuple[Sequence[ScanRun], int]:
    """List scans for one repository with optional bounded filters.

    The v1.8 page accepts ``status`` and ``trigger_type`` URL
    parameters; v2.0.1 actually applies them. Unknown values are
    rejected by the route's ``Query`` layer, so this function only
    sees valid enums or ``None``.
    """
    repository_service.get_repository_or_404(session, repository_id)
    return scan_repo.list_scans_for_repository(
        session,
        repository_id,
        page=page,
        page_size=page_size,
        status=status,
        trigger_type=trigger_type,
    )


def list_all_scans(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: ScanStatus | None = None,
    trigger_type: ScanTriggerType | None = None,
    seeded_dataset: str | None = None,
) -> tuple[Sequence[ScanRun], int]:
    """List scans across every repository, paginated.

    Used by the dashboard rollup; the per-repository listing keeps
    its narrower contract on ``/repositories/{id}/scans``.

    ``seeded_dataset`` selects only rows carrying the explicit
    seeded-data provenance marker. It exists so product surfaces
    (the Demo page) can resolve seeded demo rows by identity
    rather than by numeric primary key or status heuristics.
    """
    return scan_repo.list_scans(
        session,
        page=page,
        page_size=page_size,
        status=status,
        trigger_type=trigger_type,
        seeded_dataset=seeded_dataset,
    )


def list_stages_for_scan(session: Session, scan_id: int) -> Sequence[ScanStage]:
    get_scan_or_404(session, scan_id)
    return stage_repo.list_stages_for_scan(session, scan_id)


def transition_scan(
    session: Session,
    scan_id: int,
    *,
    target: ScanStatus,
    failure_code: str | None = None,
    failure_summary: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> ScanRun:
    """Move a scan to ``target`` after validating the transition."""
    scan = get_scan_or_404(session, scan_id)
    assert_legal_scan_transition(scan.status, target)
    scan.status = target
    if target == ScanStatus.RUNNING and started_at is not None:
        scan.started_at = started_at
    if target in TERMINAL_SCAN_STATUSES and completed_at is not None:
        scan.completed_at = completed_at
    if failure_code is not None:
        scan.failure_code = failure_code
    if failure_summary is not None:
        scan.failure_summary = failure_summary[:2048]
    session.commit()
    session.refresh(scan)
    return scan


def transition_stage(
    session: Session,
    scan_id: int,
    stage_type: StageType,
    *,
    target: StageStatus,
    failure_code: str | None = None,
    failure_summary: str | None = None,
) -> ScanStage:
    """Move a stage to ``target`` after validating the transition."""
    get_scan_or_404(session, scan_id)
    stage = stage_repo.get_stage(session, scan_id, stage_type)
    if stage is None:
        raise ApiError(
            ApiErrorCode.NOT_FOUND,
            "Stage not found for this scan.",
            details={"scan_id": scan_id, "stage_type": stage_type.value},
        )
    assert_legal_stage_transition(stage.status, target)
    stage.status = target
    now = utcnow()
    if target == StageStatus.RUNNING and stage.started_at is None:
        stage.started_at = now
    if target in TERMINAL_STAGE_STATUSES and stage.completed_at is None:
        stage.completed_at = now
    if failure_code is not None:
        stage.failure_code = failure_code
    if failure_summary is not None:
        stage.failure_summary = failure_summary[:2048]
    session.commit()
    session.refresh(stage)
    return stage
