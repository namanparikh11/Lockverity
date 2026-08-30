"""The per-scan analysis pipeline.

The :class:`AnalysisPipeline` does the real analysis work that
the orchestrator used to record as ``not_requested`` in v0.2. It
is invoked from :class:`app.services.orchestrator_service.ScanOrchestrator`
once the local stages (intake, archive validation, manifest
discovery) are complete.

The pipeline is intentionally split from the orchestrator:

- The orchestrator owns the *state machine* (queued -> running ->
  completed / partial / failed / cancelled) and the cross-cutting
  concerns (provider observations, cancellation, failure
  recording).
- This module owns the *work*: parsing manifests, building
  components and edges, running analyzers, evaluating rules,
  persisting findings, and (in v0.4) calling the external
  providers (OSV, deps.dev, OpenSSF Scorecard) for the
  ``dependency_enrichment``, ``vulnerability_query``, and
  ``repository_posture`` stages.

The pipeline degrades gracefully: any network-dependent stage
records its failure as a :class:`ProviderObservation` with
status ``unavailable`` or ``partial``. Local work (parsers,
manifest discovery, GitHub Actions rules, finding
reconciliation) always runs to completion; it never makes
network calls.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.analyzers.dependency_graph import build_dependency_components
from app.analyzers.github_actions import GitHubActionsAnalyzer
from app.models.component import Component
from app.models.dependency_edge import DependencyEdge
from app.models.finding import FindingCategory
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.scan_run import ScanRun
from app.parsers import get_registry
from app.providers.results import (
    AnalyzerResult,
    FindingEvidence,
    ParserResult,
    ParserWarning,
)
from app.providers.selection import ExternalEvidenceProviders
from app.rules import default_rules
from app.services import write_service
from app.services.provider_aggregation import aggregate_provider_observations
from app.services.provider_service import (
    DEPS_DEV_PROVIDER,
    OP_DEPS_DEV_ENRICH,
    OP_OSV_QUERY,
    OP_SCORECARD_READ,
    OSV_PROVIDER,
    SCORECARD_PROVIDER,
    EnrichmentLookup,
    PostureLookup,
    ProviderService,
    VulnerabilityLookup,
)
from app.services.workspace_service import WorkspaceService
from app.utils.errors import ApiError, ApiErrorCode
from app.utils.redaction import redact_provider_summary

logger = logging.getLogger("lockverity.analysis")

# Stages implemented in this module. Kept in module scope so a
# test can stub a single method without touching the orchestrator.
MAX_COMPONENT_BYTES = 5_000_000  # 5 MiB cap per file (defensive)
MAX_TOTAL_BYTES = 50_000_000  # 50 MiB cap per scan (defensive)


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """The summary a pipeline stage returns to the orchestrator."""

    stage: str
    status: str  # "completed" | "partial" | "failed" | "skipped"
    records_processed: int
    failure_code: str | None = None
    failure_summary: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    """The full result of running the analysis pipeline."""

    stages: tuple[StageOutcome, ...]
    components_persisted: int
    edges_persisted: int
    findings_persisted: int
    findings_skipped: int
    component_advisories_persisted: int
    vulnerabilities_persisted: int
    enrichments_persisted: int
    posture_findings_persisted: int


class AnalysisPipeline:
    """Drives one scan through dependency parsing, analysis, and rules."""

    def __init__(
        self,
        *,
        session_factory,
        settings,
        provider_service_factory: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        # ``provider_service_factory`` is a callable
        # ``(session) -> ProviderService``. The factory is
        # optional so the existing v0.3 call sites still work;
        # when ``None`` we build a default factory that opens a
        # real :class:`ProviderService` per session.
        self._provider_service_factory = (
            provider_service_factory or _default_provider_service_factory(settings=settings)
        )

    def run(
        self,
        scan_id: int,
        *,
        provider_selection: ExternalEvidenceProviders | None = None,
    ) -> PipelineSummary:
        """Run the full analysis pipeline against an already-queued scan.

        The pipeline is robust against missing artefacts: if a
        workspace is not ``ready`` or has no manifests, the
        corresponding stages are recorded as ``skipped`` and the
        pipeline still finalizes the scan.
        """
        provider_selection = provider_selection or ExternalEvidenceProviders()
        stages: list[StageOutcome] = []
        components_persisted = 0
        edges_persisted = 0
        findings_persisted = 0
        findings_skipped = 0
        component_advisories_persisted = 0
        vulnerabilities_persisted = 0
        enrichments_persisted = 0
        posture_findings_persisted = 0

        # --- dependency_parsing -----------------------------------------
        (
            outcome,
            components_persisted,
            edges_persisted,
        ) = self._stage_dependency_parsing(scan_id)
        stages.append(outcome)

        # --- dependency_enrichment (deps.dev) ---------------------------
        if provider_selection.deps_dev:
            outcome, persisted_enrichments = self._stage_dependency_enrichment(scan_id)
        else:
            outcome, persisted_enrichments = self._stage_provider_not_requested(
                scan_id,
                provider=DEPS_DEV_PROVIDER,
                operation=OP_DEPS_DEV_ENRICH,
                stage="dependency_enrichment",
                reason="disabled_by_operator",
            )
        stages.append(outcome)
        enrichments_persisted = persisted_enrichments

        # --- vulnerability_query (OSV) ----------------------------------
        if provider_selection.osv:
            outcome, persisted_vulns = self._stage_vulnerability_query(scan_id)
        else:
            outcome, persisted_vulns = self._stage_provider_not_requested(
                scan_id,
                provider=OSV_PROVIDER,
                operation=OP_OSV_QUERY,
                stage="vulnerability_query",
                reason="disabled_by_operator",
            )
        stages.append(outcome)
        vulnerabilities_persisted = persisted_vulns

        # --- workflow_analysis ------------------------------------------
        outcome, findings_persisted, findings_skipped = self._stage_workflow_analysis(scan_id)
        stages.append(outcome)

        # --- repository_posture (OpenSSF Scorecard) ---------------------
        if provider_selection.openssf:
            outcome, persisted_posture = self._stage_repository_posture(scan_id)
        else:
            outcome, persisted_posture = self._stage_openssf_not_requested(scan_id)
        stages.append(outcome)
        posture_findings_persisted = persisted_posture

        # --- finding_reconciliation -------------------------------------
        outcome, more_persisted, more_skipped, more_advisories = self._stage_finding_reconciliation(
            scan_id
        )
        stages.append(outcome)
        findings_persisted += more_persisted
        findings_skipped += more_skipped
        component_advisories_persisted += more_advisories

        return PipelineSummary(
            stages=tuple(stages),
            components_persisted=components_persisted,
            edges_persisted=edges_persisted,
            findings_persisted=findings_persisted,
            findings_skipped=findings_skipped,
            component_advisories_persisted=component_advisories_persisted,
            vulnerabilities_persisted=vulnerabilities_persisted,
            enrichments_persisted=enrichments_persisted,
            posture_findings_persisted=posture_findings_persisted,
        )

    def _stage_provider_not_requested(
        self,
        scan_id: int,
        *,
        provider: str,
        operation: str,
        stage: str,
        reason: str,
    ) -> tuple[StageOutcome, int]:
        """Persist a zero-call not-requested outcome for one provider."""

        with self._session_factory() as session:
            _get_scan_or_404(session, scan_id)
            session.add(
                ProviderObservation(
                    scan_run_id=scan_id,
                    provider=provider,
                    operation=operation,
                    status=ProviderStatus.NOT_REQUESTED,
                    requested_at=None,
                    completed_at=None,
                    http_status=None,
                    records_returned=0,
                    cache_status=None,
                    retry_after=None,
                    error_code=reason,
                    error_summary=None,
                    evidence_json=None,
                )
            )
            session.commit()
        return (
            StageOutcome(
                stage=stage,
                status="skipped",
                records_processed=0,
            ),
            0,
        )

    def _stage_openssf_not_requested(self, scan_id: int) -> tuple[StageOutcome, int]:
        """Skip OpenSSF without constructing its service or consulting its cache."""

        from app.models.repository import Repository, RepositorySourceType

        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            repository = session.get(Repository, scan.repository_id)
            is_archive = bool(
                repository is not None
                and repository.source_type == RepositorySourceType.UPLOADED_ARCHIVE
            )
        return self._stage_provider_not_requested(
            scan_id,
            provider=SCORECARD_PROVIDER,
            operation=OP_SCORECARD_READ,
            stage="repository_posture",
            reason="not_applicable" if is_archive else "disabled_by_operator",
        )

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------
    def _stage_dependency_parsing(self, scan_id: int) -> tuple[StageOutcome, int, int]:
        """Parse every discovered manifest, build components and edges.

        This stage is purely local. It does not call any provider.
        A failure here is recorded as ``failed`` and the
        orchestrator short-circuits the rest of the pipeline.
        """
        files = self._collect_workspace_files(scan_id)
        if not files:
            return (
                StageOutcome(
                    stage="dependency_parsing",
                    status="skipped",
                    records_processed=0,
                    failure_summary="No files were available in the workspace.",
                ),
                0,
                0,
            )
        registry = get_registry()
        envelopes: list[dict[str, Any]] = []
        manifests_by_path: dict[str, dict[str, Any]] = {}
        total_warnings = 0
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            manifests = session.query(Manifest).filter(Manifest.scan_run_id == scan.id).all()
            for manifest in manifests:
                if manifest.parse_status == ManifestParseStatus.PARSED:
                    continue
                content = self._read_manifest_bytes(scan_id, manifest.path)
                if content is None:
                    continue
                registration = registry.get(manifest.manifest_type)
                if registration is None:
                    # No parser registered for this manifest
                    # type. Mark as ``partial`` so the UI can
                    # show the manifest as acknowledged but not
                    # parsed; a per-manifest ``SKIPPED`` status
                    # does not exist in the enum.
                    write_service.update_manifest_parse_status(
                        session,
                        manifest_id=manifest.id,
                        status=ManifestParseStatus.PARTIAL,
                    )
                    continue
                try:
                    result: ParserResult[list[dict[str, Any]]] = registration.parser.parse(
                        content=content, path=manifest.path
                    )
                except Exception as exc:
                    logger.warning(
                        "manifest parse failed scan=%s path=%s: %s",
                        scan_id,
                        manifest.path,
                        exc,
                    )
                    write_service.update_manifest_parse_status(
                        session,
                        manifest_id=manifest.id,
                        status=ManifestParseStatus.FAILED,
                    )
                    session.add(
                        ProviderObservation(
                            scan_run_id=scan_id,
                            provider=manifest.manifest_type,
                            operation="parse_manifest",
                            status=ProviderStatus.UNAVAILABLE,
                            records_returned=0,
                            cache_status="miss",
                            error_code="manifest_parse_failed",
                            error_summary=redact_provider_summary(str(exc)),
                        )
                    )
                    total_warnings += 1
                    continue
                sha = hashlib.sha256(content).hexdigest()
                write_service.update_manifest_parse_status(
                    session,
                    manifest_id=manifest.id,
                    status=ManifestParseStatus.PARSED,
                    content_sha256=sha,
                    parse_warning_count=len(result.warnings),
                )
                envelopes.append(
                    {
                        "manifest": {
                            "id": manifest.id,
                            "path": manifest.path,
                            "manifest_type": manifest.manifest_type,
                            "ecosystem": manifest.ecosystem,
                        },
                        "records": list(result.data),
                    }
                )
                manifests_by_path[manifest.path] = {
                    "manifest_id": manifest.id,
                    "manifest_type": manifest.manifest_type,
                    "ecosystem": manifest.ecosystem,
                }
                total_warnings += len(result.warnings)
                for warning in result.warnings:
                    if not isinstance(warning, ParserWarning):
                        continue
                    session.add(
                        ProviderObservation(
                            scan_run_id=scan_id,
                            provider=manifest.manifest_type,
                            operation="parse_manifest",
                            status=ProviderStatus.PARTIAL,
                            records_returned=0,
                            cache_status="miss",
                            error_code=warning.code,
                            error_summary=redact_provider_summary(warning.message),
                        )
                    )
            try:
                components, edges, graph_findings = build_dependency_components(
                    envelopes,
                    manifests_by_path=manifests_by_path,
                )
            except Exception as exc:
                session.commit()
                logger.exception("dependency graph build failed for scan %s", scan_id)
                return (
                    StageOutcome(
                        stage="dependency_parsing",
                        status="failed",
                        records_processed=0,
                        failure_code="dependency_graph_failed",
                        failure_summary=str(exc)[:512],
                    ),
                    0,
                    0,
                )
            # Stitch the manifest_id onto each component record
            # using the envelope. The dependency-graph analyzer
            # returns components keyed by package_name without
            # the foreign-key reference, so we back-fill it here
            # rather than mutating the analyzer.
            envelope_by_path = {e["manifest"]["path"]: e["manifest"] for e in envelopes}
            for component in components:
                path = component.get("manifest_path")
                envelope = envelope_by_path.get(path) if path else None
                if envelope is not None:
                    component["manifest_id"] = envelope["id"]
            # Persist components.
            component_ids: list[int] = []
            if components:
                component_ids = write_service.upsert_components(
                    session, scan_run_id=scan_id, records=components
                )
            # Persist edges.
            edges_persisted = 0
            if edges:
                edges_persisted = write_service.upsert_dependency_edges(
                    session, scan_run_id=scan_id, edges=edges
                )
            # The graph analyzer also emits its own LOCK-VULN-*
            # findings (e.g. missing lockfile). Persist them too.
            _more_persisted, _more_skipped = 0, 0
            if graph_findings:
                _more_persisted, _more_skipped = write_service.upsert_findings(
                    session,
                    scan_run_id=scan_id,
                    repository_id=scan.repository_id,
                    records=graph_findings,
                    default_category="vulnerability",
                )
            session.add(
                ProviderObservation(
                    scan_run_id=scan_id,
                    provider="dependency_parsing",
                    operation="build_graph",
                    status=ProviderStatus.AVAILABLE,
                    records_returned=len(component_ids),
                    cache_status="miss",
                    error_code=None,
                    error_summary=None,
                )
            )
            session.commit()
            # ``components`` is unused in the return value but
            # reading it once forces evaluation in case we want
            # to log it later. Keep the explicit name binding.
            _ = components
            return (
                StageOutcome(
                    stage="dependency_parsing",
                    status="completed",
                    records_processed=len(component_ids),
                    failure_summary=(
                        f"{total_warnings} parser warnings" if total_warnings else None
                    ),
                ),
                len(component_ids),
                edges_persisted,
            )

    def _stage_vulnerability_query(self, scan_id: int) -> tuple[StageOutcome, int]:
        """Call OSV for every component and persist advisories.

        v0.4 wires the existing :class:`OsvVulnerabilityProvider`
        into the normal pipeline. A provider failure is recorded
        as a :class:`ProviderStatus.UNAVAILABLE` observation;
        the local findings (rule engine + GitHub Actions) still
        run to completion in later stages.

        The stage is only marked ``completed`` when the provider
        returned a real successful call. An ``unavailable`` /
        ``rate_limited`` / ``partial`` provider maps to a stage
        status of ``skipped``; an exception in the provider
        itself maps to ``failed``. ``completed`` with an empty
        result list is only legal when the latest per-component
        observation for OSV is ``available`` or ``cached``.
        """
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            components = session.query(Component).filter(Component.scan_run_id == scan.id).all()
            if not components:
                # Record an honest ``not_requested`` observation
                # so the providers list always has a row for
                # OSV, matching the pre-v0.4 contract.
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="osv",
                        operation="vulnerability_query",
                        status=ProviderStatus.NOT_REQUESTED,
                        records_returned=0,
                        cache_status="miss",
                        error_code="no_components",
                        error_summary="OSV was not queried because the scan produced no components.",
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="vulnerability_query",
                        status="skipped",
                        records_processed=0,
                        failure_summary="No components were available to query.",
                    ),
                    0,
                )
            provider = self._provider_service_factory(session)
            try:
                lookups: list[VulnerabilityLookup] = provider.enrich_vulnerabilities_for_components(
                    scan_run_id=scan_id, components=components
                )
            except Exception as exc:
                logger.exception("vulnerability provider crashed for scan %s", scan_id)
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="osv",
                        operation="vulnerability_query",
                        status=ProviderStatus.UNAVAILABLE,
                        records_returned=0,
                        cache_status="miss",
                        error_code="provider_internal_error",
                        error_summary=redact_provider_summary(str(exc)),
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="vulnerability_query",
                        status="failed",
                        records_processed=0,
                        failure_code="provider_internal_error",
                        failure_summary=str(exc)[:512],
                    ),
                    0,
                )
            session.commit()
            # The aggregate over ALL OSV observations is the
            # source of truth for whether the call was a real
            # success. An ``unavailable`` / ``rate_limited`` /
            # ``partial`` observation for *any* request means
            # the stage is not ``completed``; a later
            # unrelated success must not launder an earlier
            # per-request failure into a clean result. Explicit
            # retries for the same logical request do supersede
            # the earlier attempt (see
            # :mod:`app.services.provider_aggregation`).
            osv_rows = (
                session.query(ProviderObservation)
                .filter(
                    ProviderObservation.scan_run_id == scan_id,
                    ProviderObservation.provider == "osv",
                )
                .all()
            )
            aggregate = aggregate_provider_observations(osv_rows)
            status = "completed"
            failure_summary: str | None = None
            failure_code: str | None = None
            if aggregate is not None and aggregate.degraded:
                # The provider is honest-unavailable. The
                # orchestrator must NOT mark this stage
                # ``completed``; the truthful terminal
                # transition for a running stage is
                # ``partial`` (legal under
                # ``_STAGE_TRANSITIONS``). The
                # ``failure_code`` distinguishes this from
                # the legitimate ``no components to query``
                # skip path further down.
                status = "skipped"
                failure_code = "provider_unavailable"
                failure_summary = (
                    f"OSV provider evidence degraded (aggregate "
                    f"{aggregate.status.value}); see provider observations "
                    f"for the redacted errors."
                )
            elif not lookups:
                # Successful call, zero matching advisories.
                # Honest empty state.
                failure_summary = "No OSV advisories were returned for this scan."
            return (
                StageOutcome(
                    stage="vulnerability_query",
                    status=status,
                    records_processed=len(components),
                    failure_code=failure_code,
                    failure_summary=failure_summary,
                ),
                len(lookups),
            )

    def _stage_workflow_analysis(self, scan_id: int) -> tuple[StageOutcome, int, int]:
        """Run the GitHub Actions analyzer and persist its findings."""
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            files = self._collect_workspace_files(scan_id)
            workflow_files = [
                (path, content) for path, content in files if path.startswith(".github/workflows/")
            ]
            logger.info(
                "workflow analysis: %d files, %d workflow files",
                len(files),
                len(workflow_files),
            )
            if not workflow_files:
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="github_actions",
                        operation="analyze_workflows",
                        status=ProviderStatus.NOT_REQUESTED,
                        records_returned=0,
                        cache_status="miss",
                        error_code="no_workflow_files",
                        error_summary="No workflow files were discovered.",
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="workflow_analysis",
                        status="skipped",
                        records_processed=0,
                        failure_summary="No workflow files were discovered.",
                    ),
                    0,
                    0,
                )
            analyzer = GitHubActionsAnalyzer()
            try:
                result: AnalyzerResult = analyzer.analyze(files=workflow_files, scan_run_id=scan_id)
            except Exception as exc:
                logger.exception("workflow analyzer failed for scan %s", scan_id)
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="github_actions",
                        operation="analyze_workflows",
                        status=ProviderStatus.UNAVAILABLE,
                        records_returned=0,
                        cache_status="miss",
                        error_code="workflow_analyzer_internal_error",
                        error_summary=redact_provider_summary(str(exc)),
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="workflow_analysis",
                        status="failed",
                        records_processed=0,
                        failure_code="workflow_analyzer_internal_error",
                        failure_summary=str(exc)[:512],
                    ),
                    0,
                    0,
                )
            logger.info(
                "workflow analysis: analyzer produced %d findings",
                len(result.findings),
            )
            persisted, skipped = write_service.upsert_findings(
                session,
                scan_run_id=scan_id,
                repository_id=scan.repository_id,
                records=result.findings,
                default_category="workflow",
            )
            logger.info(
                "workflow analysis: persisted=%d skipped=%d",
                persisted,
                skipped,
            )
            for warning in result.warnings:
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="github_actions",
                        operation="analyze_workflows",
                        status=ProviderStatus.PARTIAL,
                        records_returned=0,
                        cache_status="miss",
                        error_code=warning.code,
                        error_summary=redact_provider_summary(warning.message),
                    )
                )
            session.add(
                ProviderObservation(
                    scan_run_id=scan_id,
                    provider="github_actions",
                    operation="analyze_workflows",
                    status=ProviderStatus.AVAILABLE,
                    records_returned=len(workflow_files),
                    cache_status="miss",
                    error_code=None,
                    error_summary=None,
                )
            )
            session.commit()
            return (
                StageOutcome(
                    stage="workflow_analysis",
                    status="completed",
                    records_processed=len(workflow_files),
                ),
                persisted,
                skipped,
            )

    def _stage_dependency_enrichment(self, scan_id: int) -> tuple[StageOutcome, int]:
        """Call deps.dev for every component and persist enrichment.

        The result is used by the rule engine to populate the
        licence inventory and the missing-licence observations.
        Provider failures are recorded as
        :class:`ProviderStatus.UNAVAILABLE` observations; the
        local work still runs to completion in the rule engine.
        """
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            components = session.query(Component).filter(Component.scan_run_id == scan.id).all()
            if not components:
                # Record an honest ``not_requested`` observation
                # so the providers list always has a row for
                # deps.dev, matching the pre-v0.4 contract.
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="deps_dev",
                        operation="dependency_enrichment",
                        status=ProviderStatus.NOT_REQUESTED,
                        records_returned=0,
                        cache_status="miss",
                        error_code="no_components",
                        error_summary=(
                            "deps.dev was not queried because the scan produced no components."
                        ),
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="dependency_enrichment",
                        status="skipped",
                        records_processed=0,
                        failure_summary="No components were available to enrich.",
                    ),
                    0,
                )
            provider = self._provider_service_factory(session)
            try:
                lookups: list[EnrichmentLookup] = provider.enrich_components_with_deps_dev(
                    scan_run_id=scan_id, components=components
                )
            except Exception as exc:
                logger.exception("deps.dev provider crashed for scan %s", scan_id)
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="deps_dev",
                        operation="dependency_enrichment",
                        status=ProviderStatus.UNAVAILABLE,
                        records_returned=0,
                        cache_status="miss",
                        error_code="provider_internal_error",
                        error_summary=redact_provider_summary(str(exc)),
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="dependency_enrichment",
                        status="failed",
                        records_processed=0,
                        failure_code="provider_internal_error",
                        failure_summary=str(exc)[:512],
                    ),
                    0,
                )
            session.commit()
            # The aggregate over ALL deps.dev observations is
            # the source of truth for whether the enrichment
            # was a real success. A per-component failure
            # (``unavailable`` / ``rate_limited`` /
            # ``partial``) degrades the whole stage even when
            # later components succeeded; see
            # :mod:`app.services.provider_aggregation` for the
            # retry/replacement rule.
            deps_rows = (
                session.query(ProviderObservation)
                .filter(
                    ProviderObservation.scan_run_id == scan_id,
                    ProviderObservation.provider == "deps_dev",
                )
                .all()
            )
            aggregate = aggregate_provider_observations(deps_rows)
            status = "completed"
            failure_summary: str | None = (
                "No deps.dev enrichments returned." if not lookups else None
            )
            failure_code: str | None = None
            if aggregate is not None and aggregate.degraded:
                status = "skipped"
                failure_code = "provider_unavailable"
                failure_summary = (
                    f"deps.dev provider evidence degraded (aggregate "
                    f"{aggregate.status.value}); see provider observations "
                    f"for the redacted errors."
                )
            return (
                StageOutcome(
                    stage="dependency_enrichment",
                    status=status,
                    records_processed=len(components),
                    failure_code=failure_code,
                    failure_summary=failure_summary,
                ),
                len(lookups),
            )

    def _stage_repository_posture(self, scan_id: int) -> tuple[StageOutcome, int]:
        """Call OpenSSF Scorecard for a supported public GitHub source.

        Archive-only scans short-circuit to a
        :class:`ProviderStatus.NOT_REQUESTED` observation; the
        Scorecard API only knows how to look up public GitHub
        repositories. We never fabricate a Scorecard score for
        an archive.
        """
        from app.models.repository import Repository

        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            repository = session.get(Repository, scan.repository_id)
            is_archive = bool(
                repository is not None
                and getattr(repository, "source_type", None) is not None
                and repository.source_type.value == "uploaded_archive"
            )
            canonical_url = getattr(repository, "canonical_url", None) if repository else None
            provider = self._provider_service_factory(session)
            try:
                lookup: PostureLookup | None = provider.import_scorecard_for_repository(
                    scan_run_id=scan_id,
                    canonical_url=canonical_url,
                    is_archive=is_archive,
                )
            except Exception as exc:
                logger.exception("scorecard import crashed for scan %s", scan_id)
                session.add(
                    ProviderObservation(
                        scan_run_id=scan_id,
                        provider="openssf",
                        operation="scorecard",
                        status=ProviderStatus.UNAVAILABLE,
                        records_returned=0,
                        cache_status="miss",
                        error_code="provider_internal_error",
                        error_summary=redact_provider_summary(str(exc)),
                    )
                )
                session.commit()
                return (
                    StageOutcome(
                        stage="repository_posture",
                        status="failed",
                        records_processed=0,
                        failure_code="provider_internal_error",
                        failure_summary=str(exc)[:512],
                    ),
                    0,
                )
            session.commit()
            if lookup is None:
                return (
                    StageOutcome(
                        stage="repository_posture",
                        status="skipped",
                        records_processed=0,
                        failure_code="provider_unavailable",
                        failure_summary="OpenSSF Scorecard was not available for this scan.",
                    ),
                    0,
                )
            if lookup.not_applicable:
                return (
                    StageOutcome(
                        stage="repository_posture",
                        status="skipped",
                        records_processed=0,
                        failure_summary=(
                            lookup.not_applicable_reason
                            or "OpenSSF Scorecard is not applicable for this source."
                        ),
                    ),
                    0,
                )
            # 1 meta finding + 1 per check.
            persisted = 1 + len(lookup.checks)
            return (
                StageOutcome(
                    stage="repository_posture",
                    status="completed",
                    records_processed=persisted,
                ),
                persisted,
            )

    def _stage_finding_reconciliation(self, scan_id: int) -> tuple[StageOutcome, int, int, int]:
        """Run the rule engine over the per-component evidence and persist findings."""
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            components = session.query(Component).filter(Component.scan_run_id == scan.id).all()
            edges = (
                session.query(DependencyEdge).filter(DependencyEdge.scan_run_id == scan.id).all()
            )
            child_to_parents: dict[int, list[DependencyEdge]] = {}
            for edge in edges:
                child_to_parents.setdefault(edge.child_component_id, []).append(edge)

            observations = (
                session.query(ProviderObservation)
                .filter(ProviderObservation.scan_run_id == scan.id)
                .all()
            )
            rules = default_rules()
            all_evidence: list[FindingEvidence] = []
            for component in components:
                evidence = _build_evidence_envelope(
                    component=component,
                    edges=child_to_parents.get(component.id, []),
                    observations=observations,
                )
                for rule in rules:
                    if rule.category == FindingCategory.WORKFLOW.value:
                        # Workflow findings are produced by the
                        # GitHub Actions analyzer, not the
                        # dependency rule engine.
                        continue
                    try:
                        produced = rule.evaluate(
                            evidence=evidence,
                            scan_run_id=scan_id,
                            repository_id=scan.repository_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "rule %s raised on scan %s: %s",
                            rule.rule_id,
                            scan_id,
                            exc,
                        )
                        continue
                    all_evidence.extend(produced)
            persisted, skipped = write_service.upsert_findings(
                session,
                scan_run_id=scan_id,
                repository_id=scan.repository_id,
                records=all_evidence,
                default_category="vulnerability",
            )
            session.add(
                ProviderObservation(
                    scan_run_id=scan_id,
                    provider="rule_engine",
                    operation="finding_reconciliation",
                    status=ProviderStatus.AVAILABLE,
                    records_returned=len(components),
                    cache_status="miss",
                    error_code=None,
                    error_summary=None,
                )
            )
            session.commit()
            return (
                StageOutcome(
                    stage="finding_reconciliation",
                    status="completed",
                    records_processed=len(components),
                ),
                persisted,
                skipped,
                0,  # component_advisories_persisted (v0.3 keeps it 0)
            )

    # ------------------------------------------------------------------
    # Workspace helpers
    # ------------------------------------------------------------------
    def _collect_workspace_files(self, scan_id: int) -> list[tuple[str, bytes]]:
        """Read every small file under the workspace contents dir.

        Files are bounded defensively. The orchestrator never
        executes any of these files; it only reads their bytes.
        """
        with self._session_factory() as session:
            scan = _get_scan_or_404(session, scan_id)
            workspaces = WorkspaceService(session, settings=self._settings)
            try:
                workspace = workspaces.get_for_scan(scan.id)
            except ApiError:
                return []
            paths = workspaces.paths_for(workspace.workspace_key)
            contents_dir = paths.contents_dir
            if not contents_dir.exists() or not contents_dir.is_dir():
                return []
            files: list[tuple[str, bytes]] = []
            total = 0
            for child in sorted(contents_dir.rglob("*")):
                if not child.is_file():
                    continue
                size = child.stat().st_size
                if size > MAX_COMPONENT_BYTES:
                    continue
                if total + size > MAX_TOTAL_BYTES:
                    break
                rel = child.relative_to(contents_dir).as_posix()
                try:
                    content = child.read_bytes()
                except OSError:
                    continue
                total += size
                files.append((rel, content))
            return files

    def _read_manifest_bytes(self, scan_id: int, rel_path: str) -> bytes | None:
        for path, content in self._collect_workspace_files(scan_id):
            if path == rel_path:
                return content
        return None


def _build_evidence_envelope(
    *,
    component: Component,
    edges: Iterable[DependencyEdge],
    observations: Iterable[ProviderObservation],
) -> dict[str, Any]:
    """Assemble the per-component evidence envelope the rules consume."""
    edge_payloads = [
        {
            "parent_component_id": edge.parent_component_id,
            "child_component_id": edge.child_component_id,
            "relationship": edge.relationship,
            "depth": edge.depth,
        }
        for edge in edges
    ]
    observation_payloads = [
        {
            "provider": obs.provider,
            "operation": obs.operation,
            "status": obs.status.value if hasattr(obs.status, "value") else obs.status,
            "error_code": obs.error_code,
            "records_returned": obs.records_returned,
        }
        for obs in observations
    ]
    return {
        "component": {
            "id": component.id,
            "package_name": component.package_name,
            "version": component.version,
            "version_source": (
                component.version_source.value
                if hasattr(component.version_source, "value")
                else component.version_source
            ),
            "ecosystem": component.ecosystem,
            "scope": component.scope,
            "direct": component.direct,
            "development": component.development,
            "manifest_path": None,
        },
        "advisories": [],
        "dependency_paths": edge_payloads,
        "provider_observations": observation_payloads,
        "licence_assertions": [],
    }


def _get_scan_or_404(session: Session, scan_id: int) -> ScanRun:
    scan = session.get(ScanRun, scan_id)
    if scan is None:
        raise ApiError(
            ApiErrorCode.NOT_FOUND,
            "Scan not found.",
            details={"scan_id": scan_id},
        )
    return scan


__all__ = [
    "MAX_COMPONENT_BYTES",
    "MAX_TOTAL_BYTES",
    "AnalysisPipeline",
    "PipelineSummary",
    "StageOutcome",
]


def _default_provider_service_factory(*, settings):
    """Return a factory that builds a :class:`ProviderService` per session.

    The factory pattern keeps the :class:`ProviderService` out
    of the import-time dependency graph; each stage opens its
    own session and the service lives for the lifetime of the
    session.
    """
    from app.services.provider_service import ProviderService

    def _factory(session) -> ProviderService:
        return ProviderService(session, settings=settings)

    return _factory
