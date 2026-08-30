"""Provider-observation aggregation.

The evidence-honesty policy requires the aggregate state of a
provider for one scan to be derived from **all** relevant
observations, not from the chronologically latest row. A
per-component failure followed by an unrelated component's
success must never become a clean, fully-successful aggregate:
dependency/licence enrichment was incomplete, and the UI, the
stage rows, and the exports must say so.

The single rule implemented here is:

1. Every observation belongs to one *logical request* keyed by
   ``(provider, operation, component_id)``. Scan-level calls
   (OSV batch query, Scorecard read) share the ``None``
   component key.
2. Within one logical request, the **latest** observation is
   authoritative. An explicit later retry for the same request
   (for example an on-demand re-enrichment of the same
   component) genuinely replaces the earlier attempt; the
   earlier failure is superseded, not forgotten-and-laundered.
3. Across the surviving per-request observations, the aggregate
   is the honest combination:

   - any degraded final observation (``unavailable`` >
     ``rate_limited`` > ``partial``) degrades the whole
     aggregate;
   - only when every final observation is ``available`` or
     ``cached`` is the aggregate a successful checked state;
   - otherwise the aggregate keeps the distinct
     ``not_requested`` / ``unknown`` vocabulary (operator
     disabled, not applicable, nothing to query), never a
     clean success and never a failure.

The module is pure: it operates on already-fetched
:class:`~app.models.provider_observation.ProviderObservation`
rows and never opens a session, so the orchestrator, the
analysis pipeline, and the exporters all share one
interpretation of the observation vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.provider_observation import ProviderObservation, ProviderStatus

# Degraded evidence states, worst first. The order drives the
# aggregate: an ``unavailable`` row outweighs a ``rate_limited``
# row, which outweighs a ``partial`` row.
_DEGRADED_SEVERITY: dict[ProviderStatus, int] = {
    ProviderStatus.UNAVAILABLE: 3,
    ProviderStatus.RATE_LIMITED: 2,
    ProviderStatus.PARTIAL: 1,
}

_SUCCESS_STATUSES: frozenset[ProviderStatus] = frozenset(
    {
        ProviderStatus.AVAILABLE,
        ProviderStatus.CACHED,
    }
)

# The distinct not-requested reasons the observation vocabulary
# records in ``error_code``. They stay distinguishable on the
# aggregate so no page has to re-derive them from raw rows.
_CODE_DISABLED_BY_OPERATOR = "disabled_by_operator"
_CODE_NOT_APPLICABLE = "not_applicable"


def request_key(observation: ProviderObservation) -> tuple[str, str, int | None]:
    """Return the logical-request key of one observation row."""

    return (observation.provider, observation.operation, observation.component_id)


def final_observation_per_request(
    observations: Iterable[ProviderObservation],
) -> dict[tuple[str, str, int | None], ProviderObservation]:
    """Return the latest observation for every logical request.

    Rows are compared by ``id`` (monotonically increasing with
    insertion order), so a later retry for the same
    ``(provider, operation, component_id)`` replaces the earlier
    attempt while unrelated requests keep their own history.
    """

    final: dict[tuple[str, str, int | None], ProviderObservation] = {}
    for observation in observations:
        key = request_key(observation)
        current = final.get(key)
        if current is None or observation.id > current.id:
            final[key] = observation
    return final


@dataclass(frozen=True, slots=True)
class ProviderEvidenceAggregate:
    """The combined evidence state of a set of observations.

    ``status`` is the aggregate :class:`ProviderStatus` value
    (the truthful pill a stage row or a UI badge renders).
    The boolean flags carry the distinctions the enum alone
    cannot express, so callers never have to re-scan the raw
    rows:

    - ``degraded`` — at least one final observation is
      unavailable / rate-limited / partial; the aggregate is
      the worst of those.
    - ``all_checked`` — every final observation is available
      or cached; a zero-finding result is now justified as
      "checked and found nothing".
    - ``omitted_by_operator`` — at least one final observation
      was not requested because the operator disabled the
      provider.
    - ``not_applicable`` — at least one final observation was
      not requested because the provider cannot apply to the
      source (for example Scorecard on an uploaded archive).
    """

    status: ProviderStatus
    degraded: bool
    all_checked: bool
    omitted_by_operator: bool
    not_applicable: bool
    final_observations: tuple[ProviderObservation, ...]

    @property
    def degraded_status(self) -> ProviderStatus | None:
        """Return the worst degraded final status, if any."""

        if not self.degraded:
            return None
        worst = max(
            (obs.status for obs in self.final_observations if obs.status in _DEGRADED_SEVERITY),
            key=lambda status: _DEGRADED_SEVERITY[status],
        )
        return worst


def aggregate_provider_observations(
    observations: Sequence[ProviderObservation] | Iterable[ProviderObservation],
) -> ProviderEvidenceAggregate | None:
    """Aggregate observations into one truthful evidence state.

    Returns ``None`` when no observations were supplied: the
    honest answer for "was this provider used?" is then
    *unknown / not recorded*, never a silent success.
    """

    final = final_observation_per_request(observations)
    if not final:
        return None
    rows = tuple(final.values())
    statuses = [obs.status for obs in rows]
    degraded_statuses = [status for status in statuses if status in _DEGRADED_SEVERITY]
    if degraded_statuses:
        worst = max(degraded_statuses, key=lambda status: _DEGRADED_SEVERITY[status])
        return ProviderEvidenceAggregate(
            status=worst,
            degraded=True,
            all_checked=False,
            omitted_by_operator=_flagged(rows, _CODE_DISABLED_BY_OPERATOR),
            not_applicable=_flagged(rows, _CODE_NOT_APPLICABLE),
            final_observations=rows,
        )
    if all(status in _SUCCESS_STATUSES for status in statuses):
        return ProviderEvidenceAggregate(
            status=ProviderStatus.AVAILABLE,
            degraded=False,
            all_checked=True,
            omitted_by_operator=False,
            not_applicable=False,
            final_observations=rows,
        )
    # Mixed success + not-requested/unknown, or only
    # not-requested/unknown. The aggregate keeps the distinct
    # not-requested vocabulary: it is neither a clean success
    # nor a failure.
    not_requested = [status for status in statuses if status == ProviderStatus.NOT_REQUESTED]
    status = ProviderStatus.NOT_REQUESTED if not_requested else ProviderStatus.UNKNOWN
    return ProviderEvidenceAggregate(
        status=status,
        degraded=False,
        all_checked=False,
        omitted_by_operator=_flagged(rows, _CODE_DISABLED_BY_OPERATOR),
        not_applicable=_flagged(rows, _CODE_NOT_APPLICABLE),
        final_observations=rows,
    )


def _flagged(rows: tuple[ProviderObservation, ...], code: str) -> bool:
    return any(obs.error_code == code for obs in rows)


__all__ = [
    "ProviderEvidenceAggregate",
    "aggregate_provider_observations",
    "final_observation_per_request",
    "request_key",
]
