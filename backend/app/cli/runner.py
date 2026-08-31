"""Start and stop helpers for the ``lockverity`` CLI.

The :class:`Runner` class is the runtime workhorse for
the ``start`` and ``stop`` commands. The class is
intentionally small: it wraps the platform-portable
operations the CLI needs to do, and leaves the
operator-facing UX (argparse, formatting) to the
:mod:`app.cli.commands` modules.

Start flow
==========

1. Acquire the cross-platform start lock so two
   simultaneous ``lockverity start`` commands cannot
   both launch servers. The lock is held for the
   entire lifetime of the child process and is
   released in the ``finally`` block.
2. Resolve the runtime home (caller has done this).
3. Validate the frontend dist (caller has done this
   via the Part B1 settings validator).
4. Validate the port is free (caller has done this).
5. Generate a non-secret instance UUID and pass it
   to the private child serve entry point as
   ``--instance-id <UUID>``. The UUID is the strong
   identity fingerprint the live-process check
   verifies at ``stop`` / ``status`` time.
6. Run ``alembic upgrade head`` in a clean
   subprocess. The subprocess is constructed with an
   explicit argument list (no ``shell=True``); the
   database URL is passed through the documented
   ``LOCKVERITY_DATABASE_URL`` environment variable
   so the application's ``alembic/env.py`` does not
   override it.
7. Launch ``python -m uvicorn app.main:app`` as a
   background subprocess with the
   ``--instance-id <UUID>`` argument. The child
   inherits the Part B1 production posture.
8. Wait for the health endpoint to report ready.
9. Write the state file atomically. The state file
   contains the PID, creation time, instance UUID,
   and the documented non-secret schema.
10. Return a structured :class:`StartResult`.

Stop flow
=========

1. Read the state file.
2. Verify the recorded process identity via
   :func:`app.cli.process.verify_identity`. The
   check requires the live process to match the
   recorded PID + creation time + instance UUID +
   module. A PID that has been recycled for an
   unrelated process never matches.
3. Send a graceful termination signal via
   :func:`app.cli.process.terminate_process`.
4. Wait for the process to exit (bounded).
5. Force-kill only on explicit ``--force`` and only
   after the grace period has elapsed. The
   force-kill uses :func:`app.cli.process.force_terminate_process`
   which maps to ``SIGKILL`` on POSIX and
   ``TerminateProcess`` on Windows.
6. Clear the state file.
7. Return a structured :class:`StopResult`.

The runner never calls ``shell=True``. Every subprocess
is constructed with an explicit argument list so the
caller can audit the exact command the CLI issues.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil

from app import __version__
from app.cli import lock as start_lock
from app.cli.child_job import OwnedChildJob
from app.cli.gui_stop import signal_gui_stop
from app.cli.home import data_dir, ensure_home, logs_dir
from app.cli.logging_setup import configure_logging, get_cli_logger
from app.cli.process import (
    IdentityMatch,
    IdentityMismatch,
    ProcessGone,
    ProcessInaccessible,
    force_terminate_process,
    is_zombie,
    terminate_process,
    verify_identity,
)
from app.cli.state import (
    InstanceState,
    clear_state,
    make_state,
    read_state,
    state_file_path,
    write_state,
)
from app.core.config import get_settings
from app.static_frontend import validate_dist

# Default host / port for the production single-port
# runtime. The CLI does not allow a non-loopback host
# without the explicit ``--allow-remote`` flag; the
# default port is 8000 to match the documented Part B1
# startup command.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# Private CLI child-serve entry point. The supervisor
# launches the production server through this private
# module rather than ``python -m uvicorn`` directly
# because the module owns the supervisor's
# ``--instance-id`` flag: Uvicorn's CLI rejects the
# flag, so the supervisor needs a small wrapper that
# strips the flag and then calls :func:`uvicorn.run`.
SERVER_ENTRY = "app.cli._serve"

# Module recorded in the state file as a soft
# identity hint. The strong identity is the
# ``--instance-id`` token; the module string is the
# secondary hint the ``verify_identity`` function
# checks when the strong identity is empty. The
# recorded module is the *child entry point* the
# supervisor launches (``app.cli._serve``), which
# is the value that actually appears in the live
# command line and is therefore the right
# fingerprint to compare against.
SERVER_MODULE = "app.cli._serve"

# The private CLI-managed argument the child server
# reads to confirm it is the expected instance. The
# argument is a non-secret UUID generated at start
# time; the live-process check verifies the token
# is present in the live command line at stop time.
INSTANCE_ID_ARG = "--instance-id"

# Bounded number of health-probe attempts. Each
# attempt waits ``_HEALTH_PROBE_INTERVAL`` seconds; the
# total wait is ``start_timeout`` (or
# ``DEFAULT_START_TIMEOUT``).
HEALTH_PROBE_INTERVAL = 0.5
DEFAULT_START_TIMEOUT = 30
DEFAULT_STOP_TIMEOUT = 15
HEALTH_PROBE_TIMEOUT = 2

# A health-probe attempt that fails to even open a
# socket is not a fatal error -- the server is still
# starting up. Only a non-200 response is reported.
_HEALTH_OK_STATUSES = frozenset({200})


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class StartResult:
    """The outcome of a ``start`` invocation."""

    state: InstanceState
    process_handle: subprocess.Popen[bytes] | None
    elapsed_seconds: float
    health_check_ok: bool


@dataclass(slots=True, frozen=True)
class StopResult:
    """The outcome of a ``stop`` invocation."""

    outcome: str  # "stopped" | "was_not_running" | "force_killed" | "error"
    elapsed_seconds: float
    details: str = ""


@dataclass(slots=True, frozen=True)
class PortProbe:
    """The result of probing a host/port for availability."""

    host: str
    port: int
    in_use: bool
    detail: str = ""


@dataclass(slots=True, frozen=True)
class DoctorCheck:
    """A single doctor check."""

    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    # ``redacted_message`` is the message the doctor
    # command prints in ``--json`` mode or when the
    # operator requested a redacted view. It is
    # identical to ``message`` for non-sensitive
    # checks; sensitive checks replace secrets with
    # ``***``.
    redacted_message: str = ""


@dataclass(slots=True, frozen=True)
class DoctorReport:
    """The aggregated doctor report."""

    checks: tuple[DoctorCheck, ...] = field(default_factory=tuple)
    overall: str = "pass"  # "pass" | "warn" | "fail"
    home: str = ""
    version: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall,
            "version": self.version,
            "home": self.home,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.redacted_message or check.message,
                }
                for check in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Port probe
# ---------------------------------------------------------------------------


def probe_port(host: str, port: int, *, timeout: float = 0.5) -> PortProbe:
    """Return whether ``host:port`` is reachable.

    The probe attempts a TCP connect; a successful
    connect means the port is in use. A refused
    connection means the port is free. The probe does
    not send application data; it only opens the
    socket. The probe is intentionally short (sub-
    second) so a port-conflict check is fast.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return PortProbe(host=host, port=port, in_use=True, detail="connect succeeded")
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        # ``ConnectionRefusedError`` is the expected
        # signal for "port free". ``TimeoutError``
        # and the generic ``OSError`` cover the edge
        # cases (firewall drops, no route, etc.);
        # either way the port is *not* acceptably
        # occupied by a Lockverity instance.
        return PortProbe(host=host, port=port, in_use=False, detail=str(exc))


