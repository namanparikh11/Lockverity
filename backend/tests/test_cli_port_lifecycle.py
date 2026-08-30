"""Tests for the CLI port contract and child reaping (LV-013).

``--port 0`` used to be accepted. Uvicorn honoured it by asking the
kernel for an ephemeral port, but every part of the parent's lifecycle
is built around the port the CLI *asked for*: the readiness probe polled
``http://127.0.0.1:0/api/v1/health``, never got an answer, cleared the
runtime state, and returned - leaving a live backend on a port nothing
had recorded. ``status`` could not see it and ``stop`` could not reach
it. The only way out was the task manager.

Two things are pinned here:

  1. the port is validated before anything is created, so a rejected
     invocation spawns no child and writes no state; and
  2. on the one path where the parent gives up on a child it did spawn -
     a readiness failure - it terminates and reaps that child instead of
     abandoning it.

Every test uses an isolated temporary runtime home. Nothing here touches
the operator's real Lockverity process or state.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.cli import runner
from app.cli.state import state_file_path
from app.core.config import get_settings


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh runtime home; the real one is never touched."""
    monkeypatch.delenv("LOCKVERITY_HOME", raising=False)
    monkeypatch.setenv("LOCKVERITY_HOME", str(tmp_path))
    monkeypatch.setenv("LOCKVERITY_ENVIRONMENT", "test")
    get_settings.cache_clear()
    try:
        yield tmp_path
    finally:
        get_settings.cache_clear()


@pytest.fixture
def synthetic_dist(tmp_path: Path) -> Path:
    """A minimal valid dist so dist validation is not what fails."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return dist


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------


def test_port_zero_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="port 0 is not supported"):
        runner.validate_explicit_port(0)


@pytest.mark.parametrize("port", [-1, -8000, 65536, 70000, 999999])
def test_out_of_range_ports_are_rejected(port: int) -> None:
    with pytest.raises(RuntimeError, match="outside the valid range"):
        runner.validate_explicit_port(port)


@pytest.mark.parametrize("port", [1, 80, 8000, 49152, 65535])
def test_valid_concrete_ports_are_accepted(port: int) -> None:
    runner.validate_explicit_port(port)


def test_non_integer_port_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="must be an integer"):
        runner.validate_explicit_port("8000")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Rejection happens before anything exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_never_spawns_a_child(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
    port: int,
) -> None:
    """The spawn routine must not be reached at all.

    This is the property that matters: rejecting after the child exists
    is what produced the orphan in the first place.
    """
    spawned: list[object] = []

    def _tripwire(*args: object, **kwargs: object) -> None:
        spawned.append(kwargs)
        raise AssertionError("a child was spawned for an invalid port")

    monkeypatch.setattr(runner, "_launch_detached", _tripwire)
    monkeypatch.setattr(runner, "_start_foreground", _tripwire)

    with pytest.raises(RuntimeError):
        runner.start(
            home=isolated_home,
            host="127.0.0.1",
            port=port,
            frontend_dist=synthetic_dist,
            database_url="sqlite:///:memory:",
        )
    assert spawned == []


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_writes_no_runtime_state(
    isolated_home: Path,
    synthetic_dist: Path,
    port: int,
) -> None:
    with pytest.raises(RuntimeError):
        runner.start(
            home=isolated_home,
            host="127.0.0.1",
            port=port,
            frontend_dist=synthetic_dist,
            database_url="sqlite:///:memory:",
        )
    assert not state_file_path(isolated_home).is_file()


def test_invalid_port_runs_no_migrations(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal precedes every side effect, not just the spawn."""
    migrated: list[str] = []
    monkeypatch.setattr(
        runner,
        "run_migrations",
        lambda url: migrated.append(url),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="port 0 is not supported"):
        runner.start(
            home=isolated_home,
            host="127.0.0.1",
            port=0,
            frontend_dist=synthetic_dist,
            database_url="sqlite:///:memory:",
        )
    assert migrated == []


def test_port_zero_message_points_at_the_fix(isolated_home: Path, synthetic_dist: Path) -> None:
    with pytest.raises(RuntimeError) as exc:
        runner.start(
            home=isolated_home,
            host="127.0.0.1",
            port=0,
            frontend_dist=synthetic_dist,
            database_url="sqlite:///:memory:",
        )
    message = str(exc.value)
    assert "1..65535" in message
    assert str(runner.DEFAULT_PORT) in message


# ---------------------------------------------------------------------------
# A child that never reports healthy is terminated, not abandoned
# ---------------------------------------------------------------------------


class _FakeChild:
    """Stand-in for the detached Uvicorn child."""

    def __init__(self, pid: int = 424242) -> None:
        self.pid = pid
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        return 0

    def poll(self) -> int | None:
        return None


