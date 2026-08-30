"""API tests for the v0.5 evidence-aware comparison endpoint.

These tests assert the v0.5 acceptance criteria at the HTTP
boundary. They build the v0.5 evidence through the database
directly (no orchestrator, no network), then exercise the
endpoint to confirm the response shape, the state vocabulary,
the validation contract, and the read-only promise.
"""

from __future__ import annotations

import json

from app.db import session as _db_session
from app.main import app
from app.models.advisory import Advisory
from app.models.component import Component, ComponentVersionSource
from app.models.component_advisory import ComponentAdvisory
from app.models.finding import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.services import repository_service, scan_service

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_repo(session, *, canonical_url: str) -> int:
    if "octocat" in canonical_url:
        repo = repository_service.create_repository_from_url(session, canonical_url)
    else:
        repo = Repository(
            source_type=RepositorySourceType.GITHUB,
            provider=RepositoryProvider.GITHUB,
            owner=canonical_url.rsplit("/", 2)[-2],
            name=canonical_url.rsplit("/", 1)[-1],
            canonical_url=canonical_url,
            default_branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        )
        session.add(repo)
        session.flush()
    return repo.id


def _setup_terminal_scan(
    session,
    *,
    repository_id: int,
    components: list[Component] | None = None,
    manifests: list[Manifest] | None = None,
    findings: list[Finding] | None = None,
    provider_observations: list[ProviderObservation] | None = None,
    component_advisories: list[ComponentAdvisory] | None = None,
) -> int:
    scan = scan_service.create_scan(
        session, repository_id=repository_id, trigger_type=ScanTriggerType.MANUAL
    )
    if manifests:
        for m in manifests:
            m.scan_run_id = scan.id
        session.add_all(manifests)
        session.flush()
    if components:
        for c in components:
            c.scan_run_id = scan.id
        session.add_all(components)
        session.flush()
    if findings:
        for f in findings:
            f.scan_run_id = scan.id
            f.repository_id = repository_id
        session.add_all(findings)
        session.flush()
    if component_advisories:
        for ca in component_advisories:
            ca.scan_run_id = scan.id
        session.add_all(component_advisories)
        session.flush()
    if provider_observations:
        for obs in provider_observations:
            obs.scan_run_id = scan.id
        session.add_all(provider_observations)
        session.flush()
    scan_service.transition_scan(session, scan.id, target=ScanStatus.RUNNING)
    scan_service.transition_scan(session, scan.id, target=ScanStatus.COMPLETED)
    session.commit()
    return scan.id


def _build_manifest(
    session,
    *,
    scan_id: int,
    path: str = "package.json",
    ecosystem: str = "npm",
    content_sha256: str | None = "a" * 64,
) -> Manifest:
    manifest = Manifest(
        scan_run_id=scan_id,
        path=path,
        manifest_type="package_json",
        ecosystem=ecosystem,
        parse_status=ManifestParseStatus.PARSED,
        content_sha256=content_sha256,
    )
    session.add(manifest)
    session.flush()
    return manifest


def _build_component(
    session,
    *,
    scan_id: int,
    manifest: Manifest,
    name: str,
    version: str = "1.0.0",
    direct: bool = True,
) -> Component:
    component = Component(
        scan_run_id=scan_id,
        manifest_id=manifest.id,
        ecosystem=manifest.ecosystem,
        package_name=name,
        version=version,
        version_source=ComponentVersionSource.LOCKFILE,
        direct=direct,
    )
    session.add(component)
    session.flush()
    return component


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


