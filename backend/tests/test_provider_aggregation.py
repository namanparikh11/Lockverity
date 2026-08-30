"""Provider-observation aggregation semantics (LV-011).

The evidence-honesty policy requires the aggregate state of a
provider for one scan to be derived from ALL relevant
observations, not from the chronologically latest row. The
tests here pin the shared aggregation helper
(:mod:`app.services.provider_aggregation`) and the retry /
replacement rule, plus the retry-aware provider-coverage
derivation the exporters share
(:func:`app.exporters._common._provider_coverage_from_observations`).

The contracts pinned:

- success + success -> successful (``available``)
- failure + later *unrelated* success -> degraded (the later
  success for another component must not launder the earlier
  failure into a clean result)
- rate limited + successful other component -> degraded
- ``not_requested`` stays distinct (never clean, never failure)
- operator-disabled stays distinct
- ``not_applicable`` stays distinct
- an explicit later retry for the SAME logical request
  (same provider + operation + component) supersedes the
  earlier failed attempt
"""

from __future__ import annotations

import pytest
from app.exporters._common import _provider_coverage_from_observations
from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.services.provider_aggregation import (
    aggregate_provider_observations,
    final_observation_per_request,
)

_NEXT_ID = 0


def _observation(
    *,
    provider: str = "deps_dev",
    operation: str = "deps_dev_enrichment",
    component_id: int | None = None,
    status: ProviderStatus,
    error_code: str | None = None,
) -> ProviderObservation:
    """Build a detached observation row with a monotonic id."""

    global _NEXT_ID
    _NEXT_ID += 1
    return ProviderObservation(
        id=_NEXT_ID,
        scan_run_id=1,
        component_id=component_id,
        provider=provider,
        operation=operation,
        status=status,
        records_returned=0,
        cache_status="miss",
        error_code=error_code,
        error_summary=None,
    )


def _scan(status: ScanStatus = ScanStatus.COMPLETED) -> ScanRun:
    return ScanRun(
        repository_id=1,
        status=status,
        trigger_type=ScanTriggerType.MANUAL,
    )


