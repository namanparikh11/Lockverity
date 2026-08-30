"""Regression test: per-component enrichment binding.

Blocker 2 defect: a component with a concrete normalised
version received an ``unavailable_reason`` copied from a
different component in the same scan whose deps.dev lookup
failed because that other component had no version. The
underlying cause was that ``ProviderObservation`` had no
``component_id`` column and the read-side endpoint selected
the latest observation for ``(scan_run_id, provider)``,
regardless of which component it referred to.

The fix adds ``ProviderObservation.component_id`` (nullable
FK) and the API filter:

    WHERE scan_run_id = ? AND provider = ? AND component_id = ?

This regression test exercises the exact scenario: two npm
components, one with ``version='1.3.0'`` (and a successful
deps.dev lookup) and one with ``version=NULL`` (and the
"missing concrete version" reason). The enrichment
endpoint must surface each component's own state.
"""

from __future__ import annotations

import json

from app.db import session as _db_session
from app.models.component import Component, ComponentVersionSource
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceState

from tests.api_client import api_client


def _setup_two_components(
    session,
    *,
    components: list[tuple[str, str, str | None]],
) -> int:
    """Create one scan with two npm components."""
    repository = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="octocat",
        name="Hello-World",
        canonical_url="https://github.com/octocat/Hello-World",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repository)
    session.flush()
    scan = ScanRun(
        repository_id=repository.id,
        status=ScanStatus.COMPLETED,
        trigger_type=ScanTriggerType.MANUAL,
    )
    session.add(scan)
    session.flush()
    workspace = Workspace(
        scan_run_id=scan.id,
        workspace_key=f"workspace-key-{scan.id:016d}",
        kind=WorkspaceKind.GITHUB,
        state=WorkspaceState.READY,
        archive_sha256="a" * 64,
        archive_size=10,
        file_count=1,
        uncompressed_size=10,
    )
    session.add(workspace)
    manifest = Manifest(
        scan_run_id=scan.id,
        path="package.json",
        manifest_type="package_json",
        ecosystem="npm",
        parse_status=ManifestParseStatus.PARSED,
    )
    session.add(manifest)
    session.flush()
    for ecosystem, name, version in components:
        session.add(
            Component(
                scan_run_id=scan.id,
                manifest_id=manifest.id,
                ecosystem=ecosystem,
                package_name=name,
                version=version,
                version_source=ComponentVersionSource.LOCKFILE,
                direct=True,
            )
        )
    session.commit()
    return scan.id


def test_enrichment_endpoint_associates_status_with_the_correct_component(
    app_config,
) -> None:
    """A concrete-version component never receives another component's reason.

    The exact scenario from the v0.4 live-response bug:

    - Component A: npm ``left-pad`` ``1.3.0`` (concrete
      version) — the deps.dev lookup returns
      ``AVAILABLE`` with licence and dependency evidence.
    - Component B: npm ``left-pad`` ``NULL`` (no version)
      — the deps.dev lookup returns
      ``UNAVAILABLE`` with the redacted reason
      ``deps.dev requires a concrete version for
      enrichment``.

    The endpoint must return:

    - Component A row: ``provider_status == "available"``,
      ``unavailable_reason is None``,
      ``license_observations == ["WTFPL"]``,
      ``dependency_count == 0``,
      ``source_provenance == "deps.dev"``.
    - Component B row: ``provider_status == "unavailable"``,
      ``unavailable_reason == "deps.dev requires a
      concrete version for enrichment"``,
      ``license_observations == []``,
      ``dependency_count is None``,
      ``source_provenance is None``.
    """
    with _db_session.SessionLocal() as s:
        scan_id = _setup_two_components(
            s,
            components=[
                ("npm", "left-pad", "1.3.0"),
                ("npm", "left-pad", None),
            ],
        )
        # Re-fetch components to bind observations to
        # their real IDs.
        components = (
            s.query(Component)
            .filter(Component.scan_run_id == scan_id)
            .order_by(Component.id.asc())
            .all()
        )
        assert len(components) == 2
        c_with_version = components[0]
        c_without_version = components[1]
        assert c_with_version.version == "1.3.0"
        assert c_without_version.version is None

        # Component A: successful deps.dev lookup.
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                component_id=c_with_version.id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.AVAILABLE,
                records_returned=1,
                cache_status="miss",
                error_code=None,
                error_summary=None,
                evidence_json=json.dumps(
                    {
                        "package_name": "left-pad",
                        "ecosystem": "npm",
                        "version": "1.3.0",
                        "licences": ["WTFPL"],
                        "dependency_count": 0,
                    }
                ),
            )
        )
        # Component B: missing version -> unavailable.
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                component_id=c_without_version.id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                cache_status="miss",
                error_code="provider_unavailable",
                error_summary=("deps.dev requires a concrete version for enrichment"),
            )
        )
        s.commit()

    from app.api import v0_3 as v03
    from app.db import session as _db_session_mod
    from app.main import app

    def _get_db():
        s = _db_session_mod.SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[v03.DBSession] = _get_db
    try:
        with api_client(app) as client:
            r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
            assert r.status_code == 200
            body = r.json()
            assert len(body["items"]) == 2
            by_component_id = {row["component_id"]: row for row in body["items"]}

            row_a = by_component_id[c_with_version.id]
            row_b = by_component_id[c_without_version.id]

            # Component A: honest success.
            assert row_a["provider_status"] == "available", (
                f"Component A must be 'available'; got {row_a['provider_status']!r}"
            )
            assert row_a["unavailable_reason"] is None, (
                "Component A must not receive Component B's "
                "missing-version reason. The blocker 2 contract "
                f"was violated; got {row_a['unavailable_reason']!r}"
            )
            assert row_a["license_observations"] == ["WTFPL"]
            assert row_a["dependency_count"] == 0
            assert row_a["source_provenance"] == "deps.dev"
            assert row_a["version"] == "1.3.0"

            # Component B: honest unavailable, with the
            # specific reason that matches the attempted
            # query (no version).
            assert row_b["provider_status"] == "unavailable"
            assert row_b["unavailable_reason"] == (
                "deps.dev requires a concrete version for enrichment"
            )
            assert row_b["license_observations"] == []
            assert row_b["dependency_count"] is None
            assert row_b["source_provenance"] is None
            assert row_b["version"] is None
    finally:
        app.dependency_overrides.pop(v03.DBSession, None)


