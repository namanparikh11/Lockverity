"""Lockverity FastAPI application entry point.

Wires the structured error envelope, request-id middleware, CORS,
and every API router under the configured prefix. When the
``LOCKVERITY_SERVE_FRONTEND`` flag is enabled in a production
environment, the same process also hosts the built React UI
from the configured dist directory (single-port runtime).

Run locally (two-port dev workflow, unchanged)::

    uvicorn app.main:app --reload

Run in single-port production mode::

    LOCKVERITY_ENVIRONMENT=production \\
    LOCKVERITY_SERVE_FRONTEND=true \\
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

The app does not serve any user-uploaded content, does not expose
the workspace root, and does not proxy to external providers.
The static frontend, when hosted by this process, is a
read-only mount of a pre-built Vite output; the backend never
executes npm or runs the Vite build itself.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.core.config import get_settings
from app.core.control_plane import (
    CONTROL_HEADER,
    install_control_plane,
    mint_control_token,
)
from app.core.errors import register_exception_handlers
from app.db import engine
from app.static_frontend import FrontendDistError, mount_frontend
from app.utils.datetime import isoformat_utc, utcnow

logger = logging.getLogger("lockverity")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Sanity-check the schema at startup.

    We do not run migrations automatically - the operator runs
    ``alembic upgrade head`` before starting the app. We do,
    however, log a warning if the metadata doesn't match the
    database, which usually means migrations were skipped.
    """
    settings = get_settings()
    logger.info(
        "lockverity %s starting (env=%s, db=%s)",
        __version__,
        settings.environment,
        settings.database_url,
    )
    yield
    logger.info("lockverity shutting down (now=%s)", isoformat_utc(utcnow()))
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=settings.app_tagline,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    # The per-process control token. It is minted here, kept only on
    # ``app.state``, and never written to disk, logged, or serialised
    # into any response other than the ``index.html`` this same process
    # serves to its own window. See :mod:`app.core.control_plane`.
    app.state.control_token = mint_control_token(settings)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Accept", "Content-Type", CONTROL_HEADER],
            max_age=600,
        )

    # Registered before the request-id middleware so it ends up *inside*
    # it: by the time the policy runs, ``request.state.request_id`` is
    # already set and a refusal can carry the same correlation id as any
    # other error envelope.
    app.middleware("http")(install_control_plane(app, settings=settings))

    @app.middleware("http")
    async def _request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Reuse a client-supplied request id if present, otherwise
        # mint a new one. The id is returned on every error envelope
        # so operators can correlate a user-visible failure to a
        # server log line.
        incoming = request.headers.get("x-request-id", "").strip()
        request_id = incoming or secrets.token_hex(16)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    # Single-port production frontend serving. The flag is
    # validated in :class:`Settings` to refuse the
    # non-production environments. The dist validation is
    # strict: a missing or stale build aborts startup so the
    # operator notices immediately instead of serving a
    # half-broken SPA.
    if settings.serve_frontend:
        dist_path = settings.frontend_dist_path
        try:
            mount_frontend(app, dist_path)
        except FrontendDistError as exc:
            logger.error("frontend dist validation failed: %s", exc)
            raise
        logger.info(
            "serving built frontend from %s on the API host",
            dist_path,
        )

    return app


# Convenience for ``uvicorn app.main:app``.
app = create_app()


__all__ = ["app", "create_app"]