def test_compare_endpoint_returns_typed_v5_shape(app_config) -> None:
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        base = _setup_terminal_scan(s, repository_id=repo_id)
        head = _setup_terminal_scan(s, repository_id=repo_id)
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    assert r.status_code == 200
    body = r.json()
    # Top-level keys match the v0.5 typed response.
    expected_keys = {
        "base_scan_id",
        "head_scan_id",
        "repository_id",
        "base_trigger_type",
        "head_trigger_type",
        "base_resolved_commit_sha",
        "head_resolved_commit_sha",
        "base_analyzer_version",
        "head_analyzer_version",
        "base_completed_at",
        "head_completed_at",
        "generated_at",
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
    }
    assert expected_keys <= set(body.keys())
    # The coverage sub-object is also strictly typed.
    expected_coverage_keys = {
        "base_scan_status",
        "head_scan_status",
        "components_in_base",
        "components_in_head",
        "findings_in_base",
        "findings_in_head",
        "vulnerabilities_in_base",
        "vulnerabilities_in_head",
        "workflows_in_base",
        "workflows_in_head",
        "manifests_in_base",
        "manifests_in_head",
        "licence_assertions_in_base",
        "licence_assertions_in_head",
        "openssf_checks_in_base",
        "openssf_checks_in_head",
        "providers_with_changed_state",
        "providers_with_indeterminate_head",
    }
    assert expected_coverage_keys <= set(body["coverage"].keys())


def test_compare_endpoint_rejects_identical_scans(app_config) -> None:
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        scan = _setup_terminal_scan(s, repository_id=repo_id)
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan}/compare/{scan}")
    # VALIDATION_ERROR maps to 422 by default; either is acceptable.
    assert r.status_code in {400, 422}
    assert r.json()["error"]["code"] == "validation_error"


def test_compare_endpoint_rejects_cross_workspace(app_config) -> None:
    with _db_session.SessionLocal() as s:
        repo1 = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        repo2 = _setup_repo(s, canonical_url="https://github.com/anthropics/anthropic-sdk-python")
        base = _setup_terminal_scan(s, repository_id=repo1)
        head = _setup_terminal_scan(s, repository_id=repo2)
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    assert r.status_code in {400, 422}
    assert r.json()["error"]["code"] == "validation_error"


def test_compare_endpoint_rejects_non_terminal_scans(app_config) -> None:
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        base = _setup_terminal_scan(s, repository_id=repo_id)
        head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        s.commit()
        head_id = head.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head_id}/compare/{base}")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "illegal_transition"


def test_compare_endpoint_returns_404_for_missing_scans(app_config) -> None:
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        head = _setup_terminal_scan(s, repository_id=repo_id)
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/999999")
    assert r.status_code == 404
    r = client.get(f"/api/v1/scans/999999/compare/{head}")
    assert r.status_code == 404


