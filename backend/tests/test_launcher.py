"""Focused lifecycle and navigation tests for the native Windows launcher."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from app import launcher
from app.cli.state import make_state


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    for name in ("data", "logs", "run", "config"):
        (home / name).mkdir(parents=True)
    monkeypatch.setenv("LOCKVERITY_HOME", str(home))
    return home


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return dist


class _FakeInstance:
    def __init__(self, first: bool = True) -> None:
        self.first = first
        self.closed = 0

    def acquire(self) -> bool:
        return self.first

    def close(self) -> None:
        self.closed += 1


def _patch_main_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
    *,
    home: Path,
    dist: Path,
    instance: _FakeInstance | None = None,
) -> _FakeInstance:
    guard = instance or _FakeInstance()
    monkeypatch.delenv("LOCKVERITY_DATABASE_URL", raising=False)
    monkeypatch.setattr(launcher, "WindowsSingleInstance", lambda: guard)
    monkeypatch.setattr(launcher, "_set_app_user_model_id", lambda: None)
    monkeypatch.setattr(launcher, "_webview2_runtime_version", lambda: "150.0.0.0")
    monkeypatch.setattr(launcher, "_resolve_runtime_home", lambda: home)
    monkeypatch.setattr(
        launcher,
        "_settings",
        lambda: SimpleNamespace(frontend_dist=str(dist), database_url="sqlite:///:memory:"),
    )
    return guard


def test_resolve_runtime_home_prefers_env(fake_home: Path) -> None:
    assert launcher._resolve_runtime_home() == fake_home


def test_normal_gui_waits_for_readiness_never_opens_default_browser_and_shuts_down(
    fake_home: Path,
    fake_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_main_prerequisites(monkeypatch, home=fake_home, dist=fake_dist)
    calls: list[str] = []
    browser_urls: list[str] = []
    captured: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, **kwargs: Any) -> None:
            # The launcher reserves a loopback port via
            # ``reserve_loopback_port`` and forwards
            # the live socket into the supervisor. The
            # ``--port`` CLI value is documentation
            # only; the actual port is the kernel-assigned
            # value. The captured port and the port the
            # prebound socket was bound to are both
            # recorded so the assertion block can prove
            # the two values agree exactly.
            assert kwargs["host"] == "127.0.0.1"
            assert isinstance(kwargs.get("port"), int)
            assert 1 <= int(kwargs["port"]) <= 65535
            assert kwargs["database_url"] is None
            assert kwargs.get("prebound_socket") is not None
            prebound_socket = kwargs.get("prebound_socket")
            captured["port"] = int(kwargs["port"])
            captured["prebound_socket_port"] = int(prebound_socket.getsockname()[1])
            self.error = None
            self.home = fake_home
            self.host = "127.0.0.1"
            self.url = f"http://127.0.0.1:{kwargs['port']}/"
            self.port = int(kwargs["port"])

        def start(self) -> None:
            calls.append("backend-start")

        def wait_until_ready(self) -> bool:
            calls.append("backend-ready")
            return True

        def shutdown(self) -> bool:
            calls.append("backend-shutdown")
            return True

    monkeypatch.setattr(launcher, "DesktopBackendSupervisor", FakeSupervisor)
    monkeypatch.setattr(
        launcher,
        "_run_webview",
        lambda supervisor: calls.append(f"window:{supervisor.url}"),
    )
    monkeypatch.setattr(
        launcher.webbrowser,
        "open",
        lambda url, **_kwargs: browser_urls.append(url) or True,
    )

    # The legacy ``--port`` flag is accepted for CLI
    # parity; the GUI ignores the value and asks the
    # OS for a free loopback port. Passing ``8123``
    # must NOT result in the GUI binding 8123.
    assert launcher.main(["--port", "8123"]) == launcher.LAUNCHER_EXIT_OK
    assert captured["port"] != 8123, (
        "GUI must dynamically allocate a port rather than bind the legacy 8123 value"
    )
    # The actual port the supervisor is told to use
    # must be the port the prebound socket was bound
    # to; the two values must agree exactly so the
    # state file, the WebView URL, and the health
    # check all point at the same loopback port.
    assert captured["port"] == captured["prebound_socket_port"]
    assert calls == [
        "backend-start",
        "backend-ready",
        f"window:http://127.0.0.1:{captured['port']}/",
        "backend-shutdown",
    ]
    assert browser_urls == []


def test_gui_forwards_explicit_database_override_only(
    fake_home: Path,
    fake_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_main_prerequisites(monkeypatch, home=fake_home, dist=fake_dist)
    explicit = "sqlite:///C:/operator/lockverity.sqlite"
    monkeypatch.setenv("LOCKVERITY_DATABASE_URL", explicit)
    received: list[str | None] = []

    class FakeSupervisor:
        def __init__(self, **kwargs: Any) -> None:
            received.append(kwargs["database_url"])
            self.error = None
            self.home = fake_home
            self.host = "127.0.0.1"
            self.url = f"http://127.0.0.1:{kwargs['port']}/"
            self.port = int(kwargs["port"])

        def start(self) -> None:
            pass

        def wait_until_ready(self) -> bool:
            return True

        def shutdown(self) -> bool:
            return True

    monkeypatch.setattr(launcher, "DesktopBackendSupervisor", FakeSupervisor)
    monkeypatch.setattr(launcher, "_run_webview", lambda _supervisor: None)

    assert launcher.main([]) == launcher.LAUNCHER_EXIT_OK
    assert received == [explicit]


def test_duplicate_launch_focuses_existing_window_without_starting_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = _FakeInstance(first=False)
    focused: list[bool] = []
    monkeypatch.setattr(launcher, "WindowsSingleInstance", lambda: guard)
    monkeypatch.setattr(launcher, "_focus_existing_window", lambda: focused.append(True) or True)
    monkeypatch.setattr(
        launcher,
        "DesktopBackendSupervisor",
        lambda **_kwargs: pytest.fail("duplicate launch created a backend"),
    )

    assert launcher.main([]) == launcher.LAUNCHER_EXIT_OK
    assert focused == [True]
    assert guard.closed == 1


def test_frozen_internal_serve_dispatch_reads_process_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli import _serve

    received: list[list[str]] = []
    monkeypatch.setattr(
        _serve,
        "main",
        lambda argv: received.append(list(argv)) or 7,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["Lockverity.exe", "--internal-serve", "--host", "127.0.0.1"],
    )

    assert launcher.main() == 7
    assert received == [["--host", "127.0.0.1"]]


def test_startup_failure_cleans_up_and_never_creates_window(
    fake_home: Path,
    fake_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_main_prerequisites(monkeypatch, home=fake_home, dist=fake_dist)
    cleanup: list[bool] = []
    messages: list[str] = []

    class FailedSupervisor:
        def __init__(self, **_kwargs: Any) -> None:
            self.error = launcher.BackendStartupError("health timeout")
            self.home = fake_home

        def start(self) -> None:
            pass

        def wait_until_ready(self) -> bool:
            return False

        def shutdown(self) -> bool:
            cleanup.append(True)
            return True

    monkeypatch.setattr(launcher, "DesktopBackendSupervisor", FailedSupervisor)
    monkeypatch.setattr(launcher, "_run_webview", lambda _supervisor: pytest.fail("window opened"))
    monkeypatch.setattr(
        launcher,
        "_show_message_box",
        lambda _title, message: messages.append(message) or 0,
    )

    assert launcher.main(["--timeout", "0.1"]) == launcher.LAUNCHER_EXIT_HEALTH
    assert cleanup
    assert messages and "did not become ready" in messages[0]


def test_webview2_missing_aborts_before_backend_start(
    fake_home: Path,
    fake_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_main_prerequisites(monkeypatch, home=fake_home, dist=fake_dist)
    monkeypatch.setattr(launcher, "_webview2_runtime_version", lambda: None)
    messages: list[str] = []
    monkeypatch.setattr(
        launcher,
        "_show_message_box",
        lambda _title, message: messages.append(message) or 0,
    )
    monkeypatch.setattr(
        launcher,
        "DesktopBackendSupervisor",
        lambda **_kwargs: pytest.fail("backend started without WebView2"),
    )

    assert launcher.main([]) == launcher.LAUNCHER_EXIT_WEBVIEW2_MISSING
    assert "WebView2 Runtime" in messages[0]


def test_owned_supervisor_uses_foreground_loopback_runtime_and_graceful_stop(
    fake_home: Path,
    fake_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_exited = threading.Event()
    start_args: dict[str, Any] = {}

    def fake_start(**kwargs: Any) -> None:
        start_args.update(kwargs)
        kwargs["on_ready"](
            make_state(
                pid=os.getpid(),
                created_at="2026-08-15T00:00:00Z",
                host=kwargs["host"],
                port=kwargs["port"],
                version="2.1.2",
                home=kwargs["home"],
                frontend_dist=kwargs["frontend_dist"],
                log_file=kwargs["home"] / "logs" / "lockverity.log",
                started_at="2026-08-15T00:00:00Z",
                module="app.cli._serve",
                instance_id="00000000-0000-4000-8000-000000000001",
            )
        )
        child_exited.wait(timeout=2)

    def fake_stop(**_kwargs: Any) -> SimpleNamespace:
        child_exited.set()
        return SimpleNamespace(outcome="stopped", details="graceful")

    monkeypatch.setattr(launcher.cli_runner, "start", fake_start)
    monkeypatch.setattr(launcher.cli_runner, "stop", fake_stop)
    supervisor = launcher.DesktopBackendSupervisor(
        home=fake_home,
        host="127.0.0.1",
        port=8124,
        frontend_dist=fake_dist,
        database_url="sqlite:///:memory:",
        timeout=1.0,
    )
    supervisor.start()
    assert supervisor.wait_until_ready()
    assert supervisor.shutdown()
    assert start_args["host"] == "127.0.0.1"
    assert start_args["foreground"] is True
    assert start_args["open_browser"] is False


@pytest.mark.parametrize(
    ("url", "action"),
    [
        ("http://127.0.0.1:8000/", "internal"),
        ("http://127.0.0.1:8000/scans/1?tab=evidence", "internal"),
        ("https://github.com/namanparikh11/lockverity", "external"),
        ("https://osv.dev/vulnerability/GHSA-test", "external"),
        ("https://github.com.attacker.example/phish", "blocked"),
        ("http://github.com/namanparikh11/lockverity", "blocked"),
        ("file:///C:/Windows/System32/config/SAM", "blocked"),
        ("javascript:alert(1)", "blocked"),
        ("http://127.0.0.1:8001/", "blocked"),
    ],
)
def test_navigation_policy(url: str, action: str) -> None:
    assert launcher.classify_navigation(url, "http://127.0.0.1:8000/").action == action


class _EventHook:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> _EventHook:
        self.handlers.append(handler)
        return self


def test_native_navigation_guard_opens_approved_external_and_blocks_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook = _EventHook()
    browser = SimpleNamespace(
        webview=SimpleNamespace(NavigationStarting=hook),
        on_new_window_request=lambda *_args: None,
    )
    window = SimpleNamespace(native=SimpleNamespace(browser=browser))
    opened: list[str] = []
    monkeypatch.setattr(
        launcher,
        "_open_external_url",
        lambda url: opened.append(url) or True,
    )
    launcher._install_navigation_guard(window, "http://127.0.0.1:8000/")
    navigation_handler = hook.handlers[0]

    internal = SimpleNamespace(Uri="http://127.0.0.1:8000/about", Cancel=False)
    navigation_handler(None, internal)
    assert internal.Cancel is False

    external = SimpleNamespace(Uri="https://github.com/namanparikh11/lockverity", Cancel=False)
    navigation_handler(None, external)
    assert external.Cancel is True
    assert opened == [external.Uri]

    unexpected = SimpleNamespace(Uri="https://evil.example/", Cancel=False)
    navigation_handler(None, unexpected)
    assert unexpected.Cancel is True
    assert opened == [external.Uri]

    popup = SimpleNamespace(Uri="https://osv.dev/vulnerability/test", Handled=False)
    browser.on_new_window_request(None, popup)
    assert popup.Handled is True
    assert opened[-1] == popup.Uri


def test_production_webview_is_edgechromium_without_bridge_or_devtools(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEvents:
        before_show = _EventHook()
        closing = _EventHook()

    window = SimpleNamespace(events=FakeEvents())
    create_kwargs: dict[str, Any] = {}
    start_kwargs: dict[str, Any] = {}

    class FakeWebview:
        settings: ClassVar[dict[str, Any]] = {}

        @staticmethod
        def create_window(title: str, url: str, **kwargs: Any) -> Any:
            create_kwargs.update(title=title, url=url, **kwargs)
            return window

        @staticmethod
        def start(**kwargs: Any) -> None:
            start_kwargs.update(kwargs)

    supervisor = SimpleNamespace(
        url="http://127.0.0.1:8000/",
        host="127.0.0.1",
        port=8000,
        home=fake_home,
        request_shutdown=lambda: None,
    )
    monkeypatch.setattr(launcher, "_load_webview", lambda: FakeWebview)
    launcher._run_webview(supervisor)  # type: ignore[arg-type]

    assert create_kwargs["title"] == "Lockverity"
    assert create_kwargs["js_api"] is None
    assert create_kwargs["min_size"] == (1024, 640)
    assert start_kwargs["gui"] == "edgechromium"
    assert start_kwargs["debug"] is False
    assert FakeWebview.settings["REMOTE_DEBUGGING_PORT"] is None
    assert FakeWebview.settings["ALLOW_FILE_URLS"] is False
    assert FakeWebview.settings["ALLOW_DOWNLOADS"] is True
    assert FakeWebview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is False


def test_no_shell_true_or_normal_browser_launch_path() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "_open_browser" not in source
    assert "--no-browser" not in source


def test_closing_callback_uses_zero_arg_lambda() -> None:
    """The closing callback is a zero-arg lambda wrapping ``request_shutdown``.

    The original regression wrapped ``supervisor.request_shutdown``
    directly as the ``window.events.closing`` callback. pywebview
    invokes the closing callback with the ``Window`` as a positional
    argument, which raised ``TypeError`` from inside the message
    loop. The lambda wrapper is the documented contract; the test
    guards against a future maintainer reverting to the direct
    bound-method subscription.
    """
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "window.events.closing += lambda: supervisor.request_shutdown()" in source, (
        "Closing callback must be a zero-arg lambda wrapping "
        "supervisor.request_shutdown() so the message loop does not "
        "see a TypeError when pywebview dispatches the closing event "
        "with the Window as a positional argument."
    )


def test_gui_stop_event_handling_is_wired() -> None:
    """The GUI polls the global stop event and tears the WebView down.

    The ``lockverity-cli stop`` command signals the running GUI by
    setting the global ``STOP_EVENT_NAME`` event. The GUI must
    open that event and tear the WebView down so ``webview.start``
    returns and the GUI process exits cleanly. The test guards
    against a future refactor that drops the event polling and
    leaves the GUI alive after ``lockverity-cli stop``.
    """
    from app.cli.gui_stop import STOP_EVENT_NAME

    source = Path(launcher.__file__).read_text(encoding="utf-8")
    # The launcher either references STOP_EVENT_NAME directly or
    # imports it from the shared gui_stop module; both routes
    # exercise the documented event.
    assert STOP_EVENT_NAME in source or "from app.cli.gui_stop import" in source, (
        f"Launcher must reference or import the documented stop event {STOP_EVENT_NAME}."
    )
    assert "_poll_stop_event" in source, (
        "Launcher must include a background poll thread that waits on the stop event."
    )
    assert "WaitForSingleObject" in source, (
        "The poll thread must use the Windows WaitForSingleObject API."
    )


def test_monitor_backend_closes_window_on_done() -> None:
    """The monitor thread closes the WebView when the supervisor reports done.

    The historical monitor only closed the window on an unexpected
    error; the operator-initiated ``lockverity-cli stop`` path left
    the GUI alive holding the pre-bound socket. The fix unconditionally
    tears the WebView down when the supervisor's ``done`` event fires
    so the GUI exits cleanly whenever the backend exits.
    """
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    # The monitor function must unconditionally call ``window.destroy()``
    # after the supervisor's ``done`` event fires, regardless of
    # whether the shutdown was operator-initiated or unexpected.
    monitor_block = source[
        source.index("def _monitor_backend") : source.index("def _poll_stop_event")
    ]
    assert "window.destroy()" in monitor_block, (
        "_monitor_backend must call window.destroy() unconditionally so the "
        "GUI exits whenever the backend's done event fires."
    )
