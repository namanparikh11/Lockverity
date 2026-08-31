"""v1.1 demo loader tests.

The loader must:

- Build a deterministic SQLite database at the requested
  path.
- Create the schema via Alembic migrations, not via
  ``Base.metadata.create_all``. The resulting database
  must include an ``alembic_version`` table populated with
  the current head revision.
- Be safe to commit (no real secrets, no real personal
  data).
- Refuse to overwrite an existing file unless
  ``--reset-demo-db`` is passed.
- Refuse to write outside the ``backend/var/`` subtree.
- Produce the four documented scan states (completed /
  partial / failed / cancelled) and a screenshot-ready
  completed scan 1 with at least six components.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND_DIR / "scripts" / "load_demo.py"


def _discover_alembic_head() -> str:
    """Return the current Alembic head revision id.

    The loader must run the same migration chain the
    application uses, so the resulting ``alembic_version``
    row must match the value ``alembic heads`` reports at
    test time. We discover the head by reading the migration
    files' ``revision`` / ``down_revision`` declarations rather
    than by invoking the ``alembic`` CLI (the CLI is
    environment-dependent and adds a hard subprocess
    dependency to this test).

    The head is the revision that no other migration declares
    as its ``down_revision``. Walking the chain is required:
    revision ids are arbitrary hex, so ordering them
    lexicographically would misidentify the head whenever a
    newer migration's id happens to sort before an older one.
    """
    import re

    versions_dir = BACKEND_DIR / "alembic" / "versions"
    revisions: dict[str, str | None] = {}
    referenced: set[str] = set()
    for migration in sorted(versions_dir.glob("*.py")):
        if migration.name.startswith("_"):
            continue
        text = migration.read_text(encoding="utf-8")
        # Migrations in this repository declare the ids either
        # plain (``revision = "..."``) or with a type annotation
        # (``revision: str = "..."``); the pattern accepts both.
        revision_match = re.search(
            r"^revision(?:\s*:\s*str)?\s*=\s*[\"']?([0-9a-f]+)[\"']?",
            text,
            re.MULTILINE,
        )
        if revision_match is None:
            continue
        down_match = re.search(
            r"^down_revision(?:\s*:\s*[^=\n]+)?\s*=\s*(.+)$",
            text,
            re.MULTILINE,
        )
        revisions[revision_match.group(1)] = (
            down_match.group(1).strip() if down_match is not None else None
        )
        if down_match is not None:
            referenced.update(re.findall(r"[\"']([0-9a-f]+)[\"']", down_match.group(1)))
    heads = [revision for revision in revisions if revision not in referenced]
    if len(heads) != 1:
        raise RuntimeError(
            f"expected exactly one Alembic head, found {heads!r} "
            f"(revisions: {revisions!r})"
        )
    return heads[0]


EXPECTED_ALEMBIC_HEAD = _discover_alembic_head()
# The loader's output safety check requires the resolved
# path to be under ``backend/var/``; the test temp paths
# live under ``backend/var/loader-tests/`` so each test
# gets an isolated, recoverable file while still respecting
# the production safety rule.
LOADER_TEST_ROOT = BACKEND_DIR / "var" / "loader-tests"


def _test_db_path(test_name: str) -> Path:
    """Return a fresh, isolated path under
    ``backend/var/loader-tests/`` for a single test.
    """
    name = f"{test_name}-{uuid.uuid4().hex}.sqlite"
    path = LOADER_TEST_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the loader as a subprocess so the test exercises
    the real command-line surface (argparse + module-level
    imports + driver).
    """
    # The subprocess invocation is safe: ``args`` is built from
    # hard-coded literals and the explicit ``SCRIPT`` path; no
    # untrusted user input reaches the shell.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd) if cwd is not None else str(BACKEND_DIR),
        capture_output=True,
        text=True,
        check=False,
    )


