"""Regression tests for the removed ``LOCKVERITY_GITHUB_API_URL`` setting.

The configurable GitHub API origin override was removed
in the v2.0.6 public-closure cycle. The bounded HTTP
client allowlist pins the canonical
``https://api.github.com`` host; every URL builder
hardcodes the canonical origin. Operators who set
``LOCKVERITY_GITHUB_API_URL=https://malicious.example/api``
in the environment must NOT see that origin take effect.

The tests in this file prove the contract:

1. ``Settings`` does not expose a ``github_api_url``
   attribute; the obsolete env var is silently ignored.
2. The system info response does not leak any
   ``github_api_url`` field.
3. The canonical GitHub URL is unchanged regardless of
   the obsolete env var.
4. Loading settings with the obsolete env var set does
   not raise.
"""

from __future__ import annotations

from app.core.config import Settings

from tests.api_client import api_client


def test_settings_does_not_expose_github_api_url(monkeypatch) -> None:
    """The ``Settings`` class does not expose a
    ``github_api_url`` attribute; the obsolete env var
    is silently ignored.
    """
    monkeypatch.setenv("LOCKVERITY_GITHUB_API_URL", "https://malicious.example/api")
    s = Settings()
    assert not hasattr(s, "github_api_url"), (
        "LOCKVERITY_GITHUB_API_URL must not be exposed on Settings"
    )
    # The Pydantic ``extra='ignore'`` policy means the
    # field is silently dropped; the canonical GitHub
    # origin is unaffected.
    assert "github_api_url" not in s.model_dump()


def test_settings_loads_with_obsolete_env_var(monkeypatch) -> None:
    """Loading ``Settings`` with the obsolete env var
    set does not raise; the value is silently ignored.
    """
    monkeypatch.setenv("LOCKVERITY_GITHUB_API_URL", "https://malicious.example/api")
    # Re-instantiate; this is the same path the
    # application takes on startup.
    s = Settings()
    # The canonical origin is unaffected. We assert the
    # absence of a ``github_api_url`` field; the
    # production code that builds URLs does not consult
    # the env var.
    assert s.model_dump().get("github_api_url") is None


def test_canonical_github_url_builder_is_unaffected(
    monkeypatch,
) -> None:
    """The GitHub URL builder uses the canonical origin
    regardless of the obsolete env var. A change that
    re-introduces the override would surface as a
    different URL.
    """
    from app.providers.github_provider import github_api_repo_url
    from app.utils.repo_url import NormalizedRepositoryUrl

    monkeypatch.setenv("LOCKVERITY_GITHUB_API_URL", "https://malicious.example/api")
    normalized = NormalizedRepositoryUrl(
        host="github.com",
        owner="octocat",
        name="Hello-World",
        canonical_url="https://github.com/octocat/Hello-World",
    )
    # The canonical URL is hardcoded; the obsolete env
    # var does not affect it.
    assert github_api_repo_url(normalized) == "https://api.github.com/repos/octocat/Hello-World"


def test_system_info_does_not_leak_github_api_url(app_config) -> None:
    """The ``GET /api/v1/system/info`` response does not
    expose any ``github_api_url`` field. The endpoint
    intentionally omits the obsolete field; the test
    pins that contract.
    """
    from app.main import app

    client = api_client(app)
    response = client.get("/api/v1/system/info")
    assert response.status_code == 200
    body = response.json()
    # The body must not contain a ``github_api_url`` key
    # at the top level or under the ``intake`` sub-dict
    # (the intake block lists the actual configuration
    # knobs the operator can change).
    assert "github_api_url" not in body
    assert "github_api_url" not in body.get("intake", {})
