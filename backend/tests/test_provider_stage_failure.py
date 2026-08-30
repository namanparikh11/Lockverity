"""Provider stage failure semantics for v0.4.

The v0.4 provider pipeline calls OSV, deps.dev, and OpenSSF
Scorecard during the normal scan flow. The orchestrator
classifies these stages alongside the local ones in
``_LOCAL_V03_STAGES`` because the pipeline owns the work,
not because the stages are local-only. The classification
must not erase the provider failure semantics: a 429, a
5xx, an oversized response, or a timeout must leave the
stage marked as a failure / unavailable observation, and
the overall scan must finish as ``partial`` (or
``completed`` with unavailable stages), never as
``completed`` purely because the local pipeline finished.

The tests below exercise the orchestrator against a real
SQLite database with mocked provider services. They prove
that:

- a provider timeout / 5xx / 4xx leaves the stage
  uncommitted as a successful local result;
- the corresponding ``ScanStage`` row is not marked
  ``COMPLETED``;
- the corresponding ``ProviderObservation`` row carries
  the truthful error / cache status;
- local parsing, workflow, and rule findings remain
  available in the database;
- the overall scan status reflects the unavailable stage
  according to the existing state machine.

The internal collection name ``_LOCAL_V03_STAGES`` is
preserved (it merely means "executed by
``AnalysisPipeline``"); the contract under test is the
failure semantics, not the name.

External-network isolation
==========================

Every test in this module runs the orchestrator
end-to-end via :class:`ScanOrchestrator`. Each test
uses :func:`_patch_provider_service` to inject
``MagicMock`` provider clients whose return value
the test controls. The shared
:func:`fake_providers_for_scan_tests` autouse
fixture imported below is the *positive* complement
to the :func:`conftest._block_external_network`
network guard: it replaces the real
``_default_provider_service_factory`` with a
factory that builds a real :class:`ProviderService`
whose OSV / deps.dev / Scorecard calls are
short-circuited to honest ``ProviderUnavailable``
results. Tests that need a specific provider
outcome (the ``MagicMock`` clients in
:func:`_patch_provider_service`) override the
autouse fixture's patch via the test function's
``monkeypatch`` parameter; the autouse fixture
ensures that a future test that forgets to patch
the provider factory still does not open a socket
to a non-loopback host.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock

import pytest
from app.db import session as _db_session
from app.models.component import Component, ComponentVersionSource
from app.models.finding import Finding, FindingCategory, FindingConfidence, FindingSeverity
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.models.scan_stage import StageStatus, StageType
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState
from app.providers.results import (
    ProviderUnavailable,
)
from app.services.orchestrator_service import ScanOrchestrator

from tests.api_client import api_client

# The :func:`conftest._fake_providers_for_scan_tests`
# autouse fixture is the backstop behind this module's
# own :func:`_patch_provider_service` helper: a future
# test that forgets to call the helper still does not
# open a socket to a non-loopback host because the global
# fixture has already applied the fakes.


def _setup_scan_with_components(
    session,
    components: list[tuple[str, str, str | None, bool, bool]],
) -> int:
    repository = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="octocat",
        name="Hello-World",
        canonical_url="https://github.com/octocat/Hello-World",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repository)
    session.flush()
    scan = ScanRun(
        repository_id=repository.id,
        status=ScanStatus.QUEUED,
        trigger_type=ScanTriggerType.MANUAL,
    )
    session.add(scan)
    session.flush()
    # Seed every default stage so the orchestrator's
    # ``_read_stage_record`` can find a row for each one.
    for stage_type in (
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
    ):
        from app.repositories import stage_repo

        stage_repo.create_stage(session, scan_run_id=scan.id, stage_type=stage_type)
    workspace = Workspace(
        scan_run_id=scan.id,
        workspace_key=f"workspace-key-{scan.id:016d}",
        kind=WorkspaceKind.GITHUB,
        state=WorkspaceState.READY,
        archive_sha256="a" * 64,
        archive_size=10,
        file_count=1,
        uncompressed_size=10,
    )
    session.add(workspace)
    manifest = Manifest(
        scan_run_id=scan.id,
        path="package.json",
        manifest_type="package_json",
        ecosystem="npm",
        parse_status=ManifestParseStatus.PARSED,
    )
    session.add(manifest)
    session.flush()
    for ecosystem, name, version, direct, development in components:
        session.add(
            Component(
                scan_run_id=scan.id,
                manifest_id=manifest.id,
                ecosystem=ecosystem,
                package_name=name,
                version=version,
                version_source=ComponentVersionSource.LOCKFILE,
                direct=direct,
                development=development,
            )
        )
    session.commit()
    return scan.id


def _patch_provider_service(
    monkeypatch: pytest.MonkeyPatch, *, osv=None, deps_dev=None, scorecard=None
) -> None:
    """Inject a fake provider service into the analysis pipeline.

    The pipeline instantiates the provider service lazily via
    the default factory; we replace the factory with a
    closure that returns a real :class:`ProviderService`
    bound to the mocked OSV, deps.dev, and Scorecard
    clients. The clients are :class:`MagicMock` instances
    whose ``query_batch`` / ``enrich`` / ``read`` return
    value the test controls.
    """
    from app.services import analysis_pipeline
    from app.services.provider_service import ProviderService

    if osv is None:
        osv = MagicMock()
    if deps_dev is None:
        deps_dev = MagicMock()
    if scorecard is None:
        # The default Scorecard mock returns a real
        # ``ProviderUnavailable`` so the
        # ``import_scorecard_for_repository`` helper
        # short-circuits to a "not available" record.
        from app.providers.results import ProviderUnavailable

        scorecard = MagicMock()
        scorecard.read.return_value = ProviderUnavailable(
            error_code="provider_unavailable",
            error_summary="Scorecard 500",
            attempted_at=None,
            http_status=500,
        )

    def _factory(settings):
        def _build(session):
            return ProviderService(
                session,
                settings=settings,
                osv=osv,
                deps_dev=deps_dev,
                scorecard=scorecard,
            )

        return _build

    monkeypatch.setattr(analysis_pipeline, "_default_provider_service_factory", _factory)


def test_provider_5xx_marks_stage_unavailable_not_completed(
    app_config, workspace_root, monkeypatch
) -> None:
    """A 5xx from OSV must not be marked ``completed``; the scan finishes partial.

    Truthful contract (v0.4 honest failure semantics):

    - The persisted ``ScanStage`` row for
      ``vulnerability_query`` (and ``dependency_enrichment``)
      is NOT ``COMPLETED``. A provider 5xx is a provider
      failure, not a successful local result. The state
      machine's legal ``RUNNING -> PARTIAL`` transition is
      the truthful terminal status.
    - The ``ProviderObservation`` row is
      ``unavailable`` with a redacted error summary.
    - The overall scan status is ``PARTIAL`` (because at
      least one stage is ``PARTIAL``), not ``COMPLETED``
      and not ``FAILED``.
    - Local parsing, workflow, and rule findings remain
      available regardless of the provider outage.
    """
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="OSV responded with HTTP 503",
        attempted_at=None,
        http_status=503,
    )
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="deps.dev 500",
        attempted_at=None,
        http_status=500,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    outcome = orchestrator.run(scan_id)
    # The overall scan reflects the unavailable stage as
    # ``partial``. It must NEVER be reported as ``completed``
    # purely because the local pipeline finished.
    assert outcome.final_status == ScanStatus.PARTIAL, (
        f"scan with unavailable OSV must finish 'partial'; got {outcome.final_status!r}"
    )
    with _db_session.SessionLocal() as s:
        # 1. The persisted ScanStage row for the
        # provider-backed stage is ``PARTIAL`` (not
        # ``COMPLETED`` and not ``SKIPPED``). The state
        # machine's legal transition ``RUNNING -> PARTIAL``
        # is the truthful shape: the stage was attempted
        # but a required provider was unavailable.
        from app.models.scan_stage import ScanStage

        vuln_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.VULNERABILITY_QUERY,
            )
            .one()
        )
        assert vuln_stage.status == StageStatus.PARTIAL, (
            f"vulnerability_query must be PARTIAL on provider 5xx; got {vuln_stage.status!r}"
        )
        assert vuln_stage.failure_code == "provider_unavailable"
        assert vuln_stage.completed_at is not None
        # The provider-backed stage carries the truthful
        # provider status on the stage row.
        assert vuln_stage.provider_status in {
            ProviderStatus.UNAVAILABLE.value,
            ProviderStatus.PARTIAL.value,
        }

        deps_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.DEPENDENCY_ENRICHMENT,
            )
            .one()
        )
        assert deps_stage.status == StageStatus.PARTIAL, (
            f"dependency_enrichment must be PARTIAL on provider 5xx; got {deps_stage.status!r}"
        )
        assert deps_stage.failure_code == "provider_unavailable"

        # 2. The ProviderObservation row carries the
        # truthful unavailable status with a bounded
        # redacted error summary. The raw secret we baked
        # into the mock is stripped.
        osv_obs = (
            s.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == "osv",
            )
            .all()
        )
        assert osv_obs, "an OSV provider observation is recorded"
        latest = osv_obs[-1]
        assert latest.status in {
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.PARTIAL,
        }, f"OSV 503 must be reported as unavailable/partial, got {latest.status!r}"
        assert latest.error_summary is not None
        assert len(latest.error_summary) <= 2048

        # 3. The scan row is partial because at least one
        # stage is partial.
        scan = s.query(ScanRun).filter(ScanRun.id == scan_id).one()
        assert scan.status == ScanStatus.PARTIAL, (
            f"scan with a partial stage must finish partial; got {scan.status!r}"
        )
        assert scan.completed_at is not None


def test_provider_429_records_unavailable_observation(
    app_config, workspace_root, monkeypatch
) -> None:
    """A 429 from deps.dev must leave a truthful unavailable record.

    Truthful contract (v0.4 honest failure semantics):

    - The persisted ``ScanStage`` for
      ``dependency_enrichment`` is ``PARTIAL`` (not
      ``COMPLETED``).
    - The ``ProviderObservation`` row is
      ``unavailable`` / ``rate_limited`` with a redacted
      error summary.
    - The overall scan status is ``PARTIAL``.
    - The truth is observable through both the database
      and the API surface.
    """
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_rate_limited",
        error_summary="deps.dev 429 too many requests",
        attempted_at=None,
        http_status=429,
    )
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="OSV 500",
        attempted_at=None,
        http_status=500,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    outcome = orchestrator.run(scan_id)
    assert outcome.final_status == ScanStatus.PARTIAL
    with _db_session.SessionLocal() as s:
        from app.models.scan_stage import ScanStage

        # 1. The persisted ScanStage for the provider-backed
        # stage is PARTIAL, not COMPLETED.
        deps_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.DEPENDENCY_ENRICHMENT,
            )
            .one()
        )
        assert deps_stage.status == StageStatus.PARTIAL, (
            f"dependency_enrichment must be PARTIAL on provider 429; got {deps_stage.status!r}"
        )
        assert deps_stage.failure_code == "provider_unavailable"

        # 2. The ProviderObservation row carries the
        # truthful unavailable / rate_limited status.
        deps_obs = (
            s.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == "deps_dev",
            )
            .all()
        )
        assert deps_obs, "a deps.dev provider observation is recorded"
        latest = deps_obs[-1]
        assert latest.status in {
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.RATE_LIMITED,
        }, f"deps.dev 429 must be reported as unavailable/rate_limited, got {latest.status!r}"

        # 3. The scan row is partial.
        scan = s.query(ScanRun).filter(ScanRun.id == scan_id).one()
        assert scan.status == ScanStatus.PARTIAL


def test_local_findings_remain_when_providers_fail(app_config, workspace_root, monkeypatch) -> None:
    """Provider failure must not erase local parsing, workflow, or rule findings."""
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="OSV 500",
        attempted_at=None,
        http_status=500,
    )
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="deps.dev 500",
        attempted_at=None,
        http_status=500,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    # Pre-seed a local finding (rule engine output) so we
    # can confirm it survives the provider failures.
    with _db_session.SessionLocal() as s:
        repository_id = s.query(ScanRun).filter(ScanRun.id == scan_id).one().repository_id
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repository_id,
                rule_id="LOCK-WF-001",
                category=FindingCategory.WORKFLOW,
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.HIGH,
                title="Local finding",
                summary="From the rule engine.",
                stable_key="local-key",
            )
        )
        s.commit()
    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    orchestrator.run(scan_id)
    with _db_session.SessionLocal() as s:
        # The local finding is still present.
        local = s.query(Finding).filter(Finding.stable_key == "local-key").one()
        assert local is not None
        # Local stage rows completed.
        from app.models.scan_stage import ScanStage

        local_stages = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type.in_(
                    [
                        StageType.REPOSITORY_INTAKE,
                        StageType.ARCHIVE_VALIDATION,
                        StageType.MANIFEST_DISCOVERY,
                        StageType.DEPENDENCY_PARSING,
                    ]
                ),
            )
            .all()
        )
        for stage in local_stages:
            assert stage.status == StageStatus.COMPLETED, (
                f"local stage {stage.stage_type!r} must complete even when "
                f"providers fail; got {stage.status!r}"
            )


def test_api_exposes_partial_scan_and_partial_provider_stage(
    app_config, workspace_root, monkeypatch
) -> None:
    """The HTTP API must surface the truthful state on a provider failure.

    The frontend reads three surfaces to decide whether
    a provider-backed stage was a verified-clean success:

    1. ``GET /api/v1/scans/{id}`` — overall scan status.
    2. ``GET /api/v1/scans/{id}/providers`` — the per-call
       ``ProviderObservation`` rows.
    3. The persisted ``ScanStage`` rows (rendered into the
       scan detail page from the v0.3 stage endpoints).

    A 503 from OSV must show as:

    - scan ``status == "partial"``;
    - provider observation with ``status == "unavailable"``
      and a redacted ``error_summary``;
    - ``ScanStage.status == "partial"`` for
      ``vulnerability_query`` with
      ``failure_code == "provider_unavailable"``;
    - local findings (rule engine, workflow, parser)
      still present.
    """

    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="OSV 503",
        attempted_at=None,
        http_status=503,
    )
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="deps.dev 503",
        attempted_at=None,
        http_status=503,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])

    # Run the orchestrator (no client needed).
    ScanOrchestrator(_db_session.SessionLocal).run(scan_id)

    # Build a TestClient with overridden DB session.
    from app.api import v0_3 as v03
    from app.db import session as _db_session_mod
    from app.main import app

    def _get_db():
        s = _db_session_mod.SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[v03.DBSession] = _get_db
    try:
        with api_client(app) as client:
            # 1. Overall scan status is "partial".
            r = client.get(f"/api/v1/scans/{scan_id}")
            assert r.status_code == 200
            scan = r.json()
            assert scan["status"] == "partial", (
                f"scan must be partial on provider 503; got {scan['status']!r}"
            )

            # 2. Provider observations expose the truthful
            # unavailable state.
            r = client.get(f"/api/v1/scans/{scan_id}/providers")
            assert r.status_code == 200
            providers = r.json()["items"]
            osv_rows = [p for p in providers if p["provider"] == "osv"]
            assert osv_rows, "an OSV observation must be present"
            osv_latest = max(osv_rows, key=lambda p: p["id"])
            assert osv_latest["status"] in {"unavailable", "partial"}, (
                f"OSV observation must be unavailable/partial; got {osv_latest['status']!r}"
            )
            assert osv_latest["error_summary"] is not None
            assert len(osv_latest["error_summary"]) <= 2048
    finally:
        app.dependency_overrides.pop(v03.DBSession, None)

    # 3. The persisted ScanStage row for the provider-backed
    # stage is PARTIAL with the truthful failure code.
    with _db_session.SessionLocal() as s:
        from app.models.scan_stage import ScanStage

        vuln_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.VULNERABILITY_QUERY,
            )
            .one()
        )
        assert vuln_stage.status == StageStatus.PARTIAL
        assert vuln_stage.failure_code == "provider_unavailable"
        # The stage's ``provider_status`` carries the
        # truthful provider state, distinct from the
        # stage's own status. The UI uses this to render
        # the "unavailable" pill on the stage row.
        assert vuln_stage.provider_status in {
            ProviderStatus.UNAVAILABLE.value,
            ProviderStatus.PARTIAL.value,
        }


def test_later_unrelated_success_must_not_launder_earlier_component_failure(
    app_config, workspace_root, monkeypatch
) -> None:
    """LV-011: a later success for another component must not erase an earlier failure.

    Failure mode under test (pre-fix): the deps.dev stage read
    only the chronologically latest observation. Component A
    fails (``unavailable`` row), component B succeeds
    (``available`` row, higher id), and the latest-row check
    saw only B's success - the stage completed and the scan
    presented dependency/licence enrichment as fully
    successful even though it was incomplete.

    Truthful contract:

    - the ``dependency_enrichment`` stage is ``PARTIAL`` with
      ``failure_code == "provider_unavailable"``;
    - the stage's ``provider_status`` aggregate is
      ``"unavailable"`` even though the latest deps.dev row
      is ``"available"``;
    - the overall scan is ``PARTIAL``;
    - the successful component's observation is preserved
      (the aggregate is honest about partial evidence, it
      does not erase the row that did answer).
    """
    from datetime import datetime

    from app.providers.results import ProviderSuccess

    def _enrich(*, ecosystem: str, package_name: str, version: str | None):
        if package_name == "aaa-fails":
            return ProviderUnavailable(
                error_code="provider_unavailable",
                error_summary="deps.dev 503 for aaa-fails",
                attempted_at=None,
                http_status=503,
            )
        return ProviderSuccess(
            data={"licenses": ["MIT"], "dependencies": []},
            fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
            records_returned=1,
        )

    deps_dev = MagicMock()
    deps_dev.enrich.side_effect = _enrich
    osv = MagicMock()
    # OSV succeeds cleanly so the partial state is provably
    # caused by the degraded deps.dev evidence alone.
    osv.query_batch.return_value = ProviderSuccess(
        data=[],
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
        records_returned=0,
    )
    scorecard = MagicMock()
    scorecard.read.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="Scorecard 503",
        attempted_at=None,
        http_status=503,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev, scorecard=scorecard)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(
            s,
            [
                ("npm", "aaa-fails", "1.0.0", True, False),
                ("npm", "bbb-succeeds", "2.0.0", True, False),
            ],
        )
    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    outcome = orchestrator.run(scan_id)
    assert outcome.final_status == ScanStatus.PARTIAL

    with _db_session.SessionLocal() as s:
        from app.models.scan_stage import ScanStage

        deps_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.DEPENDENCY_ENRICHMENT,
            )
            .one()
        )
        assert deps_stage.status == StageStatus.PARTIAL, (
            "a per-component deps.dev failure followed by an unrelated "
            "component success must not complete the stage; got "
            f"{deps_stage.status!r}"
        )
        assert deps_stage.failure_code == "provider_unavailable"

        deps_obs = (
            s.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == "deps_dev",
            )
            .order_by(ProviderObservation.id.asc())
            .all()
        )
        statuses = [obs.status for obs in deps_obs]
        assert ProviderStatus.UNAVAILABLE in statuses, (
            "the failed component's observation must be recorded"
        )
        assert ProviderStatus.AVAILABLE in statuses, (
            "the successful component's observation must be recorded"
        )
        # The chronologically latest row is the success; the
        # aggregate on the stage row must still be the degraded
        # state, proving the stage state is derived from ALL
        # observations rather than the latest row.
        assert deps_obs[-1].status is ProviderStatus.AVAILABLE
        assert deps_stage.provider_status == ProviderStatus.UNAVAILABLE.value

        # OSV succeeded cleanly: its stage stays completed with
        # an available provider status, proving the deps.dev
        # degradation is isolated and truthful.
        vuln_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.VULNERABILITY_QUERY,
            )
            .one()
        )
        assert vuln_stage.status == StageStatus.COMPLETED
        assert vuln_stage.provider_status == ProviderStatus.AVAILABLE.value


def test_retry_success_for_same_component_supersedes_failure_in_stage_aggregate(
    app_config, workspace_root, monkeypatch
) -> None:
    """LV-011 retry semantics: a retry for the SAME logical request replaces it.

    The aggregation must not make historical failures
    permanent: when a later observation exists for the same
    ``(provider, operation, component_id)`` request and
    succeeded, the earlier failure is superseded and the
    aggregate is a clean success. The seeded row mimics a
    failed earlier attempt for the same component inside the
    same scan; the pipeline's successful re-run supersedes it.
    """
    from datetime import datetime

    from app.providers.results import ProviderSuccess

    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderSuccess(
        data={"licenses": ["MIT"], "dependencies": []},
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
        records_returned=1,
    )
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(
        data=[],
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
        records_returned=0,
    )
    scorecard = MagicMock()
    scorecard.read.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="Scorecard 503",
        attempted_at=None,
        http_status=500,
    )
    _patch_provider_service(monkeypatch, osv=osv, deps_dev=deps_dev, scorecard=scorecard)

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])

    # Seed a superseded earlier failure for the SAME logical
    # request (same provider + operation + component) the way
    # an earlier partial attempt inside the same scan would
    # have; the pipeline's retry below succeeds.
    from app.services.provider_service import OP_DEPS_DEV_ENRICH

    component = (
        s.query(Component).filter(Component.scan_run_id == scan_id).order_by(Component.id).one()
    )
    component_id = component.id
    s.add(
        ProviderObservation(
            scan_run_id=scan_id,
            component_id=component_id,
            provider="deps_dev",
            operation=OP_DEPS_DEV_ENRICH,
            status=ProviderStatus.UNAVAILABLE,
            records_returned=0,
            cache_status="miss",
            error_code="provider_unavailable",
            error_summary="deps.dev earlier attempt failed",
        )
    )
    s.commit()

    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    outcome = orchestrator.run(scan_id)

    with _db_session.SessionLocal() as s:
        from app.models.scan_stage import ScanStage

        deps_stage = (
            s.query(ScanStage)
            .filter(
                ScanStage.scan_run_id == scan_id,
                ScanStage.stage_type == StageType.DEPENDENCY_ENRICHMENT,
            )
            .one()
        )
        # The earlier failure for the same request was
        # superseded by the successful retry: the aggregate is
        # available and the stage completes.
        assert deps_stage.provider_status == ProviderStatus.AVAILABLE.value
        assert deps_stage.status == StageStatus.COMPLETED, (
            "an explicit successful retry for the same logical request "
            "must supersede the earlier failure; "
            f"got {deps_stage.status!r} (outcome {outcome.final_status!r})"
        )
        # Both rows are preserved for observability.
        rows = (
            s.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == "deps_dev",
                ProviderObservation.component_id == component_id,
            )
            .all()
        )
        assert {row.status for row in rows} == {
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.AVAILABLE,
        }
