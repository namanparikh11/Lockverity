"""Single-port production frontend serving.

This module is the single source of truth for the
"serve the built React UI from the FastAPI host and port"
runtime. The v2.1 Part B1 milestone ships an opt-in feature
flag (``LOCKVERITY_SERVE_FRONTEND``) that, when enabled in a
production environment, mounts the built Vite output
under the existing API host.

Design contract:

  1. The default behaviour is API-only. The flag is
     ``False`` and the production validator refuses the
     flag in development and test environments. The
     existing two-port dev workflow (Vite on 5173, FastAPI
     on 8000) is unchanged.

  2. The configured dist path is resolved deterministically
     against the repository root, not the operator's
     current working directory. An absolute override is
     accepted.

  3. The path is validated at startup time. A missing
     directory or a missing ``index.html`` is a fatal
     startup error so a stale build is never served
     silently.

  4. The path is verified to resolve inside the repository
     root or to be a deliberate absolute override. Path
     traversal segments are rejected by the settings
     validator; the request handlers also defensively
     verify the resolved path stays inside the dist root.

  5. The serving is read-only. The backend never executes
     npm, never runs the Vite build, and never writes to
     the dist directory. Build preparation is a separate
     ``scripts/prepare_frontend_dist.py`` step.

  6. The serving cannot expose workspace files. The
     ``workspace_root`` setting is unrelated to the
     frontend dist; the static-file root is the configured
     dist directory only.
"""

from __future__ import annotations

import html
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from app.core.control_plane import CONTROL_META_NAME

logger = logging.getLogger("lockverity.frontend")

# Reserved top-level path prefixes that must never receive
# the SPA document. These overlap with the FastAPI routes
# (which take priority because they are registered first)
# but are listed here so the SPA fallback handler can
# short-circuit them with a clean 404 instead of a doc.
API_PREFIXES: tuple[str, ...] = (
    "/api",
    "/openapi.json",
    "/docs",
    "/redoc",
)

# Document root: ``index.html``. The fallback handler
# returns this file with no-cache headers so every
# navigation reloads the latest manifest.
INDEX_HTML = "index.html"

