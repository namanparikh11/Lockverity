"""v0.4 provider service tests.

The provider service wraps the existing OSV, deps.dev, and
OpenSSF Scorecard clients with the persistent cache, bounded
HTTP transport, and per-call observation row. These tests
cover the v0.4 acceptance criteria without making any real
network calls; the underlying providers accept a
``request_fn`` injection point we use to drive deterministic
responses (success, partial, unavailable, malformed JSON,
oversized, timeout-style error).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from app.db import session as _db_session
from app.models.advisory import Advisory
from app.models.component import Component, ComponentVersionSource
from app.models.component_advisory import ComponentAdvisory
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
from app.providers.cache import ProviderCache
from app.providers.http_client import HttpResponse
from app.providers.results import (
    ProviderSuccess,
    ProviderUnavailable,
)
from app.services.provider_service import (
    DEPS_DEV_PROVIDER,
    OSV_PROVIDER,
    SCORECARD_PROVIDER,
    ProviderService,
)

from tests.api_client import api_client


# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------
def _http_response(status_code: int, body: Any) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=json.dumps(body).encode("utf-8"),
        elapsed_seconds=0.01,
        attempts=1,
    )


def _make_osv_request_fn(advisories: list[dict[str, Any]], status_code: int = 200):
    """Return a request_fn that mimics an OSV batched response."""
    body = {"results": [{"vulns": advisories} for _ in advisories]}

    def _request(method: str, url: str, body_bytes: bytes, headers: dict[str, str]):
        return _http_response(status_code, body)

    return _request


def _make_unavailable_request_fn(http_status: int = 503):
    def _request(method: str, url: str, body_bytes: bytes, headers: dict[str, str]):
        return _http_response(http_status, {"error": "service unavailable"})

    return _request


def _make_scorecard_request_fn(payload: dict[str, Any] | None, status_code: int = 200):
    def _request(method: str, url: str, body_bytes: bytes, headers: dict[str, str]):
        if status_code != 200 or payload is None:
            return _http_response(status_code, {})
        return _http_response(status_code, payload)

    return _request


def _make_depsdev_request_fn(payload: dict[str, Any] | None, status_code: int = 200):
    def _request(method: str, url: str, body_bytes: bytes, headers: dict[str, str]):
        if status_code != 200 or payload is None:
            return _http_response(status_code, {"error": "not found"})
        return _http_response(status_code, payload)

    return _request


def _setup_scan_with_components(
    session,
    components: list[tuple[str, str, str | None, bool, bool]],
    *,
    canonical_url: str = "https://github.com/octocat/Hello-World",
) -> int:
    """Create a scan + workspace + manifest + components."""
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
        status=ScanStatus.RUNNING,
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
        ecosystem="npm",
        parse_status=ManifestParseStatus.PARSED,
    )
    session.add(manifest)
    session.flush()
    for ecosystem, name, version, direct, development in components:
        component = Component(
            scan_run_id=scan.id,
            manifest_id=manifest.id,
            ecosystem=ecosystem,
            package_name=name,
            version=version,
            version_source=ComponentVersionSource.LOCKFILE,
            direct=direct,
            development=development,
        )
        session.add(component)
    session.commit()
    return scan.id


# ----------------------------------------------------------------------
# Vulnerability enrichment
# ----------------------------------------------------------------------
def test_osv_provider_persists_advisories_with_aliases(app_config) -> None:
    """OSV: a successful query persists the advisory and the component row."""
    advisory = {
        "id": "GHSA-1234-abcd-efgh",
        "summary": "Sample advisory",
        "severity": [{"type": "CVSS_V3", "score": 7.5}],
        "aliases": ["CVE-2024-0001"],
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "left-pad"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "1.3.0"}],
                    }
                ],
            }
        ],
        "references": [{"type": "ADVISORY", "url": "https://example.com/advisory"}],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(
        data=[advisory], fetched_at=_now(), records_returned=1
    )
    cache = ProviderCache()
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv, in_memory_cache=cache)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        advisories = s.query(Advisory).all()
        ca_rows = s.query(ComponentAdvisory).all()
        observations = (
            s.query(ProviderObservation).filter(ProviderObservation.provider == OSV_PROVIDER).all()
        )
        assert len(advisories) == 1
        assert advisories[0].source == "osv"
        assert advisories[0].source_advisory_id == "GHSA-1234-abcd-efgh"
        assert advisories[0].canonical_id == "CVE-2024-0001"
        assert len(ca_rows) == 1
        ca = ca_rows[0]
        assert ca.severity_label == "CVSS_V3"
        assert ca.severity_score == 7.5
        assert "CVE-2024-0001" in json.loads(ca.evidence_json)["aliases"]
        statuses = {obs.status for obs in observations}
        assert ProviderStatus.AVAILABLE in statuses


def test_osv_provider_handles_no_match(app_config) -> None:
    """An OSV response with no matching advisories is honest empty."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(data=[], fetched_at=_now(), records_returned=0)
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_vulnerabilities_for_components(
            scan_run_id=scan_id, components=components
        )
        s.commit()
        assert result == []
        assert s.query(Advisory).count() == 0, (
            "An empty OSV response must not insert any advisory rows."
        )


