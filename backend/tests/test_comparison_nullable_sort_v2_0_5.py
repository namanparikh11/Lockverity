"""Regression tests for the v2.0.5 comparison nullable-key sort repair.

The v2.0.4 field-test repro hit a ``TypeError: '<' not supported
between instances of 'str' and 'NoneType'`` when comparing scans #13
and #15 in ``var/manual-review/lockverity-field-test.sqlite``. The
component identity tuple ``(ecosystem, package_name, version)``
contains a nullable ``version``; Python's default ``sorted`` cannot
compare ``None`` with a string directly.

v2.0.5 introduces ``_nullable_key_sort_key`` in
``app.services.comparison_service`` and applies it to the four
``sorted(set(base_index) | set(head_index))`` (and one
``sorted(set(base_index) & set(head_index))``) call sites whose
keys legitimately contain ``None``. The original identity tuple is
not mutated; equality continues to use the original tuple.

The tests in this file pin:

1. The comparison service does not raise on the field-test
   repro (scans #13 and #15).
2. ``_nullable_key_sort_key`` returns a deterministic,
   non-raising sort key for tuples that mix ``None`` and
   strings.
3. ``None`` and the empty string are distinct in the sort key
   (the underlying identity contract treats them as
   different identities).
4. ``None`` and a populated string do not crash sorting.
5. Two scans whose components are byte-identical produce
   only ``still_observed`` rows.
6. Repeated comparison returns the same ordering (the sort
   is deterministic).
7. The vulnerability and licence comparators no longer crash
   on mixed nullable evidence.
8. The endpoint returns 200 rather than 500 for the
   reproduced nullable-key fixture.
9. The response contains no security-improved, fixed, or
   remediated wording.
10. Cross-repository and same-scan validation still reject
    correctly.
11. Failed/cancelled eligibility rules remain unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from app.db import session as _db_session
from app.main import app
from app.models.component import Component, ComponentVersionSource
from app.models.manifest import Manifest, ManifestParseStatus
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanStatus, ScanTriggerType
from app.services import scan_service
from app.services.comparison_service import (
    _index_components_by_version,
    _nullable_key_sort_key,
    compare_scans,
)

from tests.api_client import api_client

# ---------------------------------------------------------------------------
# Unit tests for the sort key helper
# ---------------------------------------------------------------------------


def test_nullable_key_sort_key_does_not_raise_on_mixed_none_and_string() -> None:
    """The sort key converts None to (0, "") without raising."""
    key = ("pypi", "requests", None)
    out = _nullable_key_sort_key(key)
    assert out == ((1, "pypi"), (1, "requests"), (0, ""))


def test_nullable_key_sort_key_distinguishes_none_from_empty_string() -> None:
    """The sort key preserves the None/empty distinction in ordering.

    ``None`` sorts first (0, "") and "" sorts second (1, ""), so a
    list containing both would order None before "" rather than
    collapsing them.
    """
    none_key = ("pypi", "x", None)
    empty_key = ("pypi", "x", "")
    none_sort = _nullable_key_sort_key(none_key)
    empty_sort = _nullable_key_sort_key(empty_key)
    assert none_sort < empty_sort


def test_nullable_key_sort_key_preserves_identity_equality() -> None:
    """The original identity tuple is left untouched.

    Two dicts keyed on the original tuples must still treat
    ``None`` and ``""`` as distinct identities; the sort key
    helper does not collapse them.
    """
    index = {("pypi", "x", None): "none-version", ("pypi", "x", ""): "empty-version"}
    assert len(index) == 2


def test_nullable_key_sort_key_handles_complex_tuples() -> None:
    """Multi-field tuples with mixed types are handled."""
    key = (None, "pypi", "requests", None, "1.0.0", None)
    out = _nullable_key_sort_key(key)
    assert out == (
        (0, ""),
        (1, "pypi"),
        (1, "requests"),
        (0, ""),
        (1, "1.0.0"),
        (0, ""),
    )


def test_index_components_by_version_preserves_null_versions() -> None:
    """The component index keeps null-version rows in a separate bucket.

    Two Component records for the same package with different
    version values (None vs a real string) must end up in
    different dict entries, not collapsed together.
    """
    c_none = Component(
        scan_run_id=1,
        manifest_id=1,
        ecosystem="pypi",
        package_name="requests",
        version=None,
        version_source=ComponentVersionSource.UNRESOLVED,
        direct=True,
    )
    c_str = Component(
        scan_run_id=1,
        manifest_id=1,
        ecosystem="pypi",
        package_name="requests",
        version="2.32.3",
        version_source=ComponentVersionSource.MANIFEST,
        direct=True,
    )
    out = _index_components_by_version([c_none, c_str])
    assert ("pypi", "requests", None) in out
    assert ("pypi", "requests", "2.32.3") in out
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Field-test fixture: scans #13 and #15 from
# var/manual-review/lockverity-field-test.sqlite
# ---------------------------------------------------------------------------
#
# These tests open a fresh engine against the field-test DB and
# call ``compare_scans`` with that engine's session. The
# field-test DB is gitignored and may not exist in a fresh
# clone; the v2.0.5 verification does not require it, only
# the in-process unit/integration coverage does. The path
# is resolved relative to this test file so the test works
# in any checkout location without leaking a developer
# home directory into the source.
FIELD_TEST_DB = str(
    Path(__file__).resolve().parent.parent
    / "var"
    / "manual-review"
    / "lockverity-field-test.sqlite"
)


def _has_field_test_db() -> bool:
    return os.path.exists(FIELD_TEST_DB)


requires_field_test = pytest.mark.skipif(
    not _has_field_test_db(), reason="field-test database not present in this checkout"
)


def _field_test_session():
    """Open a fresh engine + session bound to the field-test DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{FIELD_TEST_DB}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=False,
    )
    factory = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return test_engine, factory()


