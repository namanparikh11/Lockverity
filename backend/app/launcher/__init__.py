"""Native Windows desktop launcher for Lockverity.

``Lockverity.exe`` owns a foreground FastAPI runtime for exactly as long as
the desktop window is open. The existing React application is rendered by
pywebview's Edge Chromium backend (Microsoft Edge WebView2); the existing
HTTP API remains the only frontend/backend backend surface.

Windows and pywebview imports stay behind small adapters so unit tests can
exercise lifecycle and navigation decisions without creating a real window.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.cli import runner as cli_runner
from app.cli.gui_stop import STOP_EVENT_NAME, signal_gui_stop
from app.cli.home import ensure_home, resolve_home
from app.cli.logging_setup import configure_launcher_logging
from app.cli.port_reservation import (
    PortReservationError,
    reserve_loopback_port,
)
from app.cli.state import InstanceState
from app.runtime_paths import application_root, is_frozen

logger = logging.getLogger("lockverity.launcher")

LAUNCHER_EXIT_OK = 0
LAUNCHER_EXIT_ERROR = 20
LAUNCHER_EXIT_PORT_IN_USE = 21
LAUNCHER_EXIT_MIGRATION = 22
LAUNCHER_EXIT_HEALTH = 23
LAUNCHER_EXIT_MISSING_DIST = 24
LAUNCHER_EXIT_WEBVIEW2_MISSING = 25

WINDOW_TITLE = "Lockverity"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 1024
WINDOW_MIN_HEIGHT = 640
APP_USER_MODEL_ID = "Lockverity.Desktop"
INSTANCE_MUTEX_NAME = r"Global\{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}"

_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_SW_SHOW = 5

# External documents and evidence links which the product itself can render.
# Exact hosts are intentional: suffix matching would make
# ``github.com.attacker.example`` look trusted.
APPROVED_EXTERNAL_HOSTS = frozenset(
    {
        "api.deps.dev",
        "deps.dev",
        "docs.github.com",
        "github.com",
        "osv.dev",
        "policies.google.com",
        "securityscorecards.dev",
        "www.github.com",
        "www.linuxfoundation.org",
    }
)

_WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


class PortInUseError(RuntimeError):
    """Compatibility exit-category for a loopback bind conflict."""


class MigrationError(RuntimeError):
    """Compatibility exit-category for a migration failure."""


class MissingDistError(RuntimeError):
    """Raised when the bundled React distribution is unavailable."""


class BackendStartupError(RuntimeError):
    """Raised when the owned backend fails before readiness."""


class BackendUnexpectedExitError(RuntimeError):
    """Raised when the owned backend exits while the window is open."""


@dataclass(frozen=True, slots=True)
class NavigationDecision:
    """Decision returned by :func:`classify_navigation`."""

    action: str
    url: str


class WindowsSingleInstance:
    """Process-lifetime named mutex used by the graphical launcher.

    The same stable mutex appears in Inno Setup's ``AppMutex`` directive.
    The CLI start lock remains the second line of defence for database and
    runtime state.
    """

    def __init__(self, name: str = INSTANCE_MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        self._handle = int(handle)
        return int(kernel32.GetLastError()) != _ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None


class DesktopBackendSupervisor:
    """Own the existing foreground CLI runtime from a worker thread."""

    def __init__(
        self,
        *,
        home: Path,
        host: str,
        port: int,
        frontend_dist: Path,
        database_url: str | None,
        timeout: float,
        prebound_socket: socket.socket | None = None,
    ) -> None:
        self.home = home
        self.host = host
        # The port is the actual port the kernel assigned.
        # The supervisor records this value into the
        # runtime state, drives the ``self.url``
        # property, and is the value the ``WebView2``
        # window navigates to. The ``prebound_socket``
        # is the source of truth for the port; if it is
        # provided the ``port`` argument is the actual
        # port from ``getsockname()`` and must not be
        # replaced.
        self.port = port
        self.frontend_dist = frontend_dist
        self.database_url = database_url
        self.timeout = timeout
        self.prebound_socket = prebound_socket
        self.state: InstanceState | None = None
        self.error: BaseException | None = None
        self.ready = threading.Event()
        self.done = threading.Event()
        self.shutdown_requested = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="lockverity-desktop-backend",
            daemon=False,
        )

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> None:
        self._thread.start()

    def _on_ready(self, state: InstanceState) -> None:
        self.state = state
        self.port = state.port
        self.ready.set()
        logger.info(
            "WebView startup: backend ready url=http://%s:%d/ port=%d instance_id=%s",
            self.host,
            self.port,
            self.port,
            getattr(state, "instance_id", "<unknown>"),
        )

    def _run(self) -> None:
        try:
            cli_runner.start(
                home=self.home,
                host=self.host,
                port=self.port,
                frontend_dist=self.frontend_dist,
                foreground=True,
                timeout=self.timeout,
                database_url=self.database_url,
                log_level="info",
                open_browser=False,
                on_ready=self._on_ready,
                prebound_socket=self.prebound_socket,
            )
            if self.ready.is_set() and not self.shutdown_requested.is_set():
                self.error = BackendUnexpectedExitError("the local backend stopped unexpectedly")
        except BaseException as exc:  # worker must report every startup/child failure
            self.error = exc
        finally:
            self.done.set()

    def wait_until_ready(self) -> bool:
        deadline = time.monotonic() + self.timeout
        logger.info("WebView startup: wait_until_ready entered timeout=%.1fs", self.timeout)
        polls = 0
        while time.monotonic() < deadline:
            if self.ready.wait(timeout=0.05):
                logger.info(
                    "WebView startup: wait_until_ready observed ready after %d polls",
                    polls,
                )
                return True
            if self.done.is_set():
                logger.error(
                    "WebView startup: wait_until_ready observed done with error=%r",
                    self.error,
                )
                return False
            polls += 1
        self.error = BackendStartupError(
            f"backend did not report ready on {self.host}:{self.port} within {self.timeout:.1f}s"
        )
        logger.error("WebView startup: wait_until_ready timed out: %s", self.error)
        return False

    def request_shutdown(self) -> None:
        self.shutdown_requested.set()

    def shutdown(self) -> bool:
        """Stop and reap the backend, escalating only after the grace period."""
        self.request_shutdown()
        if self.ready.is_set() and not self.done.is_set():
            result = cli_runner.stop(home=self.home, timeout=15.0, force=False)
            if result.outcome == "error":
                logger.warning("graceful backend stop did not converge: %s", result.details)
                result = cli_runner.stop(home=self.home, timeout=2.0, force=True)
            stopped = result.outcome in {"stopped", "force_killed", "was_not_running"}
        else:
            stopped = True
        if self._thread.is_alive():
            self._thread.join(timeout=20.0)
        return stopped and not self._thread.is_alive()


def _resolve_runtime_home() -> Path:
    explicit = os.environ.get("LOCKVERITY_HOME")
    return resolve_home(cli_override=explicit) if explicit else resolve_home()


def _settings() -> object:
    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


def _frontend_dist(settings: Any) -> Path:
    configured = Path(str(settings.frontend_dist)).expanduser()
    if not configured.is_absolute():
        configured = (application_root() / configured).resolve()
    return configured


def _show_message_box(title: str, message: str) -> int:
    if sys.platform != "win32":
        sys.stderr.write(f"[{title}] {message}\n")
        return 0
    import ctypes

    return int(ctypes.windll.user32.MessageBoxW(None, message, title, 0x00040010))


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    if int(result) != 0:
        logger.warning("SetCurrentProcessExplicitAppUserModelID failed: HRESULT=%s", result)


def _focus_existing_window(timeout: float = 5.0) -> bool:
    """Restore and focus the existing titled window where Windows permits it."""
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() <= deadline:
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, _SW_RESTORE if user32.IsIconic(hwnd) else _SW_SHOW)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.1)
    return False


def _webview2_runtime_version() -> str | None:
    """Return the installed Evergreen WebView2 version using Microsoft's keys."""
    if sys.platform != "win32":
        return "non-windows-test-host"
    import winreg

    locations = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}",
        ),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value = str(winreg.QueryValueEx(key, "pv")[0]).strip()
        except OSError:
            continue
        if value and value != "0.0.0.0":  # noqa: S104 - WebView2 version sentinel
            return value
    return None


