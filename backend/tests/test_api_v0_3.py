"""API tests for the v0.3 end-to-end scan flow endpoints.

These tests build a scan by writing the data the orchestrator
would have written during a real run, then exercise every new
read endpoint to confirm the shape the v0.3 frontend depends on.

A separate test runs the orchestrator end-to-end against a
fresh intake to confirm the v0.3 pipeline populates the
expected tables.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.db import session as _db_session
from app.main import app
from app.models.component import Component, ComponentVersionSource
from app.models.dependency_edge import DependencyEdge
from app.models.finding import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.models.workspace import WorkspaceKind, WorkspaceState
from app.services import repository_service, scan_service
from app.services.orchestrator_service import ScanOrchestrator
from app.services.workspace_service import WorkspaceService
from app.utils.finding_keys import stable_finding_key

from tests.api_client import api_client

# The :func:`conftest._fake_providers_for_scan_tests`
# autouse fixture replaces the real provider factories
# with in-process fakes globally; this module no longer
# needs to import the per-module fixture. The
# :func:`conftest._block_external_network` autouse
# fixture is the negative complement that fails any
# test which attempts a non-loopback socket.


def _setup_scan_with_zip(session, workspace_root: Path, *, include_workflow: bool = False):
    repo = repository_service.create_repository_from_url(
        session, "https://github.com/octocat/Hello-World"
    )
    scan = scan_service.create_scan(
        session, repository_id=repo.id, trigger_type=ScanTriggerType.MANUAL
    )
    workspaces = WorkspaceService(session)
    workspace = workspaces.create_for_scan(scan, kind=WorkspaceKind.GITHUB)
    paths = workspaces.paths_for(workspace.workspace_key)
    paths.contents_dir.mkdir(parents=True, exist_ok=True)
    (paths.contents_dir / "package.json").write_text(
        '{"name":"sample","version":"1.0.0","dependencies":{"left-pad":"^1.0.0"}}',
        encoding="utf-8",
    )
    if include_workflow:
        wf = paths.contents_dir / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        # Use a third-party action that the analyzer will flag as
        # unpinned (``LOCK-WF-001``).
        (wf / "ci.yml").write_text(
            "name: ci\non: pull_request\njobs:\n  build:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: third-party/setup-thing@main\n"
            "      - run: echo hi\n",
            encoding="utf-8",
        )
    workspaces.transition(workspace, target=WorkspaceState.VALIDATING)
    workspaces.transition(
        workspace,
        target=WorkspaceState.READY,
        archive_sha256="a" * 64,
        archive_size=200,
        file_count=2,
        uncompressed_size=200,
    )
    return scan.id, repo.id, workspace.workspace_key


def test_components_endpoint_returns_paginated_list(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "pagination" in body
    # Even an empty component list is a valid response; what
    # matters is the shape.
    assert isinstance(body["items"], list)


def test_vulnerabilities_endpoint_handles_empty_state(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/vulnerabilities")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_advisories_endpoint_handles_empty_state(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/advisories")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_workflow_findings_endpoint_returns_workflow_category(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repo_id,
                rule_id="LOCK-WF-001",
                category=FindingCategory.WORKFLOW,
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.HIGH,
                title="Unpinned third-party action",
                summary="actions/checkout is not pinned to a SHA",
                remediation="Pin to a SHA.",
                location_path=".github/workflows/ci.yml",
                location_start_line=5,
                location_end_line=5,
                stable_key=stable_finding_key("LOCK-WF-001", {"path": ".github/workflows/ci.yml"}),
                evidence_json=json.dumps(
                    {
                        "permissions": [],
                        "triggers": ["pull_request"],
                        "unpinned_actions": ["actions/checkout"],
                        "yaml_path": "$.jobs.build.steps[0].uses",
                        "limitations": [],
                    }
                ),
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/workflows")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["rule_id"] == "LOCK-WF-001"
    assert item["workflow_name"] == "ci.yml"
    assert item["triggers"] == ["pull_request"]


def test_licences_endpoint_returns_licence_category(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repo_id,
                rule_id="LOCK-LIC-001",
                category=FindingCategory.LICENCE,
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.MEDIUM,
                title="Component has no licence assertion",
                summary="left-pad has no licence assertion.",
                stable_key=stable_finding_key("LOCK-LIC-001", {"package_name": "left-pad"}),
                evidence_json=json.dumps(
                    {
                        "evidence": {
                            "package_name": "left-pad",
                            "version": "1.0.0",
                            "licences": [],
                        }
                    }
                ),
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/licences")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["package_name"] == "left-pad"
    assert item["review_status"] == "unreviewed"


def test_dependency_path_for_known_component(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        parent = Component(
            scan_run_id=scan_id,
            manifest_id=manifest.id,
            ecosystem="npm",
            package_name="left-pad",
            version="1.0.0",
            version_source=ComponentVersionSource.LOCKFILE,
            direct=True,
        )
        child = Component(
            scan_run_id=scan_id,
            manifest_id=manifest.id,
            ecosystem="npm",
            package_name="left-pad-deep",
            version="0.0.1",
            version_source=ComponentVersionSource.LOCKFILE,
            direct=False,
        )
        s.add_all([parent, child])
        s.flush()
        s.add(
            DependencyEdge(
                scan_run_id=scan_id,
                parent_component_id=parent.id,
                child_component_id=child.id,
                depth=1,
            )
        )
        s.commit()
        child_id = child.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/{child_id}/path")
    assert r.status_code == 200
    body = r.json()
    names = {c["package_name"] for c in body["components"]}
    assert "left-pad" in names
    assert "left-pad-deep" in names


def test_exports_listing_returns_supported_formats(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    # The scan is QUEUED at this point; the 1.7 descriptor
    # is therefore not yet supported (the download endpoint
    # would 422). We move the scan to COMPLETED to verify
    # the supported-formats contract for a finished scan.
    with _db_session.SessionLocal() as s:
        scan = s.get(scan_service.ScanRun, scan_id)
        assert scan is not None
        scan.status = ScanStatus.COMPLETED
        s.add(scan)
        s.commit()
    r = client.get(f"/api/v1/scans/{scan_id}/exports")
    assert r.status_code == 200
    body = r.json()
    formats = {item["format"] for item in body["items"]}
    assert formats == {
        "cyclonedx_json",
        "cyclonedx_1_7",
        "findings_json",
        "findings_csv",
        "sarif_json",
    }
    for item in body["items"]:
        assert item["supported"] is True
        assert item["filename_hint"]
        assert item["content_type"]


@pytest.mark.parametrize(
    "scan_status, expected_supported, expected_reason_substring",
    [
        (ScanStatus.FAILED, False, "failed"),
        (ScanStatus.CANCELLED, False, "cancelled"),
        (ScanStatus.QUEUED, False, "queued"),
        (ScanStatus.RUNNING, False, "running"),
    ],
)
def test_list_exports_disables_cyclonedx_1_7_for_ineligible_scans(
    app_config,
    workspace_root,
    scan_status: ScanStatus,
    expected_supported: bool,
    expected_reason_substring: str,
) -> None:
    """The CycloneDX 1.7 descriptor is gated by the
    authoritative eligibility helper. Failed / cancelled /
    queued / running scans never offer the 1.7 download
    through the UI; the descriptor carries a bounded
    ``not_supported_reason`` so the consumer sees the exact
    reason.

    Other legacy exports remain ``supported: true`` for
    these states because they produce empty-but-valid
    outputs; the UI does not break them."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    # Force the scan into the requested state without
    # rerunning the orchestrator.
    with _db_session.SessionLocal() as s:
        scan = s.get(scan_service.ScanRun, scan_id)
        assert scan is not None
        scan.status = scan_status
        s.add(scan)
        s.commit()
    r = client.get(f"/api/v1/scans/{scan_id}/exports")
    assert r.status_code == 200
    body = r.json()
    by_format = {item["format"]: item for item in body["items"]}
    cdx17 = by_format["cyclonedx_1_7"]
    assert cdx17["supported"] is False
    assert cdx17["not_supported_reason"] is not None
    assert expected_reason_substring in cdx17["not_supported_reason"].lower()
    # Other legacy exports remain available.
    assert by_format["cyclonedx_json"]["supported"] is True
    assert by_format["findings_json"]["supported"] is True
    assert by_format["findings_csv"]["supported"] is True
    assert by_format["sarif_json"]["supported"] is True


