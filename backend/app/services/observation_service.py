"""Provider observation service.

The per-scan observation listing is read-only. The
cross-scan provider-health rollup is read-only too - it is the
single source of truth for what the dashboard's "Provider health"
panel renders.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.provider_observation import ProviderObservation, ProviderStatus
from app.repositories import observation_repo
from app.services import scan_service


def list_provider_observations(
    session: Session,
    scan_id: int,
    *,
    page: int,
    page_size: int,
    status: ProviderStatus | None = None,
    provider: str | None = None,
) -> tuple[Sequence[ProviderObservation], int]:
    scan_service.get_scan_or_404(session, scan_id)
    return observation_repo.list_observations_for_scan(
        session,
        scan_id,
        page=page,
        page_size=page_size,
        status=status,
        provider=provider,
    )


def provider_health(
    session: Session,
) -> dict:
    """Return the per-provider health rollup.

    The result is shaped for direct consumption by the frontend's
    ``useApiList`` and dashboard card components. Providers that
    have never been queried are still returned with status
    ``not_requested`` so the UI does not silently hide a
    never-used provider.
    """
    observed = observation_repo.provider_health_rollup(session)
    by_name = {entry["provider"]: entry for entry in observed}
    entries: list[dict] = []
    for name in observation_repo.known_provider_names():
        if name in by_name:
            entries.append(by_name[name])
        else:
            entries.append(
                {
                    "provider": name,
                    "status": ProviderStatus.NOT_REQUESTED.value,
                    "records_returned": 0,
                    "cache_status": None,
                    "last_retrieved_at": None,
                    "redacted_failure_summary": None,
                    "last_error_code": None,
                    "scans_with_observations": 0,
                }
            )
    return {
        "providers": list(observation_repo.known_provider_names()),
        "entries": entries,
    }