def _same_origin(candidate: urllib.parse.SplitResult, app: urllib.parse.SplitResult) -> bool:
    try:
        candidate_port = candidate.port
        app_port = app.port
    except ValueError:
        return False
    return (
        candidate.scheme.lower() == app.scheme.lower()
        and (candidate.hostname or "").lower() == (app.hostname or "").lower()
        and candidate_port == app_port
    )


def classify_navigation(url: str, app_origin: str) -> NavigationDecision:
    """Classify a top-level WebView navigation using an exact-origin policy."""
    candidate = urllib.parse.urlsplit(url)
    app = urllib.parse.urlsplit(app_origin)
    if candidate.scheme == "about" and candidate.path == "blank":
        return NavigationDecision("internal", url)
    if _same_origin(candidate, app):
        return NavigationDecision("internal", url)
    host = (candidate.hostname or "").lower()
    if candidate.scheme.lower() == "https" and host in APPROVED_EXTERNAL_HOSTS:
        return NavigationDecision("external", url)
    return NavigationDecision("blocked", url)


def _open_external_url(url: str) -> bool:
    """Open an already-approved external URL in the system browser."""
    return bool(webbrowser.open(url, new=2, autoraise=True))


def _event_url(args: object) -> str:
    value = getattr(args, "Uri", None)
    if value is None and hasattr(args, "get_Uri"):
        value = args.get_Uri()  # type: ignore[attr-defined]
    return str(value or "")


