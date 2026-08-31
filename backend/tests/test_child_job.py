"""Tests for parent-death containment of the owned backend child.

The runner already reaps its child on every path that unwinds through
Python: a readiness failure (see ``test_cli_port_lifecycle``), a graceful
``stop``, a ``Ctrl+C``, the desktop launcher's ``finally``. None of those run
when the supervisor is killed outright -- Task Manager "End task", a native
WebView2 crash, logoff, an external harness killing the tree's root.

Before :mod:`app.cli.child_job` the child simply outlived that: still
listening on its loopback port, still holding the database open, and
unreachable afterwards because the next launch reserves a *fresh* dynamic
port and writes a *fresh* state file, so nothing recorded points at the
orphan. Repeated launch/kill cycles accumulated one live backend each.

What is pinned here:

  1. the foreground supervisor contains the child it spawns, and releases
     the job on every exit path;
  2. containment is best-effort -- a host that cannot provide it still
     starts normally;
  3. detached (background) starts are deliberately *not* contained, because
     they are designed to outlive the CLI that launched them; and
  4. on Windows the containment actually works, and is scoped to the PID the
     supervisor assigned -- it cannot reach anything else.

Every test uses an isolated runtime home and only ever signals processes it
created itself. The operator's real Lockverity instance is never touched.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar

import psutil
import pytest
from app.cli import runner
from app.cli.child_job import OwnedChildJob
from app.core.config import get_settings

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="job objects are a Windows containment primitive",
)


def wait_until(predicate: Callable[[], bool], *, timeout: float) -> bool:
    """Poll ``predicate`` until it holds or ``timeout`` elapses.

    Bounded polling, never a bare sleep sized to "long enough": the test
    fails on the timeout rather than on a machine that happened to be slow.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh runtime home; the real one is never touched."""
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


@pytest.fixture
def spawn_sleeper() -> Iterator[Callable[[], subprocess.Popen[bytes]]]:
    """Spawn throwaway child processes, guaranteeing none outlives the test."""
    started: list[subprocess.Popen[bytes]] = []

    def _spawn() -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(proc)
        return proc

    try:
        yield _spawn
    finally:
        # Reap the whole tree: on a host whose interpreter is a redirector
        # stub, ``proc.pid`` is the stub and the real interpreter is its
        # child, so killing only ``proc`` would leave a process behind.
        for proc in started:
            with contextlib.suppress(psutil.Error):
                for descendant in psutil.Process(proc.pid).children(recursive=True):
                    with contextlib.suppress(psutil.Error):
                        descendant.kill()
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# The supervisor wires containment around the child it spawns
# ---------------------------------------------------------------------------


class _RecordingJob:
    """Records the containment calls the runner makes."""

    instances: ClassVar[list[_RecordingJob]] = []

    def __init__(self) -> None:
        self.opened = False
        self.contained: list[int] = []
        self.closed = 0
        self.open_result = True
        _RecordingJob.instances.append(self)

    def open(self) -> bool:
        self.opened = True
        return self.open_result

    def contain(self, pid: int) -> bool:
        self.contained.append(pid)
        return True

    def close(self) -> None:
        self.closed += 1


class _FakeChild:
    """Stand-in for the spawned ``app.cli._serve`` process."""

    def __init__(self, pid: int, returncode: int = 0) -> None:
        self.pid = pid
        self.stdin = None
        self.returncode = returncode
        self.terminated = False

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


class _SubprocessShim:
    """``runner.subprocess`` with only ``Popen`` replaced."""

    def __init__(self, child: _FakeChild) -> None:
        self._child = child
        self.argv: list[str] | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(subprocess, name)

    def Popen(self, argv: list[str], **kwargs: object) -> _FakeChild:
        self.argv = argv
        return self._child


@pytest.fixture
def foreground_harness(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_dist: Path,
) -> Callable[..., object]:
    """Drive ``runner.start`` in foreground mode without a real child."""

    def _run(home: Path, *, pid: int = 313131, open_result: bool = True, healthy: bool = True):
        _RecordingJob.instances.clear()
        child = _FakeChild(pid)
        shim = _SubprocessShim(child)

        def _job_factory() -> _RecordingJob:
            job = _RecordingJob()
            job.open_result = open_result
            return job

        monkeypatch.setattr(runner, "run_migrations", lambda url: None)
        monkeypatch.setattr(runner, "subprocess", shim)
        monkeypatch.setattr(runner, "OwnedChildJob", _job_factory)
        monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: healthy)
        monkeypatch.setattr(runner, "_read_child_creation_time", lambda pid: time.time())
        monkeypatch.setattr(runner, "_install_foreground_signal_handlers", lambda: None)
        monkeypatch.setattr(runner, "terminate_process", lambda *a, **k: True)
        monkeypatch.setattr(runner, "force_terminate_process", lambda *a, **k: True)
        return runner.start(
            home=home,
            host="127.0.0.1",
            port=8321,
            frontend_dist=synthetic_dist,
            foreground=True,
            database_url="sqlite:///:memory:",
            timeout=0.5,
        ), child

    return _run


def test_foreground_start_contains_the_child_it_spawned(
    isolated_home: Path,
    foreground_harness: Callable[..., object],
) -> None:
    """The job is opened and the spawned PID -- and only it -- assigned."""
    foreground_harness(isolated_home, pid=987654)

    assert len(_RecordingJob.instances) == 1
    job = _RecordingJob.instances[0]
    assert job.opened is True
    assert job.contained == [987654]


def test_foreground_start_releases_the_job_on_the_success_path(
    isolated_home: Path,
    foreground_harness: Callable[..., object],
) -> None:
    """The handle is not leaked when the child exits normally."""
    foreground_harness(isolated_home)
    assert _RecordingJob.instances[0].closed == 1


def test_foreground_start_releases_the_job_when_startup_fails(
    isolated_home: Path,
    foreground_harness: Callable[..., object],
) -> None:
    """A readiness failure still runs the release.

    Closing the job on the way out is what makes the guarantee symmetric:
    if any branch ever failed to reap the child, the close reaps it instead
    of leaving it behind.
    """
    with pytest.raises(RuntimeError, match="did not report healthy"):
        foreground_harness(isolated_home, healthy=False)

    assert _RecordingJob.instances[0].closed == 1


def test_start_succeeds_when_containment_is_unavailable(
    isolated_home: Path,
    foreground_harness: Callable[..., object],
) -> None:
    """Containment is a safety net, never a startup requirement.

    A host that refuses the job object -- an old Windows, a restrictive
    policy, a non-Windows platform -- must still start the backend.
    """
    result, _child = foreground_harness(isolated_home, open_result=False)

    job = _RecordingJob.instances[0]
    assert job.opened is True
    assert job.contained == []
    assert result.health_check_ok is True


def test_detached_start_is_not_contained(
    isolated_home: Path,
    synthetic_dist: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background starts are designed to outlive the CLI that launched them.

    ``lockverity start`` (no ``--foreground``) daemonises deliberately, and
    the operator stops it later with ``lockverity stop``. Containing it would
    kill the server the moment the launching shell exited.
    """
    _RecordingJob.instances.clear()
    child = _FakeChild(pid=222333)

    monkeypatch.setattr(runner, "run_migrations", lambda url: None)
    monkeypatch.setattr(runner, "OwnedChildJob", _RecordingJob)
    monkeypatch.setattr(runner, "_launch_detached", lambda **kwargs: (child, object()))
    monkeypatch.setattr(runner, "_wait_for_health", lambda *a, **k: True)
    monkeypatch.setattr(runner, "_read_child_creation_time", lambda pid: time.time())

    runner.start(
        home=isolated_home,
        host="127.0.0.1",
        port=8322,
        frontend_dist=synthetic_dist,
        foreground=False,
        database_url="sqlite:///:memory:",
        timeout=0.5,
    )

    assert _RecordingJob.instances == []