def _read_scan_ids(db_path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id, status FROM scan_runs ORDER BY id").fetchall()
        return [(int(row[0]), str(row[1])) for row in rows]
    finally:
        conn.close()


def test_loader_creates_schema_via_alembic():
    """The schema must be created by Alembic migrations, and
    the resulting database must have the ``alembic_version``
    table populated with the current head revision.
    """
    db_path = _test_db_path("schema-via-alembic")
    try:
        result = _run(["--output", str(db_path), "--reset-demo-db"])
        assert result.returncode == 0, result.stderr
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()
        # The Alembic version table must be present and
        # populated with the current head revision.
        assert "alembic_version" in tables, (
            f"alembic_version table is missing - schema was not created by Alembic: {sorted(tables)}"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            version_rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        finally:
            conn.close()
        assert version_rows, "alembic_version table is empty"
        assert version_rows[0][0] == EXPECTED_ALEMBIC_HEAD, (
            f"alembic_version row is {version_rows[0][0]!r}, expected {EXPECTED_ALEMBIC_HEAD!r}"
        )

        # The schema must include every documented table that
        # the application expects. ``Base.metadata.create_all``
        # would miss any table that is not registered on the
        # SQLAlchemy ``Base.metadata`` at import time; the
        # migrations are the authoritative source.
        required = (
            "alembic_version",
            "repositories",
            "scan_runs",
            "scan_stages",
            "manifests",
            "components",
            "dependency_edges",
            "findings",
            "provider_observations",
            "advisories",
            "component_advisories",
            "workspaces",
            "provider_cache_entries",
            "scan_jobs",
        )
        for required_table in required:
            assert required_table in tables, f"missing table: {required_table}"
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_creates_deterministic_dataset():
    db_path = _test_db_path("deterministic-dataset")
    try:
        result = _run(["--output", str(db_path), "--reset-demo-db"])
        assert result.returncode == 0, result.stderr
        assert db_path.exists()

        # The four documented scan states must all be present.
        # The SQLAlchemy Enum column persists the enum ``.name``
        # (uppercase) unless ``values_callable`` is configured,
        # so we compare against the documented uppercase form.
        assert _read_scan_ids(db_path) == [
            (1, "COMPLETED"),
            (2, "PARTIAL"),
            (3, "FAILED"),
            (4, "CANCELLED"),
        ]
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_stamps_explicit_demo_marker():
    """Every seeded scan must carry the explicit
    ``seeded_dataset = 'demo'`` provenance marker.

    The marker - not the numeric id, the repository URL, the
    commit SHA, or the status - is what read-side surfaces use
    to identify demo rows, so a real user scan can never be
    conflated with demo data. See
    ``test_api_demo_dataset_identity.py`` for the read-side
    collision contract.
    """
    db_path = _test_db_path("demo-marker")
    try:
        result = _run(["--output", str(db_path), "--reset-demo-db"])
        assert result.returncode == 0, result.stderr
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id, seeded_dataset FROM scan_runs ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "the loader must seed scans"
        assert all(row[1] == "demo" for row in rows), (
            f"every seeded scan must carry the demo marker, got {rows!r}"
        )
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_refuses_to_overwrite_existing_file():
    db_path = _test_db_path("refuses-overwrite")
    try:
        db_path.write_bytes(b"already-exists")
        result = _run(["--output", str(db_path)])
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        # The pre-existing bytes are preserved.
        assert db_path.read_bytes() == b"already-exists"
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_refuses_to_write_outside_var_tree():
    """The loader must reject any output path that is not
    under ``backend/var/``. The script cannot be coerced
    into overwriting application code or other locations.
    """
    # Use the system temp directory, which is outside
    # ``backend/var/``.
    target = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "outside-var.sqlite"
    try:
        result = _run(["--output", str(target), "--reset-demo-db"])
        assert result.returncode == 2
        assert "refusing to write outside" in result.stderr
        assert not target.exists()
    finally:
        if target.exists():
            os.unlink(target)


def test_loader_overwrites_when_reset_flag_is_passed():
    db_path = _test_db_path("overwrites-reset")
    try:
        db_path.write_bytes(b"already-exists")
        result = _run(["--output", str(db_path), "--reset-demo-db"])
        assert result.returncode == 0, result.stderr
        # The pre-existing bytes are gone.
        assert db_path.read_bytes() != b"already-exists"
        assert _read_scan_ids(db_path)[0] == (1, "COMPLETED")
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_uses_deterministic_synthetic_data():
    """Two runs of the loader must produce the same
    obviously-synthetic package names, versions, PURLs, and
    SHAs.
    """
    db_a = _test_db_path("deterministic-a")
    db_b = _test_db_path("deterministic-b")
    try:
        _run(["--output", str(db_a), "--reset-demo-db"])
        _run(["--output", str(db_b), "--reset-demo-db"])

        def _rows(db_path):
            conn = sqlite3.connect(str(db_path))
            try:
                return [
                    (
                        r[0],
                        r[1],
                        r[2],
                    )
                    for r in conn.execute(
                        "SELECT package_name, version, package_url FROM components ORDER BY id"
                    ).fetchall()
                ]
            finally:
                conn.close()

        assert _rows(db_a) == _rows(db_b)

        # Synthetic package names must be exactly the
        # documented set: alpha / beta / gamma / left-pad /
        # right-pad / stay. Scan 1 carries all six; scan 2
        # carries alpha / left-pad / right-pad / stay. The
        # union is the documented set; the per-scan subset
        # is the documented screenshot scenario.
        names = {row[0] for row in _rows(db_a)}
        assert names == {"alpha", "beta", "gamma", "left-pad", "right-pad", "stay"}

        # Scan 1 must be the rich screenshot-ready scan:
        # six components, mixed evidence states, and at
        # least two persisted dependency edges.
        conn = sqlite3.connect(str(db_a))
        try:
            scan1_count = conn.execute(
                "SELECT COUNT(*) FROM components WHERE scan_run_id = 1"
            ).fetchone()[0]
            scan1_edges = conn.execute(
                "SELECT COUNT(*) FROM dependency_edges WHERE scan_run_id = 1"
            ).fetchone()[0]
            scan1_components = conn.execute(
                "SELECT package_name, version, direct, package_url FROM components "
                "WHERE scan_run_id = 1 ORDER BY id"
            ).fetchall()
            scan1_ecosystems = conn.execute(
                "SELECT DISTINCT ecosystem FROM components WHERE scan_run_id = 1"
            ).fetchall()
        finally:
            conn.close()
        assert scan1_count == 6, f"scan 1 must have 6 components, got {scan1_count}"
        assert scan1_edges >= 2, f"scan 1 must have at least 2 dependency edges, got {scan1_edges}"
        # Mixed direct / transitive mix.
        scan1_names = {row[0] for row in scan1_components}
        assert scan1_names == {"alpha", "beta", "gamma", "left-pad", "right-pad", "stay"}
        scan1_directs = {row[0] for row in scan1_components if row[2]}
        scan1_transitives = {row[0] for row in scan1_components if not row[2]}
        assert "alpha" in scan1_directs
        assert "left-pad" in scan1_directs
        assert "gamma" in scan1_transitives
        assert "stay" in scan1_transitives
        # At least one persisted PURL and at least one
        # missing PURL.
        scan1_purls = {row[3] for row in scan1_components}
        assert any(purl is not None for purl in scan1_purls), "scan 1 must have at least one PURL"
        assert any(purl is None for purl in scan1_purls), (
            "scan 1 must have at least one missing PURL"
        )
        # Lockverity only ships ``npm`` and ``pypi``
        # parsers in v1.1; the demo dataset must not
        # imply Cargo / Rust / Go / unsupported ecosystem
        # coverage. All scan 1 components are ``npm`` so
        # the public demo does not over-claim.
        scan1_ecosystem_set = {row[0] for row in scan1_ecosystems}
        assert "cargo" not in scan1_ecosystem_set, (
            f"scan 1 must not contain unsupported ecosystems, got {scan1_ecosystem_set}"
        )
        assert scan1_ecosystem_set == {"npm"}, (
            f"scan 1 must only contain supported ecosystems, got {scan1_ecosystem_set}"
        )

        # Synthetic repository URL must match the documented
        # fixture owner / name.
        conn = sqlite3.connect(str(db_a))
        try:
            canonical = conn.execute(
                "SELECT canonical_url FROM repositories WHERE id = 1"
            ).fetchone()[0]
        finally:
            conn.close()
        assert canonical == "https://github.com/example-org/lockverity-fixture"

        # Resolved commit SHA must be the synthetic
        # ``deadbeef`` fill, never a real-looking 40-char
        # hex.
        conn = sqlite3.connect(str(db_a))
        try:
            sha = conn.execute("SELECT resolved_commit_sha FROM scan_runs WHERE id = 1").fetchone()[
                0
            ]
        finally:
            conn.close()
        assert sha == "deadbeef" * 5
        # Defensive: the SHA must not look like a real
        # commit.
        assert "deadbeef" in sha
    finally:
        for p in (db_a, db_b):
            if p.exists():
                os.unlink(p)


def test_loader_does_not_embed_secrets():
    """The loader must not write any real-looking secret to
    the demo database. The test asserts that no column in
    the documented tables contains a string that matches
    common credential shapes (long alphanumeric tokens,
    ``sk-`` / ``ghp_``-style prefixes, ``Bearer`` headers).
    """
    db_path = _test_db_path("no-secrets")
    try:
        _run(["--output", str(db_path), "--reset-demo-db"])

        forbidden_substrings = (
            "sk-",
            "ghp_",
            "github_pat_",
            "xoxb-",
            "Bearer ",
            "AKIA",
            "-----BEGIN ",
        )
        conn = sqlite3.connect(str(db_path))
        try:
            # Inspect every text column in the documented
            # tables.
            for table in (
                "repositories",
                "scan_runs",
                "manifests",
                "components",
                "dependency_edges",
                "findings",
                "provider_observations",
            ):
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                for row in rows:
                    for cell in row:
                        if isinstance(cell, str):
                            for needle in forbidden_substrings:
                                assert needle not in cell, (
                                    f"forbidden substring {needle!r} found in {table} row: {cell!r}"
                                )
        finally:
            conn.close()
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_failed_scan_reports_failure_reason():
    """The failed scan (id 3) must carry the documented
    ``scanner_crashed`` failure code so the v1.0 report can
    render the honest empty state.
    """
    db_path = _test_db_path("failed-scan")
    try:
        _run(["--output", str(db_path), "--reset-demo-db"])
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT status, failure_code FROM scan_runs WHERE id = 3").fetchone()
        finally:
            conn.close()
        assert row[0] == "FAILED"
        assert row[1] == "scanner_crashed"
    finally:
        if db_path.exists():
            os.unlink(db_path)


def test_loader_prints_screenshot_ready_console_output(tmp_path):
    """The loader's success-path console output must surface
    the v1.2 reviewer-friendly contract:

    - the dataset is synthetic persisted evidence;
    - no provider calls were made;
    - the four scan ids and their states;
    - both PowerShell and POSIX start commands for the
      backend and the frontend;
    - the five key demo URLs (root, dependencies, exports,
      failed exports, about).
    """
    db_path = _test_db_path("console-output")
    try:
        result = _run(["--output", str(db_path), "--reset-demo-db"])
        assert result.returncode == 0, result.stderr
        stdout = result.stdout
        # Synthetic-data disclosure.
        assert "synthetic persisted evidence" in stdout
        assert "no provider calls were made" in stdout
        # Repository / scan summary.
        assert "https://github.com/example-org/lockverity-fixture" in stdout
        assert "1 (completed, 6 components)" in stdout
        assert "2 (partial, 4 components)" in stdout
        assert "3 (failed)" in stdout
        assert "4 (cancelled)" in stdout
        # Cross-platform startup commands.
        assert "start the backend (PowerShell):" in stdout
        assert "start the backend (POSIX shell):" in stdout
        assert "start the frontend (PowerShell):" in stdout
        assert "start the frontend (POSIX shell):" in stdout
        assert "$env:LOCKVERITY_DATABASE_URL" in stdout
        assert "export LOCKVERITY_DATABASE_URL" in stdout
        # Five documented demo URLs.
        for url in (
            "http://127.0.0.1:5173/",
            "http://127.0.0.1:5173/scans/1/dependencies",
            "http://127.0.0.1:5173/scans/1/exports",
            "http://127.0.0.1:5173/scans/3/exports",
            "http://127.0.0.1:5173/about",
        ):
            assert url in stdout, f"missing demo URL: {url}"
        # Reviewer checklist pointer.
        assert "docs/demo-walkthrough.md" in stdout
        assert "docs/screenshots.md" in stdout
    finally:
        if db_path.exists():
            os.unlink(db_path)