def _install_navigation_guard(window: Any, app_origin: str) -> None:
    """Install Edge WebView2 top-level and new-window navigation guards."""
    try:
        browser = window.native.browser
    except Exception:
        logger.exception(
            "WebView startup: navigation guard could not read window.native.browser; "
            "window.native=%r",
            getattr(window, "native", None),
        )
        raise

    def handle(url: str) -> NavigationDecision:
        decision = classify_navigation(url, app_origin)
        if decision.action == "external":
            with contextlib.suppress(OSError, webbrowser.Error):
                _open_external_url(decision.url)
        elif decision.action == "blocked":
            logger.warning("blocked unexpected WebView navigation to %s", decision.url)
        return decision

    def on_navigation_starting(_sender: object, args: object) -> None:
        decision = handle(_event_url(args))
        if decision.action != "internal":
            if hasattr(args, "Cancel"):
                args.Cancel = True
            elif hasattr(args, "set_Cancel"):
                args.set_Cancel(True)  # type: ignore[attr-defined]

    def on_new_window_request(_sender: object, args: object) -> None:
        handle(_event_url(args))
        if hasattr(args, "Handled"):
            args.Handled = True
        elif hasattr(args, "set_Handled"):
            args.set_Handled(True)  # type: ignore[attr-defined]

    # BrowserForm constructs this WebView2 control before ``before_show``.
    # Replacing the callback now means pywebview subscribes our guarded
    # callback when CoreWebView2 initialization completes.
    try:
        browser.webview.NavigationStarting += on_navigation_starting
        browser.on_new_window_request = on_new_window_request
        window._lockverity_navigation_handlers = (on_navigation_starting, on_new_window_request)
    except Exception:
        logger.exception(
            "WebView startup: navigation guard callback subscription failed; "
            "browser=%r browser.webview=%r",
            browser,
            getattr(browser, "webview", None),
        )
        raise
    logger.info("WebView startup: navigation guard installed (origin=%s)", app_origin)


def _icon_path() -> Path:
    return application_root() / "favicon-exe.ico"


def _pywebview_version() -> str:
    """Return the installed ``pywebview`` version, or ``"unknown"``."""
    try:
        import webview  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - import-failure path
        return f"import-failed: {type(exc).__name__}: {exc}"
    version = getattr(webview, "__version__", None)
    if version:
        return str(version)
    try:
        from importlib.metadata import version as _md_version  # type: ignore

        return _md_version("pywebview")
    except Exception as exc:  # pragma: no cover - metadata-failure path
        return f"unknown ({type(exc).__name__})"


