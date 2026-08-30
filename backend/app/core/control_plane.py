"""Local control-plane authentication for state-changing API requests.

Why this exists (LV-007)
========================

Lockverity binds loopback only. Loopback is a *network* boundary, not a
*caller* boundary: every process on the machine can reach the port, and
so can any web page the user happens to have open, because a browser
will happily issue a cross-site request to ``http://127.0.0.1:<port>``.
Without an application-level check, a page on any website could have
started scans, cancelled work, or wiped workspaces simply because
Lockverity was running.

The protection is deliberately small. This is a single-user desktop
application's ephemeral control boundary, not account authentication:

  * The process mints one unguessable **control token** at startup. It
    lives in memory for the lifetime of the process, is never written to
    disk, never logged, never placed in a URL, and never included in
    diagnostics, exports, evidence, or the CLI state file (whose
    secret-free schema is a documented contract).

  * The token reaches the trusted frontend by being injected into the
    ``index.html`` this same process serves. A page from another origin
    cannot read that document, so it cannot learn the token.

  * Every state-changing API request must present the token in the
    ``X-Lockverity-Control`` request header. A custom header makes the
    request a non-simple cross-origin request, so a browser must
    preflight it, and the preflight fails: the application ships with no
    permissive CORS policy. The token is accepted from that header and
    nowhere else - never a query parameter, a URL fragment, a cookie, or
    a path segment.

  * ``Host`` is checked on every API request so a DNS-rebinding page
    cannot reach the port under an attacker-controlled name, and
    ``Origin``, when present, must be same-origin or an origin the
    operator explicitly configured.

Threat model, stated honestly
=============================

This is strong protection against **arbitrary websites** and against
**accidental unauthenticated callers** (a curl in the wrong terminal, a
local tool probing ports). It is *not* a boundary against same-user
malware: any process running as the user can read this process's memory,
read the served document over the same loopback port, or simply act as
the user directly. Nothing an application can do inside its own process
changes that. The claim made here is exactly the first one, no more.

Classification
==============

Requests are gated by HTTP method. Every route in this application that
uses a mutating method is genuinely state-changing - there is no
deliberately side-effect-free ``POST``. :data:`STATE_CHANGING_ROUTES` is
the reviewed inventory of those routes and the test suite asserts the
application's real routing table still matches it, so a new mutating
route cannot be added without that review happening.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.utils.errors import ApiErrorCode

# The request header that carries the control token. A custom header is
# the point: a browser cannot send one cross-origin without a successful
# CORS preflight, which this application never grants to a foreign
# origin.
CONTROL_HEADER = "X-Lockverity-Control"

# The ``<meta>`` name the served document carries. The frontend reads the
# token from here; it is the only delivery channel in the shipped
# single-port runtime.
CONTROL_META_NAME = "lockverity-control"

# Entropy for the minted token, in bytes, before URL-safe encoding.
CONTROL_TOKEN_BYTES = 32

# Methods that may change state. Gating on the method is safe here only
# because the inventory below is asserted against the live routing table.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The reviewed inventory of state-changing routes, relative to the API
# prefix. Every entry was read and confirmed to mutate persistent state,
# spawn work, or reach the network on the operator's behalf:
#
#   /repositories                     registers a repository row
#   /repositories/github              fetches a remote archive, creates rows
#   /repositories/upload              accepts an archive, creates rows
#   /repositories/{id}/scans          queues a scan
#   /repositories/{id}/rescan         prepares a workspace and queues a scan
#   /scans/{id}/run                   schedules a scan on the local worker
#   /scans/{id}/auto-run              runs a scan synchronously
#   /scans/{id}/cancel                marks a running scan for cancellation
#   /system/workspaces/cleanup        deletes stale workspaces from disk
#
# ``tests/test_control_plane.py`` asserts this set equals the mutating
# routes the application actually registers.
STATE_CHANGING_ROUTES: frozenset[str] = frozenset(
    {
        "/repositories",
        "/repositories/github",
        "/repositories/upload",
        "/repositories/{repository_id}/scans",
        "/repositories/{repository_id}/rescan",
        "/scans/{scan_id}/run",
        "/scans/{scan_id}/auto-run",
        "/scans/{scan_id}/cancel",
        "/system/workspaces/cleanup",
    }
)

# Hostnames that always name this machine's loopback interface. Any
# ``127.0.0.0/8`` literal is accepted too (see :func:`_is_loopback_name`);
# a name that merely *resolves* to loopback is not, because that is
# precisely the DNS-rebinding trick the check exists to stop.
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Rejection reasons. They are diagnostic labels only: none of them
# reveals the expected token or any part of it.
REASON_HOST = "control_host_rejected"
REASON_ORIGIN = "control_origin_rejected"
REASON_TOKEN_MISSING = "control_token_missing"  # noqa: S105 - a label, not a secret
REASON_TOKEN_INVALID = "control_token_invalid"  # noqa: S105 - a label, not a secret

_REJECTION_MESSAGES = {
    REASON_HOST: (
        "Request was refused: the Host header does not name this "
        "instance's loopback address and port."
    ),
    REASON_ORIGIN: (
        "Request was refused: the request originated from an origin this instance does not trust."
    ),
    REASON_TOKEN_MISSING: (
        "Request was refused: state-changing requests must present the "
        f"{CONTROL_HEADER} header issued to the local Lockverity window."
    ),
    REASON_TOKEN_INVALID: (
        "Request was refused: the control header did not match this "
        "instance. Reload the Lockverity window and try again."
    ),
}


def mint_control_token(settings: object) -> str:
    """Return the control token for a freshly created application.

    Normally the token is a fresh random value with process lifetime.
    Outside production an operator may pin it with
    ``LOCKVERITY_CONTROL_TOKEN`` so the two-port Vite dev workflow (where
    the dev server, not this process, serves ``index.html``) can send a
    matching ``VITE_CONTROL_TOKEN``. The override is ignored in
    production, so the shipped runtime always mints its own.
    """
    override = str(getattr(settings, "control_token", "") or "").strip()
    if override and getattr(settings, "environment", None) != "production":
        return override
    return secrets.token_urlsafe(CONTROL_TOKEN_BYTES)


def _split_authority(authority: str) -> tuple[str, int | None] | None:
    """Split ``host[:port]`` into ``(hostname, port)``.

    Returns ``None`` when the value is empty or the port is not a
    number. IPv6 literals keep their brackets stripped so the result
    compares against :data:`_LOOPBACK_NAMES`.
    """
    value = authority.strip()
    if not value:
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing == -1:
            return None
        host = value[1:closing]
        rest = value[closing + 1 :]
        if not rest:
            return host.lower(), None
        if not rest.startswith(":"):
            return None
        try:
            return host.lower(), int(rest[1:])
        except ValueError:
            return None
    if value.count(":") > 1:
        # A bare IPv6 literal without brackets. It cannot carry a port.
        return value.lower(), None
    if ":" in value:
        host, _, port_text = value.partition(":")
        try:
            return host.lower(), int(port_text)
        except ValueError:
            return None
    return value.lower(), None


def _is_loopback_name(hostname: str) -> bool:
    """Return ``True`` for a literal that always names this machine."""
    if hostname in _LOOPBACK_NAMES:
        return True
    # Any 127.0.0.0/8 literal. Parsed rather than prefix-matched so
    # ``127.0.0.1.evil.example`` is not mistaken for a loopback literal.
    parts = hostname.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def host_is_trusted(host_header: str, settings: object) -> bool:
    """Return ``True`` iff ``Host`` names this instance's own authority.

    The check defeats DNS rebinding: a page served from
    ``evil.example`` whose name resolves to ``127.0.0.1`` still sends
    ``Host: evil.example``, which is refused here.

    When the runtime knows the port it was started on
    (``LOCKVERITY_CLI_PORT``, set by the CLI for every managed child)
    the port must match exactly, so the dynamic GUI port is pinned as
    tightly as a fixed one. When the port is unknown - a bare
    ``uvicorn app.main:app``, or an in-process test client - only the
    hostname is checked.
    """
    split = _split_authority(host_header)
    if split is None:
        return False
    hostname, port = split
    bound_host = str(getattr(settings, "cli_host", "") or "").strip().lower()
    # A non-loopback name is trusted only when the operator deliberately
    # bound that exact interface with ``--allow-remote``.
    if not _is_loopback_name(hostname) and (not bound_host or hostname != bound_host.strip("[]")):
        return False
    expected_port = getattr(settings, "cli_port", None)
    if expected_port is not None:
        try:
            expected_port = int(expected_port)
        except (TypeError, ValueError):
            return True
        if port != expected_port:
            return False
    return True


def _normalise_origin(origin: str) -> tuple[str, str, int] | None:
    """Return ``(scheme, hostname, port)`` for an origin string."""
    parts = urlsplit(origin.strip())
    if not parts.scheme or not parts.netloc:
        return None
    split = _split_authority(parts.netloc)
    if split is None:
        return None
    hostname, port = split
    scheme = parts.scheme.lower()
    return scheme, hostname, port if port is not None else _DEFAULT_PORTS.get(scheme, 0)


def origin_is_trusted(origin: str | None, request: Request, settings: object) -> bool:
    """Return ``True`` iff the request's ``Origin`` may reach this API.

    A missing ``Origin`` is allowed: the CLI, the launcher's readiness
    probe, and any non-browser caller legitimately omit it, and those
    callers are still held to the control-token requirement on every
    state-changing request. ``Origin: null`` - what a sandboxed frame or
    a ``file://`` document sends - is *not* treated as missing; it is a
    foreign origin and is refused.

    A present ``Origin`` must be same-origin with the authority the
    request was addressed to, or an origin the operator listed in
    ``LOCKVERITY_CORS_ORIGINS``.
    """
    if origin is None:
        return True
    candidate = _normalise_origin(origin)
    if candidate is None:
        # Includes the literal ``null``, which has no scheme.
        return False
    configured = getattr(settings, "cors_origins", None) or []
    for allowed in configured:
        allowed_parts = _normalise_origin(str(allowed))
        if allowed_parts is not None and allowed_parts == candidate:
            return True
    host_header = request.headers.get("host", "")
    self_origin = _normalise_origin(f"{request.url.scheme}://{host_header}")
    return self_origin is not None and self_origin == candidate


def token_verdict(request: Request, expected: str) -> str | None:
    """Return a rejection reason, or ``None`` when the token is valid.

    The token is read from :data:`CONTROL_HEADER` and from nowhere
    else. Query parameters, cookies, and path segments are never
    consulted, so the secret cannot leak through a URL, a log line, a
    referrer, or a bookmark.
    """
    supplied = request.headers.get(CONTROL_HEADER)
    if not supplied:
        return REASON_TOKEN_MISSING
    if not expected:
        return REASON_TOKEN_INVALID
    if not secrets.compare_digest(supplied, expected):
        return REASON_TOKEN_INVALID
    return None


def _reject(request: Request, reason: str) -> JSONResponse:
    """Build the standard 403 envelope for a refused request.

    The body carries the diagnostic ``reason`` and nothing about the
    expected value. The refusal happens in middleware, before routing,
    so no handler runs and no partial side effect is possible.
    """
    body: dict[str, object] = {
        "code": ApiErrorCode.FORBIDDEN.value,
        "message": _REJECTION_MESSAGES[reason],
        "details": {"reason": reason},
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=403, content={"error": body})


def install_control_plane(
    app: FastAPI, *, settings: object
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return the ASGI middleware that enforces the control policy.

    The policy is applied once, centrally, to every request under the
    API prefix. A per-route dependency would have to be remembered on
    every new handler; middleware cannot be forgotten.
    """
    api_prefix = str(getattr(settings, "api_prefix", "/api/v1")).rstrip("/")

    async def _control_plane_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not (path == api_prefix or path.startswith(api_prefix + "/")):
            return await call_next(request)
        if request.method == "OPTIONS":
            # A CORS preflight carries no custom header by definition.
            # It is answered by the CORS middleware, which grants only
            # operator-configured origins; the real request that follows
            # is gated here.
            return await call_next(request)
        if not host_is_trusted(request.headers.get("host", ""), settings):
            return _reject(request, REASON_HOST)
        if not origin_is_trusted(request.headers.get("origin"), request, settings):
            return _reject(request, REASON_ORIGIN)
        if request.method in MUTATING_METHODS:
            expected = getattr(request.app.state, "control_token", "")
            reason = token_verdict(request, expected)
            if reason is not None:
                return _reject(request, reason)
        return await call_next(request)

    return _control_plane_middleware


__all__ = [
    "CONTROL_HEADER",
    "CONTROL_META_NAME",
    "CONTROL_TOKEN_BYTES",
    "MUTATING_METHODS",
    "REASON_HOST",
    "REASON_ORIGIN",
    "REASON_TOKEN_INVALID",
    "REASON_TOKEN_MISSING",
    "STATE_CHANGING_ROUTES",
    "host_is_trusted",
    "install_control_plane",
    "mint_control_token",
    "origin_is_trusted",
    "token_verdict",
]