def test_compare_endpoint_component_diffs(app_config) -> None:
    """A clean component diff for added / removed / changed components."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        # Create a queued scan, then attach manifests and components
        # through the queued scan's id, then transition it.
        queued_base = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        base_manifest = _build_manifest(s, scan_id=queued_base.id, content_sha256="a" * 64)
        _build_component(
            s, scan_id=queued_base.id, manifest=base_manifest, name="left-pad", version="1.0.0"
        )
        _build_component(
            s, scan_id=queued_base.id, manifest=base_manifest, name="stay", version="1.0.0"
        )
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.COMPLETED)
        s.commit()
        base = queued_base.id

        queued_head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_manifest = _build_manifest(s, scan_id=queued_head.id, content_sha256="b" * 64)
        _build_component(
            s, scan_id=queued_head.id, manifest=head_manifest, name="left-pad", version="2.0.0"
        )
        _build_component(
            s, scan_id=queued_head.id, manifest=head_manifest, name="stay", version="1.0.0"
        )
        _build_component(
            s, scan_id=queued_head.id, manifest=head_manifest, name="right-pad", version="1.0.0"
        )
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.COMPLETED)
        s.commit()
        head = queued_head.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    assert r.status_code == 200
    body = r.json()
    # With identity keyed on (ecosystem, package_name, version):
    # - left-pad 1.0.0 only in base -> no_longer_observed
    # - left-pad 2.0.0 only in head -> newly_observed
    # - stay 1.0.0 in both -> still_observed
    # - right-pad 1.0.0 only in head -> newly_observed
    by_pv = {(row["package_name"], row["version"]): row["state"] for row in body["components"]}
    assert by_pv == {
        ("left-pad", "1.0.0"): "no_longer_observed",
        ("left-pad", "2.0.0"): "newly_observed",
        ("stay", "1.0.0"): "still_observed",
        ("right-pad", "1.0.0"): "newly_observed",
    }
    # No row carries the legacy shape or the fabricated
    # "unambiguous_version_change" / "ambiguity_reason" fields.
    for row in body["components"]:
        assert "version_base" not in row
        assert "version_head" not in row
        assert "unambiguous_version_change" not in row
        assert "ambiguity_reason" not in row
        assert row["state"] in {
            "newly_observed",
            "still_observed",
            "no_longer_observed",
            "changed_observation",
            "coverage_changed",
            "comparison_indeterminate",
        }


def test_compare_endpoint_workflow_diff(app_config) -> None:
    """Workflow findings are diffed by stable_key."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        base = _setup_terminal_scan(
            s,
            repository_id=repo_id,
            findings=[
                Finding(
                    rule_id="LOCK-WF-001",
                    category=FindingCategory.WORKFLOW,
                    severity=FindingSeverity.HIGH,
                    confidence=FindingConfidence.HIGH,
                    title="Unpinned third-party action",
                    summary="actions/checkout is not pinned",
                    location_path=".github/workflows/ci.yml",
                    stable_key="wf-1",
                )
            ],
        )
        head = _setup_terminal_scan(
            s,
            repository_id=repo_id,
            findings=[
                Finding(
                    rule_id="LOCK-WF-001",
                    category=FindingCategory.WORKFLOW,
                    severity=FindingSeverity.MEDIUM,
                    confidence=FindingConfidence.HIGH,
                    title="Unpinned third-party action",
                    summary="actions/checkout is not pinned",
                    location_path=".github/workflows/ci.yml",
                    stable_key="wf-1",
                ),
                Finding(
                    rule_id="LOCK-WF-002",
                    category=FindingCategory.WORKFLOW,
                    severity=FindingSeverity.HIGH,
                    confidence=FindingConfidence.HIGH,
                    title="Token write permissions",
                    summary="workflow writes to GITHUB_TOKEN",
                    location_path=".github/workflows/release.yml",
                    stable_key="wf-2",
                ),
            ],
        )
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    body = r.json()
    by_key = {row["stable_key"]: row["state"] for row in body["workflows"]}
    assert by_key == {
        "wf-1": "changed_observation",
        "wf-2": "newly_observed",
    }
    wf1 = next(row for row in body["workflows"] if row["stable_key"] == "wf-1")
    assert wf1["severity_base"] == "high"
    assert wf1["severity_head"] == "medium"


