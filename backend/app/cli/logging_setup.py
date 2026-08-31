"""Bounded rotating runtime log for the ``lockverity`` CLI.

The ``start`` command uses this module to wire a
:class:`logging.handlers.RotatingFileHandler` against the
``logs/lockverity.log`` file under the runtime home. The
handler is bounded: every rotated file is at most
``max_bytes`` (10 MiB by default) and at most
``backup_count`` (5 by default) generations are kept. The
total disk footprint is therefore at most
``max_bytes * (backup_count + 1)`` bytes.

The setup function is idempotent: re-invoking it with
the same ``log_path`` returns the existing handler so
``start`` can call it from both the foreground and
background code paths without double-attaching.

The handler never writes secrets: the log records
level, time, logger name, and message. The CLI does
not configure a ``logging.Filter`` for the LogRecord
``args`` or ``msg`` because the application log
messages never embed a token or password; the
boundary is enforced by the *application* code
that emits the log records, not by this handler.
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_BACKUP_COUNT = 5

# Log format: a single line per record with a UTC ISO
# timestamp, the level, the logger name, and the message.
# The format is intentionally short so each rotated file
# holds the maximum number of records.
#
# The ``Z`` literal in the format string asserts that the
# ``%(asctime)s`` token is true UTC. The default
# :class:`logging.Formatter` populates ``%(asctime)s`` via
# :func:`time.localtime`, which on a non-UTC host would
# mislabel wall-clock local time as UTC under the ``Z``
# suffix. The handler built by :func:`_build_handler`
# therefore uses :func:`_make_formatter` which swaps the
# formatter's ``converter`` to :func:`time.gmtime` so the
# emitted timestamp and the trailing ``Z`` are both honest
# UTC.
LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def _make_formatter() -> logging.Formatter:
    """Build the file log formatter with a true-UTC timestamp.

    The default :attr:`logging.Formatter.converter` is
    :func:`time.localtime`. The ``LOG_FORMAT`` literal ``Z``
    suffix asserts UTC; the formatter therefore swaps the
    converter to :func:`time.gmtime` so the emitted
    ``%(asctime)s`` and the ``Z`` are both honest UTC.

    The function is exported (and is the only place the
    converter swap happens) so the logging tests can
    exercise the formatter end-to-end without re-deriving
    the contract from a private constant.
    """
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    formatter.converter = time.gmtime
    return formatter


# Logger name used by the CLI for its own messages (start
# / stop / status output). The application loggers
# (``lockverity``, ``uvicorn``, ``alembic``, ...) keep
# their own names; the CLI logger is additive so the
# operator can filter the CLI messages out if needed.
CLI_LOGGER_NAME = "lockverity.cli"
# Logger name used by the desktop launcher. The launcher
# runs in the GUI process; without an explicit file handler
# attached to this logger, the launcher's
# :func:`logging.Logger.exception` calls would propagate
# to the root logger and (on a windowless Windows process)
# be silently dropped. The launcher therefore configures
# its own file handler against the same ``lockverity.log``
# file the CLI uses so the operator sees a single
# chronological log across CLI + GUI lifecycles.
LAUNCHER_LOGGER_NAME = "lockverity.launcher"

# Logger names the launcher routes to the rotating file.
# ``pywebview`` and ``webview`` are the underlying GUI
# library names; ``clr_loader`` is the pythonnet runtime
# used by pywebview's Edge Chromium backend on Windows.
# ``urllib3`` is included so any certificate / proxy
# exception raised while pywebview initialises the
# WebView2 controller is captured.
LAUNCHER_ATTACHED_LOGGERS: tuple[str, ...] = (
    CLI_LOGGER_NAME,
    LAUNCHER_LOGGER_NAME,
    "pywebview",
    "webview",
    "clr_loader",
    "urllib3",
)


def _build_handler(
    log_path: Path,
    *,
    max_bytes: int,
    backup_count: int,
    level: int,
) -> RotatingFileHandler:
    """Build the rotating file handler shared by the wrappers."""
    handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(level)
    handler.setFormatter(_make_formatter())
    return handler


def _handler_already_attached(logger: logging.Logger, log_path: Path) -> bool:
    """Return True if ``logger`` already has a rotating handler for ``log_path``."""
    for existing in list(logger.handlers):
        if (
            isinstance(existing, RotatingFileHandler)
            and Path(getattr(existing, "baseFilename", "")) == log_path
        ):
            existing.setLevel(logger.level or logging.INFO)
            return True
    return False


def configure_logging(
    log_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
) -> logging.Logger:
    """Attach a rotating file handler to the CLI logger.

    The function is idempotent: a second call with the
    same ``log_path`` returns the existing logger without
    attaching a second handler. The handler uses UTF-8
    encoding so the log file is portable across hosts.

    The CLI logger sets ``propagate = False`` so a parent
    ``lockverity`` handler does not double-write the same
    record. The CLI logger is the only place the CLI
    writes its own log records; the CLI's own output
    never propagates to the root logger.
    """
    logger = logging.getLogger(CLI_LOGGER_NAME)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if _handler_already_attached(logger, log_path):
        logger.setLevel(level)
        return logger
    handler = _build_handler(
        log_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
        level=level,
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def configure_launcher_logging(
    log_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
) -> logging.Logger:
    """Attach a rotating file handler to the root logger.

    The desktop launcher is a separate process from the
    CLI's :func:`configure_logging` invocation. Without an
    explicit file handler on the root logger, any
    :func:`logging.Logger.exception` call inside the
    WebView startup path is silently dropped on a
    windowless Windows process because there is no
    ``StreamHandler`` and no parent logger with a file
    handler.

    The function attaches the rotating file handler to
    the **root logger** rather than to every individual
    logger. The root is the single propagation target:
    every logger under ``lockverity.*``, ``pywebview``,
    ``webview``, ``clr_loader``, ``urllib3``, etc. has
    ``propagate = True`` (the Python default), so the
    root handler sees every record once. Attaching
    handlers to multiple specific loggers would
    double-write the same record via the parent
    propagation chain.

    The function is idempotent: a second call with the
    same ``log_path`` does not attach a second handler.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if _handler_already_attached(root, log_path):
        root.setLevel(level)
        return logging.getLogger(LAUNCHER_LOGGER_NAME)
    handler = _build_handler(
        log_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
        level=level,
    )
    root.addHandler(handler)
    root.setLevel(level)
    return logging.getLogger(LAUNCHER_LOGGER_NAME)


def get_cli_logger() -> logging.Logger:
    """Return the CLI logger without attaching a handler.

    The function is used by callers that want to emit
    a structured log record through the same channel
    the ``start`` command configured, without re-running
    the handler configuration.
    """
    return logging.getLogger(CLI_LOGGER_NAME)


__all__ = [
    "CLI_LOGGER_NAME",
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_MAX_BYTES",
    "LAUNCHER_ATTACHED_LOGGERS",
    "LAUNCHER_LOGGER_NAME",
    "configure_launcher_logging",
    "configure_logging",
    "get_cli_logger",
]