def test_list_exports_cyclonedx_1_7_ineligible_partial_scan_without_inventory(
    app_config, workspace_root
) -> None:
    """A partial scan with zero manifests and zero components
    cannot produce a 1.7 SBOM (the eligibility helper
    rejects it). The descriptor is ``supported: false`` with
    a bounded reason."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        # Wipe the inventory so the scan is partial-without-inventory.
        s.query(Manifest).filter(Manifest.scan_run_id == scan_id).delete()
        s.query(Component).filter(Component.scan_run_id == scan_id).delete()
        scan = s.get(scan_service.ScanRun, scan_id)
        assert scan is not None
        scan.status = ScanStatus.PARTIAL
        s.add(scan)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports")
    body = r.json()
    cdx17 = next(item for item in body["items"] if item["format"] == "cyclonedx_1_7")
    assert cdx17["supported"] is False
    assert cdx17["not_supported_reason"] is not None
    assert "partial" in cdx17["not_supported_reason"].lower()


def test_list_exports_cyclonedx_1_7_partial_scan_with_inventory_carries_warning(
    app_config, workspace_root
) -> None:
    """A provider-degraded partial scan with persisted local
    inventory is **eligible** for a CycloneDX 1.7 export
    (the download endpoint returns 200). The descriptor is
    therefore ``supported: true``, but the description text
    surfaces the partial-evidence limitation so the
    consumer never mistakes a degraded SBOM for a complete
    one."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        # Add a parsed manifest and a component so the
        # eligibility helper sees a non-empty local
        # inventory (partial-with-inventory is eligible).
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        s.add(
            Component(
                scan_run_id=scan_id,
                manifest_id=manifest.id,
                ecosystem="npm",
                package_name="left-pad",
                version="1.0.0",
                version_source=ComponentVersionSource.LOCKFILE,
                direct=True,
            )
        )
        scan = s.get(scan_service.ScanRun, scan_id)
        assert scan is not None
        scan.status = ScanStatus.PARTIAL
        s.add(scan)
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="osv",
                operation="osv_vulnerability_query",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                error_code="provider_unavailable",
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports")
    body = r.json()
    cdx17 = next(item for item in body["items"] if item["format"] == "cyclonedx_1_7")
    assert cdx17["supported"] is True
    # The description text names the provider-degraded
    # limitation explicitly.
    assert "provider-degraded" in cdx17["description"].lower()
    assert "partial" in cdx17["description"].lower()


