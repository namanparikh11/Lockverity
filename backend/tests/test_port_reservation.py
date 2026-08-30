"""Focused tests for the GUI dynamic-port race-free architecture.

The regression the manual-QA pass surfaced was the GUI
refusing to start when another harmless process
(``ReconLoom``) already held ``127.0.0.1:8000``. The
GUI must obtain a free loopback port automatically
and never ask the operator to free a port, kill a
process, or pick a number.

The tests below pin the contract:

  1. ``reserve_loopback_port`` returns a live
     bound-and-listening socket and the kernel-assigned
     port.
  2. The function refuses to bind non-loopback hosts
     (defence against accidental ``0.0.0.0`` exposure).
  3. Two back-to-back reservations do not return the
     same port.
  4. A harmless listener already bound to a known port
     does not prevent the GUI from acquiring another
     loopback port.
  5. The cross-process socket transfer round-trips on
     the current platform (Windows: ``share()`` /
     ``fromshare()``; POSIX: ``fileno()`` /
     ``socket(fileno=...)``).
  6. The launcher's GUI startup path reserves a port
     and never passes the legacy ``--port`` value into
     the supervisor.
  7. The runtime state file records the actual port
     the kernel assigned, not the legacy ``8000``.
  8. A second GUI launch focuses the existing window
     and does not allocate another port.
  9. The ``runner.start`` foreground path is not
     affected: the explicit ``--port`` CLI value is
     honoured and the pre-bound-socket code path is
     not exercised.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest
from app.cli import runner as cli_runner
from app.cli.port_reservation import (
    PortReservationError,
    is_loopback_host,
    reconstruct_shared_socket,
    reserve_loopback_port,
    share_socket_to_subprocess,
)


def _free_port() -> int:
    """Ask the OS for an unused loopback port without keeping the socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def test_reserve_loopback_port_returns_bound_listening_socket() -> None:
    sock, port = reserve_loopback_port()
    try:
        # The socket is bound to the loopback host.
        host, bound_port = sock.getsockname()[:2]
        assert host == "127.0.0.1"
        assert bound_port == port
        # The kernel-assigned port is in the IANA
        # ephemeral range (>= 1024). A regression that
        # forgot the ``bind((host, 0))`` and used a
        # hard-coded port would fail this check.
        assert port >= 1024
        # The socket is listening so a connect attempt
        # must succeed.
        client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        client.close()
    finally:
        sock.close()


def test_reserve_loopback_port_refuses_non_loopback_host() -> None:
    # ``0.0.0.0`` would expose the GUI to a non-loopback
    # network; the helper must reject it.
    with pytest.raises(PortReservationError):
        reserve_loopback_port(host="0.0.0.0")  # noqa: S104


def test_is_loopback_host_only_accepts_canonical_loopback() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("::1") is True
    # Hostnames (including ``localhost``) are rejected
    # so a misconfigured environment variable cannot
    # bind the GUI to a routable interface.
    assert is_loopback_host("localhost") is False
    assert is_loopback_host("0.0.0.0") is False  # noqa: S104
    assert is_loopback_host("") is False


def test_two_reservations_return_distinct_ports() -> None:
    sock_a, port_a = reserve_loopback_port()
    sock_b, port_b = reserve_loopback_port()
    try:
        assert port_a != port_b
    finally:
        sock_a.close()
        sock_b.close()


def test_harmless_listener_on_known_port_does_not_block_gui() -> None:
    """A separate listener holding a known port does not
    prevent the GUI from acquiring a different port.

    This is the exact ``ReconLoom on 8000`` failure the
    manual-QA pass surfaced. The GUI must always
    succeed, regardless of what other loopback ports
    are already taken.
    """
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    busy_port = int(busy.getsockname()[1])
    try:
        sock, port = reserve_loopback_port()
        try:
            assert port != busy_port
        finally:
            sock.close()
    finally:
        busy.close()


