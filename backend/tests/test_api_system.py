"""API tests for the health and system-info endpoints.

The product version is asserted from the package's single source
of truth (``app._version``) so a future bump of the version
constant does not require editing this file.
"""

from __future__ import annotations

from app._version import __version__
from app.main import app

from tests.api_client import api_client


def test_health_ok(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"] == __version__
    assert body["environment"]
    assert "timestamp" in body


def test_health_version_matches_system_info(app_config) -> None:
    """The version reported by /health and /system/info must agree.

    A mismatch here would mean two different code paths are
    reading the version from two different sources, which is
    exactly the drift this milestone's polish pass is meant to
    prevent.
    """
    client = api_client(app)
    health = client.get("/api/v1/health").json()
    info = client.get("/api/v1/system/info").json()
    assert health["version"] == info["version"] == __version__


def test_health_does_not_fake_providers(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/health")
    # The health endpoint must not include provider status payloads.
    assert "providers" not in r.json()
    assert "scans" not in r.json()


def test_system_info_shape(app_config) -> None:
    client = api_client(app)
    r = client.get("/api/v1/system/info")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Lockverity"
    assert body["version"] == __version__
    assert body["tagline"] == "Evidence-first software supply-chain assurance"
    assert body["api_prefix"] == "/api/v1"
    assert "archive_limits" in body
    assert "pagination" in body
    assert "provider_safety" in body
    assert "intake" in body
