"""Tests for the CLI / launcher log timestamp formatter.

The v2.1.3 final QA review found that the runtime log was
emitting local wall-clock time with a trailing ``Z`` suffix,
which is the ISO-8601 marker for UTC. The mislabel made log
correlation with the already-UTC runtime-state timestamps
and external evidence misleading.

The fix is in :mod:`app.cli.logging_setup`: the rotating
file handler's formatter now uses :func:`time.gmtime` so
``%(asctime)s`` is honest UTC and the ``Z`` suffix is
accurate.

These tests pin the corrected behaviour:

- the formatter emits a true UTC timestamp, not local time
  with a misleading ``Z``;
- the emitted ``Z`` corresponds to UTC;
- the output is a valid ISO-8601-style prefix;
- the rest of the log record (``levelname``, ``name``,
  ``message``) is preserved.
"""

from __future__ import annotations

import datetime
import logging
import re

import app.cli.logging_setup as logging_setup


def _make_record(created_epoch: float, message: str = "hello world") -> logging.LogRecord:
    """Build a synthetic LogRecord with a controlled ``created`` epoch."""
    record = logging.LogRecord(
        name="lockverity.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.created = created_epoch
    record.msecs = (created_epoch - int(created_epoch)) * 1000
    return record


def test_make_formatter_emits_utc_timestamp_with_z_suffix() -> None:
    """The formatter's leading token is a real UTC ISO-8601 + Z."""
    formatter = logging_setup._make_formatter()
    # 2026-08-31T12:34:56Z -- a real UTC instant.
    epoch = datetime.datetime(2026, 8, 31, 12, 34, 56, tzinfo=datetime.UTC).timestamp()
    record = _make_record(epoch)
    rendered = formatter.format(record)
    assert rendered.startswith("2026-08-31T12:34:56Z "), rendered


def test_make_formatter_uses_gmtime_not_localtime() -> None:
    """The same epoch must be rendered as UTC, not as the host's local time.

    The test is timezone-independent. On a UTC host, both
    ``time.gmtime`` and ``time.localtime`` would agree; the
    assertion still holds. On a non-UTC host, the previous
    (buggy) formatter would have rendered the *local* time
    with a ``Z`` suffix, and this assertion would catch the
    regression.
    """
    formatter = logging_setup._make_formatter()
    epoch = datetime.datetime(2026, 8, 31, 12, 34, 56, tzinfo=datetime.UTC).timestamp()
    record = _make_record(epoch)
    rendered = formatter.format(record)
    # The expected UTC rendering. If the formatter used
    # local time on a host east of UTC, the rendered hour
    # would be > 12 and the assertion would fail.
    match = re.match(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z "
        r"(?P<level>\w+) (?P<name>[\w.]+): (?P<msg>.+)$",
        rendered,
    )
    assert match is not None, rendered
    ts = match.group("ts")
    # The emitted timestamp MUST equal the UTC wall time, not
    # a shifted local-time string. Cross-check against gmtime
    # of the same epoch so the assertion is timezone-stable.
    import time as _time

    gm = _time.gmtime(epoch)
    expected_ts = _time.strftime("%Y-%m-%dT%H:%M:%S", gm)
    assert ts == expected_ts, f"expected UTC {expected_ts!r}, got {ts!r}"


def test_make_formatter_preserves_level_name_and_message() -> None:
    """The level, logger name, and message fields are unchanged."""
    formatter = logging_setup._make_formatter()
    epoch = datetime.datetime(2026, 8, 31, 12, 34, 56, tzinfo=datetime.UTC).timestamp()
    record = logging.LogRecord(
        name="lockverity.demo",
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg="watch out: %s",
        args=("sketch",),
        exc_info=None,
    )
    record.created = epoch
    record.msecs = 0
    rendered = formatter.format(record)
    assert rendered.startswith("2026-08-31T12:34:56Z "), rendered
    assert "WARNING lockverity.demo: watch out: sketch" in rendered, rendered


def test_make_formatter_prefix_is_valid_iso_8601_utc() -> None:
    """The leading ``Z``-terminated token parses as a valid UTC ISO-8601 instant."""
    formatter = logging_setup._make_formatter()
    epoch = datetime.datetime(2026, 8, 31, 12, 34, 56, tzinfo=datetime.UTC).timestamp()
    record = _make_record(epoch)
    rendered = formatter.format(record)
    ts_token = rendered.split(" ", 1)[0]
    # ISO-8601 / RFC 3339 UTC form: YYYY-MM-DDTHH:MM:SSZ
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts_token), ts_token
    parsed = datetime.datetime.fromisoformat(ts_token.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == datetime.timedelta(0)


def test_log_format_constant_still_asserts_z_suffix() -> None:
    """The static ``LOG_FORMAT`` keeps its truthful UTC ``Z`` suffix."""
    assert logging_setup.LOG_FORMAT.startswith("%(asctime)sZ ")
    assert logging_setup.LOG_DATEFMT == "%Y-%m-%dT%H:%M:%S"
