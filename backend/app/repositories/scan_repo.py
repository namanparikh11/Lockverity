"""Scan data-access helpers."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType


def list_scans_by_status(
    session: Session,
    statuses: Sequence[ScanStatus],
) -> Sequence[ScanRun]:
    """Return scans whose status is in ``statuses`` (oldest first)."""
    if not statuses:
        return ()
    stmt = select(ScanRun).where(ScanRun.status.in_(list(statuses))).order_by(ScanRun.id.asc())
    return session.execute(stmt).scalars().all()


def count_by_status(session: Session, status: ScanStatus) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(ScanRun).where(ScanRun.status == status)
        ).scalar_one()
        or 0
    )


def get_scan_by_id(session: Session, scan_id: int) -> ScanRun | None:
    return session.get(ScanRun, scan_id)


def list_scans_for_repository(
    session: Session,
    repository_id: int,
    *,
    page: int,
    page_size: int,
    status: ScanStatus | None = None,
    trigger_type: ScanTriggerType | None = None,
) -> tuple[Sequence[ScanRun], int]:
    """List scans for a single repository, paginated.

    The v1.8 page added URL-persisted ``status`` and ``trigger_type``
    filters; v2.0.1 actually wires them up at the data layer. The
    filters are optional and are validated by the route's ``Query``
    layer so unknown values never reach this function. ``None`` means
    "no filter" and preserves the v1.7 ordering contract.
    """
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    base = select(ScanRun).where(ScanRun.repository_id == repository_id)
    if status is not None:
        base = base.where(ScanRun.status == status)
    if trigger_type is not None:
        base = base.where(ScanRun.trigger_type == trigger_type)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    stmt = base.order_by(ScanRun.id.desc()).limit(page_size).offset((page - 1) * page_size)
    items = session.execute(stmt).scalars().all()
    return items, int(total or 0)


def list_scans(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: ScanStatus | None = None,
    trigger_type: ScanTriggerType | None = None,
    seeded_dataset: str | None = None,
) -> tuple[Sequence[ScanRun], int]:
    """List scans across all repositories, paginated.

    Used by the cross-repository dashboard rollup. The query is
    intentionally simple: ``(status, trigger_type,
    seeded_dataset)`` are optional filters, results are ordered
    newest-first so the dashboard can show "the most recent
    scans across everything".

    ``seeded_dataset`` selects rows carrying the explicit
    seeded-data provenance marker (e.g. ``"demo"`` for
    demo-loader rows). It is the only supported way to select
    seeded rows: the filter matches the persisted marker, never
    a numeric id or any heuristic, so real user scans are
    excluded by construction.
    """
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    base = select(ScanRun)
    if status is not None:
        base = base.where(ScanRun.status == status)
    if trigger_type is not None:
        base = base.where(ScanRun.trigger_type == trigger_type)
    if seeded_dataset is not None:
        base = base.where(ScanRun.seeded_dataset == seeded_dataset)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    stmt = base.order_by(ScanRun.id.desc()).limit(page_size).offset((page - 1) * page_size)
    items = session.execute(stmt).scalars().all()
    return items, int(total or 0)


def create_scan(
    session: Session,
    *,
    repository_id: int,
    trigger_type: ScanTriggerType,
    requested_ref: str | None = None,
) -> ScanRun:
    scan = ScanRun(
        repository_id=repository_id,
        trigger_type=trigger_type,
        status=ScanStatus.QUEUED,
        requested_ref=requested_ref,
    )
    session.add(scan)
    session.flush()
    return scan
