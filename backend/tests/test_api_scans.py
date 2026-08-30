"""API tests for the scan endpoints."""

from __future__ import annotations

import pytest
from app.main import app

from tests.api_client import api_client


@pytest.fixture
def client(app_config):
    return api_client(app)


@pytest.fixture
def repository(client) -> int:
    r = client.post(
        "/api/v1/repositories",
        json={"canonical_url": "https://github.com/octocat/Hello-World"},
    )
    return r.json()["id"]


def test_create_scan_201(client, repository) -> None:
    r = client.post(f"/api/v1/repositories/{repository}/scans")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert body["repository_id"] == repository
    assert body["trigger_type"] == "manual"


def test_create_scan_for_unknown_repository_404(client) -> None:
    r = client.post("/api/v1/repositories/9999/scans")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_list_scans_for_repository(client, repository) -> None:
    for _ in range(3):
        client.post(f"/api/v1/repositories/{repository}/scans")
    r = client.get(f"/api/v1/repositories/{repository}/scans")
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["total"] == 3
    assert len(body["items"]) == 3


def test_get_scan_ok(client, repository) -> None:
    r = client.post(f"/api/v1/repositories/{repository}/scans")
    sid = r.json()["id"]
    r2 = client.get(f"/api/v1/scans/{sid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == sid
    assert r2.json()["status"] == "queued"


def test_get_scan_404(client) -> None:
    r = client.get("/api/v1/scans/9999")
    assert r.status_code == 404


def test_list_stages(client, repository) -> None:
    r = client.post(f"/api/v1/repositories/{repository}/scans")
    sid = r.json()["id"]
    r2 = client.get(f"/api/v1/scans/{sid}/stages")
    assert r2.status_code == 200
    body = r2.json()
    assert len(body["items"]) == 10
    assert body["items"][0]["stage_type"] == "repository_intake"
    assert body["items"][-1]["stage_type"] == "export_generation"
    assert all(s["status"] == "pending" for s in body["items"])


def test_list_findings_empty(client, repository) -> None:
    r = client.post(f"/api/v1/repositories/{repository}/scans")
    sid = r.json()["id"]
    r2 = client.get(f"/api/v1/scans/{sid}/findings")
    assert r2.status_code == 200
    body = r2.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_list_providers_empty(client, repository) -> None:
    r = client.post(f"/api/v1/repositories/{repository}/scans")
    sid = r.json()["id"]
    r2 = client.get(f"/api/v1/scans/{sid}/providers")
    assert r2.status_code == 200
    body = r2.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0
