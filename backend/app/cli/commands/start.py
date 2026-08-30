"""``lockverity start`` subcommand.

Launches the single-port production runtime as a
background process. The command refuses to start if a
healthy instance is already recorded, runs Alembic
migrations, validates the frontend dist, waits for the
health endpoint to respond, and writes the state file.

The start command acquires the cross-platform start
lock so two simultaneous invocations cannot both
launch servers. The lock is released by the
underlying ``runner.start`` when the child process
exits (the lock is bound to the child lifetime).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import __version__
from app.cli import runner
from app.cli.home import resolve_home
from app.static_frontend import FrontendDistError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the ``start`` argparse arguments."""
    parser.add_argument(
        "--host",
        default=runner.DEFAULT_HOST,
        help=(
            "Host to bind. Defaults to 127.0.0.1 (loopback only). "
            "Pass --allow-remote to bind a non-loopback host; the "
            "built-in server does not provide TLS."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=runner.DEFAULT_PORT,
        help="Port to bind (default: 8000).",
    )
    parser.add_argument(
        "--frontend-dist",
        type=Path,
        default=None,
        help=(
            "Path to the built frontend dist directory. Defaults to "
            "the LOCKVERITY_FRONTEND_DIST setting (frontend/dist "
            "relative to the repository root)."
        ),
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help=(
            "Run the server in the current TTY (no daemonisation). "
            "Ctrl+C propagates to the child. The state file is not "
            "written in foreground mode."
        ),
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the local URL in the default browser after startup.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="Uvicorn log level (default: info).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=runner.DEFAULT_START_TIMEOUT,
        help=("Maximum seconds to wait for the health endpoint to respond (default: 30)."),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Override the database URL for this invocation. Defaults "
            "to the LOCKVERITY_DATABASE_URL setting. Use "
            "sqlite:///<path> for a local file."
        ),
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Allow binding a non-loopback host. The built-in server "
            "does not terminate TLS; do not expose the instance "
            "beyond localhost without a reverse proxy."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the result as a single JSON object on stdout.",
    )


def main(args: argparse.Namespace) -> int:
    """Run the ``start`` subcommand.

    Returns the process exit code: 0 on success, non-
    zero on any documented failure (existing instance,
    occupied port, migration failure, health timeout,
    loopback guard).
    """
    home = resolve_home(cli_override=getattr(args, "home", None))
    host = args.host
    if not runner.is_loopback_host(host) and not args.allow_remote:
        print(
            f"ERROR: refusing to bind non-loopback host {host!r}. "
            "Pass --allow-remote to bind a non-loopback host (the "
            "built-in server does not provide TLS).",
            file=sys.stderr,
        )
        return 2
    try:
        result = runner.start(
            home=home,
            host=host,
            port=args.port,
            frontend_dist=args.frontend_dist,
            foreground=args.foreground,
            timeout=args.timeout,
            database_url=args.database_url,
            log_level=args.log_level,
            open_browser=args.open_browser,
        )
    except FrontendDistError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        payload = {
            "instance_id": result.state.instance_id,
            "pid": result.state.pid,
            "host": result.state.host,
            "port": result.state.port,
            "version": result.state.version,
            "home": result.state.home,
            "frontend_dist": result.state.frontend_dist,
            "log_file": result.state.log_file,
            "started_at": result.state.started_at,
            "created_at": result.state.created_at,
            "health_check_ok": result.health_check_ok,
            "elapsed_seconds": result.elapsed_seconds,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if result.health_check_ok:
            print(
                f"OK: lockverity {__version__} ready at "
                f"http://{result.state.host}:{result.state.port} "
                f"(pid={result.state.pid}, "
                f"instance_id={result.state.instance_id}) "
                f"in {result.elapsed_seconds:.1f}s."
            )
        else:
            print(
                f"WARNING: server did not report healthy within "
                f"{args.timeout:.0f}s. The child process "
                f"(pid={result.state.pid}) was terminated rather than "
                f"left running untracked; check the log at "
                f"{result.state.log_file} for details.",
                file=sys.stderr,
            )
            return 3
    return 0


__all__ = ["add_arguments", "main"]