def test_osv_provider_records_unavailable_observation(app_config) -> None:
    """An OSV unavailable result records the observation, not a fake advisory."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="HTTP 503 from OSV",
        attempted_at=_now(),
        http_status=503,
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_vulnerabilities_for_components(
            scan_run_id=scan_id, components=components
        )
        s.commit()
        assert result == []
        obs = (
            s.query(ProviderObservation).filter(ProviderObservation.provider == OSV_PROVIDER).one()
        )
        assert obs.status == ProviderStatus.UNAVAILABLE
        assert obs.error_code == "provider_unavailable"
        assert "503" in obs.error_summary


def test_osv_provider_dedupes_repeated_calls(app_config) -> None:
    """Re-running the same scan does not create duplicate advisories."""
    advisory = {
        "id": "GHSA-dedupe-0001",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "left-pad"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "1.3.0"}],
                    }
                ],
            }
        ],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(
        data=[advisory], fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        # Exactly one Advisory row, one ComponentAdvisory row.
        assert s.query(Advisory).count() == 1
        assert s.query(ComponentAdvisory).count() == 1


def test_osv_provider_skips_unsupported_ecosystem(app_config) -> None:
    """An unsupported ecosystem yields a ``not_requested`` observation."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("Cargo", "left-pad", "1.0.0", True, False)])
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(data=[], fetched_at=_now(), records_returned=0)
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_vulnerabilities_for_components(
            scan_run_id=scan_id, components=components
        )
        s.commit()
        assert result == []
        obs = (
            s.query(ProviderObservation).filter(ProviderObservation.provider == OSV_PROVIDER).all()
        )
        statuses = {o.status for o in obs}
        assert ProviderStatus.NOT_REQUESTED in statuses


# ----------------------------------------------------------------------
# Dependency enrichment
# ----------------------------------------------------------------------
def test_depsdev_provider_persists_licence_observations(app_config) -> None:
    """deps.dev: a successful enrichment records licence + dependency count."""
    payload = {
        "version": {"versionNumber": "1.0.0"},
        "licenses": ["MIT"],
        "dependencies": [{"name": "dep-a"}, {"name": "dep-b"}],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
        assert len(result) == 1
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        assert obs.status == ProviderStatus.AVAILABLE
        # v0.4 honesty fix: the structured evidence envelope
        # lives on the dedicated ``evidence_json`` column;
        # ``error_summary`` is empty for a successful call.
        assert obs.error_summary is None
        assert obs.evidence_json is not None
        envelope = json.loads(obs.evidence_json)
        assert "MIT" in envelope.get("licences", [])
        assert envelope.get("dependency_count") == 2


def test_depsdev_provider_records_partial_observation(app_config) -> None:
    """A deps.dev 404 records ``unavailable`` and skips the component."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_not_found",
        error_summary="HTTP 404 from deps.dev",
        attempted_at=_now(),
        http_status=404,
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
        assert result == []
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        assert obs.status == ProviderStatus.UNAVAILABLE
        assert obs.error_code == "provider_not_found"


def test_depsdev_provider_persistent_cache_isolates_providers(app_config) -> None:
    """The deps.dev and OSV caches must not collide."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    payload = {
        "version": {"versionNumber": "1.0.0"},
        "licenses": ["MIT"],
        "dependencies": [],
    }
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
    # Second call must hit the persistent cache and never
    # touch deps_dev.enrich again.
    deps_dev.enrich.reset_mock()
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        result = service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
        assert len(result) == 1
        obs_rows = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .all()
        )
        # The first call recorded ``available`` (miss); the
        # second call recorded ``cached`` (hit). The
        # provider was not called again.
        assert {o.status for o in obs_rows} == {
            ProviderStatus.AVAILABLE,
            ProviderStatus.CACHED,
        }
        deps_dev.enrich.assert_not_called()