# ---------------------------------------------------------------------------
# The primitive itself
# ---------------------------------------------------------------------------


def test_job_is_inert_until_opened() -> None:
    job = OwnedChildJob()
    assert job.active is False
    # Containing without a job is a no-op, not a crash.
    assert job.contain(999999) is False
    job.close()


@pytest.mark.skipif(sys.platform == "win32", reason="covers the non-Windows no-op contract")
def test_job_is_a_no_op_off_windows() -> None:
    job = OwnedChildJob()
    assert job.open() is False
    assert job.active is False


@WINDOWS_ONLY
def test_open_creates_an_active_job() -> None:
    job = OwnedChildJob()
    try:
        assert job.open() is True
        assert job.active is True
    finally:
        job.close()
    assert job.active is False


@WINDOWS_ONLY
def test_contained_child_dies_when_the_owner_releases_the_job(
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
) -> None:
    """The regression: closing the job reaps the contained child.

    This is what the kernel does for us when the supervisor is killed
    outright -- the handle closes with the process, and the backend goes
    with it instead of surviving as an unreachable orphan.
    """
    proc = spawn_sleeper()
    job = OwnedChildJob()
    assert job.open() is True
    assert job.contain(proc.pid) is True

    assert psutil.pid_exists(proc.pid)
    job.close()

    assert wait_until(lambda: proc.poll() is not None, timeout=15.0), (
        f"contained child {proc.pid} outlived the job"
    )


@WINDOWS_ONLY
def test_uncontained_child_survives_an_unrelated_job_closing(
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
) -> None:
    """The control for the test above.

    Without it, a child that died for any other reason would make the
    containment test pass vacuously.
    """
    proc = spawn_sleeper()
    job = OwnedChildJob()
    assert job.open() is True
    job.close()

    assert not wait_until(lambda: proc.poll() is not None, timeout=2.0)
    assert proc.poll() is None


@WINDOWS_ONLY
def test_job_only_reaps_the_process_it_was_given(
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
) -> None:
    """Containment is by PID the supervisor assigned, never by image name.

    Two identical processes, one contained: the other must be untouched.
    This is the property that keeps cleanup from reaching a second
    Lockverity installation or another user's backend.
    """
    contained = spawn_sleeper()
    bystander = spawn_sleeper()

    job = OwnedChildJob()
    assert job.open() is True
    assert job.contain(contained.pid) is True
    job.close()

    assert wait_until(lambda: contained.poll() is not None, timeout=15.0)
    assert bystander.poll() is None, "an unrelated process was reaped"


@WINDOWS_ONLY
def test_contain_reports_failure_for_a_process_that_is_gone(
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
) -> None:
    """A dead PID cannot be contained, and saying so is not an exception."""
    proc = spawn_sleeper()
    proc.kill()
    proc.wait(timeout=10)

    job = OwnedChildJob()
    try:
        assert job.open() is True
        assert job.contain(proc.pid) is False
    finally:
        job.close()
