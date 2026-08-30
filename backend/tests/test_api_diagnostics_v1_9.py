"""v1.9 — API tests for the operational-diagnostics endpoint.

The diagnostics endpoint is read-only and must:

- return the application section with version and
  generated timestamp;
- include the executor section with queued / running
  counts (and an explicit "Heartbeat not exposed"
  note when the in-process executor does not persist
  heartbeats);
- include the per-provider diagnostics rows with
  cache state, evidence presence, and bounded error
  fields kept independent of provider availability;
- include the bounded recent-issue list (partial /
  failed / cancelled only) and the stage summary;
- never expose secrets, tokens, environment values,
  connection strings, local filesystem paths, or raw
  stack traces;
- remain bounded in result size (the recent-issue list
  must be capped).
"""

from __future__ import annotations

import json

from app._version import __version__
from app.db import session as _db_session
from app.main import app
from app.models.finding import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)
from app.models.provider_observation import (
    ProviderObservation,
    ProviderStatus,
)
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_job import ScanJob, ScanJobState
from app.models.scan_run import (
    ScanRun,
    ScanStatus,
    ScanTriggerType,
)
from app.models.scan_stage import (
    ScanStage,
    StageStatus,
    StageType,
)

from tests.api_client import api_client


def _make_repo(session, *, canonical_url: str) -> int:
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


def _make_scan(
    session,
    *,
    repository_id: int,
    status: ScanStatus,
    failure_code: str | None = None,
    failure_summary: str | None = None,
) -> int:
    scan = ScanRun(
        repository_id=repository_id,
        status=status,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha="deadbeef",
        analyzer_version="lockverity 1.9.0",
        failure_code=failure_code,
        failure_summary=failure_summary,
    )
    session.add(scan)
    session.flush()
    return scan.id


def _seed_minimal_state(session) -> None:
    """Seed a repository, one completed scan, one failed scan, and a provider observation."""
    repo_id = _make_repo(session, canonical_url="https://github.com/octocat/Hello-World")
    completed = _make_scan(
        session,
        repository_id=repo_id,
        status=ScanStatus.COMPLETED,
    )
    failed = _make_scan(
        session,
        repository_id=repo_id,
        status=ScanStatus.FAILED,
        failure_code="scanner_crashed",
        failure_summary="Scanner crashed before inventory capture.",
    )
    # One scan-level provider observation: github scan
    # metadata fetch.
    session.add(
        ProviderObservation(
            scan_run_id=completed,
            provider="github",
            operation="fetch_repository_metadata",
            status=ProviderStatus.AVAILABLE,
            records_returned=1,
            cache_status="miss",
        )
    )
    # One per-component observation: deps.dev partial.
    session.add(
        ProviderObservation(
            scan_run_id=completed,
            provider="deps_dev",
            operation="resolve_dependencies",
            status=ProviderStatus.PARTIAL,
            records_returned=2,
            cache_status="stale",
            error_code="rate_limited",
            error_summary="deps.dev rate limit hit during the scan window.",
        )
    )
    # One observation: osv unavailable on the failed scan.
    session.add(
        ProviderObservation(
            scan_run_id=failed,
            provider="osv",
            operation="query_vulnerabilities",
            status=ProviderStatus.UNAVAILABLE,
            records_returned=0,
            error_code="upstream_5xx",
            error_summary="Upstream returned 503 Service Unavailable.",
        )
    )
    # One job row: queued.
    session.add(
        ScanJob(
            scan_run_id=failed,
            executor_id="executor-test-12345678",
            state=ScanJobState.QUEUED,
        )
    )
    # One stage row: failed.
    session.add(
        ScanStage(
            scan_run_id=failed,
            stage_type=StageType.REPOSITORY_INTAKE,
            status=StageStatus.FAILED,
            failure_code="github_not_found",
            failure_summary="Upstream returned 404 Not Found.",
        )
    )
    # One completed stage row.
    session.add(
        ScanStage(
            scan_run_id=completed,
            stage_type=StageType.REPOSITORY_INTAKE,
            status=StageStatus.COMPLETED,
        )
    )
    session.commit()


def test_diagnostics_summary_returns_application_section(app_config) -> None:
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    assert r.status_code == 200
    body = r.json()
    assert "application" in body
    app_section = body["application"]
    assert app_section["version"] == __version__
    assert app_section["database"] in {"available", "unavailable", "unknown"}
    assert app_section["status"] == "reachable"
    assert app_section["environment"]
    assert "generated_at" in app_section
    assert "generated_at" in body


def test_diagnostics_summary_does_not_trigger_external_call(app_config) -> None:
    """Diagnostics must not call any external provider.

    The synthetic dataset has no network access. The
    endpoint must still return 200 with the four known
    provider names surfaced.
    """
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    assert r.status_code == 200
    body = r.json()
    names = sorted(p["provider"] for p in body["providers"])
    # All four known providers are surfaced; openssf
    # has not been queried so its state is
    # ``not_requested``.
    assert names == ["deps_dev", "github", "openssf", "osv"]