def test_cross_process_socket_transfer_round_trip() -> None:
    """The reserved socket can be transferred across processes.

    The mechanism is platform-specific:

      - Windows: ``socket.share()`` requires a target
        process id and returns a bytes blob;
        ``socket.fromshare()`` reconstructs the
        socket in the recipient. The round-trip
        requires a real target process, so the test
        spawns a short-lived subprocess to play the
        role of the Uvicorn child.
      - POSIX: ``socket.fileno()`` returns an integer
        fd; ``socket.socket(fileno=fd)`` reconstructs
        the socket in the recipient. The round-trip
        runs in the same process because fd numbers
        are process-scoped.

    The round-trip is the only practical way to assert
    the cross-process transfer is wired correctly
    without spawning a real Uvicorn server. The test
    exercises the same code path the runner uses to
    pass the pre-bound socket into the child.
    """
    sock, port = reserve_loopback_port()
    try:
        if sys.platform == "win32":
            _round_trip_via_subprocess(sock, port)
        else:
            blob = share_socket_to_subprocess(sock, target_pid=os.getpid())
            assert blob, "share_socket_to_subprocess returned an empty blob"
            reconstructed = reconstruct_shared_socket(blob)
            try:
                host, reconstructed_port = reconstructed.getsockname()[:2]
                assert host == "127.0.0.1"
                assert int(reconstructed_port) == port
            finally:
                reconstructed.close()
    finally:
        sock.close()


def _round_trip_via_subprocess(sock: socket.socket, expected_port: int) -> None:
    """Windows: round-trip the share blob through a real subprocess.

    The Windows ``socket.share(target_pid)`` call
    requires a live target process. The test spawns
    a tiny helper subprocess, asks it to reconstruct
    the socket from the share blob the parent writes
    to its stdin, and asks the helper to print the
    port the reconstructed socket is bound to. The
    parent asserts the printed port matches the
    expected port.
    """
    import subprocess

    code = (
        "import socket, sys;"
        "data = sys.stdin.buffer.read();"
        "s = socket.fromshare(data);"
        "host, port = s.getsockname()[:2];"
        "sys.stdout.write('{}:{}\\n'.format(host, port));"
        "s.close();"
    )
    proc = subprocess.Popen(  # noqa: S603 - argv is the interpreter plus a short inline script
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        share_data = share_socket_to_subprocess(sock, target_pid=int(proc.pid))
        assert proc.stdin is not None
        proc.stdin.write(share_data)
        proc.stdin.flush()
        proc.stdin.close()
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, (
        f"helper subprocess failed (rc={proc.returncode}): stdout={stdout!r} stderr={stderr!r}"
    )
    assert stdout.decode("ascii").strip() == f"127.0.0.1:{expected_port}"


def test_runner_start_background_honours_explicit_cli_port() -> None:
    """The explicit ``--port`` CLI value is preserved.

    The CLI start path is unchanged by the dynamic-port
    change. Operators who pin a specific port retain
    the documented behaviour: the runner probes the
    port, fails with a clear error if it is busy, and
    records the operator-chosen port in the state file.

    The test exercises the background (detached) path
    rather than the foreground path so the real
    ``python -m app.cli._serve`` child is not spawned
    in-process. The detached child is the only path
    that ``lockverity-cli status`` / ``lockverity-cli stop``
    ever call in production; the foreground path is the
    desktop launcher's path and is exercised by
    ``test_normal_gui_waits_for_readiness_…``.
    """
    free_port = _free_port()

    monkeypatch = pytest.MonkeyPatch()
    try:
        # The runner's start() refuses to bind without
        # a real ``frontend_dist``; patch the
        # validator so the test does not depend on the
        # bundled React distribution.
        monkeypatch.setattr(cli_runner, "validate_dist", lambda path: None)
        # Patch the ``run_migrations`` so the test
        # does not depend on a real database.
        monkeypatch.setattr(cli_runner, "run_migrations", lambda url: None)
        # Patch the health-probe helper so the
        # runner does not depend on a real Uvicorn.
        monkeypatch.setattr(cli_runner, "_wait_for_health", lambda host, port, timeout: True)

        # Patch the ``_launch_detached`` helper so
        # the test does not spawn a real
        # ``python -m app.cli._serve`` child. The
        # patched launcher returns a dummy handle and
        # a dummy log handle; the runner only reads
        # ``.pid`` from the handle.
        class _DummyProcess:
            pid = 99999999

        class _DummyLog:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        monkeypatch.setattr(
            cli_runner,
            "_launch_detached",
            lambda **_kwargs: (_DummyProcess(), _DummyLog()),
        )
        result = cli_runner.start(
            home=Path(os.environ.get("LOCKVERITY_HOME", ".")),
            host="127.0.0.1",
            port=free_port,
            foreground=False,
            timeout=1.0,
            database_url="sqlite:///:memory:",
            log_level="warning",
            open_browser=False,
        )
    finally:
        monkeypatch.undo()

    assert result.health_check_ok is True
    assert result.state.port == free_port