def test_compare_endpoint_provider_coverage_states(app_config) -> None:
    """The provider coverage list surfaces explicit per-provider states."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        base = _setup_terminal_scan(
            s,
            repository_id=repo_id,
            provider_observations=[
                ProviderObservation(
                    provider="osv",
                    operation="osv_vulnerability_query",
                    status=ProviderStatus.AVAILABLE,
                    records_returned=2,
                    cache_status="miss",
                ),
                ProviderObservation(
                    provider="deps_dev",
                    operation="deps_dev_enrichment",
                    status=ProviderStatus.UNAVAILABLE,
                    records_returned=0,
                    cache_status="miss",
                    error_code="provider_unavailable",
                    error_summary="deps.dev 503",
                ),
            ],
        )
        head = _setup_terminal_scan(
            s,
            repository_id=repo_id,
            provider_observations=[
                ProviderObservation(
                    provider="osv",
                    operation="osv_vulnerability_query",
                    status=ProviderStatus.CACHED,
                    records_returned=2,
                    cache_status="hit",
                ),
                ProviderObservation(
                    provider="deps_dev",
                    operation="deps_dev_enrichment",
                    status=ProviderStatus.PARTIAL,
                    records_returned=1,
                    cache_status="miss",
                ),
            ],
        )
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    body = r.json()
    by_provider = {row["provider"]: row for row in body["providers"]}
    assert by_provider["osv"]["state_base"] == "successful"
    assert by_provider["osv"]["state_head"] == "cached"
    assert by_provider["deps_dev"]["state_base"] == "unavailable"
    assert by_provider["deps_dev"]["state_head"] == "partial"
    assert by_provider["deps_dev"]["error_summary_base"] == "deps.dev 503"
    assert by_provider["deps_dev"]["evidence_present_base"] is False
    assert by_provider["deps_dev"]["evidence_present_head"] is False


def test_compare_endpoint_vulnerability_preserves_provenance(app_config) -> None:
    """Provenance, fetched_at, and aliases are preserved across scans."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        # The advisory is a global row; share it across both scans.
        advisory = Advisory(
            source="osv",
            source_advisory_id="GHSA-aaaa-bbbb-cccc",
            canonical_id="CVE-2024-0001",
            summary="Sample advisory",
            details_url="https://example.com/advisory",
            raw_payload_sha256="a" * 64,
        )
        s.add(advisory)
        s.flush()

        queued_base = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        base_manifest = _build_manifest(s, scan_id=queued_base.id)
        base_component = _build_component(
            s, scan_id=queued_base.id, manifest=base_manifest, name="left-pad"
        )
        s.add(
            ComponentAdvisory(
                scan_run_id=queued_base.id,
                component_id=base_component.id,
                advisory_id=advisory.id,
                affected=True,
                fixed_versions_json=json.dumps(["1.3.0"]),
                severity_source="osv",
                severity_label="CVSS_V3",
                severity_score=7.5,
                evidence_json=json.dumps(
                    {
                        "provider": "osv",
                        "fetched_at": "2024-01-01T00:00:00Z",
                        "aliases": ["CVE-2024-0001"],
                    }
                ),
            )
        )
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.COMPLETED)
        s.commit()
        base = queued_base.id

        queued_head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_manifest = _build_manifest(s, scan_id=queued_head.id)
        head_component = _build_component(
            s, scan_id=queued_head.id, manifest=head_manifest, name="left-pad"
        )
        s.add(
            ComponentAdvisory(
                scan_run_id=queued_head.id,
                component_id=head_component.id,
                advisory_id=advisory.id,
                affected=True,
                fixed_versions_json=json.dumps(["1.3.0"]),
                severity_source="osv",
                severity_label="CVSS_V3",
                severity_score=7.5,
                evidence_json=json.dumps(
                    {
                        "provider": "osv",
                        "fetched_at": "2024-02-01T00:00:00Z",
                        "aliases": ["CVE-2024-0001"],
                    }
                ),
            )
        )
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.COMPLETED)
        s.commit()
        head = queued_head.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    body = r.json()
    assert len(body["vulnerabilities"]) == 1
    vuln = body["vulnerabilities"][0]
    assert vuln["provider_provenance_base"] == "osv"
    assert vuln["provider_provenance_head"] == "osv"
    assert vuln["fetched_at_base"] == "2024-01-01T00:00:00Z"
    assert vuln["fetched_at_head"] == "2024-02-01T00:00:00Z"
    assert vuln["advisory_canonical_id"] == "CVE-2024-0001"