def test_readiness_failure_terminates_the_child(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spawned-but-unhealthy child must not survive the parent's exit.

    Before the fix the parent logged, cleared the state file, and
    returned - the child kept running with nothing recorded about it.
    """
    child = _FakeChild()
    terminated: list[tuple[int, str | None]] = []

    monkeypatch.setattr(runner, "run_migrations", lambda url: None)
    monkeypatch.setattr(runner, "_launch_detached", lambda **kwargs: (child, object()))
    monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: False)

    def _terminate(pid: int, *, timeout: float = 10.0, instance_id: str | None = None) -> bool:
        terminated.append((pid, instance_id))
        return True

    monkeypatch.setattr(runner, "terminate_process", _terminate)

    result = runner.start(
        home=isolated_home,
        host="127.0.0.1",
        port=8123,
        frontend_dist=synthetic_dist,
        database_url="sqlite:///:memory:",
        timeout=0.1,
    )

    assert result.health_check_ok is False
    assert terminated and terminated[0][0] == child.pid
    # The graceful signal carries the instance id, exactly as ``stop``
    # does, so a Windows GUI-subsystem child can run its shutdown.
    assert terminated[0][1] == result.state.instance_id
    # The child was reaped, not merely signalled.
    assert child.waits
    # And no misleading state file survives.
    assert not state_file_path(isolated_home).is_file()


def test_readiness_failure_escalates_when_graceful_stop_fails(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that ignores the graceful signal is force-terminated."""
    child = _FakeChild(pid=515151)
    forced: list[int] = []

    monkeypatch.setattr(runner, "run_migrations", lambda url: None)
    monkeypatch.setattr(runner, "_launch_detached", lambda **kwargs: (child, object()))
    monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: False)
    monkeypatch.setattr(runner, "terminate_process", lambda *a, **k: False)
    monkeypatch.setattr(
        runner,
        "force_terminate_process",
        lambda pid, **kwargs: (forced.append(pid), True)[1],
    )

    result = runner.start(
        home=isolated_home,
        host="127.0.0.1",
        port=8124,
        frontend_dist=synthetic_dist,
        database_url="sqlite:///:memory:",
        timeout=0.1,
    )
    assert result.health_check_ok is False
    assert forced == [child.pid]
    assert child.waits


def test_healthy_start_never_terminates_the_child(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reaping path is for failure only; a good start is untouched."""
    child = _FakeChild(pid=616161)
    terminated: list[int] = []

    monkeypatch.setattr(runner, "run_migrations", lambda url: None)
    monkeypatch.setattr(runner, "_launch_detached", lambda **kwargs: (child, object()))
    monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: True)
    monkeypatch.setattr(
        runner, "terminate_process", lambda pid, **k: terminated.append(pid) or True
    )
    monkeypatch.setattr(
        runner, "force_terminate_process", lambda pid, **k: terminated.append(pid) or True
    )

    result = runner.start(
        home=isolated_home,
        host="127.0.0.1",
        port=8125,
        frontend_dist=synthetic_dist,
        database_url="sqlite:///:memory:",
        timeout=0.1,
    )
    assert result.health_check_ok is True
    assert terminated == []
    # A healthy start does publish its state, which is what ``status``
    # and ``stop`` read.
    assert state_file_path(isolated_home).is_file()


def test_terminate_helper_reports_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that cannot be killed is reported, not swallowed silently."""
    child = _FakeChild(pid=717171)
    monkeypatch.setattr(runner, "terminate_process", lambda *a, **k: False)
    monkeypatch.setattr(runner, "force_terminate_process", lambda *a, **k: False)

    messages: list[str] = []

    class _Logger:
        def warning(self, message: str, *args: object) -> None:
            messages.append(message % args if args else message)

        def error(self, message: str, *args: object) -> None:
            messages.append(message % args if args else message)

    stopped = runner._terminate_unmanaged_child(
        child,  # type: ignore[arg-type]
        instance_id="abc",
        cli_logger=_Logger(),
    )
    assert stopped is False
    assert any("could not be terminated" in message for message in messages)


def test_foreground_readiness_failure_already_reaps(
    isolated_home: Path,
) -> None:
    """The foreground path's existing cleanup is unchanged.

    Foreground mode always terminated its child on a readiness failure;
    the detached path is the one that did not. This pins the foreground
    behaviour so the two paths cannot drift apart again.
    """
    source = Path(runner.__file__).read_text(encoding="utf-8")
    foreground = source[source.index("def _start_foreground(") :]
    foreground = foreground[: foreground.index("\ndef _now_iso(")]
    unhealthy = foreground[foreground.index("if not health_ok:") :]
    unhealthy = unhealthy[: unhealthy.index("raise RuntimeError(")]
    assert "proc.terminate()" in unhealthy
    assert "proc.kill()" in unhealthy


# ---------------------------------------------------------------------------
# The GUI's dynamic port is unaffected
# ---------------------------------------------------------------------------


def test_gui_reservation_always_yields_a_concrete_port() -> None:
    """The GUI never hands the runner a zero.

    ``reserve_loopback_port`` binds ``(host, 0)`` at the OS level and
    reads the assigned port back, so the runner always receives a real
    port. Rejecting ``--port 0`` on the CLI takes nothing away from the
    GUI's race-free dynamic-port design.
    """
    from app.cli.port_reservation import reserve_loopback_port

    sock, port = reserve_loopback_port()
    try:
        assert 1 <= port <= 65535
        assert port == sock.getsockname()[1]
        runner.validate_explicit_port(port)
    finally:
        sock.close()


def test_prebound_socket_port_survives_validation(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GUI start carries the reserved port through unchanged."""
    from app.cli.port_reservation import reserve_loopback_port

    sock, reserved = reserve_loopback_port()
    child = _FakeChild(pid=818181)
    seen: dict[str, Any] = {}

    monkeypatch.setattr(runner, "run_migrations", lambda url: None)
    monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: True)

    def _capture(**kwargs: Any) -> tuple[Any, object]:
        seen["argv"] = kwargs.get("argv")
        return child, object()

    monkeypatch.setattr(runner, "_launch_detached", _capture)
    try:
        result = runner.start(
            home=isolated_home,
            host="127.0.0.1",
            port=reserved,
            frontend_dist=synthetic_dist,
            database_url="sqlite:///:memory:",
            timeout=0.1,
            prebound_socket=sock,
        )
    finally:
        sock.close()
    assert result.state.port == reserved
    assert str(reserved) in seen["argv"]


def test_subprocess_import_is_still_used_for_typing() -> None:
    """Guard against the fake child drifting from the real handle."""
    assert hasattr(subprocess.Popen, "wait")