def is_loopback_host(host: str) -> bool:
    """Return ``True`` iff ``host`` is a loopback address.

    The function is conservative: only literal
    ``127.0.0.1`` and ``::1`` are accepted.
    Hostnames (including ``localhost``) are rejected
    so a misconfigured environment variable cannot
    lead to binding on a routable interface.
    """
    return host == "127.0.0.1" or host == "::1"


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def run_migrations(database_url: str) -> None:
    """Run ``alembic upgrade head`` against ``database_url``.

    The function invokes the ``alembic`` CLI in source
    mode (the standard ``alembic upgrade head``
    subprocess pattern) and uses the in-process
    ``alembic.command.upgrade`` API in frozen mode.

    The in-process path is used in frozen mode because
    the frozen ``sys.executable`` is the
    ``lockverity-cli.exe`` itself, not a Python
    interpreter; ``python -m alembic`` would invoke
    the CLI's argparse and fail. The in-process call
    goes through the bundled ``alembic.env`` module
    and the bundled migration scripts under
    ``sys._MEIPASS/alembic/versions``.

    The function uses
    :func:`app.runtime_paths.alembic_config_path` to
    locate the ``alembic.ini`` file so the same path
    resolution rule applies in source and frozen
    modes. The path is documented to live at
    ``<repo_root>/backend/alembic.ini`` in source
    mode and ``<frozen_root>/alembic/cfg/alembic.ini``
    in frozen mode (the ``alembic/cfg/`` subdirectory
    prefix is the v2.1 Part B3A PyInstaller
    prefix-collision workaround).
    """
    from app.runtime_paths import alembic_config_path, is_frozen

    alembic_ini = alembic_config_path()
    if not alembic_ini.is_file():
        raise RuntimeError(f"alembic.ini not found at {alembic_ini}")
    backend_root = alembic_ini.parent
    env = dict(os.environ)
    env["LOCKVERITY_DATABASE_URL"] = database_url
    if is_frozen():
        # In-process upgrade: import the alembic
        # command and run ``upgrade head`` against
        # the configured ``alembic.ini``. The
        # ``Config`` object is loaded from the
        # absolute path so PyInstaller's
        # ``sys._MEIPASS`` resolution does not
        # interfere. ``script_location`` is
        # overridden to the bundled ``alembic/``
        # directory (the parent of ``cfg/``).
        from alembic import command as alembic_command  # type: ignore[import-not-found]
        from alembic.config import Config as AlembicConfig  # type: ignore[import-not-found]

        config = AlembicConfig(str(alembic_ini))
        # ``alembic.ini`` uses ``script_location =
        # alembic`` which is interpreted relative
        # to the ``alembic.ini`` directory. The
        # frozen layout places the config at
        # ``<frozen_root>/alembic/cfg/alembic.ini``
        # and the scripts at
        # ``<frozen_root>/alembic/``. The config
        # file uses the relative path; we
        # override the absolute path so the
        # bundled ``versions/`` directory is
        # found.
        config.set_main_option("script_location", str(alembic_ini.parent.parent))
        # The application settings override the
        # ``sqlalchemy.url`` from
        # ``alembic/env.py``; setting it here
        # avoids the env-script round-trip.
        config.set_main_option("sqlalchemy.url", database_url)
        alembic_command.upgrade(config, "head")
        return
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(alembic_ini),
            "upgrade",
            "head",
        ],
        cwd=str(backend_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Server argv construction
# ---------------------------------------------------------------------------


def build_server_argv(
    *,
    host: str,
    port: int,
    log_level: str,
    instance_id: str,
    inherit_socket_fd: int | None = None,
) -> list[str]:
    """Build the argv for the private child-serve entry point.

    The argv is the single chokepoint for "what
    command does the CLI run"; tests exercise it
    directly. The argv uses ``sys.executable`` so
    the subprocess uses the same Python interpreter
    the CLI was invoked with, including the same
    venv.

    In source mode the argv launches the private
    :mod:`app.cli._serve` module with ``python -m
    app.cli._serve`` so a separate Python process
    runs the Uvicorn server.

    In frozen mode the frozen ``lockverity-cli.exe``
    is the only interpreter in the portable bundle.
    The argv dispatches through the documented
    ``--internal-serve`` flag in
    :func:`app.cli.main.main` so the same frozen
    process re-enters as the private serve entry
    point without going through the CLI's argparse.
    The ``app.cli._serve`` module's ``main`` is
    imported and called with the same argument
    vector.

    When ``inherit_socket_fd`` is provided (POSIX
    GUI mode), the function appends the
    ``--inherit-socket-fd <fd>`` flag pair so the
    child reconstructs the supervisor's pre-bound
    socket with the inherited file descriptor. On
    Windows the share data is delivered through the
    child's stdin, so the flag is not appended and
    the caller is expected to write the share blob
    to ``proc.stdin`` before the child reads it.
    """
    from app.runtime_paths import is_frozen

    base: list[str]
    if is_frozen():
        base = [
            sys.executable,
            "--internal-serve",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            log_level,
            INSTANCE_ID_ARG,
            instance_id,
        ]
    else:
        base = [
            sys.executable,
            "-m",
            SERVER_ENTRY,
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            log_level,
            INSTANCE_ID_ARG,
            instance_id,
        ]
    if inherit_socket_fd is not None and sys.platform != "win32":
        base.extend(["--inherit-socket-fd", str(int(inherit_socket_fd))])
    return base


def build_server_env(
    *,
    database_url: str,
    frontend_dist: Path,
    host: str,
    port: int,
    log_level: str,
) -> dict[str, str]:
    """Build the environment passed to the Uvicorn child process.

    The function copies the current process environment
    and applies the documented Part B1 / Part B2
    settings so the child runs in the documented
    production posture. Sensitive env values (the
    database URL, any provider token) are passed
    through the env as-is -- the operator's env file
    is the source of truth; the CLI never records
    them.
    """
    env = dict(os.environ)
    env["LOCKVERITY_ENVIRONMENT"] = "production"
    env["LOCKVERITY_SERVE_FRONTEND"] = "true"
    env["LOCKVERITY_FRONTEND_DIST"] = str(frontend_dist)
    env["LOCKVERITY_DATABASE_URL"] = database_url
    # The child reads its own host/port from the CLI
    # argv; the env duplicates the values so a
    # ``/api/v1/health`` response can confirm the
    # configured host/port.
    env["LOCKVERITY_CLI_HOST"] = host
    env["LOCKVERITY_CLI_PORT"] = str(port)
    env["LOCKVERITY_CLI_LOG_LEVEL"] = log_level
    # Force UTF-8 on Windows so Unicode log lines are
    # not silently dropped by cp1252.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def validate_explicit_port(port: int) -> None:
    """Raise unless ``port`` is a concrete port this CLI can manage.

    The CLI only ever launches a child on a port it names explicitly, so
    the valid range is ``1..65535``. Port ``0`` is rejected (LV-013):
    Uvicorn would honour it by asking the kernel for an ephemeral port,
    but every part of the parent's lifecycle - the readiness probe, the
    recorded state file, ``status``, ``stop`` - is built around the port
    the CLI *asked for*. The parent would health-check port 0, never get
    a response, clear the runtime state, and leave a live backend on a
    port nothing had recorded, with no way to stop it short of the task
    manager.

    Dynamic ports are not lost by this: the desktop GUI has its own
    race-free reservation path
    (:func:`app.cli.port_reservation.reserve_loopback_port`) that binds
    the socket *first* and hands the runner the real port, so the runner
    always sees a concrete value. There is no supported use for
    ``--port 0`` on the CLI.

    The check runs before the frontend dist is validated, before the
    runtime home is created, before migrations, and before any child is
    spawned, so a rejected invocation touches nothing.
    """
    if not isinstance(port, int) or isinstance(port, bool):
        raise RuntimeError(f"port must be an integer, got {port!r}")
    if port == 0:
        raise RuntimeError(
            "port 0 is not supported: the CLI manages a child by the port "
            "it was given, and an ephemeral port chosen by the kernel "
            "cannot be health-checked, recorded, or stopped. Pass a "
            "concrete port in 1..65535 (default: "
            f"{DEFAULT_PORT})."
        )
    if not (1 <= port <= 65535):
        raise RuntimeError(f"port {port} is outside the valid range 1..65535")


def _terminate_unmanaged_child(
    process_handle: subprocess.Popen[bytes],
    *,
    instance_id: str,
    cli_logger: object,
) -> bool:
    """Stop and reap a child the parent is about to stop tracking.

    Called on the one path where the parent gives up on a child it
    created: the detached child bound its port but never reported
    healthy. Historically the parent cleared the runtime state and
    returned, which left a live backend with no recorded state - the
    orphan LV-013 describes. Whatever the reason for the readiness
    failure, a child the parent will not record is a child the parent
    must not leave behind.

    Escalates the same way ``stop`` does: graceful signal, grace period,
    then force. Returns ``True`` if the child is gone.
    """
    pid = process_handle.pid
    logger_obj = cli_logger
    if terminate_process(pid, timeout=10.0, instance_id=instance_id):
        with contextlib.suppress(Exception):
            process_handle.wait(timeout=1.0)
        return True
    getattr(logger_obj, "warning", lambda *a, **k: None)(
        "start: unhealthy child pid=%d did not stop gracefully; forcing", pid
    )
    forced = force_terminate_process(pid, timeout=5.0)
    with contextlib.suppress(Exception):
        # Reap the child so it does not linger as a zombie on POSIX.
        process_handle.wait(timeout=1.0)
    if not forced:
        getattr(logger_obj, "error", lambda *a, **k: None)(
            "start: unhealthy child pid=%d could not be terminated; stop it manually",
            pid,
        )
    return forced


def _ensure_no_existing_instance(home: Path) -> None:
    """Refuse to start if a state file points to a live, matching instance.

    The helper is the second line of defence behind
    the cross-platform start lock. The start lock
    prevents two ``start`` calls from racing; this
    helper prevents a single ``start`` from launching
    a duplicate server when the previous instance is
    still alive. The state file is removed in the
    stale / gone / mismatch cases so a crashed
    previous instance does not block a fresh start.

    The function is intentionally narrow: it never
    signals a process; it only inspects the recorded
    identity and refuses to proceed if the recorded
    instance is still alive and matches.
    """
    existing = read_state(home)
    if existing is None:
        return
    identity = verify_identity(
        recorded_pid=existing.pid,
        recorded_created_at=existing.created_at,
        recorded_instance_id=existing.instance_id,
        recorded_module=existing.module,
    )
    if isinstance(identity, IdentityMatch):
        raise RuntimeError(
            f"an instance is already running (pid={existing.pid}, "
            f"host={existing.host}, port={existing.port}). "
            "Run `lockverity stop` first or use a different "
            "runtime home."
        )
    if isinstance(identity, ProcessInaccessible):
        # The recorded process is alive but we cannot
        # read its identity (insufficient privileges or
        # an OS-level error). Refuse to start to avoid
        # launching a duplicate.
        raise RuntimeError(
            f"recorded instance pid={existing.pid} is alive but its "
            f"identity cannot be read: {identity.reason}. Refusing to "
            "start a duplicate. Stop the existing instance first or "
            "remove the stale state file."
        )
    # ``ProcessGone`` or ``IdentityMismatch``: the
    # recorded instance is dead or has been replaced
    # (PID reuse). Clear the stale state file so a
    # fresh start can proceed without operator
    # intervention.
    with contextlib.suppress(OSError):
        clear_state(home)


def start(
    *,
    home: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    frontend_dist: Path | None = None,
    foreground: bool = False,
    timeout: float = DEFAULT_START_TIMEOUT,
    database_url: str | None = None,
    extra_env: dict[str, str] | None = None,
    log_level: str = "info",
    open_browser: bool = False,
    on_ready: Callable[[InstanceState], None] | None = None,
    prebound_socket: socket.socket | None = None,
) -> StartResult:
    """Launch the Lockverity single-port runtime.

    The function performs every step the ``start``
    command needs to do, in order. On success it
    returns a :class:`StartResult` with the recorded
    :class:`InstanceState`. On any failure it raises a
    :class:`RuntimeError` with a clear, actionable
    message; the CLI surfaces the error to the operator
    and returns a non-zero exit code.

    The function acquires the cross-platform start lock
    for the entire lifetime of the child process. The
    lock is released in the ``finally`` block so a
    crash during the start sequence does not leave a
    held lock.
    """
    if not is_loopback_host(host):
        # The CLI refuses non-loopback binds unless the
        # caller has explicitly opted in. The caller
        # (``commands/start.py``) is the only place
        # where the ``--allow-remote`` flag is read;
        # this check is the second line of defence.
        raise RuntimeError(
            f"refusing to bind {host!r}: the CLI only binds loopback "
            "addresses by default. Pass --allow-remote to bind a "
            "non-loopback host (the built-in server does not provide "
            "TLS; do not expose it beyond localhost without a reverse "
            "proxy)."
        )
    # LV-013: the port must be a concrete, bindable port *before* any
    # child is created. See :func:`validate_explicit_port`.
    validate_explicit_port(port)
    if frontend_dist is None:
        # Default to the Part B1 settings value, which
        # resolves ``frontend/dist`` against the
        # repository root.
        settings = get_settings()
        frontend_dist = settings.frontend_dist_path
    # Validate the dist so a stale build aborts startup
    # before we touch the state file or launch a child.
    validate_dist(frontend_dist)
    # Ensure the runtime home exists and write the
    # state file from a known directory.
    home = ensure_home(home)
    log_path = logs_dir(home) / "lockverity.log"
    # Migrations first; abort if Alembic fails so we
    # never launch a server against a stale schema.
    if database_url is None:
        # Honour an explicit operator override on the
        # supervisor's process environment. The operator
        # can pin the database location with
        # ``LOCKVERITY_DATABASE_URL`` in the parent shell;
        # the supervisor must pass that through verbatim
        # so a developer / CI override is never silently
        # shadowed by the default.
        explicit = os.environ.get("LOCKVERITY_DATABASE_URL", "").strip()
        if explicit:
            database_url = explicit
        else:
            # The default must be CWD-independent. A
            # relative ``sqlite:///./lockverity.sqlite``
            # URL would resolve to whatever the process
            # CWD happened to be at start time -- which
            # in the v2.1 Part B3B acceptance flow was
            # the install directory (the install root or
            # ``{app}\app\``), leaving ``lockverity.sqlite``
            # beside the installed executables. The
            # install directory must remain read-only
            # application content in production; runtime
            # data goes under ``home/data/``. The
            # absolute ``sqlite:///<home>/data/...`` URL
            # is invariant of the caller's CWD in every
            # mode (source, portable, installed, frozen).
            default_db_path = data_dir(home) / "lockverity.sqlite"
            database_url = f"sqlite:///{default_db_path.as_posix()}"
    # Publish the resolved database URL on the
    # supervisor's process environment so every
    # downstream code path (Alembic ``env.py``,
    # ``app.db.session`` engine construction,
    # ``get_settings().database_url`` readers) sees
    # the same CWD-independent value. The Alembic
    # ``env.py`` script calls ``get_settings()`` and
    # overwrites ``config.sqlalchemy.url`` with
    # ``settings.database_url`` -- if the supervisor's
    # env does not carry the absolute URL, ``env.py``
    # would silently overwrite the runner's
    # ``set_main_option`` with the historical
    # ``sqlite:///./lockverity.sqlite`` default and
    # create the database file at the supervisor's
    # CWD (the install root). Setting the env var
    # before ``run_migrations`` is called closes the
    # CWD-relative hole.
    os.environ["LOCKVERITY_DATABASE_URL"] = database_url
    # The default ``LOCKVERITY_WORKSPACE_ROOT`` of
    # ``./var/workspace`` is a CWD-relative path. When
    # the child process is launched by the CLI the
    # supervisor's CWD is the install directory (or its
    # ``_internal`` PyInstaller support directory in
    # frozen mode), so the historical default would
    # resolve to a workspace root inside the install
    # tree rather than inside the operator-controlled
    # runtime home. That combination is a real defect:
    # a self-scan of a long-named repository produces
    # an extraction path that can exceed Windows
    # ``MAX_PATH`` (260), and the resulting
    # ``FileNotFoundError`` surfaces to the user as
    # "Unknown error" / 500 because the supervisor's
    # workspace dir is not where the operator's data
    # should live. The fix mirrors the ``database_url``
    # approach above: compute an absolute
    # runtime-home-relative workspace root and publish
    # it on the supervisor's process environment so the
    # child reads the same CWD-independent value.
    # ``<home>/var/workspace/`` keeps the documented
    # v2.1 Part B2 layout (``data/``, ``logs/``,
    # ``run/``, ``config/``) plus a ``var/`` root for
    # the per-workspace quarantine and contents.
    workspace_root = (home / "var" / "workspace").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    os.environ["LOCKVERITY_WORKSPACE_ROOT"] = str(workspace_root)
    # ``get_settings`` is LRU-cached. If a previous
    # call (during the CLI's import chain, or from
    # a prior ``start`` attempt) populated the cache
    # with the historical default URL, the Alembic
    # ``env.py`` would return the cached value
    # (not the just-set env var). Clear the cache so
    # every downstream reader re-reads the env var.
    from app.core.config import get_settings as _get_settings

    _get_settings.cache_clear()
    # Acquire the start lock for the entire lifetime
    # of the child. The lock is released in the
    # ``finally`` block so a crash during migration,
    # child launch, or readiness wait does not leave
    # a held lock for a future ``start`` to clean up.
    with start_lock.acquire(home):
        # Refuse to start if a state file points to a
        # live, matching instance. The check is the
        # second line of defence behind the start lock;
        # the start lock prevents two ``start`` calls
        # from racing, and this check prevents a single
        # ``start`` from launching a duplicate when the
        # previous instance is still alive. The state
        # file is removed in the stale / gone / mismatch
        # cases so a crashed previous instance does not
        # block a fresh start.
        _ensure_no_existing_instance(home)
        run_migrations(database_url)
        # Check the port is free; refuse if another
        # process is bound. The check is a soft probe;
        # the bind inside Uvicorn will fail with a
        # clearer error if the probe missed a race.
        # The GUI mode hands the runner a pre-bound
        # socket instead; the kernel already owns the
        # port, so the runner never re-binds it and
        # never probes the port for availability. The
        # ``--port`` CLI flag is left alone for the
        # background path so an operator can still pin
        # a specific port.
        inherit_socket_fd: int | None = None
        if prebound_socket is not None:
            try:
                bound = prebound_socket.getsockname()
            except OSError as exc:
                raise RuntimeError(f"the GUI-owned pre-bound socket is not bound: {exc}") from exc
            if len(bound) < 2:
                raise RuntimeError(
                    "the GUI-owned pre-bound socket does not expose a port; "
                    "reserve_loopback_port must produce an IPv4 or IPv6 socket."
                )
            port = int(bound[1])
            # The runner never releases the pre-bound
            # socket; the child inherits the binding via
            # ``--inherit-socket-fd`` (POSIX) or the
            # stdin share blob (Windows). On Windows the
            # share blob cannot be created until the
            # child is running, so the
            # ``share_socket_to_subprocess`` call is
            # deferred to the launch step below.
            from app.cli.port_reservation import mark_socket_inheritable

            if sys.platform != "win32":
                inherit_socket_fd = mark_socket_inheritable(prebound_socket)
        else:
            probe = probe_port(host, port)
            if probe.in_use:
                raise RuntimeError(
                    f"port {host}:{port} is already in use ({probe.detail}). "
                    "Choose a different port with --port or stop the existing "
                    "process first."
                )
        # Generate the non-secret instance UUID that
        # ties the recorded state to the live process.
        instance_id = str(uuid.uuid4())
        # Configure the CLI logger so the background
        # server boot output lands in the rotating log.
        configure_logging(log_path)
        cli_logger = get_cli_logger()
        cli_logger.info(
            "lockverity %s starting (home=%s, host=%s, port=%d, dist=%s, instance_id=%s)",
            __version__,
            home,
            host,
            port,
            frontend_dist,
            instance_id,
        )
        # Build the child argv and the environment. The
        # env explicitly sets the Part B1 production
        # settings; the CLI does not import the
        # operator's shell environment wholesale.
        env = build_server_env(
            database_url=database_url,
            frontend_dist=frontend_dist,
            host=host,
            port=port,
            log_level=log_level,
        )
        if extra_env:
            env.update(extra_env)
        argv = build_server_argv(
            host=host,
            port=port,
            log_level=log_level,
            instance_id=instance_id,
        )
        started = time.monotonic()
        if foreground:
            return _start_foreground(
                argv=argv,
                env=env,
                home=home,
                host=host,
                port=port,
                frontend_dist=frontend_dist,
                log_path=log_path,
                started=started,
                database_url=database_url,
                open_browser=open_browser,
                cli_logger=cli_logger,
                instance_id=instance_id,
                timeout=timeout,
                on_ready=on_ready,
                prebound_socket=prebound_socket,
                inherit_socket_fd=inherit_socket_fd,
            )
        process_handle, _log_handle = _launch_detached(
            argv=argv,
            env=env,
            log_path=log_path,
            prebound_socket=prebound_socket,
            inherit_socket_fd=inherit_socket_fd,
        )
        # Wait for the health endpoint to respond. The
        # timeout bounds the wait so a slow build does
        # not hang the CLI forever.
        health_ok = _wait_for_health(host, port, timeout=timeout)
        elapsed = time.monotonic() - started
        if not health_ok:
            # The server is not healthy. Do NOT publish a
            # state file: a stale state file with a dead
            # pid would mislead the launcher, ``status``,
            # ``open`` and ``stop`` from a second terminal.
            # The log file is the canonical diagnostic
            # surface; we surface the failure there and
            # the CLI's exit code (3) signals the warning
            # to the caller. We also defensively remove
            # any stale state file left from a previous
            # run so a future ``status`` does not see a
            # ghost record.
            cli_logger.error(
                "health check at http://%s:%d%s/health did not respond within %.1fs; "
                "refusing to publish a state file (the child exited or is not bound)",
                host,
                port,
                get_settings().api_prefix,
                timeout,
            )
            # LV-013: the parent is about to stop tracking this child,
            # so it must not leave it running. Without this, a child
            # that bound its port but never reported healthy survived
            # with no state file - unreachable by ``stop`` and
            # invisible to ``status``.
            _terminate_unmanaged_child(
                process_handle,
                instance_id=instance_id,
                cli_logger=cli_logger,
            )
            clear_state(home)
            return StartResult(
                state=make_state(
                    pid=process_handle.pid,
                    created_at=_now_iso(),
                    host=host,
                    port=port,
                    version=__version__,
                    home=home,
                    frontend_dist=frontend_dist,
                    log_file=log_path,
                    module=SERVER_MODULE,
                    started_at=_now_iso(),
                    instance_id=instance_id,
                ),
                process_handle=process_handle,
                elapsed_seconds=elapsed,
                health_check_ok=False,
            )
        started_at = _now_iso()
        # Read the live process creation time so the
        # state file records the OS-truth creation
        # time. This is the value the live-identity
        # check compares against on ``stop`` and
        # ``status``.
        try:
            live_process = psutil.Process(process_handle.pid)
            created_at_unix = float(live_process.create_time())
        except (psutil.Error, OSError):
            # Fallback: if we cannot read the live
            # creation time for any reason, use the
            # CLI start time as a conservative estimate.
            # The verification on ``stop`` will use the
            # same fallback and the identity check will
            # still work; the only effect is that the
            # ``created_at`` field may be a few
            # hundred milliseconds later than the
            # real creation time.
            created_at_unix = time.time()
        created_at_iso = _format_unix(created_at_unix)
        state = make_state(
            pid=process_handle.pid,
            created_at=created_at_iso,
            host=host,
            port=port,
            version=__version__,
            home=home,
            frontend_dist=frontend_dist,
            log_file=log_path,
            module=SERVER_MODULE,
            started_at=started_at,
            instance_id=instance_id,
        )
        write_state(home, state)
        if on_ready is not None:
            on_ready(state)
        cli_logger.info(
            "lockverity %s ready at http://%s:%d (pid=%d, instance_id=%s, state=%s)",
            __version__,
            host,
            port,
            process_handle.pid,
            instance_id,
            state_file_path(home),
        )
        if open_browser and health_ok:
            # The CLI does not block on the browser; the
            # call returns once the OS has been asked to
            # open the URL.
            _open_browser(host, port)
        return StartResult(
            state=state,
            process_handle=process_handle,
            elapsed_seconds=elapsed,
            health_check_ok=health_ok,
        )


def _launch_detached(
    *,
    argv: list[str],
    env: dict[str, str],
    log_path: Path,
    prebound_socket: socket.socket | None = None,
    inherit_socket_fd: int | None = None,
) -> tuple[subprocess.Popen[bytes], object]:
    """Launch the Uvicorn child as a detached process.

    The cross-platform detachment is achieved by:

      - On POSIX: ``start_new_session=True`` (the child
        becomes the leader of a new process group, so
        the CLI can ``killpg`` the whole group on
        stop).
      - On Windows: ``creationflags=DETACHED_PROCESS |
        CREATE_NEW_PROCESS_GROUP`` (the child is
        detached from the console and starts a new
        process group; ``CTRL_BREAK_EVENT`` is the
        graceful-stop signal).

    The child stdout and stderr are routed to the
    rotating log file via a separate logger thread so
    the parent process does not have to drain the
    pipes (which would deadlock if the child writes
    more than the pipe buffer can hold).

    When the GUI mode forwards a pre-bound socket, the
    function writes the Windows share blob to the
    child's stdin (Windows) or inherits the file
    descriptor through ``subprocess``'s ``pass_fds``
    argument (POSIX). The share handle is consumed by
    the child's :func:`_read_inherited_socket` helper
    before Uvicorn is constructed. The runner never
    rebinds the port and never closes the pre-bound
    socket; the OS keeps the port reserved for the
    child for the lifetime of the supervisor.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log_handle:
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "env": env,
            "cwd": str(Path(__file__).resolve().parents[2]),
        }
        if prebound_socket is not None and sys.platform == "win32":
            # Windows: the supervisor writes the share
            # blob to the child's stdin. The actual
            # blob is created *after* the child is
            # spawned because ``socket.share`` needs
            # the child PID. The pipe must be open
            # before ``Popen`` returns.
            kwargs["stdin"] = subprocess.PIPE
        if inherit_socket_fd is not None and sys.platform != "win32":
            # POSIX: forward the inherited file
            # descriptor through ``subprocess``. The
            # child reconstructs the socket from the
            # fd number encoded in
            # ``--inherit-socket-fd <N>``.
            kwargs["pass_fds"] = (int(inherit_socket_fd),)
            kwargs["close_fds"] = True
        if sys.platform.startswith("win"):
            detached_process = 0x00000008
            create_new_process_group = 0x00000200
            kwargs["creationflags"] = detached_process | create_new_process_group
            kwargs.setdefault("close_fds", True)
        else:
            kwargs.setdefault("start_new_session", True)
            kwargs.setdefault("close_fds", True)
        handle = subprocess.Popen(argv, **kwargs)
        if prebound_socket is not None and handle.stdin is not None and sys.platform == "win32":
            from app.cli.port_reservation import share_socket_to_subprocess

            try:
                share_data = share_socket_to_subprocess(prebound_socket, int(handle.pid))
                handle.stdin.write(share_data)
                handle.stdin.flush()
            except (BrokenPipeError, OSError):
                # The child may have failed to start;
                # the supervisor surfaces the failure
                # via the health check below.
                pass
            finally:
                with contextlib.suppress(OSError):
                    handle.stdin.close()
    return handle, log_handle


def _install_foreground_signal_handlers() -> None:
    """Install signal handlers that translate ``SIGBREAK`` to ``SIGINT``.

    On Windows, the CPython default action for
    :data:`signal.SIGBREAK` is to terminate the
    process without raising
    :class:`KeyboardInterrupt`. The supervisor is a
    foreground TTY application; if the operator
    presses Ctrl+C in the console the Windows console
    delivers :data:`signal.CTRL_C_EVENT`, which CPython
    already maps to ``KeyboardInterrupt``. The
    :data:`signal.CTRL_BREAK_EVENT` variant arrives
    only when the supervisor is launched in a new
    process group (e.g. by an external test
    harness or by ``START /B``). The handler below
    maps ``SIGBREAK`` to the documented
    :data:`signal.SIGINT` action so the
    :class:`KeyboardInterrupt` unwind runs and the
    ``with start_lock.acquire(home):`` block releases
    the lock before the supervisor exits.

    On POSIX the function is a no-op because
    ``SIGINT`` already raises ``KeyboardInterrupt``
    via the CPython default handler.
    """
    if sys.platform != "win32" or not hasattr(signal, "SIGBREAK"):
        return
    # ``default_int_handler`` is the CPython helper
    # that raises ``KeyboardInterrupt``; installing
    # it for ``SIGBREAK`` unifies the Windows
    # Ctrl+C and Ctrl+Break behaviour with the
    # POSIX SIGINT behaviour. ``SIGBREAK`` can only
    # be installed by the main thread on Windows;
    # ``ValueError`` covers a test worker thread
    # and ``OSError`` covers a non-main context.
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)  # type: ignore[attr-defined]


def _read_child_creation_time(pid: int) -> float:
    """Return the OS-truth creation time of ``pid`` as a UNIX timestamp.

    The helper is the single chokepoint for the
    ``created_at`` field of the state file. The
    function falls back to the current wall clock
    if psutil cannot read the live process; the
    identity check on ``stop`` then uses the same
    fallback and the PID-reuse defence still works
    (a reaped PID will report ``ProcessGone``
    before the time check is consulted).
    """
    try:
        live_process = psutil.Process(pid)
        return float(live_process.create_time())
    except (psutil.Error, OSError):
        return time.time()


def _publish_state(
    *,
    home: Path,
    pid: int,
    created_at_unix: float,
    host: str,
    port: int,
    frontend_dist: Path,
    log_path: Path,
    instance_id: str,
) -> InstanceState:
    """Build and atomically write the runtime state for the live child.

    The function is the single chokepoint used by
    both the background and the foreground paths
    after the child is healthy. The state records
    the *child* identity (``pid`` is the Uvicorn
    worker, not the supervisor's PID) and never
    persists the database URL, the full command
    line, or any provider token. The atomic
    ``tempfile + os.replace`` write inside
    :func:`app.cli.state.write_state` is the same
    primitive the rest of the runner uses.
    """
    state = make_state(
        pid=pid,
        created_at=_format_unix(created_at_unix),
        host=host,
        port=port,
        version=__version__,
        home=home,
        frontend_dist=frontend_dist,
        log_file=log_path,
        module=SERVER_MODULE,
        started_at=_now_iso(),
        instance_id=instance_id,
    )
    write_state(home, state)
    return state


def _cleanup_foreground_state(home: Path, instance_id: str) -> None:
    """Remove the state file only if it still matches the recorded instance.

    The helper is the foreground supervisor's
    instance-scoped cleanup. It is the safety net
    that prevents an older process from removing
    a newer instance's state. The function:

      1. Reads the current state file under
         ``run/lockverity.state.json``.
      2. Returns without removing the file if the
         ``instance_id`` does not match the value
         the supervisor recorded at start time
         (a newer instance has taken over the home
         and the older supervisor must not touch
         the newer instance's state).
      3. Returns without removing the file if the
         state file is missing (a parallel clean
         shutdown already removed it).
      4. Otherwise removes the file via
         :func:`app.cli.state.clear_state`.

    The function is silent on errors so a
    foreground shutdown is best-effort; the
    state file is operator-visible and an
    operator can remove it manually if the
    supervisor's cleanup is interrupted.
    """
    try:
        existing = read_state(home)
    except ValueError:
        # Corrupt or unreadable state file. Leave
        # it for the operator to inspect; the
        # supervisor's stop / start flow is the
        # documented recovery path.
        return
    if existing is None:
        return
    if existing.instance_id != instance_id:
        # A newer instance has taken over the home;
        # the older supervisor's cleanup is a no-op
        # so the newer instance is not disturbed.
        return
    with contextlib.suppress(OSError):
        clear_state(home)


def _start_foreground(
    *,
    argv: list[str],
    env: dict[str, str],
    home: Path,
    host: str,
    port: int,
    frontend_dist: Path,
    log_path: Path,
    started: float,
    database_url: str,
    open_browser: bool,
    cli_logger: object,
    instance_id: str,
    timeout: float,
    on_ready: Callable[[InstanceState], None] | None = None,
    prebound_socket: socket.socket | None = None,
    inherit_socket_fd: int | None = None,
) -> StartResult:
    """Run the server in the current TTY (no daemonisation).

    Foreground mode attaches the supervisor to the
    child in the same console. The supervisor:

      1. Installs a ``SIGBREAK`` → ``KeyboardInterrupt``
         translator so a Windows
         ``CTRL_BREAK_EVENT`` from an external
         test harness (or a ``START /B`` shell)
         unwinds the surrounding
         ``with start_lock.acquire(home):`` block
         and the documented KeyboardInterrupt exit
         code propagates to the operator.
      2. Launches the ``app.cli._serve`` child via
         :class:`subprocess.Popen` (not detached)
         so the child shares the supervisor's
         console; ``Ctrl+C`` in the console goes
         to both the supervisor and the child.
      3. Waits for the documented health readiness
         before publishing the runtime state. The
         state file records the *child* identity
         (PID, creation time, instance UUID,
         module) so a second terminal can run
         ``status`` / ``status --json`` /
         ``open --print-url`` / ``logs`` /
         ``stop`` against the same instance.
      4. Waits for the child to exit. The wait
         observes three documented exit paths:
         (a) the operator presses Ctrl+C (or
         ``CTRL_BREAK_EVENT`` is delivered to the
         supervisor's process group);
         (b) the operator runs ``lockverity stop``
         from a second terminal; the
         ``runner.stop`` function reads the state
         file, verifies the live-process identity,
         and signals the child PID; the supervisor
         observes the child exit and unwinds
         normally;
         (c) the child exits unexpectedly (crash,
         external kill). The supervisor cleans up
         the state file (instance-scoped) and the
         surrounding ``with`` block releases the
         start lock.
      5. Always cleans up the state file
         instance-scoped before returning, so no
         false-running state remains on disk.

    A :class:`KeyboardInterrupt` raised during
    the wait is caught and re-raised only after
    the child has been terminated, the state has
    been removed, and the supervisor's log line
    has been written. The caller (``start()``) does
    not catch the exception; it propagates to the
    CLI main entry point which converts it to
    ``SystemExit(130)`` and the documented exit
    code.
    """
    _install_foreground_signal_handlers()
    cli_logger.info(
        "lockverity %s foreground starting (home=%s, host=%s, port=%d, dist=%s, instance_id=%s)",
        __version__,
        home,
        host,
        port,
        frontend_dist,
        instance_id,
    )
    # Launch the child via ``Popen`` (not detached)
    # so it shares the supervisor's console. The
    # child inherits the supervisor's stdout /
    # stderr so a ``Ctrl+C`` in the console goes to
    # both processes. The supervisor's
    # :func:`cli_logger` writes to the rotating
    # log file via the handler installed by
    # :func:`configure_logging`; the child's stdout
    # is inherited from the supervisor and the
    # child itself logs via its own
    # :func:`configure_logging` call inside
    # :mod:`app.cli._serve`, so the log file is the
    # single artefact an operator inspects after a
    # foreground run.
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "env": env,
        "cwd": str(Path(__file__).resolve().parents[2]),
        "close_fds": True,
    }
    if prebound_socket is not None and sys.platform == "win32":
        # Windows: the supervisor writes the share
        # blob to the child's stdin. The actual blob
        # is created *after* the child is spawned
        # because ``socket.share`` needs the child
        # PID. The pipe must be open before ``Popen``
        # returns.
        kwargs["stdin"] = subprocess.PIPE
    if inherit_socket_fd is not None and sys.platform != "win32":
        # POSIX: forward the inherited file descriptor
        # through ``subprocess``. The child reconstructs
        # the socket from the fd number encoded in
        # ``--inherit-socket-fd <N>``.
        kwargs["pass_fds"] = (int(inherit_socket_fd),)
    if sys.stdout is not None and hasattr(sys.stdout, "fileno"):
        # The child inherits the supervisor's stdout
        # so a ``Ctrl+C`` in the console goes to both.
        # The test harness redirects the supervisor's
        # stdout to a file (see ``_fg_state_smoke``);
        # the child inherits that redirect and writes
        # to the same file.
        kwargs["stdout"] = None  # inherit
        kwargs["stderr"] = None  # inherit
    proc = subprocess.Popen(argv, **kwargs)
    # Contain the child before it can serve. The supervisor reaps this child
    # explicitly on every path that unwinds through Python, but nothing runs
    # when the supervisor is killed outright (Task Manager "End task", a
    # native WebView2 crash, logoff). The job object is the kernel's
    # guarantee for exactly those paths, scoped to this one PID -- it can
    # never reach an unrelated Lockverity instance. Assigning here, before
    # the share blob is written, means a Windows GUI child is contained
    # while it is still blocked reading its socket handle from stdin, so it
    # cannot begin serving outside the job.
    child_job = OwnedChildJob()
    if child_job.open():
        child_job.contain(proc.pid)
    if prebound_socket is not None and proc.stdin is not None and sys.platform == "win32":
        from app.cli.port_reservation import share_socket_to_subprocess

        try:
            share_data = share_socket_to_subprocess(prebound_socket, int(proc.pid))
            proc.stdin.write(share_data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            # The child may have failed to start; the
            # supervisor surfaces the failure via the
            # health check below.
            pass
        finally:
            with contextlib.suppress(OSError):
                proc.stdin.close()
    state: InstanceState | None = None
    try:
        # Wait for the health endpoint. The supervisor
        # does not publish state before the child is
        # healthy, so a child that fails to start does
        # not leave a false-running state. A bounded
        # timeout ensures a child that never becomes
        # healthy does not stall the supervisor
        # forever.
        health_ok = _wait_for_health(host, port, timeout=timeout)
        if not health_ok:
            cli_logger.error(
                "foreground: health check at http://%s:%d%s/health did not respond within %.1fs",
                host,
                port,
                get_settings().api_prefix,
                timeout,
            )
            # The child may still be starting; give it
            # a bounded chance to exit on its own, then
            # terminate it explicitly so the supervisor
            # does not leak a child process.
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)
            raise RuntimeError(
                f"foreground child did not report healthy at "
                f"http://{host}:{port}{get_settings().api_prefix}/health "
                f"within {timeout:.1f}s"
            )
        # Publish the runtime state with the live
        # child's PID and creation time. The state
        # file is the contract ``status`` /
        # ``open --print-url`` / ``logs`` / ``stop``
        # rely on from a second terminal.
        state = _publish_state(
            home=home,
            pid=proc.pid,
            created_at_unix=_read_child_creation_time(proc.pid),
            host=host,
            port=port,
            frontend_dist=frontend_dist,
            log_path=log_path,
            instance_id=instance_id,
        )
        if on_ready is not None:
            on_ready(state)
        cli_logger.info(
            "lockverity %s ready at http://%s:%d (pid=%d, instance_id=%s, state=%s)",
            __version__,
            host,
            port,
            proc.pid,
            instance_id,
            state_file_path(home),
        )
        if open_browser:
            # The CLI does not block on the browser; the
            # call returns once the OS has been asked to
            # open the URL. Errors are logged but do not
            # fail the foreground command.
            try:
                webbrowser.open(f"http://{host}:{port}/")
            except Exception as exc:  # pragma: no cover - OS-specific
                cli_logger.warning("foreground: open browser failed: %s", exc)
        # Keep the supervisor attached to the child.
        # ``proc.wait()`` blocks until the child exits.
        # On Windows ``proc.wait()`` does not react to
        # signals; the signal handler runs in the
        # supervisor's main thread but the wait
        # continues. The KeyboardInterrupt is raised
        # in the supervisor's main thread at the next
        # bytecode boundary; the wait returns when the
        # child exits (either via the supervisor's
        # ``terminate`` call below or via the child
        # reacting to the same console signal).
        try:
            returncode = proc.wait()
        except KeyboardInterrupt:
            # The operator pressed Ctrl+C in the
            # console (or an external test harness
            # delivered ``CTRL_BREAK_EVENT`` to the
            # supervisor's process group). The child
            # is in the same console, so the same
            # signal went to the child; Uvicorn's
            # graceful shutdown is already in
            # progress. Give the child a bounded
            # window to exit, escalate to
            # ``TerminateProcess`` / ``SIGKILL`` only
            # on timeout.
            cli_logger.info("foreground: KeyboardInterrupt received; terminating child")
            if proc.poll() is None:
                try:
                    proc.terminate()
                except (psutil.Error, OSError) as exc:
                    cli_logger.warning("foreground: terminate failed: %s", exc)
                try:
                    proc.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    cli_logger.warning("foreground: child did not exit after terminate; killing")
                    try:
                        proc.kill()
                    except (psutil.Error, OSError) as exc:
                        cli_logger.warning("foreground: kill failed: %s", exc)
                    proc.wait(timeout=5.0)
            raise
    except BaseException:
        # Any exception during the foreground flow
        # (health timeout, KeyboardInterrupt, OOM,
        # state-write failure, ...) must clean up the
        # state file before propagating. The lock is
        # released by the caller's ``with`` block.
        if state is not None:
            _cleanup_foreground_state(home, state.instance_id)
        elif proc.poll() is not None and state_file_path(home).is_file():
            # Health succeeded and the state was
            # published, but the exception happened
            # after the wait returned; the ``state``
            # binding was never reached. Use the
            # recorded instance_id from the file for
            # the instance-scoped cleanup.
            try:
                existing = read_state(home)
                if existing is not None:
                    _cleanup_foreground_state(home, existing.instance_id)
            except ValueError:
                pass
        # Reap the child if it is still alive. The
        # exception path always terminates the child
        # before returning so the supervisor does not
        # leak an orphan server process.
        if proc.poll() is None:
            with contextlib.suppress(psutil.Error, OSError):
                proc.terminate()
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(psutil.Error, OSError):
                    proc.kill()
                proc.wait(timeout=5.0)
        raise
    else:
        # The child exited normally. ``runner.stop``
        # from a second terminal, the operator's
        # ``Ctrl+C`` in the console, the child
        # reacting to a shutdown signal, or a child
        # crash all land here. Remove the state
        # instance-scoped so a follow-up ``start``
        # is not blocked by a stale state file.
        elapsed = time.monotonic() - started
        if state is not None:
            _cleanup_foreground_state(home, state.instance_id)
        # ``StartResult`` is the typed return; in
        # foreground mode the supervisor does not
        # retain a handle to the child (the child has
        # already exited and been reaped), and the
        # ``state`` field is a transient placeholder
        # that mirrors the recorded identity. The
        # placeholder is never written to disk
        # because the on-disk state was already
        # removed by the instance-scoped cleanup
        # above; the value is the *child* PID, not
        # the supervisor's PID.
        placeholder = make_state(
            pid=proc.pid,
            created_at=_now_iso(),
            host=host,
            port=port,
            version=__version__,
            home=home,
            frontend_dist=frontend_dist,
            log_file=log_path,
            module=SERVER_MODULE,
            started_at=_now_iso(),
            instance_id=instance_id,
        )
        cli_logger.info(
            "foreground: child exited (returncode=%d) after %.1fs",
            returncode,
            elapsed,
        )
        return StartResult(
            state=placeholder,
            process_handle=None,
            elapsed_seconds=elapsed,
            health_check_ok=health_ok,
        )
    finally:
        # Runs on the raise path and the return path alike. By here every
        # branch above has already terminated and reaped the child, so this
        # normally closes an empty job; if a branch ever failed to, closing
        # the handle reaps the child rather than leaking it.
        child_job.close()


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_unix(unix_seconds: float) -> str:
    """Format a UNIX timestamp as an ISO 8601 UTC string (second precision)."""
    return (
        datetime.fromtimestamp(int(unix_seconds), tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _wait_for_backend_exit(pid: int, timeout: float) -> bool:
    """Return ``True`` if the given pid exits within ``timeout`` seconds.

    The helper is used by :func:`stop` after the GUI self-termination
    signal is set; it polls the OS for the child's existence at
    50 ms granularity. ``psutil`` is the standard library equivalent
    of the same check and is already a runtime dependency; the
    helper does not import ``subprocess`` to avoid creating another
    ``Popen`` round-trip on the stop path.
    """
    import psutil

    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


def _wait_for_health(host: str, port: int, *, timeout: float) -> bool:
    """Block until ``/api/v1/health`` returns 200 or ``timeout`` elapses.

    The function uses the standard library
    :mod:`urllib.request` so the test suite can patch
    the request and exercise the timeout path without
    a real HTTP client. The loop sleeps
    :data:`HEALTH_PROBE_INTERVAL` between attempts.
    """
    settings = get_settings()
    url = f"http://{host}:{port}{settings.api_prefix}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - http loopback only
                url, timeout=HEALTH_PROBE_TIMEOUT
            ) as response:
                if response.status in _HEALTH_OK_STATUSES:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(HEALTH_PROBE_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def stop(
    *,
    home: Path,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    force: bool = False,
) -> StopResult:
    """Stop the running instance gracefully.

    The function reads the state file, verifies the
    recorded process identity, and signals the child.
    The behaviour is:

      1. No state file -- nothing to do, returns
         ``"was_not_running"``.
      2. State file present and process identity
         matches -- send graceful termination, wait,
         then escalate to force-kill only on
         ``--force`` and only after the grace period.
      3. State file present and identity does not
         match -- refuse to terminate (PID reuse
         suspected) and return ``"error"``.
      4. State file present and process is gone --
         clean up the state file and return
         ``"was_not_running"``.

    The function never deletes user data: only the
    state file is removed.
    """
    started = time.monotonic()
    state = read_state(home)
    if state is None:
        return StopResult(
            outcome="was_not_running",
            elapsed_seconds=time.monotonic() - started,
            details="no state file under runtime home",
        )
    identity = verify_identity(
        recorded_pid=state.pid,
        recorded_created_at=state.created_at,
        recorded_instance_id=state.instance_id,
        recorded_module=state.module,
    )
    if isinstance(identity, ProcessGone):
        clear_state(home)
        return StopResult(
            outcome="was_not_running",
            elapsed_seconds=time.monotonic() - started,
            details=f"recorded pid {state.pid} is gone; state file cleared",
        )
    if isinstance(identity, ProcessInaccessible):
        return StopResult(
            outcome="error",
            elapsed_seconds=time.monotonic() - started,
            details=(
                f"recorded pid {state.pid} is alive but its identity "
                f"cannot be read: {identity.reason}. Run with administrator "
                "rights or stop the process manually."
            ),
        )
    if isinstance(identity, IdentityMismatch):
        return StopResult(
            outcome="error",
            elapsed_seconds=time.monotonic() - started,
            details=(
                f"recorded pid {state.pid} is alive but its identity does "
                f"not match the recorded instance ({identity.reason}). "
                "Refusing to terminate an unrelated process. Clear the "
                "state file manually if the recorded instance is genuinely gone."
            ),
        )
    # IdentityMatch -- the recorded process is the one
    # we expect. Signal it.
    assert isinstance(identity, IdentityMatch)
    cli_logger = get_cli_logger()
    cli_logger.info("stopping lockverity (pid=%d)", state.pid)
    # If the recorded backend is a CLI subprocess of a running desktop
    # GUI instance, ask the GUI to self-terminate first so the GUI
    # closes the WebView and tears down the pre-bound socket cleanly
    # before the backend terminate signal lands. The signal is
    # best-effort; on non-Windows hosts (or if the GUI is not running)
    # the function is a no-op and the documented state.pid-terminate
    # path below is the authoritative shutdown.
    if state.module == "app.cli._serve":
        if signal_gui_stop():
            cli_logger.info("stop: signaled running desktop GUI to self-terminate")
        # Give the GUI a brief grace period to start its own
        # shutdown (close the WebView, run its ``finally`` block,
        # which calls ``supervisor.shutdown`` to terminate the
        # backend child, then ``cli_runner.stop`` returns success
        # because the recorded pid is already gone). The wait is
        # bounded by ``timeout``; the ``terminate_process`` call
        # below is the authoritative fallback if the GUI does not
        # tear down in time.
        grace_seconds = min(5.0, max(0.5, float(timeout)))
        if _wait_for_backend_exit(state.pid, grace_seconds):
            cli_logger.info("stop: GUI self-terminated the backend cleanly")
            return StopResult(
                outcome="stopped",
                elapsed_seconds=time.monotonic() - started,
                details=f"pid {state.pid} exited after GUI self-termination",
            )
    if is_zombie(state.pid):
        # A zombie is already dead; just clean up the
        # state file. The OS will reap the zombie when
        # its parent calls ``wait`` (or when the
        # parent exits).
        clear_state(home)
        return StopResult(
            outcome="was_not_running",
            elapsed_seconds=time.monotonic() - started,
            details=f"recorded pid {state.pid} is a zombie; state file cleared",
        )
    if not terminate_process(
        state.pid,
        timeout=timeout,
        instance_id=state.instance_id,
    ):
        # Graceful stop did not converge. Either keep
        # waiting (if not --force) or escalate.
        if not force:
            return StopResult(
                outcome="error",
                elapsed_seconds=time.monotonic() - started,
                details=(
                    f"pid {state.pid} did not exit within {timeout:.0f}s; "
                    "pass --force to terminate it."
                ),
            )
        if not force_terminate_process(state.pid, timeout=5.0):
            return StopResult(
                outcome="error",
                elapsed_seconds=time.monotonic() - started,
                details=(
                    f"pid {state.pid} did not exit after SIGKILL; investigate the process manually."
                ),
            )
        clear_state(home)
        return StopResult(
            outcome="force_killed",
            elapsed_seconds=time.monotonic() - started,
            details=f"pid {state.pid} force-killed after grace period",
        )
    clear_state(home)
    return StopResult(
        outcome="stopped",
        elapsed_seconds=time.monotonic() - started,
        details=f"pid {state.pid} exited gracefully",
    )


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def fetch_health(host: str, port: int, *, timeout: float = 3.0) -> dict[str, object] | None:
    """Return the parsed JSON of ``/api/v1/health`` or ``None`` on failure.

    The function uses the standard library so the
    caller does not need to depend on ``httpx`` for a
    single probe.
    """
    settings = get_settings()
    url = f"http://{host}:{port}{settings.api_prefix}/health"
    try:
        with urllib.request.urlopen(  # noqa: S310 - http loopback only
            url, timeout=timeout
        ) as response:
            raw = response.read()
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def format_uptime(created_at: str) -> str:
    """Return a human-readable uptime string from an ISO 8601 timestamp.

    The function accepts the recorded ``created_at``
    from the state file and returns ``"Nd Nh Nm"`` for
    durations greater than a minute, or
    ``"Nm Ns"`` / ``"Ns"`` for shorter durations.
    """
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    now = datetime.now(UTC)
    delta = now - parsed
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "unknown"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------


def open_browser(host: str, port: int) -> bool:
    """Open the local Lockverity URL in the default browser.

    The function returns ``True`` on a successful
    handoff to the OS browser facility, ``False`` on
    failure. The function never opens an
    attacker-controlled URL: it constructs the URL
    from the recorded host/port and refuses any value
    that is not loopback.
    """
    if not is_loopback_host(host):
        return False
    url = f"http://{host}:{port}/"
    try:
        return webbrowser.open(url, new=2, autoraise=True)
    except (OSError, webbrowser.Error):  # type: ignore[attr-defined]
        return False


# Re-export the canonical name for ``commands/open.py``.
_open_browser = open_browser


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


def read_log_tail(log_path: Path, *, lines: int) -> list[str]:
    """Return the last ``lines`` lines of ``log_path``.

    The function is bounded: it reads the file in
    reverse line order using a chunked seek, so a
    10 MiB log file is read in a constant number of
    seeks regardless of the line count. Missing or
    unreadable files return an empty list; the caller
    is responsible for surfacing the missing-log case
    to the operator.
    """
    if not log_path.is_file():
        return []
    if lines <= 0:
        return []
    from collections import deque

    result: deque[str] = deque(maxlen=lines)
    with log_path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        block = 4096
        # Buffer for the partial line straddling the
        # previous read.
        tail = b""
        position = end
        newline = b"\n"
        # Track whether the file ends with a newline
        # so we can suppress the empty trailing
        # segment that ``split`` produces.
        while position > 0 and len(result) < lines:
            read_size = min(block, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size) + tail
            parts = chunk.split(newline)
            tail = parts[0]
            # The last ``parts`` segment is empty
            # when the chunk ends with a newline;
            # treat it as "no data" so the trailing
            # empty string is not returned as a line.
            segments = parts[1:]
            if segments and segments[-1] == b"":
                segments = segments[:-1]
            for part in reversed(segments):
                if len(result) >= lines:
                    break
                result.appendleft(part.decode("utf-8", errors="replace"))
        # ``tail`` holds the partial line that
        # straddled the boundary of the first read;
        # append it only if it is non-empty and we
        # still have room.
        if len(result) < lines and tail:
            result.appendleft(tail.decode("utf-8", errors="replace"))
    return list(result)


def follow_log(log_path: Path, *, lines: int = 100) -> None:
    """Stream ``log_path`` like ``tail -f``.

    The function reads the last ``lines`` lines first,
    then tails the file. ``Ctrl+C`` raises
    :class:`KeyboardInterrupt`; the CLI catches it
    and returns a clean exit code.
    """
    initial = read_log_tail(log_path, lines=lines)
    for line in initial:
        print(line)
    if not log_path.is_file():
        return
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.2)


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_START_TIMEOUT",
    "DEFAULT_STOP_TIMEOUT",
    "DoctorCheck",
    "DoctorReport",
    "PortProbe",
    "StartResult",
    "StopResult",
    "fetch_health",
    "follow_log",
    "format_uptime",
    "is_loopback_host",
    "open_browser",
    "probe_port",
    "read_log_tail",
    "run_migrations",
    "start",
    "stop",
    "validate_explicit_port",
]