def test_compare_endpoint_vulnerability_indeterminate_when_provider_unavailable(
    app_config,
) -> None:
    """A disappearing vulnerability is not 'fixed' when the head provider is unavailable."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        queued_base = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        base_manifest = _build_manifest(s, scan_id=queued_base.id)
        base_component = _build_component(
            s, scan_id=queued_base.id, manifest=base_manifest, name="left-pad"
        )
        base_advisory = Advisory(
            source="osv",
            source_advisory_id="GHSA-1111-2222-3333",
            canonical_id="CVE-2024-0002",
            summary="Sample advisory",
            raw_payload_sha256="a" * 64,
        )
        s.add(base_advisory)
        s.flush()
        s.add(
            ComponentAdvisory(
                scan_run_id=queued_base.id,
                component_id=base_component.id,
                advisory_id=base_advisory.id,
                affected=True,
                fixed_versions_json=json.dumps(["1.3.0"]),
                severity_source="osv",
                severity_label="CVSS_V3",
                severity_score=7.5,
                evidence_json=json.dumps({"provider": "osv", "fetched_at": "2024-01-01T00:00:00Z"}),
            )
        )
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.COMPLETED)
        s.commit()
        base = queued_base.id

        # Head scan: same component but provider unavailable.
        queued_head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_manifest = _build_manifest(s, scan_id=queued_head.id)
        _build_component(s, scan_id=queued_head.id, manifest=head_manifest, name="left-pad")
        s.add(
            ProviderObservation(
                scan_run_id=queued_head.id,
                provider="osv",
                operation="osv_vulnerability_query",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                cache_status="miss",
                error_code="provider_unavailable",
                error_summary="osv.dev 503",
            )
        )
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.COMPLETED)
        s.commit()
        head = queued_head.id
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    body = r.json()
    assert len(body["vulnerabilities"]) == 1
    assert body["vulnerabilities"][0]["state"] == "comparison_indeterminate"
    assert any("unavailable" in r for r in body["indeterminate_reasons"])


def test_compare_endpoint_does_not_write_to_database(app_config) -> None:
    """A read-only call leaves the database unchanged."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        base = _setup_terminal_scan(s, repository_id=repo_id)
        head = _setup_terminal_scan(s, repository_id=repo_id)
        from app.models.component import Component
        from app.models.scan_run import ScanRun
        from sqlalchemy import select

        before = {}
        for model in [Component, ScanRun]:
            for row in s.execute(select(model)).scalars():
                before[(model.__tablename__, row.id)] = row.updated_at
    client = api_client(app)
    r = client.get(f"/api/v1/scans/{head}/compare/{base}")
    assert r.status_code == 200
    with _db_session.SessionLocal() as s:
        after = {}
        for model in [Component, ScanRun]:
            for row in s.execute(select(model)).scalars():
                after[(model.__tablename__, row.id)] = row.updated_at
    assert before == after


def test_compare_endpoint_response_is_deterministically_ordered(app_config) -> None:
    """Repeated calls return identical, sorted output."""
    with _db_session.SessionLocal() as s:
        repo_id = _setup_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        queued_base = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        base_manifest = _build_manifest(s, scan_id=queued_base.id)
        for i in range(5):
            _build_component(
                s,
                scan_id=queued_base.id,
                manifest=base_manifest,
                name=f"pkg-{i:02d}",
                version="1.0.0",
            )
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_base.id, target=ScanStatus.COMPLETED)
        s.commit()
        base = queued_base.id

        queued_head = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_manifest = _build_manifest(s, scan_id=queued_head.id)
        for i in range(5):
            _build_component(
                s,
                scan_id=queued_head.id,
                manifest=head_manifest,
                name=f"pkg-{i:02d}",
                version="2.0.0",
            )
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.RUNNING)
        scan_service.transition_scan(s, queued_head.id, target=ScanStatus.COMPLETED)
        s.commit()
        head = queued_head.id
    client = api_client(app)
    a = client.get(f"/api/v1/scans/{head}/compare/{base}").json()
    b = client.get(f"/api/v1/scans/{head}/compare/{base}").json()
    assert [c["package_name"] for c in a["components"]] == [
        c["package_name"] for c in b["components"]
    ]
    assert [c["state"] for c in a["components"]] == [c["state"] for c in b["components"]]
    # Components sorted alphabetically
    assert [c["package_name"] for c in a["components"]] == sorted(
        c["package_name"] for c in a["components"]
    )
