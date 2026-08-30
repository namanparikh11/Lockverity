"""Tests for the Settings class."""

from __future__ import annotations

from typing import ClassVar

import pytest
from app.core.config import Settings, get_settings
from app.core.control_plane import origin_is_trusted
from pydantic import ValidationError


def _clear_core_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``Settings(_env_file=None)`` only disables the ``.env`` file;
    # explicit environment variables would otherwise leak from the
    # test infrastructure (or the developer's shell) into the fields
    # under test.
    for name in (
        "LOCKVERITY_ENVIRONMENT",
        "LOCKVERITY_DATABASE_URL",
        "LOCKVERITY_WORKSPACE_ROOT",
        "LOCKVERITY_SERVE_FRONTEND",
        "LOCKVERITY_FRONTEND_DIST",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip every LOCKVERITY_* env var so the test sees
    # the documented default. ``Settings(_env_file=None)``
    # only disables the ``.env`` file; explicit env
    # variables override the field defaults and would
    # otherwise leak from the test infrastructure.
    for name in (
        "LOCKVERITY_ENVIRONMENT",
        "LOCKVERITY_DATABASE_URL",
        "LOCKVERITY_WORKSPACE_ROOT",
        "LOCKVERITY_SERVE_FRONTEND",
        "LOCKVERITY_FRONTEND_DIST",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.environment == "development"
    assert s.api_prefix == "/api/v1"
    assert s.pagination_default_page_size > 0
    assert s.pagination_default_page_size <= s.pagination_max_page_size


def test_cors_origins_accepts_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LOCKVERITY_ENVIRONMENT",
        "LOCKVERITY_DATABASE_URL",
        "LOCKVERITY_WORKSPACE_ROOT",
        "LOCKVERITY_SERVE_FRONTEND",
        "LOCKVERITY_FRONTEND_DIST",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    s = Settings(_env_file=None, cors_origins="a.com,b.com,c.com")  # type: ignore[call-arg]
    assert s.cors_origins == ["a.com", "b.com", "c.com"]


def test_cors_origins_rejects_wildcard_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "LOCKVERITY_ENVIRONMENT",
        "LOCKVERITY_DATABASE_URL",
        "LOCKVERITY_WORKSPACE_ROOT",
        "LOCKVERITY_SERVE_FRONTEND",
        "LOCKVERITY_FRONTEND_DIST",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", cors_origins="*")  # type: ignore[call-arg]


def test_cors_origins_allows_wildcard_in_development() -> None:
    # Production is the only restricted environment. Development
    # can be permissive so local tools work.
    s = Settings(_env_file=None, environment="development", cors_origins="*")  # type: ignore[call-arg]
    assert s.cors_origins == ["*"]


# ---------------------------------------------------------------------------
# LOCKVERITY_CORS_ORIGINS environment parsing
# ---------------------------------------------------------------------------
# pydantic-settings JSON-decodes complex fields (``list[str]``) in
# ``EnvSettingsSource`` *before* any ``mode="before"`` validator runs.
# The field is annotated ``NoDecode`` so the raw string reaches
# ``_split_cors_origins``, which accepts both the documented
# comma-separated form and a JSON list. These tests exercise the
# environment-variable path (not init kwargs) because that is the path
# that raised ``SettingsError`` before the annotation.


def test_cors_origins_env_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.delenv("LOCKVERITY_CORS_ORIGINS", raising=False)
    get_settings.cache_clear()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == []


def test_cors_origins_env_accepts_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exact form documented in ``.env.example``.
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_env_accepts_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", '["https://a.example", "https://b.example"]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_env_trims_whitespace_and_drops_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", " https://a.example , , https://b.example , ")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_env_trims_json_list_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", '[" https://a.example ", "https://b.example "]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_env_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", "[https://a.example")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_cors_origins_env_rejects_json_non_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A quoted scalar is an attempt at the JSON form; it must fail
    # loudly, not become a bogus never-matching origin.
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", '"https://a.example"')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_cors_origins_env_rejects_wildcard_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "production")
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", "https://a.example,*")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_cors_origins_env_values_are_what_the_control_plane_expects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The parsed list is consumed verbatim by the control plane's
    # origin allowlist (:func:`origin_is_trusted`) and by
    # ``CORSMiddleware(allow_origins=...)``, so the exact resulting
    # values are the contract.
    _clear_core_env(monkeypatch)
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", "https://app.example, https://other.example")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["https://app.example", "https://other.example"]

    class _Request:
        headers: ClassVar[dict[str, str]] = {"host": "127.0.0.1:8000"}

        class url:  # noqa: N801 - stand-in for the Starlette URL object
            scheme = "http"

    request = _Request()
    assert origin_is_trusted("https://app.example", request, s)  # type: ignore[arg-type]
    assert origin_is_trusted("https://other.example", request, s)  # type: ignore[arg-type]
    # An unlisted origin is not trusted by the allowlist; it is only
    # accepted when it matches the request's own authority.
    assert not origin_is_trusted("https://attacker.example", request, s)  # type: ignore[arg-type]
    assert origin_is_trusted("http://127.0.0.1:8000", request, s)  # type: ignore[arg-type]


def test_pagination_max_size_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pagination_max_page_size=2000)  # type: ignore[call-arg]


def test_archive_suspicious_ratio_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, archive_suspicious_ratio=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, archive_suspicious_ratio=-10)  # type: ignore[call-arg]


def test_pagination_default_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pagination_default_page_size=0)  # type: ignore[call-arg]
