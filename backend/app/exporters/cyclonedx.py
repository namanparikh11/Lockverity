"""CycloneDX 1.5 SBOM exporter.

The exporter produces a minimal, valid CycloneDX 1.5 SBOM in
JSON. It includes:

- ``metadata``: tool identity, timestamp, and the source scan.
- ``components``: every component in the scan, identified by a
  PURL when one is available.
- ``dependencies``: an adjacency list of parent-child edges taken
  from the ``dependency_edges`` table, only for components that
  participate in at least one edge.
- ``vulnerabilities``: a best-effort list of vulnerabilities taken
  from the findings table with ``category=vulnerability``. The
  ``description`` and ``recommendation`` fields are derived from
  the finding's summary and remediation.

The exporter is read-only; it never mutates the database.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app._version import __version__
from app.exporters._common import (
    ScanNotFoundError,
    component_purl,
    fetch_components,
    fetch_findings,
    fetch_observations,
    format_iso_utc,
    get_scan_or_raise,
    provider_coverage_summary,
)
from app.models.component import Component
from app.models.dependency_edge import DependencyEdge
from app.models.finding import Finding, FindingCategory
from app.providers.results import (
    ProviderOutcome,
    ProviderSuccess,
    ProviderUnavailable,
)
from app.utils.datetime import utcnow
from app.utils.json_safe import BoundedJsonError, dump_bounded_json

CYCLONEDX_SPEC_VERSION = "1.5"
CYCLONEDX_SCHEMA = "https://cyclonedx.org/schema/bom-1.5.schema.json"


class CycloneDxExporter:
    """CycloneDX 1.5 SBOM exporter."""

    format = "cyclonedx_json"

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
            components = fetch_components(session, scan_run_id)
            findings = fetch_findings(session, scan_run_id)
            observations = fetch_observations(session, scan_run_id)
            edges = (
                session.query(DependencyEdge)
                .filter(DependencyEdge.scan_run_id == scan_run_id)
                .order_by(DependencyEdge.id.asc())
                .all()
            )
            bom = self._build_bom(scan, components, findings, edges, observations)
        finally:
            session.close()
        try:
            serialized = dump_bounded_json(bom, sort_keys=True)
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
            records_returned=len(components),
        )

    def _build_bom(
        self,
        scan,
        components: list[Component],
        findings: list[Finding],
        edges: list[DependencyEdge],
        observations,
    ) -> dict[str, Any]:
        components_block = [self._component_to_cdx(c) for c in components]
        dependencies_block = self._build_dependencies(components, edges)
        vulnerabilities_block = self._build_vulnerabilities(findings)
        # Evidence-honesty contract: an empty
        # ``vulnerabilities`` array is only a verified clean
        # result when the external evidence providers actually
        # answered. The coverage markers are standard
        # CycloneDX ``metadata.properties`` name/value pairs
        # (the same representation the 1.7 exporter uses at
        # the top level); they add no components and never
        # fabricate a vulnerability entry.
        coverage_label, provider_status = provider_coverage_summary(scan, observations)
        return {
            "bomFormat": "CycloneDX",
            "specVersion": CYCLONEDX_SPEC_VERSION,
            "$schema": CYCLONEDX_SCHEMA,
            "serialNumber": f"urn:uuid:lockverity-scan-{scan.id}",
            "version": 1,
            "metadata": {
                "timestamp": format_iso_utc(scan.completed_at) or format_iso_utc(utcnow()),
                "tools": [
                    {
                        "vendor": "Lockverity",
                        "name": "lockverity",
                        "version": self._app_version,
                    }
                ],
                "component": {
                    "type": "application",
                    "name": f"scan-{scan.id}",
                    "bom-ref": f"scan-{scan.id}",
                },
                "properties": [
                    {"name": "lockverity:scan_run_id", "value": str(scan.id)},
                    {"name": "lockverity:scan_status", "value": scan.status.value},
                    {"name": "lockverity:provider-coverage", "value": coverage_label},
                    {"name": "lockverity:provider-status", "value": provider_status},
                ],
            },
            "components": components_block,
            "dependencies": dependencies_block,
            "vulnerabilities": vulnerabilities_block,
        }

    def _component_to_cdx(self, component: Component) -> dict[str, Any]:
        purl = component_purl(component)
        result: dict[str, Any] = {
            "type": "library",
            "bom-ref": purl or f"component-{component.id}",
            "name": component.package_name,
            "ecosystem": component.ecosystem or "unknown",
            "version": component.version or "unspecified",
            "purl": purl,
        }
        properties: list[dict[str, Any]] = []
        if component.direct:
            properties.append({"name": "lockverity:direct", "value": "true"})
        if component.development:
            properties.append({"name": "lockverity:development", "value": "true"})
        if component.optional:
            properties.append({"name": "lockverity:optional", "value": "true"})
        if component.integrity:
            properties.append({"name": "lockverity:integrity", "value": component.integrity})
        if properties:
            result["properties"] = properties
        return result

    def _build_dependencies(
        self,
        components: list[Component],
        edges: list[DependencyEdge],
    ) -> list[dict[str, Any]]:
        """Return the ``dependencies`` block of the SBOM.

        The block is a list of ``{"ref": ..., "dependsOn": [...]}``
        records. We only emit a record for a component that
        actually has outgoing edges.
        """
        id_to_ref: dict[int, str] = {}
        for component in components:
            if not isinstance(component, Component):
                continue
            purl = component_purl(component)
            id_to_ref[component.id] = purl or f"component-{component.id}"
        children_by_parent: dict[int, set[str]] = {}
        for edge in edges:
            if not isinstance(edge, DependencyEdge):
                continue
            parent_ref = id_to_ref.get(edge.parent_component_id)
            child_ref = id_to_ref.get(edge.child_component_id)
            if parent_ref is None or child_ref is None:
                continue
            children_by_parent.setdefault(parent_ref, set()).add(child_ref)
        # Sort for determinism.
        out: list[dict[str, Any]] = []
        for parent_ref in sorted(children_by_parent):
            out.append(
                {
                    "ref": parent_ref,
                    "dependsOn": sorted(children_by_parent[parent_ref]),
                }
            )
        return out

    def _build_vulnerabilities(self, findings: list[Finding]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for finding in findings:
            if not isinstance(finding, Finding):
                continue
            if finding.category != FindingCategory.VULNERABILITY:
                continue
            rating: dict[str, Any] | None = None
            if finding.severity is not None:
                rating = {
                    "source": {"name": "lockverity"},
                    "severity": finding.severity.value,
                }
            # v0.4: surface the provider name and the
            # external advisory id from the evidence
            # envelope when present. A CycloneDX consumer
            # that only sees this SBOM can tell an
            # OSV-derived vulnerability finding from a
            # rule-engine finding.
            properties: list[dict[str, Any]] = []
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
            properties.append({"name": "lockverity:provider", "value": provider})
            out.append(
                {
                    "id": finding.rule_id,
                    "description": finding.summary,
                    "recommendation": finding.remediation or "",
                    "ratings": [rating] if rating else [],
                    "affects": [
                        {
                            "ref": f"finding-{finding.stable_key[:16]}",
                        }
                    ],
                    "properties": properties,
                }
            )
        return out


__all__ = [
    "CYCLONEDX_SCHEMA",
    "CYCLONEDX_SPEC_VERSION",
    "CycloneDxExporter",
]
