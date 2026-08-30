"""v0.4 API surface tests.

These tests cover the new endpoint shapes and the extended
fields the v0.4 frontend consumes. The pipeline is exercised
indirectly through the same :class:`ProviderService` mocks
used by :mod:`tests.test_provider_service_v0_4`.
"""

from __future__ import annotations

import json

from app.db import session as _db_session
from app.main import app
from app.models.component import Component, ComponentVersionSource
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState

from tests.api_client import api_client


def _setup_scan(
    session,
    *,
    ecosystem: str = "npm",
    package: str = "left-pad",
    version: str = "1.0.0",
    canonical_url: str = "https://github.com/octocat/Hello-World",
):
    """Create a scan, workspace, manifest, and one component for API tests."""
    repository = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner=canonical_url.rsplit("/", 2)[-2],
        name=canonical_url.rsplit("/", 1)[-1],
        canonical_url=canonical_url,
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repository)
    session.flush()
    scan = ScanRun(
        repository_id=repository.id,
        status=ScanStatus.COMPLETED,
        trigger_type=ScanTriggerType.MANUAL,
        analyzer_version="0.4.0",
    )
    session.add(scan)
    session.flush()
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
        ecosystem=ecosystem,
        parse_status=ManifestParseStatus.PARSED,
    )
    session.add(manifest)
    session.flush()
    component = Component(
        scan_run_id=scan.id,
        manifest_id=manifest.id,
        ecosystem=ecosystem,
        package_name=package,
        version=version,
        version_source=ComponentVersionSource.LOCKFILE,
        direct=True,
    )
    session.add(component)
    session.commit()
    return scan.id, repository.id, component.id