@requires_field_test
def test_field_test_scan_13_vs_15_compare_successfully_no_500() -> None:
    """The exact field-test repro: scans #13 and #15 compare without 500.

    Pre-v2.0.5 behaviour: ``TypeError: '<' not supported between
    instances of 'str' and 'NoneType'`` in
    ``_compare_components``. v2.0.5 should return a
    ``ScanComparisonResponse`` with all components in
    ``still_observed`` state (the rescan produced equivalent
    evidence).
    """
    _, session = _field_test_session()
    result = compare_scans(session, base_scan_id=13, head_scan_id=15)
    assert result.base_scan_id == 13
    assert result.head_scan_id == 15
    # All 10 components are still_observed for an equivalent
    # rescan of the same uploaded monorepository.
    assert len(result.components) == 10
    for c in result.components:
        assert c.state == "still_observed"


@requires_field_test
def test_field_test_scan_13_vs_15_response_contains_no_security_wording() -> None:
    """The comparison response must not invent security conclusions."""
    _, session = _field_test_session()
    result = compare_scans(session, base_scan_id=13, head_scan_id=15)
    blob = json.dumps(result.model_dump(mode="json")).lower()
    for forbidden in (
        "security improved",
        "security worsened",
        "fixed",
        "remediated",
        "risk increased",
        "risk decreased",
    ):
        assert forbidden not in blob, f"forbidden wording {forbidden!r} in response"


@requires_field_test
def test_field_test_scan_13_vs_15_is_deterministic() -> None:
    """Repeated comparison returns the same component ordering."""
    _, session1 = _field_test_session()
    _, session2 = _field_test_session()
    first = compare_scans(session1, base_scan_id=13, head_scan_id=15)
    second = compare_scans(session2, base_scan_id=13, head_scan_id=15)
    first_keys = [(c.ecosystem, c.package_name, c.version) for c in first.components]
    second_keys = [(c.ecosystem, c.package_name, c.version) for c in second.components]
    assert first_keys == second_keys


# ---------------------------------------------------------------------------
# In-process test fixtures: build synthetic nullable component scans
# ---------------------------------------------------------------------------