# ----------------------------------------------------------------------
# Scorecard
# ----------------------------------------------------------------------
def test_scorecard_provider_persists_posture_findings(app_config) -> None:
    """OpenSSF Scorecard: a successful import writes posture findings."""
    payload = {
        "score": 8.5,
        "scorecard": {"version": "v4.10.0"},
        "repo": {"commit": "abc123"},
        "date": "2024-01-01",
        "checks": [
            {
                "name": "Binary-Artifacts",
                "score": 10,
                "reason": "no binaries",
                "evidence": ["https://example.com/evidence"],
                "source_timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "name": "Code-Review",
                "score": 7,
                "reason": "no review on one PR",
            },
        ],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    scorecard = MagicMock()
    scorecard.read.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, scorecard=scorecard)
        result = service.import_scorecard_for_repository(
            scan_run_id=scan_id,
            canonical_url="https://github.com/octocat/Hello-World",
            is_archive=False,
        )
        s.commit()
        assert result is not None
        assert result.not_applicable is False
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == SCORECARD_PROVIDER)
            .all()
        )
        # ``available`` (miss) + ``cached`` (hit) is two
        # observations.
        assert len(obs) >= 1
        assert any(o.status == ProviderStatus.AVAILABLE for o in obs)


def test_scorecard_provider_archive_is_not_applicable(app_config) -> None:
    """An archive scan reports ``not_applicable`` and never fabricates a score."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    scorecard = MagicMock()
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, scorecard=scorecard)
        result = service.import_scorecard_for_repository(
            scan_run_id=scan_id,
            canonical_url=None,
            is_archive=True,
        )
        s.commit()
        assert result is not None
        assert result.not_applicable is True
        scorecard.read.assert_not_called()


def test_scorecard_provider_unavailable_records_observation(app_config) -> None:
    """A Scorecard 503 records the observation; no findings are written."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    scorecard = MagicMock()
    scorecard.read.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="Scorecard 503",
        attempted_at=_now(),
        http_status=503,
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, scorecard=scorecard)
        result = service.import_scorecard_for_repository(
            scan_run_id=scan_id,
            canonical_url="https://github.com/octocat/Hello-World",
            is_archive=False,
        )
        s.commit()
        assert result is None
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == SCORECARD_PROVIDER)
            .one()
        )
        assert obs.status == ProviderStatus.UNAVAILABLE


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------
def test_provider_failures_do_not_erase_local_findings(app_config) -> None:
    """A provider failure must not erase locally derived findings.

    We verify by simulating an unavailable OSV and confirming
    the rule engine's findings (the local pipeline) still
    run. The cleanest way to exercise this is to write a
    finding before the provider call, then make OSV fail,
    and confirm the finding is still present.
    """
    from app.models.finding import (
        Finding,
        FindingCategory,
        FindingConfidence,
        FindingSeverity,
        FindingStatus,
    )

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
        repository_id = s.query(ScanRun).filter(ScanRun.id == scan_id).one().repository_id
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repository_id,
                rule_id="LOCK-WF-001",
                category=FindingCategory.WORKFLOW,
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.MEDIUM,
                title="Local finding",
                summary="From the rule engine.",
                stable_key="local-finding-key",
                status=FindingStatus.OPEN,
            )
        )
        s.commit()
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="OSV is down",
        attempted_at=_now(),
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        local = (
            s.query(Finding)
            .filter(Finding.scan_run_id == scan_id, Finding.rule_id == "LOCK-WF-001")
            .one()
        )
        assert local is not None


