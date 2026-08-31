"""Regression tests for the explicit seeded-demo dataset identity.

The production Demo page and the scan-list demo notice must
identify seeded demo rows by the explicit persisted
``scan_runs.seeded_dataset`` marker - never by numeric primary
key, repository URL, commit SHA, status, or creation order.

These tests pin the collision contract:

- A real user scan with id 1 (or 3, or 4), even one that targets
  the fixture repository, carries the historical ``deadbeef``
  SHA fill, and sits in a historically "demo" status, is never
  selected as demo data because the application write paths
  never set the marker.
- Seeded demo scans resolve dynamically at whatever numeric ids
  they happen to occupy.
- The cross-repo rollup filter ``GET /scans?seeded_dataset=demo``
  returns only marker-carrying rows and stays empty when no
  seeded rows exist.
"""

from __future__ import annotations

import pytest
from app.main import app
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import (
    DEMO_SEEDED_DATASET,
    ScanRun,
    ScanStatus,
    ScanTriggerType,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api_client import api_client

FIXTURE_URL = "https://github.com/example-org/lockverity-fixture"
DEADBEEF_FILL = "deadbeef" * 5


@pytest.fixture()
def client(app_config) -> TestClient:
    """Bind the FastAPI app to the test engine."""
    return api_client(app)


@pytest.fixture()
def writer(session_factory) -> Session:
    """A session whose commits are visible to the TestClient.

    The per-test ``session`` fixture rolls back a savepoint that
    the API client never sees; demo-marker rows must be seeded
    with a committed session bound to the same engine so the
    cross-repo rollup can read them.
    """
    session = sessionmaker(bind=session_factory.kw["bind"])()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _fixture_repository(session: Session) -> Repository:
    repo = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="example-org",
        name="lockverity-fixture",
        canonical_url=FIXTURE_URL,
        default_branch="main",
        description=None,
        visibility=RepositoryVisibility.PUBLIC,
        archived=False,
    )
    session.add(repo)
    session.flush()
    return repo


def _seed_scan(
    session: Session,
    repository_id: int,
    *,
    scan_id: int,
    status: ScanStatus,
    seeded_dataset: str | None,
) -> ScanRun:
    scan = ScanRun(
        id=scan_id,
        repository_id=repository_id,
        status=status,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha=DEADBEEF_FILL,
        analyzer_version="lockverity-test",
        seeded_dataset=seeded_dataset,
    )
    session.add(scan)
    session.flush()
    return scan


def _seed_collision_scenario(session: Session) -> None:
    """Seed the worst-case collision dataset.

    Real user scans occupy the historically "demo" ids 1/3/4 in
    the historically "demo" statuses on the fixture repository
    with the historical ``deadbeef`` SHA fill - everything the
    old heuristic matched except the explicit marker. Seeded
    demo scans sit at unrelated ids 27/59/68/82.
    """
    repo = _fixture_repository(session)
    # Real user scans: no marker, despite matching every legacy
    # demo heuristic.
    _seed_scan(
        session, repo.id, scan_id=1, status=ScanStatus.COMPLETED, seeded_dataset=None
    )
    _seed_scan(
        session, repo.id, scan_id=3, status=ScanStatus.FAILED, seeded_dataset=None
    )
    _seed_scan(
        session, repo.id, scan_id=4, status=ScanStatus.CANCELLED, seeded_dataset=None
    )
    # Seeded demo scans at non-historical ids.
    _seed_scan(
        session,
        repo.id,
        scan_id=27,
        status=ScanStatus.COMPLETED,
        seeded_dataset=DEMO_SEEDED_DATASET,
    )
    _seed_scan(
        session,
        repo.id,
        scan_id=59,
        status=ScanStatus.FAILED,
        seeded_dataset=DEMO_SEEDED_DATASET,
    )
    _seed_scan(
        session,
        repo.id,
        scan_id=68,
        status=ScanStatus.CANCELLED,
        seeded_dataset=DEMO_SEEDED_DATASET,
    )
    _seed_scan(
        session,
        repo.id,
        scan_id=82,
        status=ScanStatus.PARTIAL,
        seeded_dataset=DEMO_SEEDED_DATASET,
    )
    session.commit()


def test_application_created_scans_never_carry_the_demo_marker(client: TestClient) -> None:
    """Scans created through the application's own write path
    (the intake scan-creation route) must report a null
    ``seeded_dataset`` so they can never be identified as demo
    data regardless of their numeric id.
    """
    resp = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    assert resp.status_code == 201, resp.text
    repository_id = int(resp.json()["id"])

    created = client.post(
        f"/api/v1/repositories/{repository_id}/scans",
        json={"trigger_type": "manual"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["seeded_dataset"] is None

    detail = client.get(f"/api/v1/scans/{created.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["seeded_dataset"] is None


def test_seeded_dataset_filter_returns_only_marker_scans(
    client: TestClient, writer: Session
) -> None:
    """The demo filter selects marker-carrying rows only.

    Real user scans at ids 1/3/4 - matching every legacy demo
    heuristic except the marker - must be excluded; the seeded
    rows at ids 27/59/68/82 must be returned with the marker
    visible on the read shape.
    """
    _seed_collision_scenario(writer)

    resp = client.get(f"/api/v1/scans?seeded_dataset={DEMO_SEEDED_DATASET}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == {27, 59, 68, 82}
    for item in body["items"]:
        assert item["seeded_dataset"] == DEMO_SEEDED_DATASET
    # The real user scans are absent even though their ids and
    # statuses match the historical demo assumptions.
    assert {1, 3, 4}.isdisjoint(returned_ids)


def test_unfiltered_rollup_still_returns_every_scan(
    client: TestClient, writer: Session
) -> None:
    """The additive filter must not change the unfiltered
    cross-repo rollup: real and seeded rows are all listed.
    """
    _seed_collision_scenario(writer)

    resp = client.get("/api/v1/scans")
    assert resp.status_code == 200
    returned_ids = {item["id"] for item in resp.json()["items"]}
    assert returned_ids == {1, 3, 4, 27, 59, 68, 82}


def test_seeded_dataset_filter_empty_when_no_marker_rows(
    client: TestClient, writer: Session
) -> None:
    """With only real scans present, the demo filter returns a
    bounded empty page - the API-level basis for the Demo page's
    "Demo dataset not loaded." state.
    """
    repo = _fixture_repository(writer)
    _seed_scan(writer, repo.id, scan_id=7, status=ScanStatus.COMPLETED, seeded_dataset=None)
    writer.commit()

    resp = client.get(f"/api/v1/scans?seeded_dataset={DEMO_SEEDED_DATASET}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_seeded_dataset_filter_combines_with_status(
    client: TestClient, writer: Session
) -> None:
    """The demo filter composes with the existing status filter."""
    _seed_collision_scenario(writer)

    resp = client.get(
        f"/api/v1/scans?seeded_dataset={DEMO_SEEDED_DATASET}&status=partial"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [82]
    assert body["items"][0]["status"] == "partial"