def test_list_exports_cyclonedx_1_7_completed_scan_has_no_warning(
    app_config, workspace_root
) -> None:
    """A completed scan is eligible and the description
    text does NOT carry the partial-evidence warning
    (the limitation applies only to provider-degraded
    partial scans)."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan = s.get(scan_service.ScanRun, scan_id)
        assert scan is not None
        scan.status = ScanStatus.COMPLETED
        s.add(scan)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports")
    body = r.json()
    cdx17 = next(item for item in body["items"] if item["format"] == "cyclonedx_1_7")
    assert cdx17["supported"] is True
    assert cdx17["not_supported_reason"] is None
    # The completed-scan description is the short form
    # without the provider-degraded warning.
    assert "provider-degraded" not in cdx17["description"].lower()


def test_export_download_returns_attachment_with_filename(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/findings_json")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "lockverity-findings.json" in r.headers.get("content-disposition", "")


def test_export_unknown_format_returns_404(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/spdx_xml")
    assert r.status_code == 404


def test_compare_scans_endpoint_returns_diff(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        base_id, repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        # Create a second scan and bump one component.
        head_scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_id = head_scan.id
        manifest = Manifest(
            scan_run_id=head_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        s.add(
            Component(
                scan_run_id=head_id,
                manifest_id=manifest.id,
                ecosystem="npm",
                package_name="left-pad",
                version="2.0.0",
                version_source=ComponentVersionSource.LOCKFILE,
                direct=True,
            )
        )
        # The v0.5 comparison service requires both scans to
        # be in a terminal state; move both scans forward.
        scan_service.transition_scan(s, base_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, base_id, target=ScanStatus.COMPLETED)
        scan_service.transition_scan(s, head_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, head_id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head_id}/compare/{base_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["base_scan_id"] == base_id
    assert body["head_scan_id"] == head_id
    assert body["repository_id"] == repo_id
    assert isinstance(body["components"], list)
    # v0.5 typed shape: components carry the evidence-honest
    # state vocabulary, not the legacy "added/removed/updated"
    # verdicts.
    assert {c["state"] for c in body["components"]} <= {
        "newly_observed",
        "still_observed",
        "no_longer_observed",
        "changed_observation",
        "coverage_changed",
        "comparison_indeterminate",
    }
    # The v0.5 comparison surfaces manifests, workflow
    # findings, vulnerabilities, licences, OpenSSF checks,
    # and provider coverage; the read shape is preserved
    # even when the lists are empty.
    for key in (
        "coverage",
        "components",
        "manifests",
        "dependency_paths",
        "workflows",
        "vulnerabilities",
        "licences",
        "openssf",
        "providers",
        "indeterminate_reasons",
    ):
        assert key in body, f"missing top-level key {key!r} in {sorted(body)}"


def test_compare_scans_rejects_different_repositories(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        # Two scans on two different repositories.
        scan1, repo1, _ = _setup_scan_with_zip(s, workspace_root)
        scan2 = scan_service.create_scan(
            s, repository_id=repo1, trigger_type=ScanTriggerType.MANUAL
        )
        # Make scan2 belong to a different repo.
        repo2 = repository_service.create_repository_from_url(
            s, "https://github.com/anthropics/anthropic-sdk-python"
        )
        scan2.repository_id = repo2.id
        # v0.5: both scans must be in a terminal state.
        scan_service.transition_scan(s, scan1, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan1, target=ScanStatus.COMPLETED)
        scan_service.transition_scan(s, scan2.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan2.id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan2.id}/compare/{scan1}")
    assert r.status_code in {400, 422}


def test_compare_scans_rejects_non_terminal_scans(app_config, workspace_root) -> None:
    """A queued scan cannot be compared; the service rejects non-terminal inputs."""
    with _db_session.SessionLocal() as s:
        base_id, repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        # Move only the base scan to a terminal state. The
        # head scan is still queued.
        scan_service.transition_scan(s, base_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, base_id, target=ScanStatus.COMPLETED)
        s.commit()
        head_id = head.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head_id}/compare/{base_id}")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "illegal_transition"


def test_compare_scans_rejects_identical_scans(app_config, workspace_root) -> None:
    """The service rejects identical base/head scan selection."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/compare/{scan_id}")
    # The backend maps VALIDATION_ERROR to 422; accept either.
    assert r.status_code in {400, 422}
    body = r.json()
    assert body["error"]["code"] == "validation_error"


