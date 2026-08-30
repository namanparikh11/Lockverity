"""Spreadsheet-safe findings CSV exporter.

The CSV format is the easiest one for an operator to share with
non-engineers. It is also the easiest one to weaponize: a cell
that starts with ``=``, ``+``, ``-``, or ``@`` can be evaluated
as a formula by Excel / Google Sheets / LibreOffice. This
exporter sanitizes every cell with
:func:`app.utils.csv_safety.sanitize_cell` before writing, and
the writer always quotes fields (RFC 4180) to keep commas in
titles or summaries from breaking the row.

The export also writes a tiny header block (``#`` lines) that
includes the scan id, the tool version, and the export time.
The header lines start with ``#``, which spreadsheet importers
skip; the data rows are unambiguous.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app._version import __version__
from app.exporters._common import (
    ScanNotFoundError,
    fetch_findings,
    fetch_observations,
    get_scan_or_raise,
    provider_coverage_summary,
)
from app.providers.results import (
    ProviderOutcome,
    ProviderSuccess,
    ProviderUnavailable,
)
from app.utils.csv_safety import CsvCellError, csv_escape_field, sanitize_cell
from app.utils.datetime import utcnow

CSV_COLUMNS: tuple[str, ...] = (
    "scan_run_id",
    "rule_id",
    "category",
    "severity",
    "confidence",
    "title",
    "summary",
    "remediation",
    "location_path",
    "location_start_line",
    "location_end_line",
    "stable_key",
    "status",
)


class FindingsCsvExporter:
    """CSV exporter for scan findings."""

    format = "findings_csv"

    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        max_cell_length: int = 32_000,
    ) -> None:
        self._session_factory = session_factory
        self._max_cell_length = max_cell_length
        self._app_version = __version__

    def export(self, *, scan_run_id: int) -> ProviderSuccess[bytes] | ProviderUnavailable:
        session = self._session_factory()
        try:
            try:
                scan = get_scan_or_raise(session, scan_run_id)
            except ScanNotFoundError:
                return ProviderUnavailable(
                    error_code="export_scan_not_found",
                    error_summary=f"Scan {scan_run_id} not found.",
                    attempted_at=utcnow(),
                    outcome=ProviderOutcome.UNAVAILABLE,
                )
            findings = fetch_findings(session, scan_run_id)
            observations = fetch_observations(session, scan_run_id)
            output = self._render_csv(scan, findings, observations)
        finally:
            session.close()
        return ProviderSuccess(
            data=output.encode("utf-8"),
            fetched_at=utcnow(),
            records_returned=len(findings),
        )

    def _render_csv(self, scan, findings, observations) -> str:
        # Determinism contract: the export header carries
        # ``exported_at`` derived from the scan's
        # ``completed_at`` (falling back to ``created_at``).
        # The wall-clock moment the export was triggered is
        # never written into the document body. Two exports
        # of the same scan therefore emit the same bytes
        # bit-for-bit. The field name ``exported_at`` is the
        # original public contract (the v2.0.5 cycle 6
        # hardening pass renamed it to ``fetched_at`` in
        # error; the v2.0.6 cycle 7 closure restores the
        # historical name). The value semantics (a
        # deterministic per-scan timestamp) are unchanged.
        #
        # The helper is shared with the JSON exporter
        # (``app.exporters.findings_json._stable_fetched_at``);
        # the duplicated logic would drift. The CSV
        # exporter re-imports the helper at render time
        # to avoid a circular import at module load.
        from app.exporters.findings_json import _stable_fetched_at

        when_iso = _stable_fetched_at(scan)
        # Evidence-honesty contract: a CSV with zero data rows
        # must still state whether the external evidence
        # providers behind a "no findings" result actually
        # answered. The coverage lines live in the ``#``
        # comment header so they are present even when the
        # findings grid is empty, and they never add a fake
        # finding row. The values derive from the persisted
        # provider observations (deterministic per scan).
        coverage_label, provider_status = provider_coverage_summary(scan, observations)
        lines: list[str] = []
        lines.append(
            f"# lockverity findings export, tool=lockverity, version={self._app_version}, "
            f"scan_run_id={scan.id}, repository_id={scan.repository_id}, "
            f"exported_at={when_iso}"
        )
        lines.append(f"# provider_coverage={coverage_label}")
        lines.append(f"# provider_status={provider_status}")
        lines.append(_format_row(CSV_COLUMNS))
        for finding in findings:
            row = (
                str(scan.id),
                finding.rule_id or "",
                finding.category.value if finding.category else "",
                finding.severity.value if finding.severity else "",
                finding.confidence.value if finding.confidence else "",
                finding.title or "",
                finding.summary or "",
                finding.remediation or "",
                finding.location_path or "",
                str(finding.location_start_line) if finding.location_start_line else "",
                str(finding.location_end_line) if finding.location_end_line else "",
                finding.stable_key or "",
                finding.status.value if finding.status else "",
            )
            try:
                sanitized = tuple(
                    sanitize_cell(value, max_length=self._max_cell_length) for value in row
                )
            except CsvCellError as exc:
                # We do not fail the whole export; we record a
                # single cell-level error row.
                sanitized = tuple(
                    sanitize_cell(value, max_length=self._max_cell_length)
                    if i != 5
                    else f"<sanitization error: {exc}>"
                    for i, value in enumerate(row)
                )
            lines.append(_format_row(sanitized))
        return "\n".join(lines) + "\n"


def _format_row(values: tuple[str, ...]) -> str:
    return ",".join(csv_escape_field(sanitize_cell_for_csv(v)) for v in values)


def sanitize_cell_for_csv(value: str) -> str:
    if value is None:
        return ""
    return str(value)


__all__ = ["CSV_COLUMNS", "FindingsCsvExporter"]


def utc_now() -> datetime:
    """Public re-export for tests."""
    return utcnow()