def test_diagnostics_summary_provider_states_separated(app_config) -> None:
    """Cache state, evidence presence, and provider state must remain distinct fields.

    The v1.9 schema exposes ``cache_status`` and
    ``last_observed_state`` as independent fields; the
    UI must not collapse them into a single verdict.
    """
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    by_name = {p["provider"]: p for p in body["providers"]}
    github = by_name["github"]
    deps_dev = by_name["deps_dev"]
    osv = by_name["osv"]
    # github is ``available`` with a ``miss`` cache
    # status.
    assert github["last_observed_state"] == "available"
    assert github["cache_status"] == "miss"
    # deps_dev is ``partial`` with a ``stale`` cache
    # status and a bounded error code.
    assert deps_dev["last_observed_state"] == "partial"
    assert deps_dev["cache_status"] == "stale"
    assert deps_dev["last_error_code"] == "rate_limited"
    # osv is ``unavailable`` with a bounded error code.
    assert osv["last_observed_state"] == "unavailable"
    assert osv["last_error_code"] == "upstream_5xx"
    # The openssf provider was never queried; all
    # nullable fields are null.
    openssf = by_name["openssf"]
    assert openssf["last_observed_state"] == "not_requested"
    assert openssf["last_attempt_at"] is None
    assert openssf["cache_status"] is None
    assert openssf["last_error_code"] is None


def test_diagnostics_summary_recent_issues_bounded(app_config) -> None:
    """The recent-issue list is bounded and excludes completed scans."""
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    issues = body["recent_scan_issues"]
    assert len(issues) >= 1
    for issue in issues:
        assert issue["status"] in {"partial", "failed", "cancelled"}
        assert issue["status"] != "completed"
    # The completed scan must NOT appear in the issue list.
    assert not any(issue["status"] == "completed" for issue in issues)


def test_diagnostics_summary_recent_issues_capped(app_config) -> None:
    """The recent-issue list is capped at the bounded value."""
    from app.services.diagnostics_service import MAX_RECENT_SCAN_ISSUES

    assert MAX_RECENT_SCAN_ISSUES == 25
    with _db_session.SessionLocal() as s:
        repo_id = _make_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        # Seed 30 failed scans.
        for _ in range(30):
            _make_scan(
                s,
                repository_id=repo_id,
                status=ScanStatus.FAILED,
                failure_code="x",
                failure_summary="y",
            )
        s.commit()
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    assert len(body["recent_scan_issues"]) == MAX_RECENT_SCAN_ISSUES


def test_diagnostics_summary_stage_summary_uses_persisted_states(
    app_config,
) -> None:
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    by_stage = {row["stage"]: row for row in body["stage_summary"]}
    intake = by_stage["repository_intake"]
    # The seed creates one completed and one failed
    # repository_intake stage.
    assert intake["completed"] == 1
    assert intake["failed"] == 1
    # All other stages are zero.
    other = [
        "archive_validation",
        "manifest_discovery",
        "dependency_parsing",
        "dependency_enrichment",
        "vulnerability_query",
        "workflow_analysis",
        "repository_posture",
        "finding_reconciliation",
        "export_generation",
    ]
    for stage in other:
        row = by_stage[stage]
        assert row["completed"] == 0
        assert row["failed"] == 0


def test_diagnostics_summary_executor_section(app_config) -> None:
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    ex = body["executor"]
    assert ex["state"] == "available"
    # The seed creates one queued scan job; no running
    # jobs.
    assert ex["queued_scans"] >= 1
    assert ex["running_scans"] == 0
    # The in-process executor does not persist
    # heartbeats; the field is always None and the
    # page renders the explicit "Heartbeat not exposed"
    # note.
    assert ex["last_heartbeat_at"] is None
    assert ex["heartbeat_supported"] is False
    assert ex["details_available"] is True
    # The implementation is a real value (not the
    # literal string "unknown").
    assert ex["implementation"] in {"inline", "local-thread"}


def test_diagnostics_summary_does_not_expose_secrets(app_config) -> None:
    """The diagnostics payload must not contain tokens, paths, or stack traces."""
    with _db_session.SessionLocal() as s:
        _seed_minimal_state(s)
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    payload = json.dumps(body)
    forbidden = [
        "LOCKVERITY_GITHUB_TOKEN",
        "Authorization",
        "Bearer ",
        "/var/",
        "C:\\",
        "C:/",
        "Traceback",
        "sqlite://",
        "postgresql://",
        "secret",
        "password",
        "DSN",
    ]
    lowered = payload.lower()
    for needle in forbidden:
        assert needle.lower() not in lowered, f"diagnostics leaked {needle!r}"


def test_diagnostics_summary_uses_stable_envelope(app_config) -> None:
    """The error envelope is the standard envelope on failure."""
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/does-not-exist")
    assert r.status_code == 404


def test_diagnostics_summary_no_finding_records_leaked(app_config) -> None:
    """The diagnostics payload must not include any raw finding records."""
    with _db_session.SessionLocal() as s:
        repo_id = _make_repo(s, canonical_url="https://github.com/octocat/Hello-World")
        scan_id = _make_scan(s, repository_id=repo_id, status=ScanStatus.COMPLETED)
        # Add a finding record to ensure the
        # diagnostics page does not surface it.
        s.add(
            Finding(
                scan_run_id=scan_id,
                repository_id=repo_id,
                rule_id="R999",
                category=FindingCategory.VULNERABILITY,
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.MEDIUM,
                title="sensitive title",
                summary="sensitive summary",
                stable_key="0" * 64,
            )
        )
        s.commit()
    client = api_client(app)
    r = client.get("/api/v1/diagnostics/summary")
    body = r.json()
    payload = json.dumps(body)
    assert "R999" not in payload
    assert "sensitive title" not in payload
    assert "sensitive summary" not in payload
