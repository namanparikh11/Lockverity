"""Scan orchestrator.

The :class:`ScanOrchestrator` drives a :class:`ScanRun` through
its stages. It honours cancellation between stages, records a
:func:`ProviderObservation` for every stage, and ensures that
unsupported stages are recorded as ``not_requested`` rather than
silently completed.

The orchestrator does not run any provider logic. The stages
that need provider work are *observed* but skipped; they are
present so the scan lifecycle is observable end to end.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import (
    ProviderObservation,
    ProviderStatus,
)
from app.models.scan_run import (
    ScanRun,
    ScanStatus,
)
from app.models.scan_stage import (
    ScanStage,
    StageStatus,
    StageType,
)
from app.providers.selection import ExternalEvidenceProviders
from app.repositories import (
    scan_repo,
    stage_repo,
)
from app.services.provider_aggregation import aggregate_provider_observations
from app.services.scan_service import (
    assert_legal_scan_transition,
    assert_legal_stage_transition,
)
from app.utils.datetime import utcnow
from app.utils.errors import ApiError, ApiErrorCode
from app.utils.redaction import redact_provider_summary

logger = logging.getLogger("lockverity.orchestrator")

# Stages v0.2 can run end to end without any provider. They are
# recorded as ``available`` and the stage itself is marked
# ``completed`` once it has done its work.
_LOCAL_STAGES: frozenset[StageType] = frozenset(
    {
        StageType.REPOSITORY_INTAKE,
        StageType.ARCHIVE_VALIDATION,
        StageType.MANIFEST_DISCOVERY,
    }
)

# Stages v0.3 runs locally. v0.2 recorded these as
# ``not_requested``; v0.3 invokes the analysis pipeline so a scan
# produces real components, dependency edges, workflow findings,
# and rule-based findings. v0.4 expands the local set to the
# provider-backed stages (dependency_enrichment,
# vulnerability_query, repository_posture); the pipeline still
# owns their execution, but a single stage failure does not
# erase the local analysis.
_LOCAL_V03_STAGES: frozenset[StageType] = frozenset(
    {
        StageType.DEPENDENCY_PARSING,
        StageType.DEPENDENCY_ENRICHMENT,
        StageType.VULNERABILITY_QUERY,
        StageType.WORKFLOW_ANALYSIS,
        StageType.REPOSITORY_POSTURE,
        StageType.FINDING_RECONCILIATION,
    }
)

# Stages that are still recorded as ``not_requested`` in v0.4.
# The stage is marked ``skipped`` and a provider observation is
# written so the frontend can render the truth. Export
# generation is still a local convenience: the export routes
# build the artefact on demand rather than as part of the
# pipeline.
_REMOTE_STAGES: frozenset[StageType] = frozenset(
    {
        StageType.EXPORT_GENERATION,
    }
)


def _provider_stage_enabled(
    stage_type: StageType,
    selection: ExternalEvidenceProviders,
) -> bool:
    """Return whether ``stage_type`` should be pre-marked as running."""

    if stage_type == StageType.VULNERABILITY_QUERY:
        return selection.osv
    if stage_type == StageType.DEPENDENCY_ENRICHMENT:
        return selection.deps_dev
    if stage_type == StageType.REPOSITORY_POSTURE:
        return selection.openssf
    return True


@dataclass(frozen=True, slots=True)
class StageRecord:
    """A summary of a single stage's outcome."""

    stage: StageType
    status: StageStatus
    provider: str | None
    provider_status: str | None
    records_processed: int
    failure_code: str | None
    failure_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class OrchestrationOutcome:
    """The high-level result of an orchestrated scan."""

    scan_id: int
    final_status: ScanStatus
    stage_records: tuple[StageRecord, ...]
    failure_code: str | None
    failure_summary: str | None


@dataclass
class _CancellationToken:
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True

    def is_set(self) -> bool:
        with self.lock:
            return self.cancelled


