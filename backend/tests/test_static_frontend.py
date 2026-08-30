"""Tests for the single-port production frontend serving.

These tests cover the v2.1 Part B1 ``app.static_frontend``
module. The module is the single source of truth for
serving the built React UI from the FastAPI host. The
tests are synthetic: every test creates a temporary
dist directory with the expected artefacts, mounts the
frontend on a fresh FastAPI app, and exercises the
documented behaviour. The tests do not depend on a
committed production build.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings, get_settings
from app.main import create_app
from app.static_frontend import (
    HASHED_ASSET_PATTERN,
    INDEX_HTML,
    FrontendDistError,
    _cache_control_for,
    _has_file_extension,
    _is_api_like,
    _is_within,
    _media_type_for,
    serve_static_asset,
    validate_dist,
)

from tests.api_client import api_client

# --- Test fixtures ----------------------------------------------------


@pytest.fixture
def synthetic_dist(tmp_path: Path) -> Iterator[Path]:
    """Create a synthetic dist directory with a realistic
    production layout.

    The fixture creates a temporary directory that
    contains a minimal ``index.html``, a Vite ``assets/``
    directory with a hashed CSS and JS file, the
    approved favicon and brand assets, and an
    ``apple-touch-icon.png``. The fixture is used by the
    serving tests to exercise the documented behaviour
    without depending on a committed production build.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Lockverity</title></head>"
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "index-AbCdEf.css").write_text("body { color: #0f172a }", encoding="utf-8")
    (assets / "index-AbCdEf.js").write_text("console.log('lockverity');", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (dist / "favicon-32x32.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (dist / "apple-touch-icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    brand = dist / "brand"
    brand.mkdir()
    (brand / "lockverity-symbol.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    yield dist
    shutil.rmtree(dist, ignore_errors=True)


@pytest.fixture
def settings_with_dist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Settings]:
    """Return a Settings instance configured to serve a
    synthetic dist directory.

    The fixture sets the ``LOCKVERITY_ENVIRONMENT`` and
    ``LOCKVERITY_SERVE_FRONTEND`` environment variables,
    then clears the ``get_settings`` lru_cache and returns
    a fresh Settings. The fixture is used by the startup
    validation tests.
    """
    monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "production")
    monkeypatch.setenv("LOCKVERITY_SERVE_FRONTEND", "true")
    monkeypatch.setenv("LOCKVERITY_FRONTEND_DIST", str(tmp_path))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def app_with_synthetic_dist(
    synthetic_dist: Path,
) -> Iterator[Any]:
    """Return a FastAPI app with the synthetic dist mounted.

    The fixture configures the ``LOCKVERITY_SERVE_FRONTEND``
    flag via the same settings override the runtime uses.
    The TestClient exercises the app in-process so the
    tests are hermetic and fast.
    """

    os.environ["LOCKVERITY_ENVIRONMENT"] = "production"
    os.environ["LOCKVERITY_SERVE_FRONTEND"] = "true"
    os.environ["LOCKVERITY_FRONTEND_DIST"] = str(synthetic_dist)
    get_settings.cache_clear()
    try:
        app = create_app()
        with api_client(app) as client:
            yield client
    finally:
        get_settings.cache_clear()
        for key in (
            "LOCKVERITY_ENVIRONMENT",
            "LOCKVERITY_SERVE_FRONTEND",
            "LOCKVERITY_FRONTEND_DIST",
        ):
            os.environ.pop(key, None)


@pytest.fixture
def app_default_api_only() -> Iterator[Any]:
    """Return a FastAPI app with frontend serving disabled.

    The fixture clears any frontend-serving overrides and
    returns a production-environment app that serves the
    API only. The tests verify that the default behaviour
    is unchanged.
    """

    os.environ["LOCKVERITY_ENVIRONMENT"] = "production"
    os.environ.pop("LOCKVERITY_SERVE_FRONTEND", None)
    get_settings.cache_clear()
    try:
        app = create_app()
        with api_client(app) as client:
            yield client
    finally:
        get_settings.cache_clear()
        os.environ.pop("LOCKVERITY_ENVIRONMENT", None)


# --- Unit tests for the helpers --------------------------------------


class TestApiLikeDetection:
    """``_is_api_like`` must reject every reserved prefix."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api",
            "/api/v1/health",
            "/api/v1/system/info",
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
        ],
    )
    def test_api_paths_are_rejected(self, path: str) -> None:
        assert _is_api_like(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/about",
            "/scans/1/findings",
            "/repositories/1",
            "/favicon.ico",
            "/assets/index-AbCdEf.js",
        ],
    )
    def test_non_api_paths_are_accepted(self, path: str) -> None:
        assert _is_api_like(path) is False


class TestFileExtensionDetection:
    """``_has_file_extension`` must distinguish file-like
    requests from SPA routes."""

    @pytest.mark.parametrize(
        "path",
        [
            "/missing.png",
            "/favicon.ico",
            "/assets/index-AbCdEf.js",
            "/.env",
        ],
    )
    def test_file_like_paths_are_detected(self, path: str) -> None:
        assert _has_file_extension(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/about",
            "/scans/1/findings",
            "/repositories/1",
        ],
    )
    def test_spa_routes_have_no_extension(self, path: str) -> None:
        assert _has_file_extension(path) is False


class TestCacheControl:
    """``_cache_control_for`` must follow the documented
    cache policy."""

    def test_index_html_uses_no_cache(self) -> None:
        assert _cache_control_for(INDEX_HTML) == "no-cache, no-store, must-revalidate"

    def test_hashed_asset_uses_immutable(self) -> None:
        assert _cache_control_for("assets/index-AbCdEf.js") == "public, max-age=31536000, immutable"

    def test_favicon_uses_public_one_day(self) -> None:
        # The favicon is not a hashed asset and not
        # ``index.html``; it uses the moderate public
        # cache. The ``?v=3`` query in ``index.html``
        # busts the cache when the favicon changes.
        assert _cache_control_for("favicon.ico") == "public, max-age=86400"


class TestMediaType:
    """``_media_type_for`` must return the correct content
    type for the documented extensions."""

    def test_png_returns_image_png(self, tmp_path: Path) -> None:
        path = tmp_path / "test.png"
        path.write_bytes(b"")
        assert _media_type_for(path) == "image/png"

    def test_ico_returns_image_x_icon(self, tmp_path: Path) -> None:
        path = tmp_path / "test.ico"
        path.write_bytes(b"")
        assert _media_type_for(path) == "image/x-icon"

    def test_js_returns_application_javascript(self, tmp_path: Path) -> None:
        path = tmp_path / "test.js"
        path.write_bytes(b"")
        assert _media_type_for(path) == "application/javascript"

    def test_unknown_extension_returns_octet_stream(self, tmp_path: Path) -> None:
        path = tmp_path / "test.unknownext"
        path.write_bytes(b"")
        assert _media_type_for(path) == "application/octet-stream"


class TestIsWithin:
    """``_is_within`` must reject path traversal escapes."""

    def test_child_inside_parent(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child.txt"
        child.write_text("x")
        assert _is_within(child, parent) is True

    def test_child_outside_parent(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        sibling = tmp_path / "sibling.txt"
        sibling.write_text("x")
        assert _is_within(sibling, parent) is False

    def test_parent_is_within_itself(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        assert _is_within(parent, parent) is True


class TestHashedAssetPattern:
    """The HASHED_ASSET_PATTERN regex must match the
    documented Vite asset shape and reject everything else."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "assets/index-AbCdEf.js",
            "assets/index-123456.css",
            "assets/LockveritySymbol-_-A1b2C3.png",
            "assets/LockveritySymbol-__A1b2C3-D4e5F6.woff2",
        ],
    )
    def test_hashed_assets_match(self, rel_path: str) -> None:
        assert HASHED_ASSET_PATTERN.match(rel_path) is not None

    @pytest.mark.parametrize(
        "rel_path",
        [
            "favicon.ico",
            "favicon-32x32.png",
            "index.html",
            "assets/index.js",  # no hash
            "assets/index-AbCdEf",  # no extension
            "assets/-AbCdEf.js",  # empty prefix
        ],
    )
    def test_non_hashed_paths_do_not_match(self, rel_path: str) -> None:
        assert HASHED_ASSET_PATTERN.match(rel_path) is None


