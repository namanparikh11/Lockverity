"""Kernel-enforced containment for the backend child the supervisor owns.

Why this module exists
======================

Windows does not tie a child process's lifetime to its parent's. When the
desktop launcher (``Lockverity.exe``) or a ``--foreground`` supervisor exits
*through* its own code it stops and reaps the owned ``app.cli._serve`` child
explicitly: :func:`app.cli.runner.stop` reads the runtime state, verifies the
recorded identity, and signals that exact PID.

Every one of those paths runs Python. None of them runs when the supervisor
dies *without* unwinding:

  - Task Manager "End task" / ``TerminateProcess`` on the launcher;
  - an access violation in the native WebView2 / pywebview layer;
  - the OS terminating the process at logoff or shutdown;
  - an external harness killing the process tree's root.

In all of those cases the owned backend child survived indefinitely: still
listening on its loopback port, still holding the SQLite database open, and
invisible to every later launch. The next launch reserves a *fresh* dynamic
port and writes a *fresh* state file, so
:func:`app.cli.runner._ensure_no_existing_instance` has nothing to match
against and the orphan is never reclaimed. Repeated launch/kill cycles
therefore accumulate live backends, one per cycle.

The mechanism
=============

A Windows Job Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
terminates every process assigned to it when the last handle to the job
closes. The supervisor holds that single handle; a contained child holds
none. So when the supervisor dies for *any* reason -- including
``TerminateProcess``, which no user-mode cleanup can intercept -- the kernel
closes the handle and reaps the child. Grandchildren the backend spawns
inherit the job and are reaped with it.

Ownership, not process names
============================

Containment applies only to a PID this supervisor explicitly assigns after
spawning it. The job never matches on image name, never enumerates
processes, and cannot reach an unrelated ``Lockverity.exe`` -- a second
installation, another user's session, or a backend owned by a different
supervisor. It is strictly narrower than the identity-checked
:func:`app.cli.process.terminate_process` path, not a replacement for it:
graceful, identity-verified shutdown remains the normal route and this is the
backstop for the paths that never reach it.

Best effort by contract
=======================

Containment is a safety net, never a startup requirement. Every failure -- a
non-Windows host, a denied ``OpenProcess``, a policy-restricted job -- is
logged and reported as ``False``; the supervisor starts normally either way.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("lockverity.cli.child_job")

# ``JOBOBJECTINFOCLASS.JobObjectExtendedLimitInformation``
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# ``JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags``: terminate every process in
# the job when the last job handle closes.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# The two rights ``AssignProcessToJobObject`` requires on the target.
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


def _extended_limit_type() -> type:
    """Return the ctypes ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` structure.

    Built inside a function so importing this module on a non-Windows host
    never constructs Windows-shaped structures.
    """
    import ctypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            # ``ULONG_PTR``: pointer-width, not ``DWORD``.
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return ExtendedLimitInformation


class OwnedChildJob:
    """A Windows Job Object that reaps its members when the owner dies.

    Usage is three calls in order::

        job = OwnedChildJob()
        job.open()               # create the kill-on-close job
        proc = subprocess.Popen(argv, ...)
        job.contain(proc.pid)    # assign the child we just spawned
        ...
        job.close()              # reaps anything still contained

    :meth:`close` is what makes the guarantee symmetric: calling it while a
    contained child is still alive terminates that child. Every supervisor
    exit path therefore converges, whether or not it unwinds through Python.

    On a non-Windows host every method is a no-op returning ``False``; POSIX
    already has process groups and the runner's ``start_new_session``
    handling.
    """

    __slots__ = ("_handle",)

    def __init__(self) -> None:
        self._handle: int | None = None

    @property
    def active(self) -> bool:
        """``True`` while a job exists and can contain a child."""
        return self._handle is not None

    def open(self) -> bool:
        """Create the kill-on-close job. Returns ``True`` on success."""
        if sys.platform != "win32":
            return False
        if self._handle is not None:
            return True
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        # An anonymous job: with no name, no other process can open it by
        # name and pull the contained child back out.
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning(
                "child containment unavailable: CreateJobObjectW failed (error=%d)",
                ctypes.get_last_error(),
            )
            return False

        info = _extended_limit_type()()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        configured = kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        if not configured:
            # A job without the kill-on-close limit reaps nothing; keeping it
            # would imply a guarantee it cannot make.
            logger.warning(
                "child containment unavailable: SetInformationJobObject failed (error=%d)",
                ctypes.get_last_error(),
            )
            kernel32.CloseHandle(handle)
            return False
        self._handle = int(handle)
        return True

    def contain(self, pid: int) -> bool:
        """Assign ``pid`` to the job. Returns ``True`` if it is contained.

        Only a PID the caller has just spawned should be passed. The target
        is opened with the minimum rights ``AssignProcessToJobObject``
        requires and that handle is closed again immediately; the job handle
        is the only one kept.
        """
        if self._handle is None:
            return False
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        target = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA,
            False,
            int(pid),
        )
        if not target:
            logger.warning(
                "child containment: OpenProcess failed for pid=%d (error=%d); the "
                "backend will not be reaped if this process is killed abruptly",
                pid,
                ctypes.get_last_error(),
            )
            return False
        try:
            assigned = kernel32.AssignProcessToJobObject(self._handle, target)
        finally:
            kernel32.CloseHandle(target)
        if not assigned:
            logger.warning(
                "child containment: AssignProcessToJobObject failed for pid=%d "
                "(error=%d); the backend will not be reaped if this process is "
                "killed abruptly",
                pid,
                ctypes.get_last_error(),
            )
            return False
        logger.info("child containment: backend pid=%d assigned to the owned job", pid)
        return True

    def close(self) -> None:
        """Close the job handle, terminating anything still contained."""
        if self._handle is None:
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(self._handle)
        self._handle = None


__all__ = ["OwnedChildJob"]