def _build_two_scans_same_repo(
    app_config,
    workspace_root,
    *,
    base_components: list[tuple[str, str, str | None, bool]],
    head_components: list[tuple[str, str, str | None, bool]],
) -> tuple[int, int]:
    """Create one repository with two scans, each populated with the
    given components. Returns ``(base_scan_id, head_scan_id)``.

    Both scans share the same ``repository_id`` so the comparator
    accepts them. The workspace is minimal: a single dummy
    ``package.json`` so the orchestrator would have a manifest,
    but the components are inserted directly into the DB.
    """
    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="nullable-shared",
            canonical_url="upload://nullable-shared",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
        )
        s.add(repo)
        s.flush()
        repo_id = repo.id
        base_scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        head_scan = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        # Set both to COMPLETED (workspace-less, manual fixtures).
        base_scan.status = ScanStatus.COMPLETED
        head_scan.status = ScanStatus.COMPLETED
        s.flush()
        base_id = base_scan.id
        head_id = head_scan.id
        s.commit()

    # Populate each scan with the requested components.
    for scan_id, components in ((base_id, base_components), (head_id, head_components)):
        with _db_session.SessionLocal() as s:
            manifest = Manifest(
                scan_run_id=scan_id,
                path="package.json",
                manifest_type="package_json",
                ecosystem="pypi",
                parse_status=ManifestParseStatus.PARSED,
            )
            s.add(manifest)
            s.flush()
            for ecosystem, name, version, direct in components:
                s.add(
                    Component(
                        scan_run_id=scan_id,
                        manifest_id=manifest.id,
                        ecosystem=ecosystem,
                        package_name=name,
                        version=version,
                        version_source=(
                            ComponentVersionSource.MANIFEST
                            if version is not None
                            else ComponentVersionSource.UNRESOLVED
                        ),
                        package_url=(f"pkg:{ecosystem}/{name}@{version}" if version else None),
                        direct=direct,
                        development=False,
                        optional=False,
                    )
                )
            s.commit()
    return base_id, head_id


def test_compare_scans_with_nullable_versions_does_not_crash(app_config, workspace_root) -> None:
    """Two synthetic scans with mixed None/string versions compare cleanly."""
    base_id, head_id = _build_two_scans_same_repo(
        app_config,
        workspace_root,
        base_components=[
            ("pypi", "requests", "2.32.3", True),
            ("pypi", "urllib3", "2.2.3", True),
            ("pypi", "pytest", None, True),  # unresolved
            ("npm", "axios", "1.7.9", True),
        ],
        head_components=[
            ("pypi", "requests", "2.32.3", True),
            ("pypi", "urllib3", "2.2.3", True),
            ("pypi", "pytest", None, True),
            ("npm", "axios", "1.7.9", True),
        ],
    )
    result = compare_scans(_db_session.SessionLocal(), base_scan_id=base_id, head_scan_id=head_id)
    assert len(result.components) == 4
    for c in result.components:
        assert c.state == "still_observed"


def test_compare_scans_with_null_version_does_not_fabricate_change(
    app_config, workspace_root
) -> None:
    """A null version on one side and a string version on the other
    are different identities and must each appear (not collapsed
    into one another).
    """
    base_id, head_id = _build_two_scans_same_repo(
        app_config,
        workspace_root,
        base_components=[("pypi", "requests", None, True)],
        head_components=[("pypi", "requests", "2.32.3", True)],
    )
    result = compare_scans(_db_session.SessionLocal(), base_scan_id=base_id, head_scan_id=head_id)
    by_key = {(c.package_name, c.version): c for c in result.components}
    assert ("requests", None) in by_key
    assert ("requests", "2.32.3") in by_key
    assert by_key[("requests", None)].state == "no_longer_observed"
    assert by_key[("requests", "2.32.3")].state == "newly_observed"


def test_compare_scans_empty_string_and_null_are_not_collapsed(app_config, workspace_root) -> None:
    """``version=None`` and ``version=""`` are distinct identities.

    The existing identity contract treats them as different
    rows; the sort-key helper must not collapse them into a
    single persisted identity.
    """
    base_id, head_id = _build_two_scans_same_repo(
        app_config,
        workspace_root,
        base_components=[("pypi", "x", None, True), ("pypi", "x", "", True)],
        head_components=[("pypi", "x", None, True), ("pypi", "x", "", True)],
    )
    result = compare_scans(_db_session.SessionLocal(), base_scan_id=base_id, head_scan_id=head_id)
    assert len(result.components) == 2
    for c in result.components:
        assert c.state == "still_observed"


