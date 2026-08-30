"""Race-free loopback port reservation for the GUI launcher.

The Lockverity desktop application (``Lockverity.exe``)
must own a loopback backend port. The legacy behaviour
hard-coded a fixed port (``8000``); a hostile or
benign-wrong application already bound to that port made
the GUI refuse to start. The new behaviour is to ask the
operating system to assign an unused loopback port from
the ``IANA ephemeral`` range and use that exact port
for the owned backend.

The class is intentionally narrow: it returns a
already-bound ``socket.socket`` together with the
port the OS assigned. The caller is responsible for
either:

  - keeping the socket alive for the lifetime of the
    child server (the ``DesktopBackendSupervisor``
    does this on POSIX), or

  - transferring the bound socket into the child
    process via :func:`share_socket_to_subprocess` /
    :func:`reconstruct_shared_socket` (the
    cross-process Windows path).

Critical race-safety contract
=============================

This module NEVER implements the
``probe-free-port → close-probe-socket → later-bind``
pattern. Every port returned by :func:`reserve_loopback_port`
is owned by a live :class:`socket.socket` the OS will not
hand to another process while the socket stays open. The
caller either keeps the socket alive or transfers it to
the child; nothing else.

A normal Windows desktop GUI must never ask the user to
free a port, choose a port, kill a process, or wait for
a collision to time out. This module is the single
chokepoint that delivers on that contract.
"""

from __future__ import annotations

import os
import socket
import sys
from typing import Final

# The host used for all GUI backend binds. The class
# refuses to bind any host that is not the documented
# IPv4 loopback address. ``0.0.0.0`` is explicitly
# rejected; Lockverity's GUI backend is never reachable
# from a non-loopback address.
LOOPBACK_HOST: Final[str] = "127.0.0.1"


class PortReservationError(RuntimeError):
    """Raised when the OS refuses to allocate a loopback port."""


def is_loopback_host(host: str) -> bool:
    """Return ``True`` iff ``host`` is a loopback address.

    The check is conservative: only literal
    ``127.0.0.1`` and ``::1`` are accepted. Hostnames
    (including ``localhost``) are rejected so a
    misconfigured environment variable cannot lead to
    binding on a routable interface.
    """
    return host == LOOPBACK_HOST or host == "::1"


def reserve_loopback_port(*, host: str = LOOPBACK_HOST) -> tuple[socket.socket, int]:
    """Reserve an OS-assigned loopback port on ``host``.

    The function calls ``bind((host, 0))`` so the kernel
    picks the port. The returned socket is bound,
    listening, and ready to accept one connection. The
    caller may use the socket directly, transfer it to a
    child, or close it to release the port.

    The function does NOT:

      - probe an arbitrary port by opening then closing
        a connection (TOCTOU race);
      - bind ``0.0.0.0`` (would expose the GUI to
        non-loopback networks);
      - require a non-loopback host argument.

    The ``SO_REUSEADDR`` flag is **not** set; the bind
    is a fresh ephemeral allocation, never a re-bind of
    a TIME_WAIT port. The kernel will not hand the same
    port to another process while the returned socket
    stays open.
    """
    if not is_loopback_host(host):
        raise PortReservationError(
            f"refusing to reserve a port on {host!r}: the GUI backend "
            "must bind a loopback address (127.0.0.1 or ::1)."
        )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
    except OSError as exc:
        sock.close()
        raise PortReservationError(f"could not reserve a loopback port on {host!r}: {exc}") from exc
    sock.listen(128)
    _bound_host, bound_port = sock.getsockname()[:2]
    return sock, int(bound_port)


# ---------------------------------------------------------------------------
# Inter-process socket transfer
# ---------------------------------------------------------------------------
#
# The desktop launcher owns the reserved socket in the
# ``Lockverity.exe`` process. The Uvicorn child server
# runs in a child process launched via
# :mod:`subprocess`. The socket must be transferred to
# the child so the same kernel binding serves the
# child; passing the port number and rebinding in the
# child would briefly release the port and could be
# stolen by another process. The two transfer
# mechanisms are:
#
#   - **POSIX**: ``socket.fileno()`` plus the
#     ``subprocess`` ``pass_fds`` argument. The child
#     reconstructs the socket with
#     :func:`socket.socket` and the inherited fd.
#
#   - **Windows**: ``socket.share()`` returns a portable
#     shareable handle blob. The child reconstructs
#     the socket with :func:`socket.fromshare`.


def share_socket_to_subprocess(sock: socket.socket, target_pid: int) -> bytes:
    """Return a portable handle for ``sock`` for ``target_pid``.

    On Windows the function delegates to
    :meth:`socket.socket.share` and returns the
    resulting bytes blob. The :class:`socket.socket.share`
    call requires a target process id so the kernel
    can duplicate the underlying handle into the
    recipient's process space. The caller is expected
    to spawn the child first, then call this function
    with the child PID, then write the resulting blob
    to the child's stdin so the child can call
    :func:`reconstruct_shared_socket`.

    On POSIX the function returns the file descriptor
    number encoded as ASCII bytes so the same call
    site works on every platform. The ``target_pid``
    argument is unused on POSIX (file descriptor
    inheritance is process-tree-wide) but the
    signature stays the same so the caller does not
    need platform branches.
    """
    if sys.platform == "win32":
        return bytes(sock.share(int(target_pid)))
    return str(sock.fileno()).encode("ascii")


def reconstruct_shared_socket(blob: bytes) -> socket.socket:
    """Reconstruct the socket from ``blob`` on the child side.

    The function is the inverse of
    :func:`share_socket_to_subprocess`. On Windows the
    blob is the raw output of :meth:`socket.socket.share`
    and the function delegates to
    :meth:`socket.fromshare`. On POSIX the blob is the
    ASCII-encoded file descriptor number and the
    function returns ``socket.socket(fileno=fd)``.

    The returned socket owns the kernel binding; the
    caller is expected to pass it to
    :func:`uvicorn.Server.serve` via the ``sockets``
    argument.
    """
    if sys.platform == "win32":
        return socket.fromshare(blob)
    fd = int(blob.decode("ascii").strip())
    return socket.socket(fileno=fd)


def mark_socket_inheritable(sock: socket.socket) -> int:
    """Mark ``sock`` as inheritable across ``subprocess`` on POSIX.

    On Windows the function is a no-op because
    :func:`share_socket_to_subprocess` uses
    :meth:`socket.socket.share` which produces a portable
    handle; the subprocess only needs the bytes.

    On POSIX the function sets the close-on-exec flag to
    ``False`` and returns the integer file descriptor.
    The caller passes the fd to :class:`subprocess.Popen`
    via the ``pass_fds`` argument. The child then
    reconstructs the socket with
    :func:`reconstruct_shared_socket`.

    The function is intentionally narrow: it does not
    touch the socket's binding state, listen backlog, or
    any other option. The caller is expected to have
    obtained the socket from :func:`reserve_loopback_port`.
    """
    fd = sock.fileno()
    if sys.platform != "win32":
        os.set_inheritable(fd, True)
    return fd


__all__ = [
    "LOOPBACK_HOST",
    "PortReservationError",
    "is_loopback_host",
    "mark_socket_inheritable",
    "reconstruct_shared_socket",
    "reserve_loopback_port",
    "share_socket_to_subprocess",
]