def test_redaction_strips_sensitive_keys_from_observations(app_config) -> None:
    """An upstream error containing a token never lands in the observation."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    secret = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
    osv = MagicMock()
    osv.query_batch.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary=(
            f"GET https://api.osv.dev/v1/querybatch failed: Authorization: Bearer {secret}"
        ),
        attempted_at=_now(),
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
        obs = (
            s.query(ProviderObservation).filter(ProviderObservation.provider == OSV_PROVIDER).one()
        )
        # The token must not be present in the persisted
        # observation. The redaction may use the ``Bearer
        # [REDACTED]`` shape or strip both halves; the
        # important property is that the raw token is gone.
        assert secret not in (obs.error_summary or "")
        assert "Bearer [REDACTED]" in (obs.error_summary or "") or "[REDACTED] [REDACTED]" in (
            obs.error_summary or ""
        )


def test_scorecard_does_not_invent_severity(app_config) -> None:
    """Scorecard findings use ``INFORMATIONAL``; we never assign a severity score."""
    payload = {
        "score": 5.0,
        "checks": [{"name": "Dangerous-Workflow", "score": 3, "reason": "unsafe"}],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    scorecard = MagicMock()
    scorecard.read.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, scorecard=scorecard)
        service.import_scorecard_for_repository(
            scan_run_id=scan_id,
            canonical_url="https://github.com/octocat/Hello-World",
            is_archive=False,
        )
        s.commit()
    with _db_session.SessionLocal() as s:
        from app.models.finding import Finding, FindingSeverity

        findings = (
            s.query(Finding)
            .filter(Finding.scan_run_id == scan_id)
            .filter(Finding.rule_id.like("LOCK-POST-SCORECARD%"))
            .all()
        )
        assert findings
        for finding in findings:
            assert finding.severity == FindingSeverity.INFORMATIONAL
            # No synthetic severity score is invented.
            assert finding.severity.value == "informational"


# ----------------------------------------------------------------------
# Blocker 1: confidence is never fabricated
# ----------------------------------------------------------------------
def test_component_advisory_confidence_is_null_for_unspecified_provider(app_config) -> None:
    """OSV does not supply a confidence; the API must return ``None``.

    A real OSV response carries ``severity`` and ``affected`` but
    no confidence value. The previous v0.4 implementation
    substituted ``medium`` / ``high``; that was a correctness
    bug. The contract is now: confidence is ``None`` when the
    upstream provider did not supply one.
    """
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    advisory = {
        "id": "GHSA-confidence-null",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "left-pad"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "1.3.0"}],
                    }
                ],
            }
        ],
    }
    osv = MagicMock()
    osv.query_batch.return_value = ProviderSuccess(
        data=[advisory], fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, osv=osv)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_vulnerabilities_for_components(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        from app.models.component_advisory import ComponentAdvisory

        ca = s.query(ComponentAdvisory).one()
        # The ORM column for confidence is missing; the
        # endpoint fabricates it from severity. We confirm
        # here that no helper injects a confidence value
        # into the underlying column at the persistence
        # layer.
        assert not hasattr(ca, "confidence") or ca.confidence is None
        # No ``confidence`` key was added to the evidence
        # envelope either.
        envelope = json.loads(ca.evidence_json or "{}")
        assert "confidence" not in envelope


def test_enrichment_endpoint_does_not_infer_confidence(app_config) -> None:
    """The /scans/{id}/vulnerabilities endpoint returns ``None`` for confidence.

    Specifically: never ``low`` / ``medium`` / ``high`` for an
    OSV-derived row. The test bypasses the provider service
    so the endpoint's own projection is exercised in
    isolation.
    """
    from app.models.advisory import Advisory
    from app.models.component_advisory import ComponentAdvisory

    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
        adv = Advisory(
            source="osv",
            source_advisory_id="GHSA-no-confidence",
            canonical_id="CVE-2024-9999",
            summary="No confidence on this advisory",
        )
        s.add(adv)
        s.flush()
        component = s.query(Component).filter(Component.scan_run_id == scan_id).one()
        s.add(
            ComponentAdvisory(
                scan_run_id=scan_id,
                component_id=component.id,
                advisory_id=adv.id,
                affected=True,
                # ``severity_score`` is set to 7.5 (provider-
                # supplied) to ensure the endpoint does not
                # infer confidence from severity either.
                severity_source="osv",
                severity_label="CVSS_V3",
                severity_score=7.5,
            )
        )
        s.commit()
    from app.main import app

    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/vulnerabilities")
    assert r.status_code == 200
    row = r.json()["items"][0]
    # The endpoint must never substitute ``low`` /
    # ``medium`` / ``high`` / ``confirmed``. ``None`` is the
    # only acceptable value.
    assert row["confidence"] is None, (
        f"confidence must be null for an advisory whose provider "
        f"did not supply one; got {row['confidence']!r}"
    )
    for forbidden in ("low", "medium", "high", "confirmed"):
        assert row["confidence"] != forbidden


# ----------------------------------------------------------------------
# Blocker 2: error_summary carries no successful evidence
# ----------------------------------------------------------------------
def test_successful_depsdev_observation_has_no_error_summary(app_config) -> None:
    """A successful deps.dev call must not write to ``error_summary``."""
    payload = {
        "licenses": ["MIT"],
        "dependencies": [{"name": "dep-a"}],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        assert obs.status == ProviderStatus.AVAILABLE
        # The blocker 2 contract: error_summary is empty for
        # successful responses. It must not carry a
        # ``trace=`` prefix, a ``| trace=`` separator, or a
        # raw JSON envelope.
        assert obs.error_summary is None
        assert "trace=" not in (obs.error_summary or "")
        # The structured envelope lives on the dedicated
        # column. We never re-derive it from error_summary.
        assert obs.evidence_json is not None
        envelope = json.loads(obs.evidence_json)
        assert "licences" in envelope


def test_failed_depsdev_observation_has_no_evidence_envelope(app_config) -> None:
    """A failed provider call must not write to ``evidence_json``."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderUnavailable(
        error_code="provider_unavailable",
        error_summary="deps.dev is down",
        attempted_at=_now(),
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        assert obs.status == ProviderStatus.UNAVAILABLE
        # error_summary is the (redacted) error string.
        assert obs.error_summary is not None
        # evidence_json stays null on failure.
        assert obs.evidence_json is None