def _diagnose_webview_environment(supervisor: DesktopBackendSupervisor) -> None:
    """Log the WebView / pywebview / runtime environment.

    Emits one structured log line per diagnostic category so a
    reviewer can read the captured state without joining multi-line
    records.
    """
    icon = _icon_path()
    try:
        icon_exists = icon.is_file()
        icon_size = icon.stat().st_size if icon_exists else 0
    except OSError as exc:
        icon_exists = False
        icon_size = 0
        logger.warning("WebView diagnostics: icon stat failed: %s", exc)
    storage_path = supervisor.home / "webview2"
    webview2_user_data = os.environ.get("WEBVIEW2_USER_DATA_FOLDER", "<unset>")
    logger.info(
        "WebView diagnostics: python=%s frozen=%s sys_executable=%s",
        ".".join(str(part) for part in sys.version_info[:3]),
        is_frozen(),
        sys.executable,
    )
    logger.info(
        "WebView diagnostics: pywebview=%s webview2_runtime=%s webview2_user_data=%s",
        _pywebview_version(),
        _webview2_runtime_version(),
        webview2_user_data,
    )
    logger.info(
        "WebView diagnostics: url=http://%s:%d/ port=%d icon=%s icon_exists=%s "
        "icon_size=%d webview2_storage=%s",
        supervisor.host,
        supervisor.port,
        supervisor.port,
        icon,
        icon_exists,
        icon_size,
        storage_path,
    )


def _load_webview() -> Any:
    """Import pywebview with full exception capture."""
    try:
        import webview  # type: ignore[import-not-found]
    except Exception:
        logger.exception(
            "WebView import failed: pywebview could not be imported. "
            "Frozen=%s python=%s sys_executable=%s",
            is_frozen(),
            sys.executable,
            sys.executable,
        )
        raise
    logger.info(
        "WebView import ok: pywebview=%s",
        getattr(webview, "__version__", "<unknown>"),
    )
    return webview


def _monitor_backend(supervisor: DesktopBackendSupervisor, window: Any) -> None:
    """Wait for the owned backend to exit and close the WebView on exit.

    The monitor thread runs alongside the WebView's message loop and
    tears the window down whenever the supervisor's ``done`` event
    fires, so an external ``lockverity-cli stop`` (which terminates
    the backend child) or an unexpected backend crash both result in
    the GUI process exiting cleanly. The unexpected-crash path shows a
    short message box so the operator knows the window is closing
    because the backend is gone; the operator-initiated shutdown path
    is silent.
    """
    supervisor.done.wait()
    if (
        not supervisor.shutdown_requested.is_set()
        and supervisor.error is not None
        and not isinstance(supervisor.error, BackendUnexpectedExitError)
    ):
        _show_message_box(
            "Lockverity - backend stopped",
            "Lockverity's local backend stopped unexpectedly. The desktop window will close.\n\n"
            f"Check the log at:\n{supervisor.home / 'logs' / 'lockverity.log'}",
        )
    with contextlib.suppress(Exception):
        window.destroy()


def _poll_stop_event(window: Any, stop_event_handle: int, poll_interval: float = 0.5) -> None:
    """Block until the named stop event is set, then destroy the WebView.

    The poll loop runs on its own thread so the message loop and the
    other WebView event handlers are not blocked. When the event is
    set by ``lockverity-cli stop``, the loop tears down the WebView
    so ``webview.start`` returns and the GUI process exits cleanly.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    while True:
        result = kernel32.WaitForSingleObject(stop_event_handle, int(poll_interval * 1000))
        if result == 0:  # WAIT_OBJECT_0
            logger.info("WebView shutdown: lockverity-cli stop event received")
            with contextlib.suppress(Exception):
                window.destroy()
            return
        if result != 258:  # WAIT_TIMEOUT
            # ``lockverity-cli stop`` is the only producer of
            # this event; an unexpected wait result here is
            # a Windows API error. Log and keep polling.
            logger.warning(
                "WebView shutdown: WaitForSingleObject returned %d on the stop event",
                result,
            )


def _open_stop_event() -> int | None:
    """Open the global stop event handle. Returns 0 if unavailable."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    handle = kernel32.OpenEventW(0x00100000, False, STOP_EVENT_NAME)  # SYNCHRONIZE
    return int(handle) if handle else None