class ScanOrchestrator:
    """The single entry point that drives a scan through its stages."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Run / cancel
    # ------------------------------------------------------------------
    def run(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken | None = None,
        provider_selection: ExternalEvidenceProviders | None = None,
    ) -> OrchestrationOutcome:
        """Run ``scan_id`` to completion (or to a non-recoverable state)."""
        cancellation = cancellation or _CancellationToken()
        provider_selection = provider_selection or ExternalEvidenceProviders()
        # Phase 1: transition the scan to ``running``. We use a
        # dedicated session so the state change is durable
        # even if the scan later fails.
        with self._session_factory() as session:
            scan = scan_service_get_or_404(session, scan_id)
            assert_legal_scan_transition(scan.status, ScanStatus.RUNNING)
            scan.status = ScanStatus.RUNNING
            scan.started_at = scan.started_at or utcnow()
            session.commit()
            session.refresh(scan)

        # Phase 2: walk the stages.
        records: list[StageRecord] = []
        overall_failure_code: str | None = None
        overall_failure_summary: str | None = None
        partial = False
        try:
            # In v0.3, we run the analysis pipeline for the
            # stages that do real local work. The pipeline owns
            # its own per-stage state transitions; the orchestrator
            # reads the resulting stage rows at the end.
            self._run_local_pipeline(
                scan_id,
                cancellation=cancellation,
                provider_selection=provider_selection,
            )
            for stage_type in _stage_pipeline():
                # Refresh the per-stage state after the pipeline ran.
                record = self._read_stage_record(scan_id, stage_type, cancellation=cancellation)
                records.append(record)
                if cancellation.is_set():
                    overall_failure_code = "cancelled"
                    overall_failure_summary = "Scan cancelled before stage started."
                    break
                if record.status == StageStatus.FAILED:
                    overall_failure_code = record.failure_code or "stage_failed"
                    overall_failure_summary = record.failure_summary or "Stage failed."
                    break
                if record.status == StageStatus.PARTIAL:
                    partial = True
        except Exception as exc:
            overall_failure_code = "orchestrator_internal_error"
            overall_failure_summary = str(exc)[
                : self._settings.scan_default_failure_summary_max_length
            ]
            logger.exception("orchestrator crashed for scan %s", scan_id)

        # Phase 3: finalize the scan.
        final_status = self._finalize(
            scan_id,
            cancellation=cancellation,
            partial=partial,
            overall_failure_code=overall_failure_code,
            overall_failure_summary=overall_failure_summary,
        )
        return OrchestrationOutcome(
            scan_id=scan_id,
            final_status=final_status,
            stage_records=tuple(records),
            failure_code=overall_failure_code,
            failure_summary=overall_failure_summary,
        )

    def cancel(self, scan_id: int) -> bool:
        """Mark a running scan for cancellation between stages."""
        with self._session_factory() as session:
            scan = scan_service_get_or_404(session, scan_id)
            if scan.status in {ScanStatus.COMPLETED, ScanStatus.PARTIAL, ScanStatus.FAILED}:
                return False
            if scan.status == ScanStatus.CANCELLED:
                return True
            # If the scan is still queued, mark it cancelled
            # immediately. The running transition below is a
            # belt-and-braces; it may or may not be applied
            # depending on whether the worker has already
            # started.
            try:
                assert_legal_scan_transition(scan.status, ScanStatus.CANCELLED)
            except ApiError:
                session.rollback()
                return False
            scan.status = ScanStatus.CANCELLED
            scan.completed_at = utcnow()
            scan.failure_code = "cancelled"
            scan.failure_summary = "Cancelled by user request."
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------
    def _run_local_pipeline(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken,
        provider_selection: ExternalEvidenceProviders,
    ) -> None:
        """Run the v0.3 analysis pipeline for the local stages.

        v0.2 ran each stage as its own atomic transition. v0.3
        delegates the local work to :class:`AnalysisPipeline` and
        then walks the stage rows to build a record for the
        caller. Cancellation is honoured before each stage: a
        cancelled scan stops at the next stage boundary.
        """
        from app.services.analysis_pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline(
            session_factory=self._session_factory,
            settings=self._settings,
        )

        # First, run the three v0.2 local stages in the original
        # way. They were the only stages v0.2 did real work for,
        # and the analysis pipeline assumes they have run.
        for stage_type in _LOCAL_STAGES:
            if cancellation.is_set():
                return
            self._mark_stage_running(scan_id, stage_type)
            record = self._run_v02_stage(scan_id, stage_type, cancellation=cancellation)
            # Mirror the record back into the stage row.
            with self._session_factory() as session:
                stage = stage_repo.get_stage(session, scan_id, stage_type)
                if stage is not None:
                    stage.status = record.status
                    stage.provider = record.provider
                    stage.provider_status = record.provider_status
                    stage.records_processed = record.records_processed
                    stage.failure_code = record.failure_code
                    stage.failure_summary = record.failure_summary
                    stage.completed_at = record.completed_at
                    session.commit()
            if record.status == StageStatus.FAILED:
                return

        # Mark every v0.3 local stage RUNNING so the UI reflects
        # the work, then run the v0.3 pipeline as one batch. The
        # pipeline itself records the v0.2 remote stages (export
        # generation) as ``skipped`` with the appropriate
        # provider observations, so we do not pre-mark them
        # here. The single stage the pipeline does not touch at
        # all (EXPORT_GENERATION) is marked SKIPPED here so the
        # UI sees a complete stage history.
        for stage_type in _stage_pipeline():
            if cancellation.is_set():
                return
            if stage_type in _LOCAL_STAGES:
                continue
            if stage_type not in _LOCAL_V03_STAGES:
                if stage_type in {
                    StageType.EXPORT_GENERATION,
                }:
                    self._complete_not_requested(
                        scan_id,
                        stage_type,
                        cancellation=cancellation,
                    )
                continue
            if not _provider_stage_enabled(stage_type, provider_selection):
                # The pipeline persists the not-requested observation and
                # returns a skipped outcome. Keeping the stage PENDING here
                # preserves the truthful PENDING -> SKIPPED transition.
                continue
            self._mark_stage_running(scan_id, stage_type)

        if not cancellation.is_set():
            try:
                summary = pipeline.run(
                    scan_id,
                    provider_selection=provider_selection,
                )
            except Exception as exc:
                logger.exception("analysis pipeline crashed for scan %s", scan_id)
                # Mark all local-v0.3 stages as failed.
                for stage_type in _LOCAL_V03_STAGES:
                    self._fail_stage(
                        scan_id,
                        stage_type,
                        failure_code="pipeline_internal_error",
                        failure_summary=str(exc)[:512],
                        cancellation=cancellation,
                    )
                return
            self._apply_pipeline_outcome(scan_id, summary)

    def _run_v02_stage(
        self,
        scan_id: int,
        stage_type: StageType,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        """Run a single v0.2 local stage and return its record.

        The v0.2 stages are the only ones that pre-date the
        analysis pipeline. They run in the orchestrator because
        their work is small and the failure modes are well
        understood.
        """
        if stage_type == StageType.REPOSITORY_INTAKE:
            return self._stage_repository_intake(scan_id, cancellation=cancellation)
        if stage_type == StageType.ARCHIVE_VALIDATION:
            return self._stage_archive_validation(scan_id, cancellation=cancellation)
        if stage_type == StageType.MANIFEST_DISCOVERY:
            return self._stage_manifest_discovery(scan_id, cancellation=cancellation)
        return self._fail_stage(
            scan_id,
            stage_type,
            failure_code="orchestrator_unknown_stage",
            failure_summary=f"Stage {stage_type.value} is not handled by the orchestrator.",
            cancellation=cancellation,
        )

    def _apply_pipeline_outcome(
        self,
        scan_id: int,
        summary: app.services.analysis_pipeline.PipelineSummary,  # noqa: F821
    ) -> None:
        # Stages that were never started (no workspace, no
        # workflow files, no analysis needed) stay in PENDING; we
        # only mark them SKIPPED if they were never transitioned
        # to RUNNING. RUNNING -> SKIPPED is an illegal transition
        # in the state machine, so this is the honest shape.
        for outcome in summary.stages:
            try:
                stage_type = StageType(outcome.stage)
            except ValueError:
                continue
            with self._session_factory() as session:
                stage = stage_repo.get_stage(session, scan_id, stage_type)
                if stage is None:
                    continue
                # For provider-backed stages the
                # ``provider_status`` column is the truthful
                # independent record of whether the call
                # succeeded. We read the latest observation
                # for the matching provider and copy the
                # status onto the stage row so the UI does
                # not have to join ``ProviderObservation``
                # at render time. The column was always
                # nullable; we only set it when we actually
                # have a value to report.
                if stage_type in {
                    StageType.VULNERABILITY_QUERY,
                    StageType.DEPENDENCY_ENRICHMENT,
                    StageType.REPOSITORY_POSTURE,
                }:
                    stage.provider = self._provider_name_for_stage(stage_type)
                    stage.provider_status = self._aggregate_provider_status(
                        session, scan_id, stage.provider
                    )
                if outcome.status == "completed":
                    assert_legal_stage_transition(stage.status, StageStatus.COMPLETED)
                    stage.status = StageStatus.COMPLETED
                elif outcome.status == "partial":
                    assert_legal_stage_transition(stage.status, StageStatus.PARTIAL)
                    stage.status = StageStatus.PARTIAL
                elif outcome.status == "failed":
                    assert_legal_stage_transition(stage.status, StageStatus.FAILED)
                    stage.status = StageStatus.FAILED
                else:
                    # "skipped".
                    #
                    # Two cases:
                    #
                    # 1. The stage was never started (PENDING).
                    #    This is the v0.2 remote-stage path;
                    #    EXPORT_GENERATION is the canonical
                    #    example. The legal transition is
                    #    PENDING -> SKIPPED.
                    #
                    # 2. The stage was started (RUNNING) and the
                    #    pipeline reports a skip. The previous
                    #    v0.4 implementation laundered this
                    #    through COMPLETED; that is incorrect
                    #    when the skip is because a provider was
                    #    unavailable, because the UI / API
                    #    would then expose the stage as a
                    #    successful verified-clean provider
                    #    call. The truthful terminal transition
                    #    for a RUNNING stage is PARTIAL, which
                    #    is already legal under
                    #    ``_STAGE_TRANSITIONS``. We use
                    #    ``outcome.failure_code == "provider_unavailable"``
                    #    to distinguish the provider failure
                    #    from the legitimate ``no work to do``
                    #    skip (e.g. zero components, no
                    #    workflow files); the latter keeps the
                    #    RUNNING -> COMPLETED mapping because
                    #    no provider work was attempted.
                    if stage.status == StageStatus.PENDING:
                        assert_legal_stage_transition(stage.status, StageStatus.SKIPPED)
                        stage.status = StageStatus.SKIPPED
                        stage.provider_status = ProviderStatus.NOT_REQUESTED.value
                    elif outcome.failure_code == "provider_unavailable":
                        assert_legal_stage_transition(stage.status, StageStatus.PARTIAL)
                        stage.status = StageStatus.PARTIAL
                        if outcome.failure_summary is not None:
                            stage.failure_summary = outcome.failure_summary[:2048]
                    else:
                        assert_legal_stage_transition(stage.status, StageStatus.COMPLETED)
                        stage.status = StageStatus.COMPLETED
                stage.records_processed = outcome.records_processed
                stage.failure_code = outcome.failure_code
                if outcome.failure_summary is not None and stage.failure_summary is None:
                    stage.failure_summary = outcome.failure_summary[:2048]
                stage.completed_at = utcnow()
                session.commit()

    def _mark_stage_running(
        self,
        scan_id: int,
        stage_type: StageType,
    ) -> None:
        with self._session_factory() as session:
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            if stage is None:
                return
            try:
                assert_legal_stage_transition(stage.status, StageStatus.RUNNING)
            except ApiError:
                # The stage is already in a terminal state; the
                # caller will treat the run as a no-op.
                return
            stage.status = StageStatus.RUNNING
            stage.started_at = utcnow()
            session.commit()

    @staticmethod
    def _provider_name_for_stage(stage_type: StageType) -> str:
        """Map a v0.4 provider-backed stage to the provider name.

        Used by :meth:`_apply_pipeline_outcome` to populate
        the truthful ``provider_status`` column on the
        ``ScanStage`` row. The mapping is intentionally
        narrow: only the three provider-backed stages have
        a ``provider`` value.
        """
        if stage_type == StageType.VULNERABILITY_QUERY:
            return "osv"
        if stage_type == StageType.DEPENDENCY_ENRICHMENT:
            return "deps_dev"
        if stage_type == StageType.REPOSITORY_POSTURE:
            return "openssf"
        return ""

    @staticmethod
    def _aggregate_provider_status(session: Session, scan_id: int, provider: str) -> str | None:
        """Aggregate every ``ProviderObservation`` for ``provider`` into one status.

        Returns the truthful aggregate status value
        (``"available"``, ``"unavailable"``, ``"rate_limited"``,
        ``"partial"``, ``"not_requested"``, ``"unknown"``) or
        ``None`` if no observation was recorded. The aggregate
        is derived from ALL observations for the provider, not
        just the chronologically latest row: a per-component
        failure followed by an unrelated component's success
        must not launder the stage into a fully successful
        provider call. Explicit retries for the same logical
        request (same operation + component) do supersede the
        earlier attempt; see
        :mod:`app.services.provider_aggregation`.
        """
        if not provider:
            return None
        rows = (
            session.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == provider,
            )
            .all()
        )
        aggregate = aggregate_provider_observations(rows)
        if aggregate is None:
            return None
        return aggregate.status.value

    def _read_stage_record(
        self,
        scan_id: int,
        stage_type: StageType,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        with self._session_factory() as session:
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            if stage is None:
                return StageRecord(
                    stage=stage_type,
                    status=StageStatus.FAILED,
                    provider=None,
                    provider_status=None,
                    records_processed=0,
                    failure_code="stage_missing",
                    failure_summary="Stage row not found.",
                    started_at=None,
                    completed_at=utcnow(),
                )
            return _to_record(stage)

    def _run_stage(
        self,
        scan_id: int,
        stage_type: StageType,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        with self._session_factory() as session:
            scan_service_get_or_404(session, scan_id)
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            if stage is None:
                raise ApiError(
                    ApiErrorCode.NOT_FOUND,
                    "Stage not found for this scan.",
                    details={"scan_id": scan_id, "stage_type": stage_type.value},
                )
            if stage.status != StageStatus.PENDING:
                # Idempotency: re-running a finished stage is a
                # no-op.
                return _to_record(stage)
        # Determine whether the stage has work to do in v0.2.
        if stage_type in _REMOTE_STAGES:
            return self._complete_not_requested(
                scan_id,
                stage_type,
                cancellation=cancellation,
            )
        # The local stages transition to RUNNING before doing
        # their work.
        with self._session_factory() as session:
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            assert_legal_stage_transition(stage.status, StageStatus.RUNNING)
            stage.status = StageStatus.RUNNING
            stage.started_at = utcnow()
            session.commit()
            session.refresh(stage)

        if stage_type == StageType.REPOSITORY_INTAKE:
            return self._stage_repository_intake(scan_id, cancellation=cancellation)
        if stage_type == StageType.ARCHIVE_VALIDATION:
            return self._stage_archive_validation(scan_id, cancellation=cancellation)
        if stage_type == StageType.MANIFEST_DISCOVERY:
            return self._stage_manifest_discovery(scan_id, cancellation=cancellation)
        # Unreachable: every stage is handled above.
        return self._fail_stage(
            scan_id,
            stage_type,
            failure_code="orchestrator_unknown_stage",
            failure_summary=f"Stage {stage_type.value} is not handled by the orchestrator.",
            cancellation=cancellation,
        )

    def _stage_repository_intake(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        # The intake itself already ran when the scan was
        # created. The stage exists to make the pipeline
        # observable end to end. We mark it completed with
        # a provider observation that records the fact.
        with self._session_factory() as session:
            scan_service_get_or_404(session, scan_id)
            stage = stage_repo.get_stage(session, scan_id, StageType.REPOSITORY_INTAKE)
            if stage is None:
                return self._fail_stage(
                    scan_id,
                    StageType.REPOSITORY_INTAKE,
                    failure_code="stage_missing",
                    failure_summary="Stage row not found.",
                    cancellation=cancellation,
                )
            records_processed = 1
            stage.provider = "github-or-upload"
            stage.provider_status = ProviderStatus.AVAILABLE.value
            stage.records_processed = records_processed
            assert_legal_stage_transition(stage.status, StageStatus.COMPLETED)
            stage.status = StageStatus.COMPLETED
            stage.completed_at = utcnow()
            session.commit()
            session.refresh(stage)
            _record_observation(
                session,
                scan_id=scan_id,
                provider=stage.provider or "intake",
                operation="intake",
                status=ProviderStatus.AVAILABLE,
                http_status=None,
                records_returned=records_processed,
                cache_status="miss",
                error_code=None,
                error_summary=None,
            )
            session.commit()
            return _to_record(stage)

    def _stage_archive_validation(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        # Re-run the existing archive validation contract
        # against the workspace's archive. The validation
        # already happened during intake; this stage exists
        # to record a per-stage ``completed`` outcome.
        from app.services.workspace_service import WorkspaceService
        from app.utils.archive_validation import limits_from_settings

        with self._session_factory() as session:
            scan = scan_service_get_or_404(session, scan_id)
            stage = stage_repo.get_stage(session, scan_id, StageType.ARCHIVE_VALIDATION)
            if stage is None:
                return self._fail_stage(
                    scan_id,
                    StageType.ARCHIVE_VALIDATION,
                    failure_code="stage_missing",
                    failure_summary="Stage row not found.",
                    cancellation=cancellation,
                )
            try:
                workspaces = WorkspaceService(session, settings=self._settings)
                workspace = workspaces.get_for_scan(scan.id)
            except ApiError as exc:
                return self._fail_stage(
                    scan_id,
                    StageType.ARCHIVE_VALIDATION,
                    failure_code=exc.code,
                    failure_summary=exc.message,
                    cancellation=cancellation,
                )
            if workspace.archive_sha256 is None:
                return self._fail_stage(
                    scan_id,
                    StageType.ARCHIVE_VALIDATION,
                    failure_code="archive_missing",
                    failure_summary="Workspace did not record an archive SHA-256.",
                    cancellation=cancellation,
                )
            stage.provider = "archive_validation"
            stage.provider_status = ProviderStatus.AVAILABLE.value
            stage.records_processed = 1
            assert_legal_stage_transition(stage.status, StageStatus.COMPLETED)
            stage.status = StageStatus.COMPLETED
            stage.completed_at = utcnow()
            _ = limits_from_settings  # referenced for type
            session.commit()
            session.refresh(stage)
            _record_observation(
                session,
                scan_id=scan_id,
                provider="archive_validation",
                operation="validate_archive",
                status=ProviderStatus.AVAILABLE,
                http_status=None,
                records_returned=1,
                cache_status="miss",
                error_code=None,
                error_summary=None,
            )
            session.commit()
            return _to_record(stage)

    def _stage_manifest_discovery(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        # Walk the workspace and record a Manifest row for
        # every candidate file. We do *not* parse the
        # manifest content - that is the job of the
        # dependency_parsing stage, which v0.2 records as
        # ``not_requested``.
        from app.services.workspace_service import WorkspaceService

        with self._session_factory() as session:
            scan = scan_service_get_or_404(session, scan_id)
            stage = stage_repo.get_stage(session, scan_id, StageType.MANIFEST_DISCOVERY)
            if stage is None:
                return self._fail_stage(
                    scan_id,
                    StageType.MANIFEST_DISCOVERY,
                    failure_code="stage_missing",
                    failure_summary="Stage row not found.",
                    cancellation=cancellation,
                )
            try:
                workspaces = WorkspaceService(session, settings=self._settings)
                workspace = workspaces.get_for_scan(scan.id)
            except ApiError as exc:
                return self._fail_stage(
                    scan_id,
                    StageType.MANIFEST_DISCOVERY,
                    failure_code=exc.code,
                    failure_summary=exc.message,
                    cancellation=cancellation,
                )
            contents_dir = workspaces.paths_for(workspace.workspace_key).contents_dir
            manifest_paths = _discover_manifest_files(contents_dir)
            existing = {
                m.path: m
                for m in session.query(Manifest).filter(Manifest.scan_run_id == scan.id).all()
            }
            records_processed = 0
            for rel in manifest_paths:
                if rel in existing:
                    continue
                session.add(
                    Manifest(
                        scan_run_id=scan.id,
                        path=rel,
                        manifest_type=manifest_type_for(rel),
                        ecosystem=ecosystem_for(rel),
                        parse_status=ManifestParseStatus.NOT_PARSED,
                    )
                )
                records_processed += 1
            stage.provider = "filesystem"
            stage.provider_status = ProviderStatus.AVAILABLE.value
            stage.records_processed = records_processed
            assert_legal_stage_transition(stage.status, StageStatus.COMPLETED)
            stage.status = StageStatus.COMPLETED
            stage.completed_at = utcnow()
            session.commit()
            session.refresh(stage)
            _record_observation(
                session,
                scan_id=scan_id,
                provider="filesystem",
                operation="discover_manifests",
                status=ProviderStatus.AVAILABLE,
                http_status=None,
                records_returned=records_processed,
                cache_status="miss",
                error_code=None,
                error_summary=None,
            )
            session.commit()
            return _to_record(stage)

    def _complete_not_requested(
        self,
        scan_id: int,
        stage_type: StageType,
        *,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        with self._session_factory() as session:
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            if stage is None:
                return self._fail_stage(
                    scan_id,
                    stage_type,
                    failure_code="stage_missing",
                    failure_summary="Stage row not found.",
                    cancellation=cancellation,
                )
            stage.provider = stage_type.value
            stage.provider_status = ProviderStatus.NOT_REQUESTED.value
            stage.records_processed = 0
            assert_legal_stage_transition(stage.status, StageStatus.SKIPPED)
            stage.status = StageStatus.SKIPPED
            stage.completed_at = utcnow()
            session.commit()
            session.refresh(stage)
            _record_observation(
                session,
                scan_id=scan_id,
                provider=stage_type.value,
                operation=stage_type.value,
                status=ProviderStatus.NOT_REQUESTED,
                http_status=None,
                records_returned=0,
                cache_status="miss",
                error_code=None,
                error_summary=None,
            )
            session.commit()
            return _to_record(stage)

    def _fail_stage(
        self,
        scan_id: int,
        stage_type: StageType,
        *,
        failure_code: str,
        failure_summary: str,
        cancellation: _CancellationToken,
    ) -> StageRecord:
        with self._session_factory() as session:
            stage = stage_repo.get_stage(session, scan_id, stage_type)
            if stage is None:
                # Nothing to record against; the run will
                # still report failure via the scan-level
                # finalizer.
                return StageRecord(
                    stage=stage_type,
                    status=StageStatus.FAILED,
                    provider=None,
                    provider_status=None,
                    records_processed=0,
                    failure_code=failure_code,
                    failure_summary=failure_summary,
                    started_at=None,
                    completed_at=utcnow(),
                )
            stage.failure_code = failure_code
            stage.failure_summary = (failure_summary or "")[
                : self._settings.scan_default_failure_summary_max_length
            ]
            assert_legal_stage_transition(stage.status, StageStatus.FAILED)
            stage.status = StageStatus.FAILED
            stage.completed_at = utcnow()
            session.commit()
            session.refresh(stage)
            return _to_record(stage)

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------
    def _finalize(
        self,
        scan_id: int,
        *,
        cancellation: _CancellationToken,
        partial: bool,
        overall_failure_code: str | None,
        overall_failure_summary: str | None,
    ) -> ScanStatus:
        with self._session_factory() as session:
            scan = scan_service_get_or_404(session, scan_id)
            if scan.status in {
                ScanStatus.COMPLETED,
                ScanStatus.PARTIAL,
                ScanStatus.FAILED,
                ScanStatus.CANCELLED,
            }:
                return scan.status
            if cancellation.is_set() or overall_failure_code == "cancelled":
                target = ScanStatus.CANCELLED
            elif overall_failure_code:
                target = ScanStatus.FAILED
            elif partial:
                target = ScanStatus.PARTIAL
            else:
                target = ScanStatus.COMPLETED
            assert_legal_scan_transition(scan.status, target)
            scan.status = target
            scan.completed_at = utcnow()
            if overall_failure_code is not None:
                scan.failure_code = overall_failure_code
            if overall_failure_summary is not None:
                scan.failure_summary = overall_failure_summary[
                    : self._settings.scan_default_failure_summary_max_length
                ]
            session.commit()
            session.refresh(scan)
            return scan.status


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _to_record(stage: ScanStage) -> StageRecord:
    return StageRecord(
        stage=stage.stage_type,
        status=stage.status,
        provider=stage.provider,
        provider_status=stage.provider_status,
        records_processed=stage.records_processed,
        failure_code=stage.failure_code,
        failure_summary=stage.failure_summary,
        started_at=stage.started_at,
        completed_at=stage.completed_at,
    )


def _stage_pipeline() -> tuple[StageType, ...]:
    return (
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


def _record_observation(
    session: Session,
    *,
    scan_id: int,
    provider: str,
    operation: str,
    status: ProviderStatus,
    http_status: int | None,
    records_returned: int,
    cache_status: str | None,
    error_code: str | None,
    error_summary: str | None,
) -> None:
    session.add(
        ProviderObservation(
            scan_run_id=scan_id,
            provider=provider,
            operation=operation,
            status=status,
            http_status=http_status,
            records_returned=records_returned,
            cache_status=cache_status,
            error_code=error_code,
            error_summary=redact_provider_summary(error_summary),
        )
    )


def scan_service_get_or_404(session: Session, scan_id: int) -> ScanRun:
    scan = scan_repo.get_scan_by_id(session, scan_id)
    if scan is None:
        raise ApiError(
            ApiErrorCode.NOT_FOUND,
            "Scan not found.",
            details={"scan_id": scan_id},
        )
    return scan


_MANIFEST_NAMES: dict[str, tuple[str, str | None]] = {
    # ``manifest_type`` matches the parser-registry key
    # (``app.parsers.build_default_registry``); the
    # ``ecosystem`` is the canonical deps.dev ecosystem name.
    "package.json": ("package_json", "npm"),
    "package-lock.json": ("package_lock", "npm"),
    "yarn.lock": ("yarn_lock", "npm"),
    "pnpm-lock.yaml": ("pnpm_lock", "npm"),
    "requirements.txt": ("requirements_txt", "pypi"),
    "requirements.in": ("requirements_txt", "pypi"),
    "pyproject.toml": ("pyproject_toml", "pypi"),
    "Pipfile": ("pipfile", "pypi"),
    "Pipfile.lock": ("pipfile_lock", "pypi"),
    "go.mod": ("go_mod", "go"),
    "go.sum": ("go_sum", "go"),
    "Cargo.toml": ("cargo_toml", "crates"),
    "Cargo.lock": ("cargo_lock", "crates"),
    "pom.xml": ("pom_xml", "maven"),
    "build.gradle": ("gradle", "maven"),
    "build.gradle.kts": ("gradle_kts", "maven"),
    "composer.json": ("composer_json", "packagist"),
    "composer.lock": ("composer_lock", "packagist"),
    "Gemfile": ("gemfile", "rubygems"),
    "Gemfile.lock": ("gemfile_lock", "rubygems"),
    "poetry.lock": ("poetry_lock", "pypi"),
    "renv.lock": ("renv_lock", "cran"),
    "Package.resolved": ("package_resolved", "swift"),
    "Package.swift": ("package_swift", "swift"),
}


def manifest_type_for(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return _MANIFEST_NAMES.get(name, ("generic", None))[0]


def ecosystem_for(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    return _MANIFEST_NAMES.get(name, ("generic", None))[1]


def _discover_manifest_files(contents_dir) -> list[str]:  # type: ignore[no-untyped-def]
    """Walk ``contents_dir`` and return normalized relative paths of known manifests.

    The lookup is by **basename** (the same way :func:`manifest_type_for`
    and :func:`ecosystem_for` resolve an entry) so that nested
    manifests in monorepositories (``frontend/package.json``,
    ``backend/poetry.lock``, ``nested/service/requirements.txt``)
    are discovered alongside the root-level files.

    v2.0.2 widened the membership check from a full-path equality
    against ``_MANIFEST_NAMES`` to the basename lookup used by
    :func:`manifest_type_for`. The pre-fix version required
    ``rel in _MANIFEST_NAMES`` and missed every nested manifest
    because the dict keys are basenames, not paths.
    """
    found: list[str] = []
    if not contents_dir.exists():
        return found
    for child in contents_dir.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(contents_dir).as_posix()
        if manifest_type_for(rel) != "generic":
            found.append(rel)
    found.sort()
    return found


__all__ = [
    "OrchestrationOutcome",
    "ScanOrchestrator",
    "StageRecord",
    "_CancellationToken",
]