def test_orchestrator_end_to_end_pipeline_writes_components(app_config, workspace_root) -> None:
    """A real orchestrator run should write components for the discovered manifests.

    v0.4 honesty fix: the scan may finish as ``partial`` if
    the provider network is unavailable. The previous
    v0.3 baseline asserted ``completed`` because the
    state machine laundered provider-unavailable stages
    through ``COMPLETED``. The v0.4 contract accepts
    either ``completed`` (all providers reachable) or
    ``partial`` (a provider-backed stage saw an
    unavailable observation); both shapes are truthful
    given the network state of the test environment.
    """
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root, include_workflow=True)
        s.commit()
    orchestrator = ScanOrchestrator(_db_session.SessionLocal)
    outcome = orchestrator.run(scan_id)
    assert outcome.final_status in {ScanStatus.COMPLETED, ScanStatus.PARTIAL}, (
        f"v0.4 honest failure semantics: a scan with unavailable "
        f"provider data must finish 'partial', not 'failed'; "
        f"got {outcome.final_status!r}"
    )
    # The orchestrator should have at least one component row
    # for the left-pad dependency declared in package.json.
    with _db_session.SessionLocal() as s:
        rows = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        names = {r.package_name for r in rows}
        assert "left-pad" in names, names
    # And the workflow analyzer should have produced at least
    # one workflow finding for the unpinned action.
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/workflows")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(item["rule_id"] == "LOCK-WF-001" for item in items), items