def test_enrichment_endpoint_with_no_observations_returns_neutral_state(
    app_config,
) -> None:
    """Components with no per-component observation expose a neutral state.

    A component that has no ``ProviderObservation`` row at
    all (because no provider was ever queried for it) must
    surface as ``provider_status is None``,
    ``unavailable_reason is None``, empty licence list,
    ``dependency_count is None``,
    ``source_provenance is None``. The endpoint must not
    invent a "missing version" reason from another
    component's row.
    """
    with _db_session.SessionLocal() as s:
        scan_id = _setup_two_components(
            s,
            components=[
                ("npm", "left-pad", "1.3.0"),
                ("npm", "left-pad", None),
            ],
        )
        # Insert ONLY a missing-version observation for
        # the second component. The first component has
        # no observation at all.
        components = (
            s.query(Component)
            .filter(Component.scan_run_id == scan_id)
            .order_by(Component.id.asc())
            .all()
        )
        c_without_version = components[1]
        s.add(
            ProviderObservation(
                scan_run_id=scan_id,
                component_id=c_without_version.id,
                provider="deps_dev",
                operation="deps_dev_enrichment",
                status=ProviderStatus.UNAVAILABLE,
                records_returned=0,
                cache_status="miss",
                error_code="provider_unavailable",
                error_summary=("deps.dev requires a concrete version for enrichment"),
            )
        )
        s.commit()

    from app.api import v0_3 as v03
    from app.db import session as _db_session_mod
    from app.main import app

    def _get_db():
        s = _db_session_mod.SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[v03.DBSession] = _get_db
    try:
        with api_client(app) as client:
            r = client.get(f"/api/v1/scans/{scan_id}/enrichments")
            assert r.status_code == 200
            body = r.json()
            assert len(body["items"]) == 2
            by_component_id = {row["component_id"]: row for row in body["items"]}

            # Component A: no observation, neutral state.
            c_with_version = components[0]
            row_a = by_component_id[c_with_version.id]
            assert row_a["provider_status"] is None
            assert row_a["unavailable_reason"] is None
            assert row_a["license_observations"] == []
            assert row_a["dependency_count"] is None
            assert row_a["source_provenance"] is None

            # Component B: the missing-version reason.
            row_b = by_component_id[c_without_version.id]
            assert row_b["provider_status"] == "unavailable"
            assert row_b["unavailable_reason"] == (
                "deps.dev requires a concrete version for enrichment"
            )
    finally:
        app.dependency_overrides.pop(v03.DBSession, None)