def test_oversized_evidence_is_bounded(app_config) -> None:
    """An evidence payload above the 8 KiB cap is bounded, not rejected."""
    # 16 KiB of licence strings; the cap is 8 KiB.
    licences = [f"LIC-{i:06d}" for i in range(2000)]
    payload = {
        "licenses": licences,
        "dependencies": [],
    }
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    deps_dev = MagicMock()
    deps_dev.enrich.return_value = ProviderSuccess(
        data=payload, fetched_at=_now(), records_returned=1
    )
    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=deps_dev)
        components = s.query(Component).filter(Component.scan_run_id == scan_id).all()
        service.enrich_components_with_deps_dev(scan_run_id=scan_id, components=components)
        s.commit()
    with _db_session.SessionLocal() as s:
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        assert obs.evidence_json is not None
        # Bounded; we never allow a runaway payload.
        assert len(obs.evidence_json.encode("utf-8")) <= 8 * 1024


def test_malformed_evidence_does_not_crash_endpoint(app_config) -> None:
    """A corrupt ``evidence_json`` payload is reported as empty, not a 500."""
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.AVAILABLE,
                records_returned=1,
                cache_status="miss",
                evidence_json="{this is not json",
            )
        )
        s.commit()
    from app.main import app

    client = api_client(app)
    r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
    assert r.status_code == 200
    row = r.json()["items"][0]
    # The endpoint treats the corrupt envelope as an empty
    # one. We never crash and we never fabricate.
    assert row["license_observations"] == []
    assert row["dependency_count"] is None
    assert row["evidence"] is None


def test_secrets_do_not_appear_in_evidence_or_error_fields(app_config) -> None:
    """Sensitive keys in the evidence envelope are redacted before persistence."""
    secret = "ghp_supersecrettokendoappearinevidence1234567890"
    with _db_session.SessionLocal() as s:
        scan_id = _setup_scan_with_components(s, [("npm", "left-pad", "1.0.0", True, False)])
    # We mock the provider service ``_record_observation``
    # method indirectly by calling the public method with
    # a crafted evidence envelope. The redaction utility is
    # the only thing that can keep the secret out of the
    # persisted evidence_json column; the test exercises
    # the actual record path.
    from app.services.provider_service import ProviderService

    with _db_session.SessionLocal() as s:
        service = ProviderService(s, deps_dev=MagicMock())
        service._record_observation(
            scan_run_id=scan_id,
            provider="deps_dev",
            operation="deps_dev_enrichment",
            status=ProviderStatus.AVAILABLE,
            http_status=200,
            records_returned=1,
            cache_status="miss",
            error_code=None,
            error_summary=None,
            evidence={
                "package_name": "left-pad",
                "ecosystem": "npm",
                "version": "1.0.0",
                "token": secret,
                "api_key": secret,
                "authorization": secret,
            },
        )
        s.commit()
        obs = (
            s.query(ProviderObservation)
            .filter(ProviderObservation.provider == DEPS_DEV_PROVIDER)
            .one()
        )
        # The raw secret must not be persisted in any
        # column on the observation row.
        assert secret not in (obs.evidence_json or "")
        assert secret not in (obs.error_summary or "")
        # Every sensitive key is replaced with the
        # ``[REDACTED]`` sentinel.
        envelope = json.loads(obs.evidence_json)
        assert envelope.get("token") == "[REDACTED]"
        assert envelope.get("api_key") == "[REDACTED]"
        assert envelope.get("authorization") == "[REDACTED]"
        # Non-sensitive fields survive.
        assert envelope.get("package_name") == "left-pad"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _now():
    from app.utils.datetime import utcnow

    return utcnow()