# ---------------------------------------------------------------------
# v0.7 CycloneDX 1.7 preview / readiness summary
# ---------------------------------------------------------------------


def test_preview_endpoint_returns_completed_eligible_summary(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["scan"]["scan_id"] == scan_id
    assert body["scan"]["scan_status"] == "completed"
    assert body["eligibility"]["eligible"] is True
    assert body["eligibility"]["code"] == "eligible"
    assert body["eligibility"]["download_expected_to_succeed"] is True
    assert body["sbom_output"]["format"] == "CycloneDX"
    assert body["sbom_output"]["spec_version"] == "1.7"
    assert "omissions" in body
    assert "legacy_export_relationship" in body


def test_preview_endpoint_returns_404_for_unknown_scan(app_config, workspace_root) -> None:
    client = api_client(app)
    r = client.get("/api/v1/scans/999999/exports/cyclonedx_1_7/preview")
    assert r.status_code == 404


def test_preview_endpoint_returns_failed_ineligible_summary(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.FAILED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["eligibility"]["eligible"] is False
    assert body["eligibility"]["code"] == "scan_failed"
    assert body["eligibility"]["download_expected_to_succeed"] is False


def test_preview_endpoint_returns_cancelled_ineligible_summary(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.CANCELLED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["eligibility"]["eligible"] is False
    assert body["eligibility"]["code"] == "scan_cancelled"
    assert body["eligibility"]["download_expected_to_succeed"] is False


def test_preview_endpoint_response_is_deterministic(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r1 = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    r2 = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # The body is deterministic for the same scan state.
    assert r1.json() == r2.json()


def test_preview_endpoint_does_not_validate_full_bom(app_config, workspace_root) -> None:
    """The preview must not run the full CycloneDX 1.7
    schema validator. The route is a small summary; the
    actual download endpoint is the one that runs the
    validator. This test asserts the preview is reachable
    even for scans that have not produced a real SBOM (a
    queued scan returns a 200 preview with eligible=false)."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _ = _setup_scan_with_zip(s, workspace_root)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["eligibility"]["eligible"] is False
    assert body["eligibility"]["code"] == "scan_not_started"


# v0.8 component evidence drilldown — API tests.
#
# The endpoint is a sibling of the existing
# ``/components/{component_id}/path`` route. The fixture
# mirrors the dependency-path test: a small persisted
# manifest + component + (optionally) a dependency edge,
# queried through the public FastAPI route.


def test_component_evidence_endpoint_returns_full_summary(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        component = Component(
            scan_run_id=scan_id,
            manifest_id=manifest.id,
            ecosystem="npm",
            package_name="left-pad",
            version="1.0.0",
            version_source=ComponentVersionSource.LOCKFILE,
            package_url="pkg:npm/left-pad@1.0.0",
            direct=True,
        )
        s.add(component)
        s.commit()
        component_id = component.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/{component_id}/evidence")
    assert r.status_code == 200
    body = r.json()
    # The documented shape is the contract.
    assert set(body.keys()) == {
        "scan",
        "component",
        "manifest",
        "licence_evidence",
        "provider_evidence",
        "dependency_evidence",
        "export_implications",
        "omissions",
    }
    assert body["component"]["id"] == component_id
    assert body["component"]["package_name"] == "left-pad"
    assert body["component"]["package_url"] == "pkg:npm/left-pad@1.0.0"
    assert body["component"]["package_url_well_formed"] is True
    assert body["component"]["bom_ref"] == "pkg:npm/left-pad@1.0.0"
    assert body["manifest"]["path"] == "package.json"
    assert body["manifest"]["parse_status"] == "parsed"
    # The export implications are derived from the v0.6
    # exporter rules.
    assert body["export_implications"]["appears_in_cyclonedx_17"] is True
    assert body["export_implications"]["version_omitted"] is False
    assert body["export_implications"]["purl_emitted"] is True
    # No outgoing edges were persisted.
    assert body["export_implications"]["dependency_relationships_emitted"] is False
    # No provider / licence evidence.
    assert body["licence_evidence"]["available"] is False
    assert body["provider_evidence"]["available"] is False
    assert body["dependency_evidence"]["no_edges_observed"] is True


def test_component_evidence_endpoint_returns_404_for_unknown_component(
    app_config, workspace_root
) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/999999/evidence")
    assert r.status_code == 404


def test_component_evidence_endpoint_returns_404_for_cross_scan_component(
    app_config, workspace_root
) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _repo_a, _ = _setup_scan_with_zip(s, workspace_root)
        scan_b, _repo_b, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_a, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_a, target=ScanStatus.COMPLETED)
        scan_service.transition_scan(s, scan_b, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_b, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_a,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        component = Component(
            scan_run_id=scan_a,
            manifest_id=manifest.id,
            ecosystem="npm",
            package_name="left-pad",
            version="1.0.0",
            version_source=ComponentVersionSource.LOCKFILE,
            direct=True,
        )
        s.add(component)
        s.commit()
        component_id = component.id
    client = api_client(app)
    # The component belongs to scan_a; scan_b must reject.
    r = client.get(f"/api/v1/scans/{scan_b}/components/{component_id}/evidence")
    assert r.status_code == 404


def test_component_evidence_endpoint_response_is_deterministic(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        component = Component(
            scan_run_id=scan_id,
            manifest_id=manifest.id,
            ecosystem="npm",
            package_name="left-pad",
            version="1.0.0",
            version_source=ComponentVersionSource.LOCKFILE,
            package_url="pkg:npm/left-pad@1.0.0",
            direct=True,
        )
        s.add(component)
        s.commit()
        component_id = component.id
    client = api_client(app)
    r1 = client.get(f"/api/v1/scans/{scan_id}/components/{component_id}/evidence")
    r2 = client.get(f"/api/v1/scans/{scan_id}/components/{component_id}/evidence")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


# v0.9 evidence-aware search and filtering — API tests.
#
# The endpoint is a sibling of the existing
# ``/components/{component_id}/evidence`` route. The
# fixture mirrors the dependency-path test: a small
# persisted manifest + components + an optional licence
# finding + an optional provider observation + an
# optional dependency edge.


def test_evidence_summary_default_returns_all_components(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        for name in ("left-pad", "lodash", "stay"):
            _make_or_get_component(s, scan_id=scan_id, manifest_id=manifest.id, name=name)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "items",
        "pagination",
        "facets",
        "omissions",
    }
    assert body["pagination"]["total"] == 3
    # The default sort is package_name.
    names = [it["package_name"] for it in body["items"]]
    assert names == sorted(names, key=str.lower)


def test_evidence_summary_search_filter_narrows_results(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        for name in ("left-pad", "lodash", "stay"):
            _make_or_get_component(s, scan_id=scan_id, manifest_id=manifest.id, name=name)
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?search=pad")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["package_name"] == "left-pad"


def test_evidence_summary_direct_filter(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="left-pad",
            direct=True,
        )
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="lodash",
            direct=False,
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?direct=yes")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["direct"] is True
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?direct=no")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["direct"] is False


def test_evidence_summary_version_missing_filter(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="left-pad",
            version="1.0.0",
        )
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="unresolved",
            version=None,
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?version=missing")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["package_name"] == "unresolved"
    assert body["items"][0]["version"] is None
    assert body["items"][0]["evidence"]["version_present"] is False
    assert body["items"][0]["evidence"]["version_omitted_from_cyclonedx_17"] is True


def test_evidence_summary_purl_persisted_filter(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="left-pad",
            package_url="pkg:npm/left-pad@1.0.0",
        )
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="lodash",
            package_url=None,
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?purl=persisted")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["package_name"] == "left-pad"
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?purl=constructible")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["package_name"] == "lodash"
    assert body["items"][0]["evidence"]["purl_state"] == "constructible"


def test_evidence_summary_dependency_edges_present_filter(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        parent = _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="left-pad",
        )
        child = _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="lodash",
        )
        s.add(
            DependencyEdge(
                scan_run_id=scan_id,
                parent_component_id=parent.id,
                child_component_id=child.id,
                depth=1,
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary?dependency_edges=present")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == parent.id
    r = client.get(
        f"/api/v1/scans/{scan_id}/components/evidence-summary?dependency_edges=none_observed"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == child.id
    # The summary must not claim "no dependencies" for the
    # child component. The filter vocabulary is bounded
    # to ``present`` / ``none_observed``.
    text = json.dumps(body).lower()
    assert "no dependencies" not in text


def test_evidence_summary_facets_match_filtered_set(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="left-pad",
            version="1.0.0",
        )
        _make_or_get_component(
            s,
            scan_id=scan_id,
            manifest_id=manifest.id,
            name="unresolved",
            version=None,
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary")
    assert r.status_code == 200
    body = r.json()
    facets = body["facets"]
    # One of two components is missing its version.
    assert facets["missing_version"] == 1
    # Both components are npm, no other ecosystem.
    assert facets["ecosystems"] == {"npm": 2}
    # Two direct components.
    assert facets["direct_yes"] == 2
    assert facets["direct_no"] == 0
    # One component has its version omitted from the
    # CycloneDX 1.7 export.
    assert facets["cyclonedx_version_omitted"] == 1


def test_evidence_summary_returns_404_for_unknown_scan(app_config, workspace_root) -> None:
    client = api_client(app)
    r = client.get("/api/v1/scans/999999/components/evidence-summary")
    assert r.status_code == 404


def test_evidence_summary_response_is_deterministic(app_config, workspace_root) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _repo_id, _ = _setup_scan_with_zip(s, workspace_root)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, scan_id, target=ScanStatus.COMPLETED)
        manifest = Manifest(
            scan_run_id=scan_id,
            path="package.json",
            manifest_type="package_json",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
        )
        s.add(manifest)
        s.flush()
        for name in ("left-pad", "lodash", "stay"):
            _make_or_get_component(s, scan_id=scan_id, manifest_id=manifest.id, name=name)
        s.commit()
    client = api_client(app)
    r1 = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary")
    r2 = client.get(f"/api/v1/scans/{scan_id}/components/evidence-summary")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


def _make_or_get_component(
    session,
    *,
    scan_id: int,
    manifest_id: int,
    name: str,
    version: str | None = "1.0.0",
    direct: bool = True,
    package_url: str | None = "pkg:npm/example@1.0.0",
) -> Component:
    """Insert a unique component row.

    Each test uses a fresh scan, so the package name does
    not need to be disambiguated.
    """
    component = Component(
        scan_run_id=scan_id,
        manifest_id=manifest_id,
        ecosystem="npm",
        package_name=name,
        version=version,
        version_source=(
            ComponentVersionSource.UNRESOLVED
            if version is None
            else ComponentVersionSource.MANIFEST
        ),
        package_url=package_url,
        direct=direct,
        development=False,
        optional=False,
        integrity=None,
    )
    session.add(component)
    session.flush()
    return component
