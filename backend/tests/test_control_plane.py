"""Tests for the local control-plane boundary (LV-007).

Loopback is a network boundary, not a caller boundary. Before this
policy existed, any web page the user had open could POST to
``http://127.0.0.1:<port>/api/v1/scans/1/run`` and Lockverity would run
it, because the only thing standing between a website and the local
control plane was the absence of a route to it.

These tests pin the three checks that now stand in the way - ``Host``,
``Origin``, and a per-runtime control token on every mutation - plus the
things that must keep working: the launcher's readiness probe, ordinary
reads, and the frontend's own authenticated calls.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from app.core.config import get_settings
from app.core.control_plane import (
    CONTROL_HEADER,
    CONTROL_META_NAME,
    MUTATING_METHODS,
    STATE_CHANGING_ROUTES,
    host_is_trusted,
    mint_control_token,
    origin_is_trusted,
)
from app.main import create_app
from starlette.testclient import TestClient

from tests.api_client import LOOPBACK_BASE_URL, api_client

# A route that mutates but needs no database fixture, so the policy can
# be exercised without building a scan first. It deletes stale
# workspaces; on an empty runtime home that is a no-op that still proves
# the request reached the handler.
MUTATION_PATH = "/api/v1/system/workspaces/cleanup"


@pytest.fixture
def app(app_config: Any) -> Any:
    """A fresh application, and therefore a fresh control token."""
    return create_app()


@pytest.fixture
def token(app: Any) -> str:
    return str(app.state.control_token)


@pytest.fixture
def raw_client(app: Any) -> Iterator[TestClient]:
    """A client that sends a loopback Host but *no* control token."""
    with TestClient(app, base_url=LOOPBACK_BASE_URL) as client:
        yield client


# ---------------------------------------------------------------------------
# The token itself
# ---------------------------------------------------------------------------


def test_each_process_mints_its_own_token(app_config: Any) -> None:
    first = create_app().state.control_token
    second = create_app().state.control_token
    assert first and second
    assert first != second
    # 32 bytes of entropy, URL-safe encoded.
    assert len(first) >= 40


def test_token_override_is_ignored_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev pin must never weaken the shipped runtime."""

    class _Settings:
        environment = "production"
        control_token = "pinned-development-value"

    minted = mint_control_token(_Settings())
    assert minted != "pinned-development-value"


def test_token_override_applies_outside_production() -> None:
    class _Settings:
        environment = "development"
        control_token = "pinned-development-value"

    assert mint_control_token(_Settings()) == "pinned-development-value"


# ---------------------------------------------------------------------------
# Mutations require the token
# ---------------------------------------------------------------------------


def test_trusted_frontend_mutation_succeeds(app: Any, token: str) -> None:
    with api_client(app) as client:
        response = client.post(MUTATION_PATH, headers={"Origin": LOOPBACK_BASE_URL})
    assert response.status_code == 200


def test_mutation_without_a_token_is_refused(raw_client: TestClient) -> None:
    response = raw_client.post(MUTATION_PATH)
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "forbidden"
    assert body["details"]["reason"] == "control_token_missing"


def test_mutation_with_a_wrong_token_is_refused(raw_client: TestClient) -> None:
    response = raw_client.post(MUTATION_PATH, headers={CONTROL_HEADER: "not-the-token"})
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "control_token_invalid"


def test_refusal_never_echoes_the_expected_token(raw_client: TestClient, token: str) -> None:
    response = raw_client.post(MUTATION_PATH, headers={CONTROL_HEADER: "not-the-token"})
    assert token not in response.text
    # Not even a prefix that would let a caller bisect the value.
    assert token[:8] not in response.text


def test_token_is_not_accepted_through_a_query_parameter(
    raw_client: TestClient, token: str
) -> None:
    """The header is the only channel.

    Accepting the secret in a URL would leak it into browser history,
    referrer headers, and any log that records request lines.
    """
    for key in ("control_token", "token", "X-Lockverity-Control"):
        response = raw_client.post(MUTATION_PATH, params={key: token})
        assert response.status_code == 403, key
        assert response.json()["error"]["details"]["reason"] == "control_token_missing"