def _run_webview(supervisor: DesktopBackendSupervisor) -> None:
    """Create the pywebview Edge Chromium window and block on the UI loop.

    Every operation in this function logs its entry, its result,
    and any exception. The launcher shows a single user-friendly
    message box on failure; the rich diagnostic detail is captured
    in ``%LOCALAPPDATA%\\Lockverity\\logs\\lockverity.log``.
    """
    logger.info("WebView startup: entering _run_webview")
    try:
        webview = _load_webview()
    except Exception:
        logger.exception("WebView startup: pywebview import failed")
        raise

    # Lockverity's existing export UI downloads generated Blob URLs. Enabling
    # pywebview's download path is therefore required product functionality;
    # on Windows it presents a native Save As dialog. File-origin navigation,
    # arbitrary popups, and remote debugging remain disabled independently.
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["REMOTE_DEBUGGING_PORT"] = None
        logger.info("WebView startup: settings applied")
    except Exception:
        logger.exception("WebView startup: webview.settings write failed")
        raise

    logger.info(
        "WebView startup: calling webview.create_window(title=%s url=http://%s:%d/ "
        "width=%d height=%d)",
        WINDOW_TITLE,
        supervisor.host,
        supervisor.port,
        WINDOW_WIDTH,
        WINDOW_HEIGHT,
    )
    try:
        window = webview.create_window(
            WINDOW_TITLE,
            supervisor.url,
            js_api=None,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
            resizable=True,
            background_color="#F7F8FA",
            text_select=True,
            zoomable=False,
            confirm_close=False,
        )
    except Exception:
        logger.exception(
            "WebView startup: webview.create_window raised. url=http://%s:%d/ webview2_storage=%s",
            supervisor.host,
            supervisor.port,
            str(supervisor.home / "webview2"),
        )
        raise
    if window is None:
        logger.error(
            "WebView startup: webview.create_window returned None. url=http://%s:%d/",
            supervisor.host,
            supervisor.port,
        )
        raise RuntimeError("pywebview did not create the Lockverity window")
    logger.info("WebView startup: webview.create_window returned a window object")

    try:
        window.events.before_show += lambda window: _install_navigation_guard(
            window, supervisor.url
        )
        # Wrap ``supervisor.request_shutdown`` in a zero-arg lambda so the
        # callback signature matches the ``Window.closing`` event contract
        # and so the callback only sets a ``threading.Event`` flag. All
        # actual shutdown work runs on the main thread / supervisor thread
        # after the WebView returns from the message loop; the closing
        # callback must never do message-pumping, socket, or subprocess
        # work itself.
        window.events.closing += lambda: supervisor.request_shutdown()
        logger.info("WebView startup: event hooks attached")
    except Exception:
        logger.exception("WebView startup: attaching event hooks failed")
        raise

    # ``lockverity-cli stop`` signals the running GUI by setting the
    # global ``STOP_EVENT_NAME`` event. The poll thread below waits
    # on the event and tears the WebView down so ``webview.start``
    # returns and the GUI process exits cleanly. The thread is
    # daemonised so the shutdown path is not held by it.
    stop_event_handle = _open_stop_event()
    stop_poll_thread: threading.Thread | None = None
    if stop_event_handle:
        stop_poll_thread = threading.Thread(
            target=_poll_stop_event,
            args=(window, stop_event_handle),
            name="lockverity-stop-poll",
            daemon=True,
        )
        stop_poll_thread.start()
    else:
        logger.info(
            "WebView startup: global stop event not available on this platform; "
            "lockverity-cli stop will rely on backend termination only"
        )

    icon = _icon_path()
    icon_arg = str(icon) if icon.is_file() else None
    storage_arg = str(supervisor.home / "webview2")
    logger.info(
        "WebView startup: calling webview.start(gui=edgechromium icon=%s storage_path=%s)",
        icon_arg,
        storage_arg,
    )
    try:
        webview.start(
            func=_monitor_backend,
            args=(supervisor, window),
            gui="edgechromium",
            debug=False,
            private_mode=True,
            storage_path=storage_arg,
            icon=icon_arg,
        )
    except Exception:
        logger.exception(
            "WebView startup: webview.start raised. gui=edgechromium storage_path=%s icon=%s",
            storage_arg,
            icon_arg,
        )
        raise
    logger.info("WebView startup: webview.start returned (window closed)")