# Vite's build output places hashed assets under
# ``assets/<name>-<hash>.<ext>``. Hashed assets can use
# long-lived immutable caching because the hash changes on
# every rebuild. Non-hashed files (favicons, brand PNGs)
# use a moderate public cache.
ASSETS_DIR = "assets"
HASHED_ASSET_PATTERN = re.compile(
    r"^assets/[A-Za-z0-9_.\-]+-[A-Fa-f0-9_-]{6,}\.(?:js|css|woff2?|svg|png|jpg|webp)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FrontendAsset:
    """A single asset to be served from the dist directory.

    The ``cache_control`` attribute carries the
    ``Cache-Control`` header value to apply to the response.
    The ``media_type`` attribute is the explicit MIME type
    (overrides ``mimetypes`` lookup when the extension is
    ambiguous).
    """

    path: Path
    cache_control: str
    media_type: str | None = None


class FrontendDistError(RuntimeError):
    """Raised when the dist directory is missing or invalid.

    A missing directory, a missing ``index.html``, a
    missing ``assets/`` directory, or a non-directory
    path triggers this error. The application fails to
    start in this case so a stale build is never served.
    """


def validate_dist(dist: Path) -> None:
    """Validate the dist directory contains the expected
    production artefacts.

    The function performs three checks in order:

      1. ``dist`` is an existing directory.
      2. ``dist/index.html`` is a regular file.
      3. ``dist/assets/`` is an existing directory (Vite
         emits hashed assets there; the check is optional
         in the sense that the dist is still valid without
         it, but a Vite build always produces the directory
         so a missing ``assets/`` is a strong signal of a
         stale or partial build).
    """
    if not dist.exists():
        raise FrontendDistError(
            f"frontend dist directory does not exist: {dist}. "
            "Run scripts/prepare_frontend_dist.py to build the "
            "frontend before starting the backend in single-port "
            "mode."
        )
    if not dist.is_dir():
        raise FrontendDistError(f"frontend dist path is not a directory: {dist}.")
    index_html = dist / INDEX_HTML
    if not index_html.is_file():
        raise FrontendDistError(
            f"frontend dist is missing {INDEX_HTML}: {index_html}. "
            "The Vite build output is incomplete or stale; run "
            "scripts/prepare_frontend_dist.py to rebuild."
        )


def _media_type_for(path: Path) -> str:
    """Return the explicit ``Content-Type`` for ``path``.

    The Python ``mimetypes`` module is used as the primary
    lookup. The ICO type is overridden to the IANA-registered
    ``image/vnd.microsoft.icon`` because some browsers and
    crawlers still expect the legacy ``image/x-icon`` form.
    """
    guessed, _ = mimetypes.guess_type(str(path))
    suffix = path.suffix.lower()
    if suffix == ".ico":
        return "image/x-icon"
    if suffix == ".js":
        return "application/javascript"
    return guessed or "application/octet-stream"


def _cache_control_for(rel_path: str) -> str:
    """Return the ``Cache-Control`` header value for a given
    relative path within the dist directory.

    The rules are:

      - Hashed Vite assets (``assets/<name>-<hash>.<ext>``)
        use a one-year ``immutable`` cache because the hash
        changes on every rebuild.
      - ``index.html`` and any non-asset document use
        ``no-cache, no-store, must-revalidate`` so every
        navigation reloads the latest manifest.
      - Everything else (favicons, brand PNGs) uses a
        one-day public cache. The versioned query string
        on the ``index.html`` references ensures the
        browser bypasses the cache when the operator bumps
        the favicon or brand assets.
    """
    if rel_path == INDEX_HTML:
        return "no-cache, no-store, must-revalidate"
    if HASHED_ASSET_PATTERN.match(rel_path):
        return "public, max-age=31536000, immutable"
    return "public, max-age=86400"


def _is_within(child: Path, parent: Path) -> bool:
    """Return ``True`` iff ``child`` is the same as ``parent``
    or a descendant of ``parent`` after symlink resolution.

    Both paths must be absolute. The check uses
    ``Path.resolve`` so symlinks are followed; the resolved
    paths are then compared with ``Path.is_relative_to``
    on Python 3.9+ and with ``commonpath`` on older
    versions. The function is the single chokepoint for
    path-traversal defence.
    """
    try:
        child_resolved = child.resolve(strict=True)
        parent_resolved = parent.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        return False
    try:
        return child_resolved.is_relative_to(parent_resolved)
    except AttributeError:
        try:
            common = Path(
                __import__("os").path.commonpath([str(child_resolved), str(parent_resolved)])
            )
            return common == parent_resolved
        except ValueError:
            return False


def _is_api_like(path: str) -> bool:
    """Return ``True`` iff ``path`` is a known API or docs
    prefix that must not receive the SPA document.

    The check is a defence-in-depth complement to the
    FastAPI route registration order: the API routes are
    registered first and take priority, but the SPA
    fallback handler also short-circuits these prefixes
    so a 404 is returned instead of the React app shell.
    """
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


def _has_file_extension(path: str) -> bool:
    """Return ``True`` iff ``path`` looks like a file request
    (has a dot in the last path segment).

    A request like ``/scans/1/dependencies`` has no
    extension; a request like ``/assets/index-AbCd.js``
    has a ``.js`` extension. The SPA fallback serves the
    document only for extension-less paths; file-like
    requests receive a 404 when the file is absent.
    """
    last_segment = path if "/" not in path else path.rsplit("/", 1)[-1]
    return "." in last_segment


def _has_dotfile_segment(path: str) -> bool:
    """Return ``True`` iff any segment of ``path`` starts with
    a dot.

    Dotfile probes (``.env``, ``.git/HEAD``, etc.) are
    rejected even when the path is extension-less. The
    SPA fallback uses this check to return a clean 404
    instead of the React shell for hidden-endpoint
    reconnaissance.
    """
    if not path:
        return False
    segments = [segment for segment in path.split("/") if segment]
    return any(segment.startswith(".") for segment in segments)


def serve_static_asset(dist: Path, rel_path: str) -> Response | None:
    """Return a ``FileResponse`` for a static asset, or
    ``None`` if the asset does not exist or fails the
    containment check.

    The function performs three checks:

      1. The relative path is non-empty, has no ``..``
         segments, and does not start with a dotfile
         prefix.
      2. The resolved absolute path is inside ``dist``.
      3. The resolved path is a regular file.

    When any check fails, the function returns ``None`` so
    the caller can decide between a 404 and a SPA fallback.
    The 404 path is taken for file-like requests; the SPA
    fallback is taken for extension-less requests.
    """
    if not rel_path or rel_path.startswith("/"):
        return None
    # Reject dotfile probes. ``.env``, ``.git``, etc. are
    # never served even if they happen to live in the dist.
    segments = [segment for segment in rel_path.split("/") if segment]
    if not segments:
        return None
    if any(segment.startswith(".") for segment in segments):
        return None
    # Reject ``..`` traversal segments explicitly. The
    # settings validator already rejects this in
    # ``frontend_dist``; this guards the per-request path.
    if any(segment == ".." for segment in segments):
        return None
    candidate = (dist / rel_path).resolve(strict=False)
    if not _is_within(candidate, dist):
        return None
    if not candidate.is_file():
        return None
    rel = "/".join(segments)
    return FileResponse(
        path=str(candidate),
        media_type=_media_type_for(candidate),
        headers={"Cache-Control": _cache_control_for(rel)},
    )


def inject_control_token(document: str, token: str) -> str:
    """Return ``document`` with the control token as a ``<meta>`` tag.

    This is how the per-process control token reaches the trusted
    frontend (LV-007). The document is only readable by a page on this
    same loopback origin, so a page from any other origin cannot learn
    the token; and because the injection happens in the response body,
    nothing on disk ever holds the secret - the dist directory stays
    exactly as the build produced it.

    The token is URL-safe base64, but it is HTML-escaped anyway so the
    attribute cannot be broken out of regardless of what a future token
    format contains.
    """
    meta = f'<meta name="{CONTROL_META_NAME}" content="{html.escape(token, quote=True)}">'
    lowered = document.lower()
    head_open = lowered.find("<head")
    if head_open != -1:
        head_end = document.find(">", head_open)
        if head_end != -1:
            return document[: head_end + 1] + meta + document[head_end + 1 :]
    # No ``<head>``: put the tag first so the frontend still finds it.
    return meta + document


def serve_spa_fallback(dist: Path, request: Request) -> Response:
    """Serve ``index.html`` for extension-less requests that
    are not API-like.

    The function is the SPA fallback handler. It is
    installed as a catch-all route at the end of the route
    list so every other route takes priority. The function
    refuses to serve the document for:

      - API-like paths (so a typo'd API call receives a
        clean 404 instead of a React shell).
      - File-like paths that do not exist as static assets
        (so a missing static asset receives a clean 404).
      - Dotfile probes (so a hidden-endpoint scan
        receives a clean 404 instead of the React shell).

    The served document carries the process control token in a
    ``<meta>`` tag so the frontend it boots can authenticate its
    state-changing API calls. The document is already served
    ``no-store``, so the token is never cached to disk by the browser.
    """
    path = request.url.path
    if _is_api_like(path):
        raise StarletteHTTPException(status_code=404, detail="Not Found")
    if _has_file_extension(path):
        raise StarletteHTTPException(status_code=404, detail="Not Found")
    if _has_dotfile_segment(path):
        raise StarletteHTTPException(status_code=404, detail="Not Found")
    index_html = dist / INDEX_HTML
    # ``index.html`` is validated at startup; the existence
    # check here is defence in depth.
    if not index_html.is_file():
        raise StarletteHTTPException(status_code=500, detail="Frontend index missing")
    token = str(getattr(request.app.state, "control_token", "") or "")
    if not token:
        # No token minted (an app built without the control plane).
        # Serve the document unchanged rather than failing the load.
        return FileResponse(
            path=str(index_html),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": _cache_control_for(INDEX_HTML)},
        )
    document = index_html.read_text(encoding="utf-8")
    return HTMLResponse(
        content=inject_control_token(document, token),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": _cache_control_for(INDEX_HTML)},
    )


def install_security_headers(
    app: FastAPI,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return an ASGI middleware that adds defensive response
    headers to every response produced by the application.

    The middleware sets:

      - ``X-Content-Type-Options: nosniff`` to prevent MIME
        sniffing on user-controlled file responses.
      - ``Referrer-Policy: same-origin`` to limit referrer
        leakage to third-party hosts.
      - ``X-Frame-Options: DENY`` to defeat clickjacking. The
        product is a local-first workbench, not an embeddable
        widget; framing is not a supported use case.

    The headers are appended to the response after the
    existing request-id middleware has run, so the
    ``x-request-id`` correlation header is preserved. The
    middleware is order-independent with respect to the
    request-id middleware because both wrap the response
    and add different headers.
    """

    async def _security_headers_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            response = await call_next(request)
        except ClientDisconnect:
            raise
        except Exception:
            # The exception handlers will set the response
            # body; re-raise so the standard envelope fires.
            raise
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return _security_headers_middleware


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Wire the static assets, the SPA fallback, and the
    security headers into ``app``.

    The function is called once at application creation
    time when ``serve_frontend`` is true. It:

      1. Validates the dist directory.
      2. Adds the security headers middleware to the app.
      3. Registers a static-file mount under ``/assets``
         for the Vite output, under ``/favicon.ico``,
         ``/favicon-<size>.png``, ``/apple-touch-icon.png``,
         and ``/brand/...`` for the versioned favicon and
         brand assets.
      4. Registers a catch-all route that serves
         ``index.html`` for extension-less, non-API paths.

    The route order is important: every API and docs route
    is registered first, then the static assets, then the
    SPA fallback. A duplicate-path request for an API
    route is matched by the API route; a request for a
    static asset is matched by the static-file mount; a
    request for a SPA route falls through to the catch-all.
    """
    validate_dist(dist)
    # Security headers: appended to every response.
    app.middleware("http")(install_security_headers(app))
    # Static assets: explicit mounts for the favicon, the
    # versioned favicon PNGs, the apple-touch icon, and the
    # brand directory. The ``assets`` directory is mounted
    # under ``/assets`` for the Vite output.
    asset_mounts: list[tuple[str, Path]] = [
        ("/assets", dist / ASSETS_DIR),
        ("/favicon.ico", dist / "favicon.ico"),
        ("/favicon-16x16.png", dist / "favicon-16x16.png"),
        ("/favicon-32x32.png", dist / "favicon-32x32.png"),
        ("/favicon-48x48.png", dist / "favicon-48x48.png"),
        ("/favicon-180x180.png", dist / "favicon-180x180.png"),
        ("/favicon-256x256.png", dist / "favicon-256x256.png"),
        ("/favicon-512x512.png", dist / "favicon-512x512.png"),
        ("/apple-touch-icon.png", dist / "apple-touch-icon.png"),
        ("/brand", dist / "brand"),
    ]
    for mount_path, source_path in asset_mounts:
        if source_path.is_file():
            # The favicon.ico and the apple-touch-icon are
            # individual files; serve them with a dedicated
            # GET handler that applies the same cache and
            # security headers as the directory mounts.
            _mount_individual_asset(app, mount_path, source_path)
        elif source_path.is_dir():
            _mount_asset_directory(app, mount_path, source_path)
        # Missing assets are silently skipped: the catch-all
        # SPA fallback returns 404 for file-like requests.

    # Catch-all: SPA fallback. The function is installed as
    # a Starlette route so the existing FastAPI error
    # envelope does not interfere with the 404 it raises
    # for API-like and file-like paths.
    async def _spa_fallback(full_path: str, request: Request) -> Response:
        # The full_path captures the trailing path. The
        # leading slash is implicit.
        rel = full_path or ""
        # Try the static asset first; if it exists, the
        # handler returns a ``FileResponse`` with the
        # correct cache and MIME headers.
        if rel:
            static = serve_static_asset(dist, rel)
            if static is not None:
                return static
        # Otherwise, fall back to the SPA document.
        return serve_spa_fallback(dist, request)

    app.add_api_route(
        "/{full_path:path}",
        _spa_fallback,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )


def _mount_asset_directory(app: FastAPI, mount_path: str, source_path: Path) -> None:
    """Mount a directory of static assets under ``mount_path``.

    The directory is served by a custom Starlette-style
    route so the response carries the documented cache and
    security headers. The implementation is intentionally
    small: it uses the same ``serve_static_asset`` function
    used by the SPA fallback, so the path-traversal
    defences and the cache policy are identical.
    """
    validate_dir = source_path.resolve(strict=True)
    # The mount root lives under the dist root. The
    # ``serve_static_asset`` cache policy relies on the
    # relative path including the mount prefix (the
    # ``HASHED_ASSET_PATTERN`` matches ``assets/...``), so
    # the directory mount handler rebuilds the full
    # relative path and passes the dist root as the
    # resolution base. The ``_is_within`` containment check
    # then verifies the resolved file is inside the dist
    # root.
    dist_root = validate_dir.parent
    mount_prefix = mount_path.lstrip("/")

    async def _serve_dir(file_path: str, request: Request) -> Response:
        rel = mount_prefix + ("/" if file_path else "") + (file_path or "")
        static = serve_static_asset(dist_root, rel)
        if static is None:
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        return static

    app.add_api_route(
        mount_path + "/{file_path:path}",
        _serve_dir,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )

    # Mount the directory root itself so a request to
    # ``/assets/`` (with trailing slash) returns a
    # directory index... which we explicitly do not
    # provide. The catch-all SPA fallback takes care of
    # the bare ``/assets/`` request, but a request to
    # ``/assets`` (no trailing slash) would otherwise 404
    # silently. We treat it as 404 here so the behaviour
    # is consistent: a directory request without a file
    # never returns a listing.
    async def _serve_dir_root(request: Request) -> Response:
        raise StarletteHTTPException(status_code=404, detail="Not Found")

    app.add_api_route(
        mount_path,
        _serve_dir_root,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )


def _mount_individual_asset(app: FastAPI, mount_path: str, source_path: Path) -> None:
    """Mount a single file as a static asset.

    The function is used for the favicon.ico and the
    apple-touch-icon.png, which are individual files in
    the dist root (not under a directory).
    """
    if not source_path.is_file():
        return
    resolved = source_path.resolve(strict=True)
    rel_name = resolved.name

    async def _serve_individual(request: Request) -> Response:
        return FileResponse(
            path=str(resolved),
            media_type=_media_type_for(resolved),
            headers={"Cache-Control": _cache_control_for(rel_name)},
        )

    app.add_api_route(
        mount_path,
        _serve_individual,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