def test_token_is_not_accepted_through_a_cookie(raw_client: TestClient, token: str) -> None:
    raw_client.cookies.set("X-Lockverity-Control", token)
    response = raw_client.post(MUTATION_PATH)
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "control_token_missing"


# ---------------------------------------------------------------------------
# No side effect may precede authentication
# ---------------------------------------------------------------------------


def test_rejected_mutation_never_reaches_the_handler(
    app: Any, raw_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused request must not run any part of the operation.

    The policy is middleware, so routing has not happened yet when the
    refusal is issued. This pins that: the workspace cleanup service is
    replaced with a tripwire that fails the test if it is ever entered.
    """
    from app.services import workspace_service

    calls: list[object] = []

    def _tripwire(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("the mutation handler ran before authentication")

    monkeypatch.setattr(workspace_service.WorkspaceService, "cleanup_stale", _tripwire)

    assert raw_client.post(MUTATION_PATH).status_code == 403
    assert raw_client.post(MUTATION_PATH, headers={CONTROL_HEADER: "wrong"}).status_code == 403
    assert (
        raw_client.post(
            MUTATION_PATH,
            headers={CONTROL_HEADER: "wrong", "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert calls == []


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example",
        "https://127.0.0.1.evil.example",
        "http://localhost:5173",  # a different local app, not configured
        "null",  # a sandboxed frame or a file:// document
    ],
)
def test_foreign_origin_mutation_is_refused(app: Any, token: str, origin: str) -> None:
    with api_client(app) as client:
        response = client.post(MUTATION_PATH, headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "control_origin_rejected"


def test_foreign_origin_is_refused_even_on_reads(app: Any, token: str) -> None:
    with api_client(app) as client:
        response = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_absent_origin_is_allowed_for_native_callers(raw_client: TestClient) -> None:
    """The CLI and the launcher's probe send no Origin at all."""
    assert raw_client.get("/api/v1/health").status_code == 200


def test_same_origin_is_accepted(app: Any) -> None:
    with api_client(app) as client:
        response = client.post(MUTATION_PATH, headers={"Origin": "http://127.0.0.1"})
    assert response.status_code == 200


def test_configured_origin_is_accepted(app_config: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's explicitly configured origin is trusted for Origin.

    It is trusted for *Origin* only. The control token is still
    required, so a configured CORS origin is not a CSRF bypass - which
    is the whole reason the token exists alongside this check.
    """
    # JSON form: pydantic-settings parses a ``list[str]`` field from the
    # environment as JSON before any validator runs.
    monkeypatch.setenv("LOCKVERITY_CORS_ORIGINS", '["http://localhost:5173"]')
    get_settings.cache_clear()
    try:
        configured = create_app()
        with api_client(configured) as client:
            allowed = client.post(MUTATION_PATH, headers={"Origin": "http://localhost:5173"})
        with TestClient(configured, base_url=LOOPBACK_BASE_URL) as bare:
            untokened = bare.post(MUTATION_PATH, headers={"Origin": "http://localhost:5173"})
    finally:
        get_settings.cache_clear()
    assert allowed.status_code == 200
    assert untokened.status_code == 403
    assert untokened.json()["error"]["details"]["reason"] == "control_token_missing"


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


def test_foreign_host_mutation_is_refused(app: Any, token: str) -> None:
    """DNS rebinding: the name resolves to loopback, the Host does not."""
    with TestClient(
        app, base_url="http://rebind.evil.example", headers={CONTROL_HEADER: token}
    ) as client:
        response = client.post(MUTATION_PATH)
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "control_host_rejected"


def test_foreign_host_is_refused_on_reads_too(app: Any) -> None:
    with TestClient(app, base_url="http://rebind.evil.example") as client:
        assert client.get("/api/v1/health").status_code == 403


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.1:8000", "localhost:8000", "[::1]:8000", "127.9.9.9:1"],
)
def test_loopback_authorities_are_trusted(host: str) -> None:
    class _Settings:
        cli_host = None
        cli_port = None

    assert host_is_trusted(host, _Settings())


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example",
        "evil.example:8000",
        "127.0.0.1.evil.example",
        "1270.0.0.1",
        "127.0.0.1:notaport",
    ],
)
def test_non_loopback_authorities_are_refused(host: str) -> None:
    class _Settings:
        cli_host = None
        cli_port = None

    assert not host_is_trusted(host, _Settings())


def test_host_port_is_pinned_when_the_runtime_knows_it() -> None:
    """The dynamic GUI port is pinned as tightly as a fixed one."""

    class _Settings:
        cli_host = "127.0.0.1"
        cli_port = 54321

    assert host_is_trusted("127.0.0.1:54321", _Settings())
    assert not host_is_trusted("127.0.0.1:54322", _Settings())
    assert not host_is_trusted("127.0.0.1", _Settings())


def test_explicitly_bound_remote_host_is_trusted() -> None:
    """``--allow-remote`` binds one exact name; only that name passes."""

    class _Settings:
        cli_host = "192.168.1.10"
        cli_port = None

    assert host_is_trusted("192.168.1.10:8000", _Settings())
    assert not host_is_trusted("192.168.1.11:8000", _Settings())


# ---------------------------------------------------------------------------
# Reads, readiness, and the rest of the surface
# ---------------------------------------------------------------------------


def test_health_stays_unauthenticated(raw_client: TestClient) -> None:
    """The launcher waits on this before the window opens.

    It must stay reachable without a token, and it must stay harmless:
    no mutation capability and no sensitive value in the body.
    """
    response = raw_client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert set(payload) <= {
        "status",
        "database",
        "version",
        "environment",
        "time",
        "timestamp",
        "uptime_seconds",
    }


def test_health_body_never_carries_the_token(raw_client: TestClient, token: str) -> None:
    assert token not in raw_client.get("/api/v1/health").text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/health",
        "/api/v1/system/info",
        "/api/v1/provider-health",
        "/api/v1/repositories",
        "/api/v1/scans",
        "/api/v1/diagnostics/summary",
    ],
)
def test_reads_do_not_require_a_token(raw_client: TestClient, path: str) -> None:
    assert raw_client.get(path).status_code == 200


def test_read_responses_never_carry_the_token(raw_client: TestClient, token: str) -> None:
    for path in ("/api/v1/system/info", "/api/v1/diagnostics/summary"):
        response = raw_client.get(path)
        assert token not in response.text
        assert token not in str(dict(response.headers))


# ---------------------------------------------------------------------------
# Coverage: every mutating route is under the policy
# ---------------------------------------------------------------------------
# FastAPI >= 0.139 returns lazy ``_IncludedRouter`` wrappers from
# ``app.routes`` for routers added via ``include_router``; the wrappers
# expose no ``.methods``/``.path``, so the original ``route.methods``
# walk silently discovered zero mutating routes and both inventory
# tests went green-vacuous. Discovery is now dual-source so it cannot
# go quietly blind again:


def _schema_mutations(app: Any) -> dict[str, set[str]]:
    """Return ``{full_path: mutating_methods}`` from ``app.openapi()``.

    The OpenAPI path table is the public, version-stable view of every
    operation the application serves, so it is the primary discovery
    source.
    """
    paths = app.openapi().get("paths") or {}
    assert paths, "app.openapi() returned no paths; route discovery is blind."
    mutations: dict[str, set[str]] = {}
    for path, operations in paths.items():
        methods = {op.upper() for op in operations if op.upper() in MUTATING_METHODS}
        if methods:
            mutations[path] = methods
    assert mutations, "app.openapi() lists no mutating operations; route discovery is blind."
    return mutations


def _routing_table_mutations(app: Any) -> dict[str, set[str]]:
    """Return ``{full_path: mutating_methods}`` from the routing table.

    Walks ``app.routes`` directly: plain routes carry ``.methods`` and
    ``.path`` themselves, and the lazy router wrappers of newer FastAPI
    releases expose their resolved per-route contexts through
    ``effective_route_contexts`` (absent on older versions, whose plain
    routes are already complete). This is a private FastAPI detail, so
    it is only ever a cross-check - never the primary source.
    """
    mutations: dict[str, set[str]] = {}
    for entry in app.routes:
        # A wrapper resolves to its contexts; a plain route stands for
        # itself. Both shapes carry ``.methods`` and ``.path``.
        holders: Any = (
            entry.effective_route_contexts()
            if callable(getattr(entry, "effective_route_contexts", None))
            else (entry,)
        )
        for holder in holders:
            methods = getattr(holder, "methods", None)
            path = getattr(holder, "path", None)
            if not methods or not path:
                continue
            mutating = set(methods) & MUTATING_METHODS
            if mutating:
                mutations.setdefault(path, set()).update(mutating)
    assert mutations, (
        "structural routing-table walk found no mutating routes; route discovery is blind."
    )
    return mutations


def _mutating_routes(app: Any) -> dict[str, set[str]]:
    """Discover the application's real mutating routes, dual-source.

    The two sources must agree exactly: a mutation the routing table
    has but the schema lacks would be a route hidden from review (for
    example via ``include_in_schema=False``), and a mutation only the
    schema has would mean the walk no longer sees the real table.
    Either mismatch - or an empty source - fails loudly rather than
    letting the coverage gate pass vacuously.
    """
    schema = _schema_mutations(app)
    routing = _routing_table_mutations(app)
    schema_only = sorted(set(schema) - set(routing))
    assert not schema_only, f"schema lists mutations the routing table does not: {schema_only}"
    hidden = sorted(set(routing) - set(schema))
    assert not hidden, (
        "mutating routes missing from the OpenAPI schema (include_in_schema"
        f"=False?) would escape review: {hidden}"
    )
    return schema


def test_route_inventory_matches_the_application(app: Any) -> None:
    """A new mutating route cannot slip in unreviewed.

    The policy gates on HTTP method, which is only safe while every
    mutating-method route in the application is genuinely
    state-changing. This asserts the reviewed inventory in
    :data:`STATE_CHANGING_ROUTES` still describes the real routing
    table; adding a POST without updating (and re-reading) it fails
    here.
    """
    prefix = get_settings().api_prefix
    discovered_all = _mutating_routes(app)
    # A mutation outside the API prefix would fall outside the reviewed
    # inventory's vocabulary, so it must fail here rather than be
    # filtered away silently.
    off_prefix = sorted(path for path in discovered_all if not path.startswith(prefix))
    assert not off_prefix, f"mutating routes outside {prefix}: {off_prefix}"
    discovered = {path[len(prefix) :] for path in discovered_all}
    assert discovered == set(STATE_CHANGING_ROUTES)
    assert STATE_CHANGING_ROUTES, "the reviewed inventory must not be empty."


def test_every_mutating_route_refuses_an_untokened_request(
    app: Any, raw_client: TestClient
) -> None:
    """Not just the audit's examples - every one of them.

    A 403 with a control reason proves the policy fired. Anything else
    (404, 422, 200) would mean the request reached routing.
    """
    mutations = _mutating_routes(app)
    checked: set[str] = set()
    for path, methods in sorted(mutations.items()):
        # Substitute any path parameter with a plausible id; the request
        # must be refused before the id is ever looked up.
        concrete = path
        for name in ("repository_id", "scan_id"):
            concrete = concrete.replace("{" + name + "}", "1")
        assert "{" not in concrete, f"unhandled path parameter in {path}"
        for method in sorted(methods & MUTATING_METHODS):
            response = raw_client.request(method, concrete)
            assert response.status_code == 403, f"{method} {concrete}"
            assert response.json()["error"]["details"]["reason"].startswith("control_")
        checked.add(path)
    assert checked == set(mutations), "some discovered mutating route was not exercised"


# ---------------------------------------------------------------------------
# Token delivery through the served document
# ---------------------------------------------------------------------------


def test_served_document_carries_the_token(tmp_path: Any) -> None:
    """The frontend learns the token from the document it booted from.

    A page on any other origin cannot read this document, so the
    delivery channel is not readable cross-origin.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Lockverity</title></head>"
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    os.environ["LOCKVERITY_ENVIRONMENT"] = "production"
    os.environ["LOCKVERITY_SERVE_FRONTEND"] = "true"
    os.environ["LOCKVERITY_FRONTEND_DIST"] = str(dist)
    get_settings.cache_clear()
    try:
        served = create_app()
        with TestClient(served, base_url=LOOPBACK_BASE_URL) as client:
            response = client.get("/")
            asset = client.get("/assets")
    finally:
        get_settings.cache_clear()
        for key in (
            "LOCKVERITY_ENVIRONMENT",
            "LOCKVERITY_SERVE_FRONTEND",
            "LOCKVERITY_FRONTEND_DIST",
        ):
            os.environ.pop(key, None)

    token = served.state.control_token
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f'<meta name="{CONTROL_META_NAME}" content="{token}">' in response.text
    # The document is still the built shell, and still uncacheable so the
    # token is never written to the browser's disk cache.
    assert '<div id="root"' in response.text
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    # The token rides on the document only; a static asset never sees it.
    assert token not in asset.text


def test_injection_leaves_the_build_output_untouched(tmp_path: Any) -> None:
    """Nothing on disk ever holds the secret."""
    from app.static_frontend import inject_control_token

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    index = dist / "index.html"
    original = "<!doctype html><html><head></head><body></body></html>"
    index.write_text(original, encoding="utf-8")

    os.environ["LOCKVERITY_ENVIRONMENT"] = "production"
    os.environ["LOCKVERITY_SERVE_FRONTEND"] = "true"
    os.environ["LOCKVERITY_FRONTEND_DIST"] = str(dist)
    get_settings.cache_clear()
    try:
        served = create_app()
        with TestClient(served, base_url=LOOPBACK_BASE_URL) as client:
            client.get("/")
    finally:
        get_settings.cache_clear()
        for key in (
            "LOCKVERITY_ENVIRONMENT",
            "LOCKVERITY_SERVE_FRONTEND",
            "LOCKVERITY_FRONTEND_DIST",
        ):
            os.environ.pop(key, None)

    assert index.read_text(encoding="utf-8") == original
    # The helper is a pure transform on the response body.
    assert inject_control_token(original, "abc") != original
    assert 'content="abc"' in inject_control_token(original, "abc")


def test_injection_escapes_the_attribute() -> None:
    from app.static_frontend import inject_control_token

    out = inject_control_token("<html><head></head></html>", 'a"><script>x</script>')
    assert "<script>" not in out.split("</head>")[0].replace("&lt;script&gt;", "")
    assert "&quot;" in out


def test_injection_without_a_head_still_delivers() -> None:
    from app.static_frontend import inject_control_token

    out = inject_control_token("<div id='root'></div>", "tkn")
    assert out.startswith(f'<meta name="{CONTROL_META_NAME}" content="tkn">')


# ---------------------------------------------------------------------------
# Helper-level checks
# ---------------------------------------------------------------------------


def test_origin_helper_accepts_absent_and_refuses_null() -> None:
    class _Request:
        headers: ClassVar[dict[str, str]] = {"host": "127.0.0.1:8000"}

        class url:  # noqa: N801 - stand-in for the Starlette URL object
            scheme = "http"

    class _Settings:
        cors_origins: ClassVar[list[str]] = []

    request = _Request()
    settings = _Settings()
    assert origin_is_trusted(None, request, settings)  # type: ignore[arg-type]
    assert not origin_is_trusted("null", request, settings)  # type: ignore[arg-type]
    assert origin_is_trusted("http://127.0.0.1:8000", request, settings)  # type: ignore[arg-type]
    assert not origin_is_trusted("http://127.0.0.1:8001", request, settings)  # type: ignore[arg-type]
    assert not origin_is_trusted("https://127.0.0.1:8000", request, settings)  # type: ignore[arg-type]
