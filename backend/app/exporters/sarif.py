"""SARIF-compatible static findings exporter.

The exporter emits SARIF 2.1.0 JSON. The output contains a single
``run`` with one ``tool`` (Lockverity) and one ``result`` per
finding.

Lockverity's findings without a ``location_path`` are **not**
forced into SARIF. SARIF's ``physicalLocation`` requires an
``artifactLocation``; a finding without a file path is not a
"static" finding, and pretending otherwise would mislead
downstream tooling. The exporter records the count of skipped
findings in the ``properties`` block.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
from app.utils.datetime import utcnow
from app.utils.json_safe import BoundedJsonError, dump_bounded_json

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"


class SarifStaticFindingsExporter:
    """SARIF 2.1.0 exporter for static findings."""

    format = "sarif"

    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
    ) -> None:
        self._session_factory = session_factory
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
            sarif = self._build_sarif(scan, findings, observations)
        finally:
            session.close()
        try:
            serialized = dump_bounded_json(sarif, sort_keys=True)
        except BoundedJsonError as exc:
            return ProviderUnavailable(
                error_code="export_too_large",
                error_summary=str(exc),
                attempted_at=utcnow(),
                outcome=ProviderOutcome.UNAVAILABLE,
            )
        return ProviderSuccess(
            data=serialized.encode("utf-8"),
            fetched_at=utcnow(),
            records_returned=len(findings),
        )

    def _build_sarif(self, scan, findings, observations) -> dict[str, Any]:
        rules: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        skipped_count = 0
        for finding in findings:
            rules[finding.rule_id] = self._rule(finding)
            if not finding.location_path:
                skipped_count += 1
                continue
            results.append(self._result(finding))
        sarif_results = sorted(results, key=lambda r: r.get("ruleId", ""))
        # Evidence-honesty contract: an empty ``results`` array
        # is only a verified clean result when the external
        # evidence providers actually answered. The coverage
        # properties are plain string values in the standard
        # SARIF 2.1.0 run-level property bag; they add no
        # results and never fabricate a finding.
        coverage_label, provider_status = provider_coverage_summary(scan, observations)
        return {
            "$schema": SARIF_SCHEMA,
            "version": SARIF_VERSION,
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "lockverity",
                            "version": self._app_version,
                            "informationUri": "https://github.com/lockverity/lockverity",
                            "rules": sorted(rules.values(), key=lambda r: r["id"]),
                        }
                    },
                    "originalUriBaseIds": {"PROJECTROOT": {"uri": "file:///"}},
                    "properties": {
                        "lockverity:scan_run_id": str(scan.id),
                        "lockverity:scan_status": scan.status.value,
                        "lockverity:findings_skipped_no_location": skipped_count,
                        # Evidence coverage for the whole run. An
                        # empty results array plus
                        # ``provider-coverage != "ok"`` means the
                        # evidence sources did not answer, not
                        # that the code is clean.
                        "lockverity:provider-coverage": coverage_label,
                        "lockverity:provider-status": provider_status,
                        # v0.4: surface the provider
                        # provenance for every finding that
                        # carries a non-local rule id. The
                        # SARIF run stays a single
                        # ``lockverity`` driver; the
                        # ``properties`` block names the
                        # upstream that supplied the
                        # evidence so downstream tooling
                        # can tell an OSV-derived
                        # vulnerability finding from a
                        # rule-engine finding.
                        "lockverity:providers": sorted(
                            {
                                (
                                    json.loads(f.evidence_json).get("provider")
                                    if f.evidence_json
                                    else None
                                )
                                or "local"
                                for f in findings
                            }
                        ),
                    },
                    "results": sarif_results,
                }
            ],
        }

    def _rule(self, finding) -> dict[str, Any]:
        return {
            "id": finding.rule_id,
            "name": finding.rule_id,
            "shortDescription": {"text": finding.title or ""},
            "fullDescription": {"text": finding.summary or ""},
            "helpUri": "https://github.com/lockverity/lockverity/docs",
            "defaultConfiguration": {
                "level": _severity_to_sarif_level(
                    finding.severity.value if finding.severity else None
                ),
            },
            "properties": {
                "category": finding.category.value if finding.category else None,
                "confidence": finding.confidence.value if finding.confidence else None,
            },
        }

    def _result(self, finding) -> dict[str, Any]:
        region: dict[str, Any] = {}
        if finding.location_start_line is not None:
            region["startLine"] = finding.location_start_line
        if finding.location_end_line is not None:
            region["endLine"] = finding.location_end_line
        location: dict[str, Any] = {
            "physicalLocation": {
                "artifactLocation": {
                    "uri": finding.location_path or "",
                    "uriBaseId": "PROJECTROOT",
                },
            }
        }
        if region:
            location["physicalLocation"]["region"] = region
        # v0.4: extract the provider name from the
        # ``evidence_json`` envelope when present. A
        # finding without one is local (rule engine) and
        # we set ``provider="local"`` so downstream tools
        # can tell a rule engine finding from a provider
        # observation.
        provider = "local"
        if finding.evidence_json:
            try:
                envelope = json.loads(finding.evidence_json)
            except (ValueError, TypeError):
                envelope = None
            if isinstance(envelope, dict):
                raw_provider = envelope.get("provider")
                if isinstance(raw_provider, str) and raw_provider:
                    provider = raw_provider
        return {
            "ruleId": finding.rule_id,
            "level": _severity_to_sarif_level(finding.severity.value if finding.severity else None),
            "message": {"text": finding.summary or ""},
            "locations": [location],
            "properties": {
                "lockverity:stable_key": finding.stable_key,
                "lockverity:remediation": finding.remediation or "",
                "lockverity:provider": provider,
            },
        }


def _severity_to_sarif_level(severity: str | None) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity in {"medium"}:
        return "warning"
    if severity in {"low", "informational"}:
        return "note"
    return "none"


__all__ = ["SARIF_SCHEMA", "SARIF_VERSION", "SarifStaticFindingsExporter"]