def _startup_error_code(exc: BaseException | None) -> int:
    text = str(exc or "").lower()
    if "port" in text and "use" in text:
        return LAUNCHER_EXIT_PORT_IN_USE
    if "alembic" in text or "migration" in text:
        return LAUNCHER_EXIT_MIGRATION
    if isinstance(exc, BackendStartupError):
        return LAUNCHER_EXIT_HEALTH
    return LAUNCHER_EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    """Start the owned backend, show the native window, then shut it down."""
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv and effective_argv[0] == "--internal-serve":
        # Frozen foreground backend child. ``runner.build_server_argv``
        # re-enters the current windowless executable in frozen mode.
        from app.cli._serve import main as serve_main

        return serve_main(effective_argv[1:])

    parser = argparse.ArgumentParser(prog="Lockverity")
    # ``--port`` is retained for parity with the CLI;
    # the GUI ignores the value and asks the OS for a
    # free loopback port via
    # :func:`app.cli.port_reservation.reserve_loopback_port`.
    # The default value is therefore documentation only;
    # the GUI never binds it.
    parser.add_argument("--port", type=int, default=cli_runner.DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(effective_argv)

    # Configure launcher logging against the resolved
    # runtime home as early as possible so every
    # subsequent failure (including argparse errors,
    # mutex creation errors, port reservation errors,
    # Alembic errors, supervisor errors, and WebView
    # errors) lands in ``lockverity.log`` instead of
    # being silently dropped on a windowless Windows
    # process. The CLI's own ``configure_logging`` call
    # is still authoritative for ``lockverity.cli``;
    # :func:`configure_launcher_logging` is idempotent and
    # does not double-attach the shared handler.
    early_home = _resolve_runtime_home()
    configure_launcher_logging(early_home / "logs" / "lockverity.log")
    logger.info(
        "launcher: main(argv=%r) python=%s frozen=%s sys_executable=%s",
        effective_argv,
        ".".join(str(part) for part in sys.version_info[:3]),
        is_frozen(),
        sys.executable,
    )

    if not (1 <= int(args.port) <= 65535):
        _show_message_box("Lockverity - invalid port", "The desktop port must be in 1..65535.")
        return LAUNCHER_EXIT_ERROR

    instance = WindowsSingleInstance()
    try:
        first_instance = instance.acquire()
    except OSError:
        _show_message_box(
            "Lockverity - startup error",
            "Windows could not create the Lockverity single-instance guard.",
        )
        return LAUNCHER_EXIT_ERROR
    if not first_instance:
        _focus_existing_window()
        instance.close()
        return LAUNCHER_EXIT_OK

    # The GUI mode reserves a loopback port BEFORE the
    # backend ever starts so the OS commits to a port
    # number and no other process can grab it while we
    # set up. The same socket is forwarded into the
    # Uvicorn child via :mod:`app.cli.port_reservation`'s
    # cross-process transfer (Windows:
    # :func:`socket.share`; POSIX: fd inheritance).
    # The CLI retains its explicit ``--port`` flag;
    # the desktop application never uses it. The
    # default value is therefore only documentation.
    prebound_socket: socket.socket | None = None
    reserved_port: int | None = None
    try:
        prebound_socket, reserved_port = reserve_loopback_port()
    except PortReservationError as exc:
        _show_message_box(
            "Lockverity - could not reserve a port",
            "Lockverity could not reserve a free loopback port for the local backend.\n\n"
            f"Details: {exc}",
        )
        instance.close()
        return LAUNCHER_EXIT_ERROR

    supervisor: DesktopBackendSupervisor | None = None
    try:
        _set_app_user_model_id()
        if _webview2_runtime_version() is None:
            _show_message_box(
                "Lockverity - Microsoft Edge WebView2 required",
                "Microsoft Edge WebView2 Runtime is required to display Lockverity.\n\n"
                "Install Microsoft's Evergreen WebView2 Runtime, then start Lockverity again.\n\n"
                f"Official download: {WEBVIEW2_DOWNLOAD_URL}",
            )
            return LAUNCHER_EXIT_WEBVIEW2_MISSING

        home = ensure_home(_resolve_runtime_home())
        settings = _settings()
        frontend_dist = _frontend_dist(settings)
        if not (frontend_dist / "index.html").is_file():
            _show_message_box(
                "Lockverity - bundled frontend missing",
                "The packaged React frontend is missing or invalid.\n\n"
                f"Expected at: {frontend_dist}",
            )
            return LAUNCHER_EXIT_MISSING_DIST

        # The supervisor records ``reserved_port`` as
        # the actual port. The runner never re-binds
        # the port: it transfers the pre-bound socket
        # into the Uvicorn child via the cross-process
        # mechanism. The runtime state file
        # (``run/lockverity.state.json``) records the
        # same port, so ``lockverity-cli status`` and
        # ``lockverity-cli stop`` find the live
        # backend.
        supervisor = DesktopBackendSupervisor(
            home=home,
            host=cli_runner.DEFAULT_HOST,
            port=int(reserved_port),
            frontend_dist=frontend_dist,
            # Match the CLI contract exactly: ``None`` selects its
            # CWD-independent ``<runtime-home>/data`` default, while an
            # explicit operator environment override is forwarded verbatim.
            database_url=os.environ.get("LOCKVERITY_DATABASE_URL") or None,
            timeout=float(args.timeout),
            prebound_socket=prebound_socket,
        )
        logger.info(
            "WebView startup: backend reserved port=%d home=%s timeout=%.1fs",
            reserved_port,
            home,
            args.timeout,
        )
        supervisor.start()
        logger.info("WebView startup: supervisor.start() returned, waiting for readiness")
        if not supervisor.wait_until_ready():
            logger.error(
                "WebView startup: supervisor did not become ready; supervisor.error=%r",
                supervisor.error,
            )
            supervisor.shutdown()
            _show_message_box(
                "Lockverity - failed to start",
                "Lockverity's local backend did not become ready. No desktop window was opened.\n\n"
                f"Check the log at:\n{home / 'logs' / 'lockverity.log'}",
            )
            return _startup_error_code(supervisor.error)

        _diagnose_webview_environment(supervisor)
        logger.info(
            "WebView startup: backend ready at http://%s:%d/, entering native desktop shell",
            supervisor.host,
            supervisor.port,
        )
        try:
            _run_webview(supervisor)
        except BaseException:
            # ``BaseException`` (not just ``Exception``) is
            # caught so a SystemExit / KeyboardInterrupt
            # raised by pywebview's underlying Win32
            # bootstrap is captured with a full traceback
            # rather than silently terminating the GUI
            # process before the logger can flush.
            logger.exception(
                "WebView startup: native shell raised; this is the failing "
                "operation in the desktop window path"
            )
            try:
                _show_message_box(
                    "Lockverity - desktop window failed",
                    "Lockverity could not create its Microsoft Edge WebView2 window.\n\n"
                    f"Check the log at:\n{home / 'logs' / 'lockverity.log'}",
                )
            except Exception:
                logger.exception("WebView startup: _show_message_box also failed")
            return LAUNCHER_EXIT_ERROR
        logger.info("WebView startup: native shell returned (window closed)")
        return LAUNCHER_EXIT_OK
    finally:
        if supervisor is not None and not supervisor.shutdown():
            logger.error("backend supervisor did not exit after desktop shutdown")
        if prebound_socket is not None:
            with contextlib.suppress(OSError):
                prebound_socket.close()
        instance.close()


__all__ = [
    "APPROVED_EXTERNAL_HOSTS",
    "APP_USER_MODEL_ID",
    "INSTANCE_MUTEX_NAME",
    "LAUNCHER_EXIT_ERROR",
    "LAUNCHER_EXIT_HEALTH",
    "LAUNCHER_EXIT_MIGRATION",
    "LAUNCHER_EXIT_MISSING_DIST",
    "LAUNCHER_EXIT_OK",
    "LAUNCHER_EXIT_PORT_IN_USE",
    "LAUNCHER_EXIT_WEBVIEW2_MISSING",
    "BackendStartupError",
    "BackendUnexpectedExitError",
    "DesktopBackendSupervisor",
    "MigrationError",
    "MissingDistError",
    "NavigationDecision",
    "PortInUseError",
    "WindowsSingleInstance",
    "classify_navigation",
    "main",
    "signal_gui_stop",
]
