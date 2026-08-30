"""Test client that speaks like the trusted local frontend.

Two things differ from a bare ``TestClient(app)``, and both mirror what a
real request from the Lockverity window looks like:

  * **The base URL is a loopback authority.** A bare ``TestClient`` sends
    ``Host: testserver``, which the control-plane policy refuses -
    correctly, because that is the DNS-rebinding shape. Real traffic
    always carries the instance's own loopback authority.

  * **The per-process control token is sent on every request.** In the
    shipped runtime the backend injects that token into the
    ``index.html`` it serves and the frontend replays it on every
    state-changing call. Sending it here means the whole API suite
    exercises the authenticated path rather than routing around it.

Tests that exist to prove the policy *rejects* something build their own
client (or pass explicit headers) instead of using this helper.
"""

from __future__ import annotations

from typing import Any

from app.core.control_plane import CONTROL_HEADER
from starlette.testclient import TestClient

# The authority a local caller actually uses. Any loopback literal works;
# this one matches the CLI's default bind host.
LOOPBACK_BASE_URL = "http://127.0.0.1"


def api_client(app: Any, **kwargs: Any) -> TestClient:
    """Return a :class:`TestClient` wired like the trusted frontend."""
    headers = dict(kwargs.pop("headers", None) or {})
    token = getattr(app.state, "control_token", None)
    if token:
        headers.setdefault(CONTROL_HEADER, token)
    kwargs.setdefault("base_url", LOOPBACK_BASE_URL)
    return TestClient(app, headers=headers, **kwargs)


__all__ = ["LOOPBACK_BASE_URL", "api_client"]