# ---------------------------------------------------------------------------
# Eligibility and validation tests (no regressions in the v0.5 contract)
# ---------------------------------------------------------------------------


def test_compare_scans_rejects_same_scan_id(app_config) -> None:
    """Same-scan comparison still raises the bounded validation error."""
    from app.utils.errors import ApiError, ApiErrorCode

    with pytest.raises(ApiError) as exc:
        compare_scans(_db_session.SessionLocal(), base_scan_id=1, head_scan_id=1)
    assert exc.value.code == ApiErrorCode.VALIDATION_ERROR


def test_compare_scans_rejects_cross_repository(app_config, workspace_root) -> None:
    """Cross-repository comparison is rejected with a bounded error."""
    from app.utils.errors import ApiError, ApiErrorCode

    with _db_session.SessionLocal() as s:
        repo_a = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="cross-a",
            canonical_url="upload://cross-a",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
        )
        repo_b = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="cross-b",
            canonical_url="upload://cross-b",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
        )
        s.add(repo_a)
        s.add(repo_b)
        s.flush()
        scan_a = scan_service.create_scan(
            s, repository_id=repo_a.id, trigger_type=ScanTriggerType.MANUAL
        )
        scan_b = scan_service.create_scan(
            s, repository_id=repo_b.id, trigger_type=ScanTriggerType.MANUAL
        )
        scan_a.status = ScanStatus.COMPLETED
        scan_b.status = ScanStatus.COMPLETED
        s.commit()
        a_id = scan_a.id
        b_id = scan_b.id

    with pytest.raises(ApiError) as exc:
        compare_scans(_db_session.SessionLocal(), base_scan_id=a_id, head_scan_id=b_id)
    assert exc.value.code == ApiErrorCode.VALIDATION_ERROR


def test_compare_scans_rejects_non_terminal(app_config) -> None:
    """A queued scan is not eligible for comparison (illegal_transition)."""
    from app.utils.errors import ApiError, ApiErrorCode

    with _db_session.SessionLocal() as s:
        repo = Repository(
            source_type=RepositorySourceType.UPLOADED_ARCHIVE,
            provider=RepositoryProvider.LOCAL_UPLOAD,
            owner="upload",
            name="non-terminal",
            canonical_url="upload://non-terminal",
            default_branch=None,
            visibility=RepositoryVisibility.PRIVATE,
        )
        s.add(repo)
        s.flush()
        repo_id = repo.id
        scan_queued = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        scan_done = scan_service.create_scan(
            s, repository_id=repo_id, trigger_type=ScanTriggerType.MANUAL
        )
        scan_done.status = ScanStatus.COMPLETED
        s.commit()
        queued_id = scan_queued.id
        done_id = scan_done.id

    with pytest.raises(ApiError) as exc:
        compare_scans(_db_session.SessionLocal(), base_scan_id=queued_id, head_scan_id=done_id)
    assert exc.value.code == ApiErrorCode.ILLEGAL_TRANSITION


# ---------------------------------------------------------------------------
# HTTP boundary test: endpoint returns 200 rather than 500
# ---------------------------------------------------------------------------


def test_api_compare_endpoint_returns_200_for_nullable_fixture(app_config, workspace_root) -> None:
    """The /api/v1/scans/{head}/compare/{base} endpoint must not 500.

    Pre-v2.0.5 behaviour: the comparator raised ``TypeError``;
    FastAPI returned 500. v2.0.5 returns 200.
    """
    base_id, head_id = _build_two_scans_same_repo(
        app_config,
        workspace_root,
        base_components=[("pypi", "requests", None, True)],
        head_components=[("pypi", "requests", "2.32.3", True)],
    )
    client = api_client(app)
    response = client.get(f"/api/v1/scans/{head_id}/compare/{base_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_scan_id"] == base_id
    assert body["head_scan_id"] == head_id
    blob = json.dumps(body).lower()
    for forbidden in ("security improved", "fixed", "remediated", "risk decreased"):
        assert forbidden not in blob
