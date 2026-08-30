"""Request-scoped external evidence provider selection tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from app.db import session as _db_session
from app.exporters.cyclonedx_v17 import CycloneDxV17Exporter
from app.main import app
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import Repository, RepositorySourceType
from app.models.scan_run import ScanRun, ScanStatus
from app.models.scan_stage import ScanStage, StageStatus, StageType
from app.providers.results import ProviderOutcome, ProviderSuccess, ProviderUnavailable
from app.providers.selection import ExternalEvidenceProviders
from app.reports.evidence import EvidenceReportService
from app.schemas.intake import ScanRunRequest
from app.services.orchestrator_service import ScanOrchestrator

from tests.api_client import api_client
from tests.test_provider_stage_failure import (
    _patch_provider_service,
    _setup_scan_with_components,
)


def _unavailable(summary: str) -> ProviderUnavailable:
    return ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary=summary,
        attempted_at=datetime.now(UTC),
        outcome=ProviderOutcome.UNAVAILABLE,
    )


def _provider_mocks():
    osv = MagicMock()
    osv.query_batch.return_value = _unavailable("OSV unavailable")
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = _unavailable("deps.dev unavailable")
    scorecard = MagicMock()
    scorecard.read.return_value = _unavailable("OpenSSF unavailable")
    return osv, deps_dev, scorecard


def _run_with_selection(monkeypatch, selection: ExternalEvidenceProviders):
    osv, deps_dev, scorecard = _provider_mocks()
    _patch_provider_service(
        monkeypatch,
        osv=osv,
        deps_dev=deps_dev,
        scorecard=scorecard,
    )
    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(
            session,
            [("npm", "left-pad", "1.0.0", True, False)],
        )
    outcome = ScanOrchestrator(_db_session.SessionLocal).run(
        scan_id,
        provider_selection=selection,
    )
    return scan_id, outcome, osv, deps_dev, scorecard


@pytest.mark.parametrize(
    ("selection", "disabled_provider"),
    [
        (ExternalEvidenceProviders(osv=False), "osv"),
        (ExternalEvidenceProviders(deps_dev=False), "deps_dev"),
        (ExternalEvidenceProviders(openssf=False), "openssf"),
    ],
)
def test_each_provider_can_be_disabled_independently(
    app_config,
    workspace_root,
    monkeypatch,
    selection,
    disabled_provider,
) -> None:
    scan_id, _outcome, osv, deps_dev, scorecard = _run_with_selection(
        monkeypatch,
        selection,
    )

    calls = {
        "osv": osv.query_batch,
        "deps_dev": deps_dev.enrich,
        "openssf": scorecard.read,
    }
    calls[disabled_provider].assert_not_called()
    for provider, call in calls.items():
        if provider != disabled_provider:
            call.assert_called()

    with _db_session.SessionLocal() as session:
        observation = (
            session.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == disabled_provider,
            )
            .order_by(ProviderObservation.id.desc())
            .first()
        )
        assert observation is not None
        assert observation.status == ProviderStatus.NOT_REQUESTED
        assert observation.error_code == "disabled_by_operator"
        assert observation.error_summary is None
        assert observation.requested_at is None
        assert observation.completed_at is None
        assert observation.http_status is None
        assert observation.records_returned == 0
        assert observation.cache_status is None
        assert observation.retry_after is None
        assert observation.evidence_json is None


def test_all_disabled_skips_provider_factory_and_completes_local_analysis(
    app_config,
    workspace_root,
    monkeypatch,
) -> None:
    from app.services import analysis_pipeline

    def _forbidden_factory(**_kwargs):
        def _forbidden_service(_session):
            raise AssertionError("disabled providers must not construct ProviderService")

        return _forbidden_service

    monkeypatch.setattr(
        analysis_pipeline,
        "_default_provider_service_factory",
        _forbidden_factory,
    )
    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(
            session,
            [("npm", "left-pad", "1.0.0", True, False)],
        )
    outcome = ScanOrchestrator(_db_session.SessionLocal).run(
        scan_id,
        provider_selection=ExternalEvidenceProviders(
            osv=False,
            deps_dev=False,
            openssf=False,
        ),
    )
    assert outcome.final_status == ScanStatus.COMPLETED

    with _db_session.SessionLocal() as session:
        observations = (
            session.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider.in_(("osv", "deps_dev", "openssf")),
            )
            .all()
        )
        assert {row.provider for row in observations} == {"osv", "deps_dev", "openssf"}
        assert all(row.status == ProviderStatus.NOT_REQUESTED for row in observations)
        assert all(row.error_code == "disabled_by_operator" for row in observations)

        stages = {
            row.stage_type: row
            for row in session.query(ScanStage).filter(ScanStage.scan_run_id == scan_id).all()
        }
        for stage_type in (
            StageType.VULNERABILITY_QUERY,
            StageType.DEPENDENCY_ENRICHMENT,
            StageType.REPOSITORY_POSTURE,
        ):
            stage = stages[stage_type]
            assert stage.status == StageStatus.SKIPPED
            assert stage.provider_status == ProviderStatus.NOT_REQUESTED.value
            assert stage.failure_code is None
            assert stage.failure_summary is None
            assert stage.records_processed == 0
        assert stages[StageType.FINDING_RECONCILIATION].status == StageStatus.COMPLETED


def test_omitted_selection_preserves_all_enabled_provider_calls(
    app_config,
    workspace_root,
    monkeypatch,
) -> None:
    _scan_id, _outcome, osv, deps_dev, scorecard = _run_with_selection(
        monkeypatch,
        ScanRunRequest().provider_selection(),
    )
    osv.query_batch.assert_called()
    deps_dev.enrich.assert_called()
    scorecard.read.assert_called()


def test_disabled_openssf_archive_remains_not_applicable(
    app_config,
    workspace_root,
    monkeypatch,
) -> None:
    from app.services import analysis_pipeline

    def _forbidden_factory(**_kwargs):
        return lambda _session: (_ for _ in ()).throw(
            AssertionError("no provider service is allowed")
        )

    monkeypatch.setattr(
        analysis_pipeline,
        "_default_provider_service_factory",
        _forbidden_factory,
    )
    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(session, [])
        scan = session.get(ScanRun, scan_id)
        assert scan is not None
        repository = session.get(Repository, scan.repository_id)
        assert repository is not None
        repository.source_type = RepositorySourceType.UPLOADED_ARCHIVE
        session.commit()
    ScanOrchestrator(_db_session.SessionLocal).run(
        scan_id,
        provider_selection=ExternalEvidenceProviders(
            osv=False,
            deps_dev=False,
            openssf=False,
        ),
    )
    with _db_session.SessionLocal() as session:
        observation = (
            session.query(ProviderObservation)
            .filter(
                ProviderObservation.scan_run_id == scan_id,
                ProviderObservation.provider == "openssf",
            )
            .one()
        )
        assert observation.status == ProviderStatus.NOT_REQUESTED
        assert observation.error_code == "not_applicable"
        assert observation.cache_status is None


def test_run_schema_defaults_and_immutability() -> None:
    assert ScanRunRequest().provider_selection() == ExternalEvidenceProviders()
    assert ScanRunRequest(
        external_evidence_providers={"osv": False}
    ).provider_selection() == ExternalEvidenceProviders(osv=False)
    selection = ExternalEvidenceProviders(osv=False)
    with pytest.raises(FrozenInstanceError):
        selection.osv = True  # type: ignore[misc]


def test_auto_run_honours_disabled_selection(
    app_config,
    workspace_root,
    monkeypatch,
) -> None:
    from app.services import analysis_pipeline

    def _forbidden_factory(**_kwargs):
        return lambda _session: (_ for _ in ()).throw(
            AssertionError("auto-run bypassed provider selection")
        )

    monkeypatch.setattr(
        analysis_pipeline,
        "_default_provider_service_factory",
        _forbidden_factory,
    )
    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(session, [])
    response = api_client(app).post(
        f"/api/v1/scans/{scan_id}/auto-run",
        json={
            "external_evidence_providers": {
                "osv": False,
                "deps_dev": False,
                "openssf": False,
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["final_status"] == "completed"


def test_run_contract_rejects_unknown_provider_field(app_config) -> None:
    response = api_client(app).post(
        "/api/v1/scans/1/run",
        json={"external_evidence_providers": {"unknown": False}},
    )
    assert response.status_code == 422


def test_async_run_callback_captures_immutable_selection(
    app_config,
    workspace_root,
    monkeypatch,
) -> None:
    import app.api.scans as scans_api

    class CapturingExecutor:
        task = None

        def submit(self, task):
            self.task = task
            return MagicMock()

    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(session, [])

    executor = CapturingExecutor()
    orchestrator = MagicMock()
    monkeypatch.setattr(scans_api, "get_executor", lambda: executor)
    monkeypatch.setattr(scans_api, "_orchestrator_for_session", lambda _session: orchestrator)

    response = api_client(app).post(
        f"/api/v1/scans/{scan_id}/run",
        json={
            "external_evidence_providers": {
                "osv": False,
                "deps_dev": True,
                "openssf": False,
            }
        },
    )
    assert response.status_code == 200
    assert executor.task is not None
    executor.task.callback()

    selection = orchestrator.run.call_args.kwargs["provider_selection"]
    assert selection == ExternalEvidenceProviders(
        osv=False,
        deps_dev=True,
        openssf=False,
    )
    with pytest.raises(FrozenInstanceError):
        selection.openssf = True  # type: ignore[misc]


def test_operator_omission_is_honest_in_exports_and_evidence_reports(
    app_config,
    workspace_root,
) -> None:
    with _db_session.SessionLocal() as session:
        scan_id = _setup_scan_with_components(
            session,
            [("npm", "left-pad", "1.0.0", True, False)],
        )
        scan = session.get(ScanRun, scan_id)
        assert scan is not None
        scan.status = ScanStatus.COMPLETED
        session.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="osv",
                operation="osv_vulnerability_query",
                status=ProviderStatus.NOT_REQUESTED,
                records_returned=0,
                error_code="disabled_by_operator",
            )
        )
        session.commit()

    exporter = CycloneDxV17Exporter(_db_session.SessionLocal)
    preview = exporter.preview(scan_run_id=scan_id)
    assert preview is not None
    assert preview["evidence_coverage"]["provider_coverage"] == "not_requested"
    assert preview["eligibility"]["code"] == "eligible_with_provider_omission"
    assert preview["eligibility"]["limitations"] == ["provider_omitted_by_operator"]
    assert "provider_degraded" not in preview["eligibility"]["limitations"]
    assert "external_provider_evidence_omitted_by_operator" in preview["omissions"]

    exported = exporter.export(scan_run_id=scan_id)
    assert isinstance(exported, ProviderSuccess)
    document = json.loads(exported.data)
    properties = {row["name"]: row["value"] for row in document["metadata"].get("properties", [])}
    assert properties["lockverity:provider-coverage"] == "not_requested"
    assert properties["lockverity:provider-omission-reason"] == "disabled_by_operator"
    assert properties.get("lockverity:partial-reason") != "provider_degradation"

    report = EvidenceReportService(_db_session.SessionLocal).fetch(scan_run_id=scan_id)
    assert report is not None
    assert report["evidence_coverage"]["provider_coverage"] == "not_requested"
    assert report["export_relationship"]["provider_coverage"] == "not_requested"