def test_vulnerabilities_endpoint_includes_provider_provenance_and_aliases(app_config) -> None:
    from app.models.advisory import Advisory
    from app.models.component_advisory import ComponentAdvisory

    with _db_session.SessionLocal() as s:
        scan_id, _repository_id, _component_id = _setup_scan(s)
        # Manually insert the OSV-shaped advisory rows.
        advisory = Advisory(
            source="osv",
            source_advisory_id="GHSA-1234-abcd-efgh",
            canonical_id="CVE-2024-0001",
            summary="Test advisory",
            details_url="https://example.com/advisory",
            raw_payload_sha256="a" * 64,
        )
        s.add(advisory)
        s.flush()
        component = s.query(Component).filter(Component.scan_run_id == scan_id).one()
        s.add(
            ComponentAdvisory(
                scan_run_id=scan_id,
                component_id=component.id,
                advisory_id=advisory.id,
                affected=True,
                fixed_versions_json=json.dumps(["1.3.0"]),
                severity_source="osv",
                severity_label="CVSS_V3",
                severity_score=7,
                evidence_json=json.dumps(
                    {
                        "provider": "osv",
                        "fetched_at": "2024-01-01T00:00:00Z",
                        "aliases": ["CVE-2024-0001"],
                    }
                ),
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/vulnerabilities")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    row = items[0]
    # v0.4 additions on the response shape.
    assert row["provider_provenance"] == "osv"
    assert "CVE-2024-0001" in row["aliases"]
    assert row["fetched_at"] == "2024-01-01T00:00:00Z"
    assert row["package_name"] == "left-pad"
    # v0.4 honesty fix: the upstream provider did not supply
    # a confidence; the endpoint must return ``None`` and
    # must never substitute ``low`` / ``medium`` / ``high``.
    assert row["confidence"] is None, (
        "ComponentAdvisory confidence must be null when the "
        "upstream provider did not supply one; Lockverity "
        "never infers a confidence from severity, provider "
        "name, or the existence of the advisory."
    )
    assert row["ecosystem"] == "npm"


def test_enrichments_endpoint_returns_per_component_observations(app_config) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, component_id = _setup_scan(s)
        # Record a successful deps.dev observation. v0.4
        # stores the structured evidence envelope on the
        # dedicated ``evidence_json`` column; ``error_summary``
        # is empty for successful calls. The observation is
        # bound to the specific component (v0.4 honesty fix
        # for cross-component reason leakage).
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                component_id=component_id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.AVAILABLE,
                records_returned=1,
                cache_status="miss",
                error_code=None,
                error_summary=None,
                evidence_json=json.dumps(
                    {
                        "licences": ["MIT"],
                        "dependency_count": 2,
                    }
                ),
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
    assert r.status_code == 200
    body = r.json()
    assert body["items"]
    row = body["items"][0]
    assert row["package_name"] == "left-pad"
    assert row["ecosystem"] == "npm"
    assert row["provider_status"] == "available"
    assert "MIT" in row["license_observations"]
    assert row["dependency_count"] == 2
    assert row["source_provenance"] == "deps.dev"


def test_enrichments_endpoint_records_unavailable_state(app_config) -> None:
    with _db_session.SessionLocal() as s:
        scan_id, _, component_id = _setup_scan(s)
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                component_id=component_id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                cache_status="miss",
                error_code="provider_unavailable",
                error_summary="deps.dev 503",
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
    assert r.status_code == 200
    body = r.json()
    assert body["items"]
    row = body["items"][0]
    assert row["provider_status"] == "unavailable"
    assert row["unavailable_reason"] == "deps.dev 503"
    # Honest empty state, not a fabricated success.
    assert row["license_observations"] == []
    assert row["dependency_count"] is None
    assert row["source_provenance"] is None


def test_provider_health_endpoint_aggregates_real_observations(app_config) -> None:
    """The /provider-health rollup surfaces real per-provider data."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _component_id = _setup_scan(s)
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="osv",
                operation="osv_vulnerability_query",
                status=ProviderStatus.AVAILABLE,
                records_returned=3,
                cache_status="miss",
            )
        )
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="openssf",
                operation="openssf_scorecard_read",
                status=ProviderStatus.CACHED,
                records_returned=1,
                cache_status="hit",
            )
        )
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                cache_status="miss",
                error_code="provider_unavailable",
                error_summary="deps.dev 503",
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get("/api/v1/provider-health")
    assert r.status_code == 200
    body = r.json()
    providers = {entry["provider"]: entry for entry in body["entries"]}
    # The known providers are all present (even those
    # that have not yet been queried for this scan).
    for name in ("github", "osv", "deps_dev", "openssf"):
        assert name in providers
    # OSV is reported as available, Scorecard as cached,
    # deps.dev as unavailable.
    assert providers["osv"]["status"] == "available"
    assert providers["osv"]["records_returned"] == 3
    assert providers["openssf"]["status"] == "cached"
    assert providers["deps_dev"]["status"] == "unavailable"
    assert providers["deps_dev"]["last_error_code"] == "provider_unavailable"
    assert providers["github"]["status"] == "not_requested"


def test_exports_include_provider_provenance(app_config) -> None:
    """CycloneDX and findings.json exports carry the provider name."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _component_id = _setup_scan(s)
        # Write a vulnerability finding with a known
        # provider in the evidence envelope.
        from app.models.finding import (
            Finding,
            FindingCategory,
            FindingConfidence,
            FindingSeverity,
            FindingStatus,
        )

        repository_id = s.query(ScanRun).filter(ScanRun.id == scan_id).one().repository_id
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repository_id,
                rule_id="LOCK-VULN-001",
                category=FindingCategory.VULNERABILITY,
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.HIGH,
                title="Test vulnerability",
                summary="From OSV",
                stable_key="osv-finding-key",
                status=FindingStatus.OPEN,
                location_path="package.json",
                evidence_json=json.dumps({"provider": "osv", "fetched_at": "2024-01-01T00:00:00Z"}),
            )
        )
        s.commit()
    client = api_client(app)
    # CycloneDX
    r = client.get(f"/api/v1/scans/{scan_id}/exports/cyclonedx_json")
    assert r.status_code == 200
    sbom = r.json()
    vulns = sbom.get("vulnerabilities", [])
    assert any(
        v.get("id") == "LOCK-VULN-001"
        and any(p.get("value") == "osv" for p in v.get("properties", []))
        for v in vulns
    )
    # Findings JSON
    r = client.get(f"/api/v1/scans/{scan_id}/exports/findings_json")
    assert r.status_code == 200
    body = r.json()
    findings = body.get("findings", [])
    assert any(
        f.get("stable_key") == "osv-finding-key" and f.get("provider") == "osv" for f in findings
    )
    # The findings JSON also includes a ``providers`` block
    # (the per-scan observations summary).
    assert "providers" in body
    assert isinstance(body["providers"], list)
    # SARIF
    r = client.get(f"/api/v1/scans/{scan_id}/exports/sarif_json")
    assert r.status_code == 200
    sarif = r.json()
    run = sarif["runs"][0]
    providers = run["properties"].get("lockverity:providers", [])
    assert "osv" in providers
    # The per-result properties carry the provider name
    # for OSV-derived findings.
    result = run["results"][0]
    assert result["properties"].get("lockverity:provider") == "osv"


def test_endpoint_returns_empty_state_for_unscanned_components(app_config) -> None:
    """Components without observations render as honest empty state."""
    with _db_session.SessionLocal() as s:
        scan_id, _, _component_id = _setup_scan(s)
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
    assert r.status_code == 200
    body = r.json()
    assert body["items"]
    row = body["items"][0]
    # The component was never enriched; the row still
    # carries the component identity but every provider
    # field is null. The frontend renders an empty
    # state from this, not a fabricated success.
    assert row["provider_status"] is None
    assert row["license_observations"] == []
    assert row["dependency_count"] is None
    assert row["fetched_at"] is None
    assert row["cache_status"] == "miss"
