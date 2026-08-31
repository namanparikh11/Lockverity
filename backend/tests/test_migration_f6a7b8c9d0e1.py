"""Regression tests for the v2.0.6 cycle-7-final
``f6a7b8c9d0e1`` alembic migration.

The migration adds ``workspaces.safe_archive_filename`` and
backfills it from the existing ``archive_filename`` column
using a *migration-local* sanitiser. The local sanitiser is
intentionally embedded in the migration revision file
rather than imported from
:func:`app.utils.paths.basename_safely` so a future change
to the application helper cannot retroactively alter the
backfill semantics of this historical migration.

These tests pin the migration-local algorithm's behaviour
against representative inputs (the same coverage the
application helper is tested against in
``test_paths.py``) and pin the database-level invariants:

- The column is added nullable and the index is created.
- The backfill sets ``safe_archive_filename`` to the
  migration-local ``_safe_basename`` of the existing
  ``archive_filename`` value.
- The raw ``archive_filename`` value is never mutated by
  the backfill (the column is read-only from the
  migration's perspective).
- Trusted GitHub provenance in ``archive_filename``
  produces the expected last-path-component in
  ``safe_archive_filename`` (the migration-local
  sanitiser is applied to the existing value, which for
  pre-cycle-7 rows was already the last component).
- The migration is reversible: downgrade drops the
  index and the column; re-upgrade adds them back and
  re-runs the backfill.
- The alembic head remains ``f6a7b8c9d0e1`` after the
  full upgrade / downgrade / re-upgrade cycle.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "f6a7b8c9d0e1_add_workspace_safe_archive_filename.py"
)
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
MIGRATIONS_DIR = BACKEND_ROOT / "alembic"
# The chain head moved forward when the additive
# ``scan_runs.seeded_dataset`` migration landed after this
# one; ``head`` resolves to the current tip, so the cycle
# assertions follow the new head.
EXPECTED_HEAD = "b8d2f4a6c1e9"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_migration_module() -> object:
    """Import the migration file as a module.

    The migration file lives under ``alembic/versions`` and
    is not part of any package. We load it by file path
    so the tests can exercise its ``_safe_basename`` helper
    and its public ``upgrade`` / ``downgrade`` functions.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_test_f6a7b8c9d0e1_migration", str(MIGRATION_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_upgrade_to(
    db_path: Path,
    target: str,
) -> None:
    """Run ``alembic`` to ``target`` against ``db_path``."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOCKVERITY_DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(  # noqa: S603 - alembic executable + args are constants
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "upgrade",
            target,
        ],
        cwd=str(BACKEND_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alembic_downgrade_to(
    db_path: Path,
    target: str,
) -> None:
    """Run ``alembic`` to ``target`` (downgrade) against ``db_path``."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOCKVERITY_DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(  # noqa: S603 - alembic executable + args are constants
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "downgrade",
            target,
        ],
        cwd=str(BACKEND_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alembic_current(db_path: Path) -> str | None:
    """Return the current alembic version stamped in ``db_path``."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOCKVERITY_DATABASE_URL"] = f"sqlite:///{db_path}"
    result = subprocess.run(  # noqa: S603 - alembic executable + args are constants
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "current",
        ],
        cwd=str(BACKEND_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    for line in result.stdout.splitlines():
        # ``alembic current`` prints lines like
        # ``f6a7b8c9d0e1 (head)``.
        stripped = line.strip()
        if not stripped:
            continue
        head = stripped.split()[0]
        if head and all(c in "0123456789abcdef" for c in head):
            return head
    return None


def _insert_legacy_workspace(
    db_path: Path,
    *,
    archive_filename: str | None,
) -> int:
    """Insert a legacy ``workspaces`` row into ``db_path``.

    The row is inserted at the *pre-migration* schema (no
    ``safe_archive_filename`` column). The migration is
    expected to add the column and backfill it.

    The ``workspaces.scan_run_id`` column is a NOT NULL
    foreign key to ``scan_runs.id``. We therefore create a
    matching ``repositories`` row + ``scan_runs`` row first
    so the workspace insert satisfies the schema.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # The schema is at e5f6a7b8c9d0 (pre-migration),
        # which has both repositories and scan_runs
        # already in place.
        cur = conn.execute(
            "INSERT INTO repositories ("
            "source_type, provider, owner, name, canonical_url, "
            "default_branch, visibility, archived, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "uploaded_archive",
                "local_upload",
                f"upload-{archive_filename or 'null'}",
                f"name-{archive_filename or 'null'}",
                f"upload://{archive_filename or 'null'}",
                None,
                "private",
                0,
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        repo_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO scan_runs ("
            "repository_id, status, trigger_type, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                repo_id,
                "completed",
                "upload",
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        scan_id = int(cur.lastrowid)
        # Now insert the workspace row at the pre-migration
        # schema (no safe_archive_filename column yet).
        # The ``workspace_key`` CHECK constraint requires
        # length >= 16.
        workspace_key = f"key-{archive_filename or 'null'}-{scan_id:08d}-pad"
        # The full archive_filename plus padding is well
        # over 16 chars; truncate if needed.
        if len(workspace_key) < 16:
            workspace_key = workspace_key + "-" + "x" * 16
        cur = conn.execute(
            "INSERT INTO workspaces ("
            "scan_run_id, workspace_key, kind, state, archive_filename, "
            "archive_size, file_count, uncompressed_size, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                workspace_key,
                "uploaded_archive",
                "ready",
                archive_filename,
                0,
                0,
                0,
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _select_workspace(
    db_path: Path,
    row_id: int,
) -> tuple[str | None, str | None]:
    """Return ``(archive_filename, safe_archive_filename)`` for ``row_id``."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT archive_filename, safe_archive_filename FROM workspaces WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(f"row {row_id} not found after migration")
    return (row[0], row[1])


# ---------------------------------------------------------------------------
# Migration-local sanitiser unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Normal basename passes through.
        ("package.zip", "package.zip"),
        # Windows absolute path returns only the basename.
        ("C:\\Users\\me\\secret.zip", "secret.zip"),
        ("C:/Users/me/secret.zip", "secret.zip"),
        # POSIX absolute path returns only the basename.
        ("/etc/passwd", "passwd"),
        ("/home/me/private/archive.zip", "archive.zip"),
        # Windows drive-relative path returns the basename.
        ("C:secret.zip", "secret.zip"),
        # Traversal returns the last valid component.
        ("../../etc/passwd", "passwd"),
        ("a/b/../../c.zip", "c.zip"),
        # Mixed separators: the last segment is the basename.
        ("mixed\\path/with\\backslashes.zip", "backslashes.zip"),
        # Empty / whitespace / root returns None.
        ("", None),
        ("   ", None),
        ("/", None),
        ("C:", None),
        ("C:/", None),
        ("C:\\", None),
        # Dot-only paths return None.
        ("..", None),
        ("../", None),
        (".", None),
        # GitHub provenance: last path component is the basename.
        ("github/octocat/Hello-World@abc123.tar.gz", "Hello-World@abc123.tar.gz"),
        (
            "github/torvalds/linux@deadbeefcafebabefeedf00d.tar.gz",
            "linux@deadbeefcafebabefeedf00d.tar.gz",
        ),
        # UNC: share/secret.zip -> secret.zip.
        ("//server/share/secret.zip", "secret.zip"),
        # Non-string input returns None.
        (None, None),
        (123, None),
        ([], None),
        ({}, None),
        # Unicode is NFC-normalised and preserved.
        ("café.zip".replace("café", "cafe\u0301"), "café.zip"),
    ],
)
def test_safe_basename_unit_coverage(raw: object, expected: str | None) -> None:
    """The migration-local ``_safe_basename`` matches the
    application-level contract for every representative
    input class.
    """
    module = _load_migration_module()
    assert module._safe_basename(raw) == expected


def test_safe_basename_truncates_very_long_names() -> None:
    """A name longer than the column size is truncated gracefully."""
    module = _load_migration_module()
    long = "a" * 1000 + ".zip"
    out = module._safe_basename(long)
    assert out is not None
    assert len(out) <= 512
    assert out.endswith(".zip")


def test_safe_basename_is_deterministic() -> None:
    """The same input always produces the same output."""
    module = _load_migration_module()
    for raw in (
        "C:\\Users\\me\\secret.zip",
        "/home/me/private/archive.zip",
        "../../../etc/passwd",
        "github/octocat/Hello-World@abc123.tar.gz",
    ):
        first = module._safe_basename(raw)
        for _ in range(5):
            assert module._safe_basename(raw) == first


# ---------------------------------------------------------------------------
# Migration import isolation
# ---------------------------------------------------------------------------


def test_migration_does_not_import_application_helpers() -> None:
    """The migration revision file must not import mutable
    application utilities (``app.utils.paths``,
    ``app.models``, ``app.services``, ``app.core.config``).

    A future change to any of those modules would
    otherwise retroactively alter the backfill semantics
    of this historical migration. The migration-local
    ``_safe_basename`` is a frozen copy.
    """
    text = MIGRATION_PATH.read_text(encoding="utf-8")
    # ``app.utils.paths`` is the most important forbidden
    # import. The previous code imported
    # ``from app.utils.paths import basename_safely``;
    # that line must be gone.
    assert "from app.utils.paths" not in text
    assert "import app.utils.paths" not in text
    # No other application code may be imported.
    assert "from app.models" not in text
    assert "import app.models" not in text
    assert "from app.services" not in text
    assert "import app.services" not in text
    assert "from app.core" not in text
    assert "import app.core" not in text
    assert "from app.api" not in text
    assert "import app.api" not in text
    # The migration-local helper must be present.
    assert "def _safe_basename" in text


# ---------------------------------------------------------------------------
# Database-level backfill tests
# ---------------------------------------------------------------------------


def _backfill_test_inputs() -> Iterable[tuple[str, str | None]]:
    """Representative (raw, expected_backfilled) pairs."""
    return [
        ("package.zip", "package.zip"),
        ("C:\\Users\\me\\secret.zip", "secret.zip"),
        ("C:/Users/me/secret.zip", "secret.zip"),
        ("/etc/passwd", "passwd"),
        ("/home/me/private/archive.zip", "archive.zip"),
        ("C:secret.zip", "secret.zip"),
        ("../../etc/passwd", "passwd"),
        ("mixed\\path/with\\backslashes.zip", "backslashes.zip"),
        ("", None),
        ("   ", None),
        ("/", None),
        ("C:\\", None),
        ("..", None),
        ("../", None),
        ("github/octocat/Hello-World@abc123.tar.gz", "Hello-World@abc123.tar.gz"),
    ]


def test_backfill_sets_safe_archive_filename_from_existing_value(
    tmp_path: Path,
) -> None:
    """Upgrade a fresh database through every migration; assert
    the column is added; insert representative rows at the
    pre-migration schema; run the new migration in isolation;
    assert the backfill produced the expected values and the
    raw ``archive_filename`` was never mutated.
    """
    db_path = tmp_path / "backfill.sqlite"
    # Step 1: bring the schema up to e5f6a7b8c9d0 (the
    # pre-migration head).
    _alembic_upgrade_to(db_path, "e5f6a7b8c9d0")
    # Step 2: insert representative rows at the pre-migration
    # schema (no safe_archive_filename column yet).
    inserted_ids: list[tuple[int, str, str | None]] = []
    for raw, expected in _backfill_test_inputs():
        row_id = _insert_legacy_workspace(db_path, archive_filename=raw)
        inserted_ids.append((row_id, raw, expected))
    # Step 3: run the new migration.
    _alembic_upgrade_to(db_path, "head")
    # Step 4: assert backfill.
    for row_id, raw, expected in inserted_ids:
        actual_raw, actual_safe = _select_workspace(db_path, row_id)
        # The raw value must never be mutated.
        assert actual_raw == raw, (
            f"raw archive_filename mutated by migration: "
            f"row {row_id} expected {raw!r}, got {actual_raw!r}"
        )
        # The safe basename must match the migration-local
        # expectation.
        assert actual_safe == expected, (
            f"backfill mismatch for row {row_id} raw={raw!r}: "
            f"expected safe={expected!r}, got {actual_safe!r}"
        )


def test_backfill_preserves_github_provenance_verbatim(
    tmp_path: Path,
) -> None:
    """Trusted GitHub provenance in ``archive_filename`` is
    preserved as-is in the raw column; the safe column
    receives the last path component only.
    """
    db_path = tmp_path / "github.sqlite"
    _alembic_upgrade_to(db_path, "e5f6a7b8c9d0")
    provenance = "github/octocat/Hello-World@abc123def456.tar.gz"
    row_id = _insert_legacy_workspace(db_path, archive_filename=provenance)
    _alembic_upgrade_to(db_path, "head")
    actual_raw, actual_safe = _select_workspace(db_path, row_id)
    assert actual_raw == provenance
    assert actual_safe == "Hello-World@abc123def456.tar.gz"


def test_backfill_leaves_null_archive_filename_null_in_safe(
    tmp_path: Path,
) -> None:
    """A legacy row with NULL ``archive_filename`` must have
    NULL ``safe_archive_filename`` after the migration.
    """
    db_path = tmp_path / "null.sqlite"
    _alembic_upgrade_to(db_path, "e5f6a7b8c9d0")
    row_id = _insert_legacy_workspace(db_path, archive_filename=None)
    _alembic_upgrade_to(db_path, "head")
    actual_raw, actual_safe = _select_workspace(db_path, row_id)
    assert actual_raw is None
    assert actual_safe is None


def test_backfill_handles_legacy_unsanitised_values_safely(
    tmp_path: Path,
) -> None:
    """Pre-cycle-7 rows that were stored with a sanitised
    basename (the old code applied ``basename_safely`` to
    both kinds of provenance) get a safe value that matches
    the raw value verbatim.
    """
    db_path = tmp_path / "legacy.sqlite"
    _alembic_upgrade_to(db_path, "e5f6a7b8c9d0")
    row_id = _insert_legacy_workspace(db_path, archive_filename="alpha.zip")
    _alembic_upgrade_to(db_path, "head")
    actual_raw, actual_safe = _select_workspace(db_path, row_id)
    assert actual_raw == "alpha.zip"
    assert actual_safe == "alpha.zip"


# ---------------------------------------------------------------------------
# Reversibility
# ---------------------------------------------------------------------------


def test_downgrade_drops_column_and_index_then_reupgrade_restores(
    tmp_path: Path,
) -> None:
    """The migration is reversible. Downgrade drops the
    index and the column; re-upgrade adds them back and
    re-runs the backfill; the alembic head remains the
    same throughout.
    """
    db_path = tmp_path / "reversible.sqlite"
    _alembic_upgrade_to(db_path, "head")
    assert _alembic_current(db_path) == EXPECTED_HEAD
    # Insert a row at the post-migration schema.
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO repositories ("
            "source_type, provider, owner, name, canonical_url, "
            "default_branch, visibility, archived, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "uploaded_archive",
                "local_upload",
                "upload-rev",
                "name-rev",
                "upload://rev",
                None,
                "private",
                0,
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        repo_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO scan_runs ("
            "repository_id, status, trigger_type, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                repo_id,
                "completed",
                "upload",
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        scan_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO workspaces ("
            "scan_run_id, workspace_key, kind, state, archive_filename, "
            "safe_archive_filename, archive_size, file_count, uncompressed_size, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                f"key-rev-{scan_id:08d}-pad",
                "uploaded_archive",
                "ready",
                "alpha.zip",
                "alpha.zip",
                0,
                0,
                0,
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        conn.commit()
        row_id = int(cur.lastrowid)
    finally:
        conn.close()
    # Downgrade to the pre-migration head.
    _alembic_downgrade_to(db_path, "e5f6a7b8c9d0")
    assert _alembic_current(db_path) == "e5f6a7b8c9d0"
    # The column and index must be gone.
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(workspaces)").fetchall()}
        assert "safe_archive_filename" not in cols
        idx = {r[1] for r in conn.execute("PRAGMA index_list(workspaces)").fetchall()}
        assert "ix_workspaces_safe_archive_filename" not in idx
    finally:
        conn.close()
    # Re-upgrade.
    _alembic_upgrade_to(db_path, "head")
    assert _alembic_current(db_path) == EXPECTED_HEAD
    # The row is back, and the backfill reproduced the safe
    # value (the raw value is unchanged).
    actual_raw, actual_safe = _select_workspace(db_path, row_id)
    assert actual_raw == "alpha.zip"
    assert actual_safe == "alpha.zip"
