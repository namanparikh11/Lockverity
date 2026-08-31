"""add scan_runs seeded_dataset (demo identity)

Revision ID: b8d2f4a6c1e9
Revises: f6a7b8c9d0e1
Create Date: 2026-08-31 10:00:00.000000

The production Demo page and the scan-list demo notice
previously identified the seeded demo dataset by inference:
hard-coded numeric scan ids (``/scans/1``, ``/scans/3``,
``/scans/4``) and the fixture repository's canonical URL. A
numeric primary key can never imply that a row is synthetic -
after migrations, reinstalls, scan deletion, or ordinary user
activity, scan id 1/3/4 may be a real user scan, and the UI
would then present (and link to) a real scan as a demo
scenario.

This migration adds a nullable ``seeded_dataset`` column on
``scan_runs`` plus a covering index. The column is the single
explicit provenance marker for seeded data:

- Only the demo loader (``backend/scripts/load_demo.py``)
  writes ``'demo'`` into it.
- Every application write path (intake, rescan, scan
  creation) leaves the column NULL, so a real user scan can
  never collide with demo identity regardless of its primary
  key, repository URL, status, or creation order.

The migration is purely additive and nullable because every
historical row predates the marker and is, by definition, not
seeded demo data. No historical row is rewritten.

The implementation uses Alembic batch mode because SQLite
requires the copy-and-move shape for indexed column
additions; the same mode is the documented portable shape
used by the previous migrations in this chain.

The migration is reversible: ``downgrade()`` drops both the
index and the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d2f4a6c1e9"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``seeded_dataset`` (nullable) + covering index."""
    with op.batch_alter_table(
        "scan_runs",
        recreate="always",
    ) as batch_op:
        batch_op.add_column(
            sa.Column("seeded_dataset", sa.String(length=32), nullable=True)
        )
    op.create_index(
        "ix_scan_runs_seeded_dataset",
        "scan_runs",
        ["seeded_dataset"],
    )


def downgrade() -> None:
    """Drop the index and the column."""
    op.drop_index(
        "ix_scan_runs_seeded_dataset",
        table_name="scan_runs",
    )
    with op.batch_alter_table(
        "scan_runs",
        recreate="always",
    ) as batch_op:
        batch_op.drop_column("seeded_dataset")
