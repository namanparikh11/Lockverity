"""Tests for the v1.7 findings filter/sort/single-finding API."""

from __future__ import annotations

import json

import pytest
from app.db import session as _db_session
from app.main import app
from app.models.finding import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    FindingStatus,
)
from app.models.scan_run import ScanTriggerType
from app.services import repository_service, scan_service
from app.utils.finding_keys import stable_finding_key

from tests.api_client import api_client


@pytest.fixture
def client(app_config):
    return api_client(app)


def _seed_two_scans_with_findings(session) -> tuple[int, int]:
    """Two repositories, one scan each, three findings per scan."""
    repo_a = repository_service.create_repository_from_url(
        session, "https://github.com/octocat/Hello-World"
    )
    repo_b = repository_service.create_repository_from_url(
        session, "https://github.com/octocat/Spoon-Knife"
    )
    scan_a = scan_service.create_scan(
        session, repository_id=repo_a.id, trigger_type=ScanTriggerType.MANUAL
    )
    scan_b = scan_service.create_scan(
        session, repository_id=repo_b.id, trigger_type=ScanTriggerType.MANUAL
    )
    seeds = [
        # (scan, repo, rule, category, severity, confidence, status, evidence)
        (
            scan_a.id,
            repo_a.id,
            "R001",
            FindingCategory.DEPENDENCY,
            FindingSeverity.LOW,
            FindingConfidence.MEDIUM,
            FindingStatus.OPEN,
            {"provider": "osv.dev", "purl": "pkg:npm/left-pad@1.0.0"},
            "package.json",
        ),
        (
            scan_a.id,
            repo_a.id,
            "R002",
            FindingCategory.VULNERABILITY,
            FindingSeverity.HIGH,
            FindingConfidence.CONFIRMED,
            FindingStatus.OPEN,
            {
                "provider": "github_advisories",
                "advisory_id": "GHSA-xxxx-yyyy-zzzz",
                "purl": "pkg:npm/alpha@2.0.0",
            },
            "package-lock.json",
        ),
        (
            scan_a.id,
            repo_a.id,
            "R003",
            FindingCategory.LICENCE,
            FindingSeverity.INFORMATIONAL,
            FindingConfidence.HIGH,
            FindingStatus.RESOLVED,
            {"provider": "licence_check", "purl": "pkg:npm/left-pad@1.0.0"},
            "package.json",
        ),
        (
            scan_b.id,
            repo_b.id,
            "R999",
            FindingCategory.WORKFLOW,
            FindingSeverity.CRITICAL,
            FindingConfidence.UNKNOWN,
            FindingStatus.OPEN,
            {"provider": "github_advisories", "advisory_id": "GHSA-other"},
            None,
        ),
    ]
    for scan_id, repo_id, rule, cat, sev, conf, stat, ev, path in seeds:
        f = Finding(
            scan_run_id=scan_id,
            repository_id=repo_id,
            rule_id=rule,
            category=cat,
            severity=sev,
            confidence=conf,
            status=stat,
            title=f"title for {rule}",
            summary=f"summary for {rule}",
            evidence_json=json.dumps(ev),
            location_path=path,
            stable_key=stable_finding_key(rule, ev),
        )
        session.add(f)
    session.commit()
    return scan_a.id, scan_b.id


def test_filter_by_severity(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?severity=high")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["severity"] == "high"


def test_filter_by_confidence_and_status(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?confidence=confirmed&status=open")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_filter_by_rule_id_exact(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?rule_id=R002")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_filter_by_path_substring(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?path=package-lock")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_filter_by_provider_substring(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?provider=osv.dev")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R001"


def test_search_q_matches_rule_id_and_evidence(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?q=GHSA-xxxx")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_search_q_matches_purl_in_evidence(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?q=alpha")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_sort_by_severity_is_deterministic(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?sort=severity&page_size=10")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    # Enums sort by the same order they were defined in
    # the SQLAlchemy Enum on the model. We only assert
    # determinism: repeated calls yield the same order.
    severities = [it["severity"] for it in items]
    r2 = client.get(f"/api/v1/scans/{scan_a}/findings?sort=severity&page_size=10")
    assert [it["severity"] for it in r2.json()["items"]] == severities


def test_invalid_sort_value_maps_to_id(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    # An invalid sort value must NOT 500; the route
    # handler accepts the value and the repository
    # function normalises it to "id".
    r = client.get(f"/api/v1/scans/{scan_a}/findings?sort=not_a_real_field")
    assert r.status_code == 200
    assert r.json()["pagination"]["total"] == 3


def test_invalid_enum_value_returns_validation_envelope(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?severity=catastrophic")
    # FastAPI's request validation returns 422 for
    # invalid enum values. We assert the status code
    # and the bounded error envelope.
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"


def test_page_size_cap_enforced(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings?page_size=500")
    assert r.status_code == 422
    body = r.json()
    # Lockverity's stable validation envelope.
    assert body["error"]["code"] == "validation_error"
    # The validation details surface the cap value
    # in the inner errors list.
    errors = body["error"]["details"]["errors"]
    assert any("less_than_equal" in str(e.get("type", "")) for e in errors)


def test_scan_scoped_isolation(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
        # Listing scan A's findings must not include
        # scan B's findings, even though they share a
        # repository kind.
        r = client.get(f"/api/v1/scans/{scan_a}/findings")
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["scan_run_id"] == scan_a


def test_single_finding_route_returns_payload(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
        r = client.get(f"/api/v1/scans/{scan_a}/findings?rule_id=R001")
        finding_id = r.json()["items"][0]["id"]
    r2 = client.get(f"/api/v1/scans/{scan_a}/findings/{finding_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == finding_id
    assert body["rule_id"] == "R001"
    assert body["scan_run_id"] == scan_a


def test_single_finding_cross_scan_isolation_404(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, scan_b = _seed_two_scans_with_findings(s)
        r = client.get(f"/api/v1/scans/{scan_b}/findings")
        finding_id = r.json()["items"][0]["id"]
    # Try to read scan B's finding through scan A's URL.
    r2 = client.get(f"/api/v1/scans/{scan_a}/findings/{finding_id}")
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "not_found"


def test_single_finding_unknown_id_404(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r = client.get(f"/api/v1/scans/{scan_a}/findings/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_pagination_is_deterministic(client) -> None:
    with _db_session.SessionLocal() as s:
        scan_a, _ = _seed_two_scans_with_findings(s)
    r1 = client.get(f"/api/v1/scans/{scan_a}/findings?page=1&page_size=2")
    r2 = client.get(f"/api/v1/scans/{scan_a}/findings?page=1&page_size=2")
    ids1 = [it["id"] for it in r1.json()["items"]]
    ids2 = [it["id"] for it in r2.json()["items"]]
    assert ids1 == ids2
    assert len(ids1) == 2
    r3 = client.get(f"/api/v1/scans/{scan_a}/findings?page=2&page_size=2")
    ids3 = [it["id"] for it in r3.json()["items"]]
    # No overlap between page 1 and page 2.
    assert set(ids1).isdisjoint(set(ids3))
