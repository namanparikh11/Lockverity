"""Regression tests for the ``b8d2f4a6c1e9`` alembic migration.

The migration adds the nullable ``scan_runs.seeded_dataset``
column plus its covering index: the explicit provenance marker
that identifies seeded demo rows. The invariants pinned here:

- A fresh ``alembic upgrade head`` produces the column and the
  index, and stamps the new head revision.
- The migration is purely additive: rows that predate it keep a
  NULL marker (by definition they are not seeded demo data).
- The migration is reversible: downgrade to the previous head
  drops both the column and the index; re-upgrade restores
  them.

The read-side collision contract (a real user scan at a
historically "demo" numeric id is never selected as demo data)
is covered in ``test_api_demo_dataset_identity.py``.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
PREVIOUS_HEAD = "f6a7b8c9d0e1"
EXPECTED_HEAD = "b8d2f4a6c1e9"


def _alembic(db_path: Path, *args: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOCKVERITY_DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(  # noqa: S603 - alembic executable + args are constants
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=str(BACKEND_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _current_version(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    return None if row is None else str(row[0])


def _scan_run_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(scan_runs)").fetchall()}
    finally:
        conn.close()


def _scan_run_indexes(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute("PRAGMA index_list(scan_runs)").fetchall()}
    finally:
        conn.close()


def _insert_scan(db_path: Path) -> None:
    """Insert one pre-marker scan row at the previous-head schema."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO repositories ("
            "source_type, provider, owner, name, canonical_url, "
            "default_branch, visibility, archived, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "github",
                "github",
                "example-org",
                "lockverity-fixture",
                "https://github.com/example-org/lockverity-fixture",
                "main",
                "public",
                0,
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        repo_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO scan_runs ("
            "repository_id, status, trigger_type, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                repo_id,
                "COMPLETED",
                "manual",
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:00.000000",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_upgrade_adds_column_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.sqlite"
    _alembic(db_path, "upgrade", "head")
    assert _current_version(db_path) == EXPECTED_HEAD
    assert "seeded_dataset" in _scan_run_columns(db_path)
    assert "ix_scan_runs_seeded_dataset" in _scan_run_indexes(db_path)


def test_upgrade_leaves_pre_existing_rows_unmarked(tmp_path: Path) -> None:
    """Rows created before the migration keep a NULL marker: the
    migration is purely additive and never rewrites history.
    """
    db_path = tmp_path / "additive.sqlite"
    _alembic(db_path, "upgrade", PREVIOUS_HEAD)
    _insert_scan(db_path)
    assert "seeded_dataset" not in _scan_run_columns(db_path)
    _alembic(db_path, "upgrade", "head")
    conn = sqlite3.connect(str(db_path))
    try:
        markers = conn.execute("SELECT seeded_dataset FROM scan_runs").fetchall()
    finally:
        conn.close()
    assert markers == [(None,)]


def test_downgrade_drops_column_and_index_and_reupgrade_restores(tmp_path: Path) -> None:
    db_path = tmp_path / "reversible.sqlite"
    _alembic(db_path, "upgrade", "head")
    assert _current_version(db_path) == EXPECTED_HEAD
    _alembic(db_path, "downgrade", PREVIOUS_HEAD)
    assert _current_version(db_path) == PREVIOUS_HEAD
    assert "seeded_dataset" not in _scan_run_columns(db_path)
    assert "ix_scan_runs_seeded_dataset" not in _scan_run_indexes(db_path)
    _alembic(db_path, "upgrade", "head")
    assert _current_version(db_path) == EXPECTED_HEAD
    assert "seeded_dataset" in _scan_run_columns(db_path)
    assert "ix_scan_runs_seeded_dataset" in _scan_run_indexes(db_path)
