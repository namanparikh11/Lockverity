"""v1.0 human-readable evidence report tests.

The tests exercise the free function, the service, and
the API routes over a small in-memory SQLite database. The
fixtures build a minimal but representative scan: a
completed scan with one manifest, three components (with
varied evidence conditions), optional licence findings,
optional provider observations, and an optional
dependency edge.

The negative tests prove the bounded not-found / no-side-
effect rules.
"""

from __future__ import annotations

import json

import pytest

# Compatibility: the evidence module re-exports the version
# symbol so callers can introspect the running app version
# without an explicit circular import.
from app._version import __version__
from app.db.base import Base
from app.main import app
from app.models.component import Component, ComponentVersionSource
from app.models.dependency_edge import DependencyEdge
from app.models.finding import Finding, FindingCategory
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.reports.evidence import (
    COMPONENT_TABLE_LIMIT,
    EVIDENCE_REPORT_OMISSIONS,
    EvidenceReportService,
    render_evidence_report_markdown,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.api_client import api_client

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def session_factory():
    """A single in-memory SQLite engine + session factory.

    ``StaticPool`` keeps the connection alive across thread
    boundaries (FastAPI's ``TestClient`` runs the route
    handler in a worker thread).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    yield factory
    eng.dispose()


@pytest.fixture
def seeded_scan(session_factory):
    """Seed a completed scan with three components, one manifest,
    one licence finding, one provider observation, and one
    dependency edge.
    """
    session = session_factory()
    try:
        repo = Repository(
            id=1,
            source_type=RepositorySourceType.GITHUB,
            provider=RepositoryProvider.GITHUB,
            owner="example",
            name="lockverity-fixture",
            canonical_url="https://github.com/example/lockverity-fixture",
            default_branch="main",
            description=None,
            visibility=RepositoryVisibility.PUBLIC,
            archived=False,
            last_provider_sync_at=None,
        )
        scan = ScanRun(
            id=1,
            repository_id=1,
            status=ScanStatus.COMPLETED,
            trigger_type=ScanTriggerType.MANUAL,
            requested_ref="main",
            resolved_commit_sha="deadbeef" * 5,
            analyzer_version="lockverity 1.0.0",
            started_at=None,
            completed_at=None,
            failure_code=None,
            failure_summary=None,
        )
        manifest = Manifest(
            id=1,
            scan_run_id=1,
            path="package.json",
            manifest_type="npm",
            ecosystem="npm",
            parse_status=ManifestParseStatus.PARSED,
            parse_warning_count=0,
            content_sha256="a" * 64,
        )
        alpha = Component(
            id=1,
            scan_run_id=1,
            manifest_id=1,
            ecosystem="npm",
            package_name="alpha",
            version="1.2.3",
            version_source=ComponentVersionSource.MANIFEST,
            package_url="pkg:npm/alpha@1.2.3",
            scope=None,
            relationship=None,
            direct=True,
            development=False,
            optional=False,
            integrity=None,
        )
        beta = Component(
            id=2,
            scan_run_id=1,
            manifest_id=1,
            ecosystem="npm",
            package_name="beta",
            version="0.0.1",
            version_source=ComponentVersionSource.LOCKFILE,
            package_url=None,
            scope=None,
            relationship=None,
            direct=True,
            development=False,
            optional=False,
            integrity=None,
        )
        gamma = Component(
            id=3,
            scan_run_id=1,
            manifest_id=1,
            ecosystem="cargo",
            package_name="gamma",
            version=None,
            version_source=ComponentVersionSource.UNRESOLVED,
            package_url=None,
            scope=None,
            relationship=None,
            direct=False,
            development=False,
            optional=False,
            integrity=None,
        )
        session.add_all([repo, scan, manifest, alpha, beta, gamma])
        session.flush()
        session.add(
            Finding(
                id=1,
                scan_run_id=1,
                repository_id=1,
                rule_id="licence.observed",
                category=FindingCategory.LICENCE,
                severity="informational",
                confidence="high",
                title="alpha licence observed",
                summary="alpha MIT observed",
                remediation=None,
                evidence_json=json.dumps(
                    {
                        "evidence": {
                            "component_id": 1,
                            "licences": ["MIT"],
                        }
                    }
                ),
                location_path="package.json",
                location_start_line=None,
                location_end_line=None,
                stable_key="licence-alpha-1",
                status="open",
            )
        )
        session.add(
            ProviderObservation(
                id=1,
                scan_run_id=1,
                provider="osv",
                operation="query",
                status=ProviderStatus.AVAILABLE,
                cache_status=None,
                http_status=200,
                records_returned=1,
                requested_at=None,
                completed_at=None,
                component_id=1,
                error_code=None,
                error_summary=None,
            )
        )
        session.add(
            DependencyEdge(
                id=1,
                scan_run_id=1,
                parent_component_id=1,
                child_component_id=2,
                depth=1,
                relationship="runtime",
            )
        )
        session.commit()
    finally:
        session.close()
    return {"scan_id": 1, "component_ids": [1, 2, 3]}


@pytest.fixture
def client(session_factory, monkeypatch):
    """A FastAPI TestClient with the DB session overridden to
    use the in-memory fixture.
    """
    from app.api import deps as _deps
    from app.db import session as _db_session

    def _override_get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[_deps.DBSession] = _override_get_db
    monkeypatch.setattr(_db_session, "SessionLocal", session_factory)
    yield api_client(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# Preview response shape
# ---------------------------------------------------------------------


class TestPreviewResponse:
    def test_preview_returns_deterministic_metadata(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        meta = report["metadata"]
        assert meta["report_name"] == "Lockverity Evidence Report"
        assert meta["generator"] == "lockverity"
        assert meta["generator_version"] == __version__
        assert meta["report_format"] == "markdown"
        assert meta["report_format_version"] == "1.0"
        assert "scan_id" in meta
        assert "repository_id" in meta

    def test_preview_scan_identity(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        scan = report["scan"]
        assert scan["scan_id"] == seeded_scan["scan_id"]
        assert scan["scan_status"] == "completed"
        assert scan["repository_canonical_url"] == ("https://github.com/example/lockverity-fixture")
        assert scan["repository_source_type"] == "github"
        assert scan["repository_visibility"] == "public"

    def test_preview_summary_counts(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        summary = report["summary"]
        assert summary["component_count"] == 3
        assert summary["manifest_count"] == 1
        assert summary["direct_count"] == 2
        assert summary["transitive_count"] == 1
        assert summary["version_present_count"] == 2
        assert summary["version_missing_count"] == 1
        assert summary["licence_observed_count"] == 1
        assert summary["licence_missing_count"] == 2
        assert summary["provider_observed_count"] == 1
        assert summary["provider_missing_count"] == 2
        assert summary["edges_observed_count"] == 1
        assert summary["edges_none_observed_count"] == 2
        assert summary["purl_persisted_count"] == 1
        assert summary["purl_constructible_count"] == 1
        assert summary["purl_omitted_count"] == 1
        assert summary["appears_in_cyclonedx_17_count"] == 3
        assert summary["cyclonedx_version_omitted_count"] == 1
        assert summary["cyclonedx_relationships_emitted_count"] == 1
        assert summary["ecosystems"] == {"cargo": 1, "npm": 2}

    def test_preview_evidence_gaps(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        gaps = report["evidence_gaps"]
        assert gaps["missing_version_count"] == 1
        assert gaps["missing_licence_evidence_count"] == 2
        assert gaps["missing_provider_evidence_count"] == 2
        assert gaps["no_persisted_edges_count"] == 2
        assert gaps["purl_omitted_count"] == 1

    def test_preview_component_table_is_deterministic(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report_a = service.fetch(scan_run_id=seeded_scan["scan_id"])
        report_b = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report_a is not None and report_b is not None
        assert report_a["components"] == report_b["components"]
        assert report_a["components"][0]["package_name"] == "alpha"
        assert report_a["components"][-1]["package_name"] == "gamma"
        assert report_a["truncated"]["truncated"] is False
        assert report_a["truncated"]["shown"] == 3
        assert report_a["truncated"]["total"] == 3

    def test_preview_omissions_block(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        assert report["omissions"] == list(EVIDENCE_REPORT_OMISSIONS)
        assert "no_clean_verdict" in report["omissions"]
        assert "no_certification" in report["omissions"]
        assert "no_compliance_pass_or_fail" in report["omissions"]
        assert "no_complete_dependency_graph_claim" in report["omissions"]

    def test_preview_disclaimer_is_evidence_only(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        disclaimer = report["disclaimer"]
        assert "evidence report" in disclaimer.lower()
        assert "not a security verdict" in disclaimer.lower()
        assert "not a certification" in disclaimer.lower()

    def test_preview_export_relationship_block(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        rel = report["export_relationship"]
        assert rel["appears_in_cyclonedx_17_count"] == 3
        assert rel["cyclonedx_version_omitted_count"] == 1
        assert rel["cyclonedx_relationships_emitted_count"] == 1
        assert rel["cyclonedx_relationships_omitted_count"] == 2


# ---------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------


class TestMarkdownRendering:
    def test_markdown_contains_metadata(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "# Lockverity Evidence Report" in md
        assert "Generator: `lockverity`" in md
        assert f"Generator version: `{__version__}`" in md
        assert "Report format: `markdown`" in md
        assert "Report format version: `1.0`" in md
        assert "Scan id: `1`" in md

    def test_markdown_contains_evidence_only_disclaimer(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "evidence report" in md.lower()
        assert "not a security verdict" in md.lower()
        assert "not a certification" in md.lower()
        assert "not a compliance" in md.lower()

    def test_markdown_contains_scan_identity(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "## Scan identity" in md
        assert "github.com/example/lockverity-fixture" in md
        assert "Scan status: `completed`" in md
        assert "Repository visibility: `public`" in md

    def test_markdown_contains_summary_counts(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "## Scan summary" in md
        assert "Component count: **3**" in md
        assert "Manifest count: **1**" in md
        assert "Direct components: **2**" in md
        assert "Transitive components: **1**" in md
        assert "Components with version present: **2**" in md
        assert "Components with version missing: **1**" in md
        assert "Components with licence evidence observed: **1**" in md
        assert "Components with licence evidence missing: **2**" in md
        assert "Components with provider evidence observed: **1**" in md
        assert "Components with provider evidence missing: **2**" in md
        assert "Components with persisted dependency edges: **1**" in md
        assert "Components with no persisted edges: **2**" in md
        assert "PURL persisted: **1**" in md
        assert "PURL constructible: **1**" in md
        assert "PURL omitted: **1**" in md

    def test_markdown_uses_no_persisted_edges_wording(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "no persisted edges" in md.lower()
        lower = md.lower()
        # The misleading phrase is never used.
        assert "no dependencies" not in lower

    def test_markdown_contains_component_table(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "## Component table" in md
        assert "| Ecosystem | Package | Version |" in md
        for name in ("alpha", "beta", "gamma"):
            assert f"| `npm` | `{name}`" in md or f"| `cargo` | `{name}`" in md

    def test_markdown_contains_cyclonedx_relationship(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "## Export relationship" in md
        assert "Components that appear in the CycloneDX 1.7 BOM: **3**" in md
        assert "Components with version omitted from the BOM: **1**" in md
        assert "Components with dependency relationships emitted: **1**" in md
        assert "Components with dependency relationships omitted (no persisted edges): **2**" in md

    def test_markdown_contains_omissions_block(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report)
        assert "## Evidence-honesty markers" in md
        for marker in EVIDENCE_REPORT_OMISSIONS:
            assert f"`{marker}`" in md

    def test_markdown_contains_no_forbidden_conclusions(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report).lower()
        for forbidden in [
            "clean bill",
            "compliant",
            "complete dependency graph",
            "fixed all issues",
        ]:
            assert forbidden not in md

    def test_markdown_is_deterministic(self, seeded_scan, session_factory):
        service = EvidenceReportService(session_factory)
        report_a = service.fetch(scan_run_id=seeded_scan["scan_id"])
        report_b = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report_a is not None and report_b is not None
        md_a = render_evidence_report_markdown(report_a)
        md_b = render_evidence_report_markdown(report_b)
        assert md_a == md_b


# ---------------------------------------------------------------------
# Component table truncation
# ---------------------------------------------------------------------


class TestTruncation:
    def test_table_truncation_is_explicit(self, session_factory):
        """When the component count exceeds the limit, the
        Markdown carries an explicit truncation note.
        """
        session = session_factory()
        try:
            session.add(
                Repository(
                    id=1,
                    source_type=RepositorySourceType.GITHUB,
                    provider=RepositoryProvider.GITHUB,
                    owner="example",
                    name="big",
                    canonical_url="https://github.com/example/big",
                    default_branch="main",
                    description=None,
                    visibility=RepositoryVisibility.PUBLIC,
                    archived=False,
                    last_provider_sync_at=None,
                )
            )
            session.add(
                ScanRun(
                    id=1,
                    repository_id=1,
                    status=ScanStatus.COMPLETED,
                    trigger_type=ScanTriggerType.MANUAL,
                    requested_ref="main",
                    resolved_commit_sha=None,
                    analyzer_version=None,
                    started_at=None,
                    completed_at=None,
                    failure_code=None,
                    failure_summary=None,
                )
            )
            session.add(
                Manifest(
                    id=1,
                    scan_run_id=1,
                    path="package.json",
                    manifest_type="npm",
                    ecosystem="npm",
                    parse_status=ManifestParseStatus.PARSED,
                    parse_warning_count=0,
                    content_sha256="a" * 64,
                )
            )
            session.flush()
            for i in range(COMPONENT_TABLE_LIMIT + 1):
                session.add(
                    Component(
                        id=i + 1,
                        scan_run_id=1,
                        manifest_id=1,
                        ecosystem="npm",
                        package_name=f"pkg-{i:04d}",
                        version="1.0.0",
                        version_source=ComponentVersionSource.MANIFEST,
                        package_url=f"pkg:npm/pkg-{i:04d}@1.0.0",
                        scope=None,
                        relationship=None,
                        direct=True,
                        development=False,
                        optional=False,
                        integrity=None,
                    )
                )
            session.commit()
        finally:
            session.close()

        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=1)
        assert report is not None
        assert report["truncated"]["truncated"] is True
        assert report["truncated"]["shown"] == COMPONENT_TABLE_LIMIT
        assert report["truncated"]["total"] == COMPONENT_TABLE_LIMIT + 1
        assert "100" in report["truncated"]["reason"]
        md = render_evidence_report_markdown(report)
        assert "truncated" in md.lower()
        assert f"shown {COMPONENT_TABLE_LIMIT} of {COMPONENT_TABLE_LIMIT + 1}" in md


# ---------------------------------------------------------------------
# Negative / boundary tests
# ---------------------------------------------------------------------


class TestBoundaries:
    def test_unknown_scan_returns_none(self, session_factory):
        service = EvidenceReportService(session_factory)
        assert service.fetch(scan_run_id=9999) is None

    def test_failed_scan_produces_honest_empty_report(self, session_factory):
        """A failed scan has no inventory and must report
        ``not_applicable`` coverage, not a fake success.
        """
        session = session_factory()
        try:
            session.add(
                Repository(
                    id=1,
                    source_type=RepositorySourceType.GITHUB,
                    provider=RepositoryProvider.GITHUB,
                    owner="example",
                    name="failed",
                    canonical_url="https://github.com/example/failed",
                    default_branch="main",
                    description=None,
                    visibility=RepositoryVisibility.PUBLIC,
                    archived=False,
                    last_provider_sync_at=None,
                )
            )
            session.add(
                ScanRun(
                    id=1,
                    repository_id=1,
                    status=ScanStatus.FAILED,
                    trigger_type=ScanTriggerType.MANUAL,
                    requested_ref="main",
                    resolved_commit_sha=None,
                    analyzer_version=None,
                    started_at=None,
                    completed_at=None,
                    failure_code="scanner_crashed",
                    failure_summary="Scanner crashed before inventory capture.",
                )
            )
            session.commit()
        finally:
            session.close()

        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=1)
        assert report is not None
        assert report["summary"]["component_count"] == 0
        assert report["summary"]["manifest_count"] == 0
        assert report["evidence_coverage"]["inventory_coverage"] == "not_applicable"
        assert report["export_relationship"]["cyclonedx_eligible"] is False
        md = render_evidence_report_markdown(report)
        assert "no components recorded" in md.lower()
        lower = md.lower()
        for forbidden in ["clean bill", "compliant", "complete dependency graph"]:
            assert forbidden not in lower

    def test_no_external_http(self, seeded_scan, session_factory, monkeypatch):
        """The report must not call any external HTTP."""
        import socket as _socket

        called = {"value": False}
        real_getaddrinfo = _socket.getaddrinfo

        def _spy(*args, **kwargs):
            called["value"] = True
            return real_getaddrinfo(*args, **kwargs)

        _socket.getaddrinfo = _spy
        try:
            service = EvidenceReportService(session_factory)
            service.fetch(scan_run_id=seeded_scan["scan_id"])
        finally:
            _socket.getaddrinfo = real_getaddrinfo
        assert called["value"] is False, "service triggered DNS resolution"

    def test_no_database_writes(self, seeded_scan, session_factory):
        """The report must not write to the database. The
        row counts must not change after the report is
        fetched.
        """
        service = EvidenceReportService(session_factory)

        session = session_factory()
        before = sum(1 for _ in session.query(Component).all())
        session.close()

        service.fetch(scan_run_id=seeded_scan["scan_id"])

        session = session_factory()
        after = sum(1 for _ in session.query(Component).all())
        session.close()
        assert before == after

    def test_no_local_paths_or_secrets_leak(self, seeded_scan, session_factory):
        """The Markdown must not contain local filesystem
        paths or other internal details.
        """
        service = EvidenceReportService(session_factory)
        report = service.fetch(scan_run_id=seeded_scan["scan_id"])
        assert report is not None
        md = render_evidence_report_markdown(report).lower()
        for forbidden in [
            "c:\\",
            "c:/",
            "users\\naman",
            "users/naman",
            "traceback",
            "stack trace",
            "internal path",
        ]:
            assert forbidden not in md


# ---------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------


class TestApiRoutes:
    def test_preview_route_returns_report(self, client, seeded_scan):
        r = client.get(f"/api/v1/scans/{seeded_scan['scan_id']}/reports/evidence-summary/preview")
        assert r.status_code == 200
        body = r.json()
        assert body["metadata"]["generator"] == "lockverity"
        assert body["metadata"]["report_format"] == "markdown"
        assert body["summary"]["component_count"] == 3
        assert body["scan"]["scan_id"] == seeded_scan["scan_id"]

    def test_preview_route_returns_404_for_unknown_scan(self, client):
        r = client.get("/api/v1/scans/9999/reports/evidence-summary/preview")
        assert r.status_code == 404

    def test_markdown_download_route_returns_markdown(self, client, seeded_scan):
        r = client.get(f"/api/v1/scans/{seeded_scan['scan_id']}/reports/evidence-summary.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert f"lockverity-scan-{seeded_scan['scan_id']}.evidence-report.md" in cd
        body = r.text
        assert "# Lockverity Evidence Report" in body
        assert "evidence report" in body.lower()

    def test_markdown_download_route_returns_404_for_unknown_scan(self, client):
        r = client.get("/api/v1/scans/9999/reports/evidence-summary.md")
        assert r.status_code == 404

    def test_markdown_download_route_does_not_collide_with_legacy_export(self, client, seeded_scan):
        """The new dedicated ``/reports/evidence-summary.md``
        route must be matched before the legacy
        ``/exports/{format}`` dispatcher.
        """
        r = client.get(f"/api/v1/scans/{seeded_scan['scan_id']}/reports/evidence-summary.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
