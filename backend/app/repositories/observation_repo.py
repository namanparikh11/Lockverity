"""Provider observation data-access helpers."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.provider_observation import ProviderObservation, ProviderStatus


def list_observations_for_scan(
    session: Session,
    scan_run_id: int,
    *,
    page: int,
    page_size: int,
    status: ProviderStatus | None = None,
    provider: str | None = None,
) -> tuple[Sequence[ProviderObservation], int]:
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    stmt_total = (
        select(func.count())
        .select_from(ProviderObservation)
        .where(ProviderObservation.scan_run_id == scan_run_id)
    )
    stmt = select(ProviderObservation).where(ProviderObservation.scan_run_id == scan_run_id)
    if status is not None:
        stmt_total = stmt_total.where(ProviderObservation.status == status)
        stmt = stmt.where(ProviderObservation.status == status)
    if provider:
        stmt_total = stmt_total.where(ProviderObservation.provider == provider)
        stmt = stmt.where(ProviderObservation.provider == provider)
    total = session.execute(stmt_total).scalar_one()
    stmt = (
        stmt.order_by(ProviderObservation.id.asc()).limit(page_size).offset((page - 1) * page_size)
    )
    items = session.execute(stmt).scalars().all()
    return items, int(total or 0)


def provider_health_rollup(session: Session) -> list[dict]:
    """Return the per-provider availability rollup across all scans.

    The shape is intentionally narrow so the frontend can render a
    small card per provider without an additional mapping pass. A
    provider that has never been queried is still returned with
    ``status=not_requested`` and zero counts; that is the honest
    baseline.

    The query is written in a portable two-step form so it works
    on both SQLite (development) and PostgreSQL (production):

    1. Aggregate per-provider counts in one subquery.
    2. Pull the *latest* row per provider via an ``IN`` predicate
       against a sub-select of ``max(id) GROUP BY provider``.

    This avoids cross joins and is the safest portable shape.
    """
    aggregate_subq = (
        select(
            ProviderObservation.provider.label("provider"),
            func.max(ProviderObservation.id).label("latest_id"),
            func.count(ProviderObservation.id).label("total_count"),
            func.count(func.distinct(ProviderObservation.scan_run_id)).label("scans_with_obs"),
        )
        .group_by(ProviderObservation.provider)
        .subquery()
    )
    rows = session.execute(
        select(
            aggregate_subq.c.provider,
            aggregate_subq.c.total_count,
            aggregate_subq.c.scans_with_obs,
            ProviderObservation.status,
            ProviderObservation.records_returned,
            ProviderObservation.cache_status,
            ProviderObservation.completed_at,
            ProviderObservation.error_code,
            ProviderObservation.error_summary,
        )
        .select_from(aggregate_subq)
        .join(
            ProviderObservation,
            ProviderObservation.id == aggregate_subq.c.latest_id,
        )
    ).all()
    return [
        {
            "provider": row.provider,
            "status": row.status,
            "records_returned": int(row.records_returned or 0),
            "cache_status": row.cache_status,
            "last_retrieved_at": row.completed_at,
            "redacted_failure_summary": row.error_summary,
            "last_error_code": row.error_code,
            "scans_with_observations": int(row.scans_with_obs or 0),
        }
        for row in rows
    ]


def known_provider_names() -> list[str]:
    """Return the static set of provider names the UI knows about.

    These match the union of providers Lockverity can call. A
    provider is always rendered even if no scan has yet queried
    it - that is the honest "not_requested" baseline.
    """
    return ["github", "osv", "deps_dev", "openssf"]