# --- Dist validation tests --------------------------------------------


class TestValidateDist:
    """``validate_dist`` must reject a missing or stale dist."""

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(FrontendDistError) as exc_info:
            validate_dist(missing)
        assert "does not exist" in str(exc_info.value)

    def test_missing_index_html_raises(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        with pytest.raises(FrontendDistError) as exc_info:
            validate_dist(dist)
        assert "missing" in str(exc_info.value).lower()
        assert "index.html" in str(exc_info.value)

    def test_valid_dist_succeeds(self, synthetic_dist: Path) -> None:
        # Must not raise.
        validate_dist(synthetic_dist)


# --- Static asset serving tests ---------------------------------------


class TestServeStaticAsset:
    """``serve_static_asset`` must return ``FileResponse``
    for known files and ``None`` for everything else."""

    def test_returns_file_for_known_asset(self, synthetic_dist: Path) -> None:
        response = serve_static_asset(synthetic_dist, "favicon.ico")
        assert response is not None
        assert response.headers["content-type"] == "image/x-icon"
        assert "cache-control" in response.headers

    def test_returns_file_for_hashed_asset(self, synthetic_dist: Path) -> None:
        response = serve_static_asset(synthetic_dist, "assets/index-AbCdEf.js")
        assert response is not None
        assert response.headers["content-type"] == "application/javascript"
        assert "immutable" in response.headers["cache-control"]

    def test_returns_none_for_missing_file(self, synthetic_dist: Path) -> None:
        assert serve_static_asset(synthetic_dist, "missing.png") is None

    def test_returns_none_for_traversal(self, synthetic_dist: Path) -> None:
        assert serve_static_asset(synthetic_dist, "../etc/passwd") is None

    def test_returns_none_for_dotfile(self, synthetic_dist: Path) -> None:
        assert serve_static_asset(synthetic_dist, ".env") is None


# --- App-level integration tests --------------------------------------


class TestAppDefaultBehavior:
    """When ``serve_frontend`` is false the app serves the
    API only and does not mount any static route."""

    def test_root_returns_404_when_serving_disabled(self, app_default_api_only: Any) -> None:
        response = app_default_api_only.get("/")
        assert response.status_code == 404

    def test_favicon_returns_404_when_serving_disabled(self, app_default_api_only: Any) -> None:
        response = app_default_api_only.get("/favicon.ico")
        assert response.status_code == 404


class TestAppServingEnabled:
    """When ``serve_frontend`` is true the app serves the
    built UI from the same host and port."""

    def test_root_serves_index(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert b'<div id="root"' in response.content
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"

    def test_spa_route_serves_index(self, app_with_synthetic_dist: Any) -> None:
        # The ``/about`` route has no file extension and is
        # not API-like; the SPA fallback must serve the
        # document.
        response = app_with_synthetic_dist.get("/about")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert b'<div id="root"' in response.content

    def test_nested_spa_route_serves_index(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/repositories/123/scans/456/findings")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_static_asset_served(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/favicon-32x32.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "public, max-age=86400"

    def test_hashed_asset_served(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/assets/index-AbCdEf.js")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/javascript"
        assert "immutable" in response.headers["cache-control"]

    def test_brand_asset_served(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/brand/lockverity-symbol.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"


class TestApiRoutesNotShadowed:
    """The static serving must not shadow the API, docs,
    OpenAPI, health, or diagnostics routes."""

    def test_api_health(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    def test_api_system_info(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/api/v1/system/info")
        assert response.status_code == 200
        body = response.json()
        assert "version" in body

    def test_openapi_json(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/openapi.json")
        assert response.status_code == 200
        body = response.json()
        assert "openapi" in body
        assert "paths" in body

    def test_docs(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


class Test404Behavior:
    """Unknown file-like paths and unknown API-like paths
    must return a clean 404 instead of the SPA document."""

    def test_missing_file_returns_404(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/missing.png")
        assert response.status_code == 404

    def test_missing_hashed_asset_returns_404(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/assets/missing-AbCdEf.js")
        assert response.status_code == 404

    def test_unknown_api_route_returns_404(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/api/v1/this-does-not-exist")
        assert response.status_code == 404


class TestPathTraversalProtection:
    """The serving must reject path traversal, encoded
    traversal, backslash traversal, and dotfile probes."""

    def test_dotfile_probe_rejected(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/.env")
        assert response.status_code == 404

    def test_dotfile_in_subpath_rejected(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/.git/HEAD")
        # A dotfile-prefixed segment in the path is
        # rejected by ``serve_static_asset``. The path
        # itself does not have a file extension, so the
        # SPA fallback would otherwise serve the
        # document. The dotfile check takes priority.
        assert response.status_code == 404

    def test_encoded_traversal_in_asset_rejected(self, app_with_synthetic_dist: Any) -> None:
        # The TestClient normalises the URL before
        # sending; the request reaches the server with
        # the path already decoded. The server's
        # segment check rejects ``..`` regardless of how
        # it was encoded in the request line.
        response = app_with_synthetic_dist.get("/assets/..%2Fetc%2Fpasswd")
        assert response.status_code == 404


class TestSecurityHeaders:
    """Every response must carry the documented defensive
    headers."""

    def test_index_has_nosniff(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/")
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_index_has_referrer_policy(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/")
        assert response.headers["referrer-policy"] == "same-origin"

    def test_index_has_frame_options(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/")
        assert response.headers["x-frame-options"] == "DENY"

    def test_404_has_nosniff(self, app_with_synthetic_dist: Any) -> None:
        response = app_with_synthetic_dist.get("/missing.png")
        assert response.headers["x-content-type-options"] == "nosniff"


class TestStartupValidation:
    """The dist is validated at startup; a missing or
    stale build aborts the process."""

    def test_missing_dist_aborts_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "no-such-dist"
        monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "production")
        monkeypatch.setenv("LOCKVERITY_SERVE_FRONTEND", "true")
        monkeypatch.setenv("LOCKVERITY_FRONTEND_DIST", str(missing))
        get_settings.cache_clear()
        with pytest.raises(FrontendDistError):
            create_app()
        get_settings.cache_clear()

    def test_missing_index_aborts_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_dist = tmp_path / "empty-dist"
        empty_dist.mkdir()
        monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "production")
        monkeypatch.setenv("LOCKVERITY_SERVE_FRONTEND", "true")
        monkeypatch.setenv("LOCKVERITY_FRONTEND_DIST", str(empty_dist))
        get_settings.cache_clear()
        with pytest.raises(FrontendDistError):
            create_app()
        get_settings.cache_clear()

    def test_serve_frontend_refused_in_development(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The flag is only supported in production.
        monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "development")
        monkeypatch.setenv("LOCKVERITY_SERVE_FRONTEND", "true")
        get_settings.cache_clear()
        with pytest.raises(Exception) as exc_info:
            get_settings()
        assert "production" in str(exc_info.value).lower()
        get_settings.cache_clear()


class TestAbsoluteDistOverride:
    """An absolute ``frontend_dist`` path must be accepted
    and used as-is."""

    def test_absolute_path_used_as_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        abs_dist = tmp_path / "abs-dist"
        abs_dist.mkdir()
        (abs_dist / "index.html").write_text("<html><body>absolute</body></html>", encoding="utf-8")
        monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "production")
        monkeypatch.setenv("LOCKVERITY_SERVE_FRONTEND", "true")
        monkeypatch.setenv("LOCKVERITY_FRONTEND_DIST", str(abs_dist))
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.frontend_dist_path == abs_dist.resolve()
        finally:
            get_settings.cache_clear()


class TestRelativeDistPath:
    """A relative ``frontend_dist`` path must resolve to
    the repository root, not the current working
    directory."""

    def test_relative_path_resolves_to_repo_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The default ``frontend/dist`` must resolve to
        # the repo root regardless of the CWD. The
        # test does not depend on the actual dist
        # existing; it only checks the resolved path.
        monkeypatch.delenv("LOCKVERITY_FRONTEND_DIST", raising=False)
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.frontend_dist_path.is_absolute()
            assert settings.frontend_dist_path.name == "dist"
            assert settings.frontend_dist_path.parent.name == "frontend"
        finally:
            get_settings.cache_clear()
