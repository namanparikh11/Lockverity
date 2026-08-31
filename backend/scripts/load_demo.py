"""Lockverity v1.1 demo loader.

A small, deterministic, public-friendly seed script that
builds a Lockverity demo SQLite database from scratch. The
script is safe to commit because:

- Every package name, version, repository URL, and SHA is
  obviously synthetic. The repository is
  ``https://github.com/example-org/lockverity-fixture``; the
  scan ids are hardcoded; the resolved commit SHA is
  ``deadbeef`` repeated.
- No real secret, no real personal data, no real-looking
  token, no real-looking API key is generated.
- Every persisted column is bounded to a documented enum
  or to one of the synthetic literal strings.
- The schema is created **only** by Alembic migrations -
  ``Base.metadata.create_all`` is never used as the primary
  schema creator. The script invokes the same migration
  chain (``7efc41b356da`` -> ``d4e5f6a7b8c9``) that the
  application uses, so the resulting database is
  byte-equivalent to a fresh ``alembic upgrade head``.

The script is the v1.1 public demo entry point. It replaces
the gitignored ``backend/var/manual-review/review.sqlite``
dataset so the demo does not require a hidden file. The new
file lives under ``backend/var/demo/lockverity-demo.sqlite``
(gitignored) and is created on demand.

Typical usage:

.. code-block:: powershell

   cd backend
   .venv\\Scripts\\python.exe scripts\\load_demo.py --reset-demo-db
   $env:LOCKVERITY_DATABASE_URL = "sqlite:///var/demo/lockverity-demo.sqlite"
   .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765

The script is intentionally small and does not call any
provider or external network. It is a pure projection of
hard-coded fixture data over the SQLAlchemy 2 ORM, on top of
a database whose schema was created by Alembic migrations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make ``app`` importable when this script is run directly
# (``python scripts/load_demo.py`` from the backend directory).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app._version import __version__  # noqa: E402
from app.models.component import (  # noqa: E402
    Component,
    ComponentVersionSource,
)
from app.models.dependency_edge import DependencyEdge  # noqa: E402
from app.models.finding import Finding, FindingCategory  # noqa: E402
from app.models.manifest import Manifest, ManifestParseStatus  # noqa: E402
from app.models.provider_observation import (  # noqa: E402
    ProviderObservation,
    ProviderStatus,
)
from app.models.repository import (  # noqa: E402
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import (  # noqa: E402
    DEMO_SEEDED_DATASET,
    ScanRun,
    ScanStatus,
    ScanTriggerType,
)
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# ---------------------------------------------------------------------
# Constants - every value here is safe to commit.
# ---------------------------------------------------------------------

REPOSITORY_OWNER = "example-org"
REPOSITORY_NAME = "lockverity-fixture"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
REPOSITORY_DESCRIPTION = (
    "Synthetic public fixture repository for the Lockverity "
    "v1.1 demo. All package names, versions, and SHAs are "
    "obviously fake."
)

# The resolved commit SHA is a 40-character hex string. The
# fixture uses the canonical ``deadbeef`` repeated fill, which
# never collides with a real git object.
RESOLVED_COMMIT_SHA = "deadbeef" * 5

# The relative path to the canonical demo SQLite file. The
# path is resolved against the backend directory and is the
# only output the script is allowed to write by default.
DEFAULT_OUTPUT = "var/demo/lockverity-demo.sqlite"

# The path to the alembic configuration. The loader invokes
# ``alembic upgrade head`` against this file to create the
# schema. We never call ``Base.metadata.create_all`` as the
# primary schema creator.
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


# ---------------------------------------------------------------------
# Output safety - the loader only writes under ``var/``.
# ---------------------------------------------------------------------


def _is_safe_output(path: Path) -> bool:
    """Return True if ``path`` is under ``var/`` in the backend.

    The demo loader is intentionally constrained to the
    gitignored ``var/`` subtree so the operator can never
    overwrite application code, source files, or arbitrary
    paths by accident. A path outside the ``var/`` subtree
    must be rejected.
    """
    try:
        path.relative_to(_BACKEND_DIR / "var")
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------
# Schema creation via Alembic migrations.
# ---------------------------------------------------------------------


def _run_alembic_upgrade(database_url: str) -> None:
    """Create the demo database schema via Alembic migrations.

    The function programmatically configures Alembic with
    the supplied ``database_url`` so the same migration chain
    used by the application is also used here. The
    ``Base.metadata.create_all`` shortcut is deliberately
    avoided so the demo DB schema stays in lock-step with
    the application's Alembic head.

    The application's ``alembic/env.py`` reads its
    ``sqlalchemy.url`` from :func:`app.core.get_settings`,
    which in turn reads the ``LOCKVERITY_DATABASE_URL``
    environment variable. We therefore set the env var
    for the duration of the migration, clear the
    ``get_settings`` cache so the new value is picked up,
    and restore both afterwards. The session_factory the
    driver creates afterwards binds to the same
    ``database_url`` directly, so the loader does not
    depend on the env var staying set.
    """
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    previous_value = os.environ.get("LOCKVERITY_DATABASE_URL")
    os.environ["LOCKVERITY_DATABASE_URL"] = database_url
    try:
        # ``get_settings`` is cached; the lru_cache must be
        # cleared so the new env var is read by env.py.
        from app.core.config import get_settings

        get_settings.cache_clear()
        command.upgrade(config, "head")
    finally:
        if previous_value is None:
            os.environ.pop("LOCKVERITY_DATABASE_URL", None)
        else:
            os.environ["LOCKVERITY_DATABASE_URL"] = previous_value
        # Reset the cache once more so the application
        # outside the loader sees the original setting.
        from app.core.config import get_settings

        get_settings.cache_clear()


# ---------------------------------------------------------------------
# Demo data - every value is a hard-coded literal.
# ---------------------------------------------------------------------


def _build_repository() -> Repository:
    return Repository(
        id=1,
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner=REPOSITORY_OWNER,
        name=REPOSITORY_NAME,
        canonical_url=REPOSITORY_URL,
        default_branch="main",
        description=REPOSITORY_DESCRIPTION,
        visibility=RepositoryVisibility.PUBLIC,
        archived=False,
        last_provider_sync_at=None,
    )


def _build_scan_1_completed() -> tuple[
    ScanRun,
    list[Component],
    list[Manifest],
    list[Finding],
    list[ProviderObservation],
    list[DependencyEdge],
]:
    """A completed scan with six components, mixed evidence.

    The v1.1 screenshot-ready scan: six components in the
    ``npm`` ecosystem (the only JavaScript ecosystem the
    application's parsers cover), with a mix of evidence
    states that is visually meaningful across the v0.6
    CycloneDX export, the v0.7 evidence preview, the v0.8
    component drilldown, the v0.9 evidence-aware search,
    and the v1.0 human-readable evidence report.

    Components (id 1-6, scan 1, manifest 1):

    - ``alpha`` (1, npm, 1.2.3, direct, manifest, persisted
      PURL, licence observed, provider observed) - the
      "anchor" component.
    - ``beta`` (2, npm, 0.0.1, direct, lockfile, no PURL,
      licence missing, provider missing) - the "lockfile-only"
      component.
    - ``gamma`` (3, npm, unresolved, transitive, no PURL,
      licence missing, provider missing) - the "no edges,
      no licence, no provider" component. Lockverity does
      not have a Cargo parser, so ``gamma`` is intentionally
      an ``npm`` component; the dataset only uses ecosystems
      the application actually supports.
    - ``left-pad`` (4, npm, 1.3.0, direct, manifest,
      persisted PURL, licence observed, provider observed) -
      a second well-evidenced component.
    - ``right-pad`` (5, npm, 0.0.2, direct, lockfile, no
      PURL, licence missing, provider observed) - the
      "provider only" component.
    - ``stay`` (6, npm, unresolved, transitive, no PURL,
      licence missing, provider missing) - the "evidence
      gap" component.
    """
    scan = ScanRun(
        id=1,
        repository_id=1,
        status=ScanStatus.COMPLETED,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha=RESOLVED_COMMIT_SHA,
        analyzer_version=f"lockverity {__version__}",
        # Explicit seeded-demo provenance. This marker - never
        # the numeric id, the repository URL, or the status -
        # is what the read-side APIs use to identify demo rows.
        seeded_dataset=DEMO_SEEDED_DATASET,
        started_at=None,
        completed_at=None,
        failure_code=None,
        failure_summary=None,
    )
    manifest = Manifest(
        id=1,
        scan_run_id=1,
        path="package.json",
        manifest_type="npm",
        ecosystem="npm",
        parse_status=ManifestParseStatus.PARSED,
        parse_warning_count=0,
        content_sha256="a" * 64,
    )
    alpha = Component(
        id=1,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="alpha",
        version="1.2.3",
        version_source=ComponentVersionSource.MANIFEST,
        package_url="pkg:npm/alpha@1.2.3",
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    beta = Component(
        id=2,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="beta",
        version="0.0.1",
        version_source=ComponentVersionSource.LOCKFILE,
        package_url=None,
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    gamma = Component(
        id=3,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="gamma",
        version=None,
        version_source=ComponentVersionSource.UNRESOLVED,
        package_url=None,
        scope=None,
        relationship=None,
        direct=False,
        development=False,
        optional=False,
        integrity=None,
    )
    left_pad = Component(
        id=4,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="left-pad",
        version="1.3.0",
        version_source=ComponentVersionSource.MANIFEST,
        package_url="pkg:npm/left-pad@1.3.0",
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    right_pad = Component(
        id=5,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="right-pad",
        version="0.0.2",
        version_source=ComponentVersionSource.LOCKFILE,
        package_url=None,
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    stay = Component(
        id=6,
        scan_run_id=1,
        manifest_id=1,
        ecosystem="npm",
        package_name="stay",
        version=None,
        version_source=ComponentVersionSource.UNRESOLVED,
        package_url=None,
        scope=None,
        relationship=None,
        direct=False,
        development=False,
        optional=False,
        integrity=None,
    )
    licence_finding = Finding(
        id=1,
        scan_run_id=1,
        repository_id=1,
        rule_id="licence.observed",
        category=FindingCategory.LICENCE,
        severity="informational",
        confidence="high",
        title="alpha licence observed",
        summary="alpha MIT observed",
        remediation=None,
        evidence_json=json.dumps(
            {
                "evidence": {
                    "component_id": 1,
                    "licences": ["MIT"],
                }
            }
        ),
        location_path="package.json",
        location_start_line=None,
        location_end_line=None,
        stable_key="licence-alpha-1",
        status="open",
    )
    left_pad_licence_finding = Finding(
        id=2,
        scan_run_id=1,
        repository_id=1,
        rule_id="licence.observed",
        category=FindingCategory.LICENCE,
        severity="informational",
        confidence="high",
        title="left-pad licence observed",
        summary="left-pad MIT observed",
        remediation=None,
        evidence_json=json.dumps(
            {
                "evidence": {
                    "component_id": 4,
                    "licences": ["MIT"],
                }
            }
        ),
        location_path="package.json",
        location_start_line=None,
        location_end_line=None,
        stable_key="licence-left-pad-1",
        status="open",
    )
    alpha_provider = ProviderObservation(
        id=1,
        scan_run_id=1,
        provider="osv",
        operation="query",
        status=ProviderStatus.AVAILABLE,
        cache_status=None,
        http_status=200,
        records_returned=1,
        requested_at=None,
        completed_at=None,
        component_id=1,
        error_code=None,
        error_summary=None,
        evidence_json=json.dumps(
            {
                "evidence": {
                    "component_id": 1,
                    "fetched_at": "2026-07-17T00:00:00+00:00",
                }
            }
        ),
    )
    left_pad_provider = ProviderObservation(
        id=2,
        scan_run_id=1,
        provider="osv",
        operation="query",
        status=ProviderStatus.AVAILABLE,
        cache_status=None,
        http_status=200,
        records_returned=1,
        requested_at=None,
        completed_at=None,
        component_id=4,
        error_code=None,
        error_summary=None,
        evidence_json=json.dumps(
            {
                "evidence": {
                    "component_id": 4,
                    "fetched_at": "2026-07-17T00:00:00+00:00",
                }
            }
        ),
    )
    right_pad_provider = ProviderObservation(
        id=3,
        scan_run_id=1,
        provider="deps_dev",
        operation="query",
        status=ProviderStatus.AVAILABLE,
        cache_status=None,
        http_status=200,
        records_returned=1,
        requested_at=None,
        completed_at=None,
        component_id=5,
        error_code=None,
        error_summary=None,
        evidence_json=None,
    )
    edge_alpha_to_beta = DependencyEdge(
        id=1,
        scan_run_id=1,
        parent_component_id=1,
        child_component_id=2,
        depth=1,
        relationship="runtime",
    )
    edge_alpha_to_left_pad = DependencyEdge(
        id=2,
        scan_run_id=1,
        parent_component_id=1,
        child_component_id=4,
        depth=1,
        relationship="runtime",
    )
    return (
        scan,
        [alpha, beta, gamma, left_pad, right_pad, stay],
        [manifest],
        [licence_finding, left_pad_licence_finding],
        [alpha_provider, left_pad_provider, right_pad_provider],
        [edge_alpha_to_beta, edge_alpha_to_left_pad],
    )


def _build_scan_2_partial() -> tuple[
    ScanRun,
    list[Component],
    list[Manifest],
    list[Finding],
    list[ProviderObservation],
    list[DependencyEdge],
]:
    """A partial scan that compares against scan 1.

    The scan 2 fixture shares the same repository id (1) and
    the same resolved commit SHA as scan 1, so the
    v0.5 evidence-aware comparison endpoint can diff them.
    The component set is shifted: ``alpha`` is upgraded to
    ``1.2.4`` and ``gamma`` is dropped, while ``left-pad``,
    ``right-pad``, and ``stay`` are preserved. Scan 2 is a
    PARTIAL scan (eligible for comparison) with one
    rate-limited provider observation so the v0.4 provider
    degradation is visible.
    """
    scan = ScanRun(
        id=2,
        repository_id=1,
        status=ScanStatus.PARTIAL,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha=RESOLVED_COMMIT_SHA,
        analyzer_version=f"lockverity {__version__}",
        seeded_dataset=DEMO_SEEDED_DATASET,
        started_at=None,
        completed_at=None,
        failure_code=None,
        failure_summary=None,
    )
    manifest = Manifest(
        id=2,
        scan_run_id=2,
        path="package.json",
        manifest_type="npm",
        ecosystem="npm",
        parse_status=ManifestParseStatus.PARSED,
        parse_warning_count=0,
        content_sha256="b" * 64,
    )
    alpha_upgraded = Component(
        id=7,
        scan_run_id=2,
        manifest_id=2,
        ecosystem="npm",
        package_name="alpha",
        version="1.2.4",
        version_source=ComponentVersionSource.MANIFEST,
        package_url="pkg:npm/alpha@1.2.4",
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    left_pad = Component(
        id=8,
        scan_run_id=2,
        manifest_id=2,
        ecosystem="npm",
        package_name="left-pad",
        version="1.3.0",
        version_source=ComponentVersionSource.MANIFEST,
        package_url="pkg:npm/left-pad@1.3.0",
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    right_pad = Component(
        id=9,
        scan_run_id=2,
        manifest_id=2,
        ecosystem="npm",
        package_name="right-pad",
        version="0.0.2",
        version_source=ComponentVersionSource.LOCKFILE,
        package_url=None,
        scope=None,
        relationship=None,
        direct=True,
        development=False,
        optional=False,
        integrity=None,
    )
    stay = Component(
        id=10,
        scan_run_id=2,
        manifest_id=2,
        ecosystem="npm",
        package_name="stay",
        version=None,
        version_source=ComponentVersionSource.UNRESOLVED,
        package_url=None,
        scope=None,
        relationship=None,
        direct=False,
        development=False,
        optional=False,
        integrity=None,
    )
    left_pad_rate_limited = ProviderObservation(
        id=4,
        scan_run_id=2,
        provider="deps_dev",
        operation="query",
        status=ProviderStatus.RATE_LIMITED,
        cache_status="stale",
        http_status=429,
        records_returned=0,
        requested_at=None,
        completed_at=None,
        component_id=8,
        error_code="rate_limited",
        error_summary="deps.dev rate limit hit during the scan window.",
        evidence_json=None,
    )
    return (
        scan,
        [alpha_upgraded, left_pad, right_pad, stay],
        [manifest],
        [],
        [left_pad_rate_limited],
        [],
    )


def _build_scan_3_failed() -> tuple[
    ScanRun,
    list[Component],
    list[Manifest],
    list[Finding],
    list[ProviderObservation],
    list[DependencyEdge],
]:
    """A failed scan: no inventory, no manifests, no findings."""
    scan = ScanRun(
        id=3,
        repository_id=1,
        status=ScanStatus.FAILED,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha=RESOLVED_COMMIT_SHA,
        analyzer_version=f"lockverity {__version__}",
        seeded_dataset=DEMO_SEEDED_DATASET,
        started_at=None,
        completed_at=None,
        failure_code="scanner_crashed",
        failure_summary="Scanner crashed before inventory capture.",
    )
    return (scan, [], [], [], [], [])


def _build_scan_4_cancelled() -> tuple[
    ScanRun,
    list[Component],
    list[Manifest],
    list[Finding],
    list[ProviderObservation],
    list[DependencyEdge],
]:
    """A cancelled scan: no inventory, no manifests, no findings."""
    scan = ScanRun(
        id=4,
        repository_id=1,
        status=ScanStatus.CANCELLED,
        trigger_type=ScanTriggerType.MANUAL,
        requested_ref="main",
        resolved_commit_sha=RESOLVED_COMMIT_SHA,
        analyzer_version=f"lockverity {__version__}",
        seeded_dataset=DEMO_SEEDED_DATASET,
        started_at=None,
        completed_at=None,
        failure_code="operator_cancelled",
        failure_summary="Operator cancelled the scan before completion.",
    )
    return (scan, [], [], [], [], [])


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Load the Lockverity v1.1 demo dataset into a SQLite "
            "database. The script is deterministic, safe to commit, "
            "and never calls a provider or external network."
        )
    )
    parser.add_argument(
        "--reset-demo-db",
        action="store_true",
        help=(
            "Overwrite the target SQLite file if it already exists. "
            "Without this flag the script refuses to overwrite a "
            "non-empty file. The target file must always live under "
            "``backend/var/``."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Path to the output SQLite file. Defaults to "
            "``var/demo/lockverity-demo.sqlite`` relative to the "
            "backend directory. The resolved path must be under "
            "``backend/var/``."
        ),
    )
    return parser.parse_args()


def _resolve_output(path: Path | None) -> Path:
    if path is None:
        return (_BACKEND_DIR / DEFAULT_OUTPUT).resolve()
    if not path.is_absolute():
        return (_BACKEND_DIR / path).resolve()
    return path.resolve()


def main() -> int:
    args = _parse_args()
    output = _resolve_output(args.output)
    if not _is_safe_output(output):
        print(
            f"refusing to write outside the backend var/ tree: {output}",
            file=sys.stderr,
        )
        return 2
    if output.exists():
        if not args.reset_demo_db:
            print(
                f"refusing to overwrite existing file: {output}\n"
                "re-run with --reset-demo-db to overwrite.",
                file=sys.stderr,
            )
            return 2
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{output}"
    # The schema is created by Alembic migrations, not by
    # ``Base.metadata.create_all``. After this call the
    # ``alembic_version`` table is populated with the current
    # head revision.
    _run_alembic_upgrade(database_url)
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        session.add(_build_repository())
        session.flush()
        for builder in (
            _build_scan_1_completed,
            _build_scan_2_partial,
            _build_scan_3_failed,
            _build_scan_4_cancelled,
        ):
            (
                scan,
                components,
                manifests,
                findings,
                provider_observations,
                edges,
            ) = builder()
            session.add(scan)
            session.flush()
            for manifest in manifests:
                session.add(manifest)
            for component in components:
                session.add(component)
            for finding in findings:
                session.add(finding)
            for obs in provider_observations:
                session.add(obs)
            for edge in edges:
                session.add(edge)
        session.commit()
    finally:
        session.close()
        engine.dispose()
    relative = output.relative_to(_BACKEND_DIR) if output.is_relative_to(_BACKEND_DIR) else output
    relative_posix = relative.as_posix() if hasattr(relative, "as_posix") else str(relative)
    print(
        f"Lockverity demo database ready.\n"
        f"  dataset: synthetic persisted evidence (no provider calls were made)\n"
        f"  repository: https://github.com/example-org/lockverity-fixture\n"
        f"  scan ids: 1 (completed, 6 components), "
        f"2 (partial, 4 components), 3 (failed), 4 (cancelled)\n"
        f"  path: {output}\n"
        f"\n"
        f"start the backend (PowerShell):\n"
        f'  $env:LOCKVERITY_DATABASE_URL = "sqlite:///{relative_posix}"\n'
        f"  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765\n"
        f"start the backend (POSIX shell):\n"
        f'  export LOCKVERITY_DATABASE_URL="sqlite:///{relative_posix}"\n'
        f"  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765\n"
        f"start the frontend (PowerShell):\n"
        f'  $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:8765"\n'
        f"  npm run dev\n"
        f"start the frontend (POSIX shell):\n"
        f'  export VITE_API_PROXY_TARGET="http://127.0.0.1:8765"\n'
        f"  npm run dev\n"
        f"\n"
        f"open the demo (frontend on 127.0.0.1:5173 unless overridden):\n"
        f"  http://127.0.0.1:5173/                          scan list (4 scans)\n"
        f"  http://127.0.0.1:5173/scans/1/dependencies      Dependency Explorer (6 components, evidence filters)\n"
        f"  http://127.0.0.1:5173/scans/1/exports          Export Center (CycloneDX 1.7 + evidence report)\n"
        f"  http://127.0.0.1:5173/scans/3/exports          bounded empty state (failed scan)\n"
        f"  http://127.0.0.1:5173/about                    About page + product boundaries\n"
        f"\n"
        f"see docs/demo-walkthrough.md and docs/screenshots.md for the reviewer checklist."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