class TestAggregateProviderObservations:
    def test_success_plus_success_is_available(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.AVAILABLE),
                _observation(component_id=2, status=ProviderStatus.CACHED),
            ]
        )
        assert aggregate is not None
        assert aggregate.status is ProviderStatus.AVAILABLE
        assert aggregate.all_checked is True
        assert aggregate.degraded is False

    def test_failure_then_later_unrelated_success_stays_degraded(self) -> None:
        # Component A fails first; component B succeeds later.
        # The chronologically latest row is a success, but the
        # enrichment was incomplete: the aggregate must not
        # become a fully successful state.
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.UNAVAILABLE),
                _observation(component_id=2, status=ProviderStatus.AVAILABLE),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded is True
        assert aggregate.all_checked is False
        assert aggregate.status is ProviderStatus.UNAVAILABLE

    def test_rate_limited_plus_other_success_is_degraded(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.RATE_LIMITED),
                _observation(component_id=2, status=ProviderStatus.AVAILABLE),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded is True
        assert aggregate.status is ProviderStatus.RATE_LIMITED

    def test_unavailable_outranks_partial_in_mixed_degraded_set(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.PARTIAL),
                _observation(component_id=2, status=ProviderStatus.UNAVAILABLE),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded_status is ProviderStatus.UNAVAILABLE

    def test_not_requested_only_stays_distinct(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="no_components",
                )
            ]
        )
        assert aggregate is not None
        assert aggregate.status is ProviderStatus.NOT_REQUESTED
        assert aggregate.degraded is False
        assert aggregate.all_checked is False

    def test_disabled_by_operator_stays_distinct(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="disabled_by_operator",
                )
            ]
        )
        assert aggregate is not None
        assert aggregate.status is ProviderStatus.NOT_REQUESTED
        assert aggregate.degraded is False
        assert aggregate.omitted_by_operator is True
        assert aggregate.not_applicable is False

    def test_not_applicable_stays_distinct(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(
                    provider="openssf",
                    operation="openssf_scorecard_read",
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="not_applicable",
                )
            ]
        )
        assert aggregate is not None
        assert aggregate.status is ProviderStatus.NOT_REQUESTED
        assert aggregate.degraded is False
        assert aggregate.not_applicable is True
        assert aggregate.omitted_by_operator is False

    def test_retry_for_same_request_supersedes_earlier_failure(self) -> None:
        # Component 1 fails, then the same logical request is
        # explicitly retried and succeeds: the later row wins
        # for that request and the aggregate is a clean success.
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.UNAVAILABLE),
                _observation(component_id=1, status=ProviderStatus.AVAILABLE),
                _observation(component_id=2, status=ProviderStatus.AVAILABLE),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded is False
        assert aggregate.all_checked is True
        assert aggregate.status is ProviderStatus.AVAILABLE
        # The superseded row no longer drives the aggregate.
        assert len(aggregate.final_observations) == 2

    def test_retry_success_does_not_hide_other_components_failure(self) -> None:
        aggregate = aggregate_provider_observations(
            [
                _observation(component_id=1, status=ProviderStatus.UNAVAILABLE),
                _observation(component_id=1, status=ProviderStatus.AVAILABLE),
                _observation(component_id=2, status=ProviderStatus.RATE_LIMITED),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded is True
        assert aggregate.status is ProviderStatus.RATE_LIMITED

    def test_empty_input_returns_none(self) -> None:
        assert aggregate_provider_observations([]) is None
        assert aggregate_provider_observations([]) is None

    def test_scan_level_and_component_rows_are_separate_requests(self) -> None:
        # OSV writes one scan-level batch row plus per-component
        # skip rows; a failing batch row plus a successful
        # per-component retry of a different request stays
        # degraded.
        aggregate = aggregate_provider_observations(
            [
                _observation(
                    provider="osv",
                    operation="osv_vulnerability_query",
                    component_id=None,
                    status=ProviderStatus.UNAVAILABLE,
                ),
                _observation(
                    provider="osv",
                    operation="osv_vulnerability_query",
                    component_id=7,
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="unsupported_ecosystem",
                ),
            ]
        )
        assert aggregate is not None
        assert aggregate.degraded is True


class TestFinalObservationPerRequest:
    def test_keys_group_by_provider_operation_and_component(self) -> None:
        rows = [
            _observation(component_id=1, status=ProviderStatus.AVAILABLE),
            _observation(component_id=1, status=ProviderStatus.CACHED),
            _observation(
                provider="osv", operation="osv_vulnerability_query", status=ProviderStatus.AVAILABLE
            ),
        ]
        final = final_observation_per_request(rows)
        assert set(final) == {
            ("deps_dev", "deps_dev_enrichment", 1),
            ("osv", "osv_vulnerability_query", None),
        }
        assert final[("deps_dev", "deps_dev_enrichment", 1)].status is ProviderStatus.CACHED


class TestRetryAwareProviderCoverage:
    def test_coverage_degraded_when_any_final_request_failed(self) -> None:
        label, omitted = _provider_coverage_from_observations(
            _scan(),
            [
                _observation(component_id=1, status=ProviderStatus.UNAVAILABLE),
                _observation(component_id=2, status=ProviderStatus.AVAILABLE),
            ],
        )
        assert label == "degraded"
        assert omitted is False

    def test_coverage_ok_only_when_no_final_request_failed(self) -> None:
        label, _omitted = _provider_coverage_from_observations(
            _scan(),
            [
                _observation(component_id=1, status=ProviderStatus.AVAILABLE),
                _observation(
                    provider="osv",
                    operation="osv_vulnerability_query",
                    status=ProviderStatus.CACHED,
                ),
            ],
        )
        assert label == "ok"

    def test_coverage_retry_supersedes_earlier_failure(self) -> None:
        label, _omitted = _provider_coverage_from_observations(
            _scan(),
            [
                _observation(component_id=1, status=ProviderStatus.UNAVAILABLE),
                _observation(component_id=1, status=ProviderStatus.AVAILABLE),
            ],
        )
        assert label == "ok"

    def test_coverage_disabled_is_not_failure(self) -> None:
        label, omitted = _provider_coverage_from_observations(
            _scan(),
            [
                _observation(
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="disabled_by_operator",
                )
            ],
        )
        assert label == "not_requested"
        assert omitted is True

    def test_coverage_not_applicable_is_not_failure(self) -> None:
        label, omitted = _provider_coverage_from_observations(
            _scan(),
            [
                _observation(
                    provider="openssf",
                    operation="openssf_scorecard_read",
                    status=ProviderStatus.NOT_REQUESTED,
                    error_code="not_applicable",
                )
            ],
        )
        assert label == "not_applicable"
        assert omitted is False

    def test_coverage_unknown_without_observations(self) -> None:
        label, _omitted = _provider_coverage_from_observations(_scan(), None)
        assert label == "unknown"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((ProviderStatus.AVAILABLE, ProviderStatus.CACHED), ProviderStatus.AVAILABLE),
        ((ProviderStatus.RATE_LIMITED, ProviderStatus.AVAILABLE), ProviderStatus.RATE_LIMITED),
        ((ProviderStatus.PARTIAL,), ProviderStatus.PARTIAL),
        ((ProviderStatus.UNAVAILABLE, ProviderStatus.RATE_LIMITED), ProviderStatus.UNAVAILABLE),
    ],
)
def test_aggregate_priority_matrix(statuses, expected) -> None:
    aggregate = aggregate_provider_observations(
        [
            _observation(component_id=index + 1, status=status)
            for index, status in enumerate(statuses)
        ]
    )
    assert aggregate is not None
    assert aggregate.status is expected


def test_observation_listing_filters_by_provider(session) -> None:
    """The per-scan observation endpoint's provider filter is honoured.

    The frontend ``useProviderObservation`` hook and the
    Provider Health filter bar both send ``?provider=``; the
    listing must actually filter on it so a page asking for
    ``osv`` never receives another provider's rows.
    """

    from app.models.repository import (
        Repository,
        RepositoryProvider,
        RepositorySourceType,
        RepositoryVisibility,
    )
    from app.repositories import observation_repo

    repository = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="o",
        name="agg",
        canonical_url="https://github.com/o/agg",
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
    for provider in ("osv", "deps_dev", "openssf"):
        session.add(
            ProviderObservation(
                scan_run_id=scan.id,
                provider=provider,
                operation=f"{provider}_op",
                status=ProviderStatus.AVAILABLE,
                records_returned=0,
                cache_status="miss",
            )
        )
    session.flush()

    items, total = observation_repo.list_observations_for_scan(
        session,
        scan.id,
        page=1,
        page_size=25,
        provider="osv",
    )
    assert total == 1
    assert [row.provider for row in items] == ["osv"]

    _items_all, total_all = observation_repo.list_observations_for_scan(
        session,
        scan.id,
        page=1,
        page_size=25,
    )
    assert total_all == 3
