"""Scan API schemas."""

from __future__ import annotations

from dataclasses import field
from datetime import datetime
from typing import Any

from app.models.scan_run import ScanStatus, ScanTriggerType
from app.schemas.common import NonEmptyStr, SchemaModel, TimestampMixin


class ScanCreate(SchemaModel):
    """Payload for ``POST /api/v1/repositories/{id}/scans``."""

    trigger_type: ScanTriggerType = ScanTriggerType.MANUAL
    requested_ref: NonEmptyStr | None = None


class ScanRead(TimestampMixin):
    id: int
    repository_id: int
    status: ScanStatus
    trigger_type: ScanTriggerType
    requested_ref: str | None = None
    resolved_commit_sha: str | None = None
    analyzer_version: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_summary: str | None = None


class ScanStageRead(TimestampMixin):
    id: int
    scan_run_id: int
    stage_type: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    provider: str | None = None
    provider_status: str | None = None
    records_processed: int
    failure_code: str | None = None
    failure_summary: str | None = None
    # v2.0.6: derived message severity. Computed at the
    # API boundary from ``status``, ``records_processed``,
    # ``failure_code``, and ``failure_summary``. Never
    # persisted; never used to fabricate a security
    # conclusion. Values: ``"error"`` (failed stage with
    # a real failure code or summary), ``"warning"``
    # (partial stage or completed stage with non-zero
    # records and a residual summary, e.g. parser
    # warnings), ``"info"`` (completed stage with zero
    # records and a normal no-data summary, e.g. "No OSV
    # advisories were returned"), or ``"none"`` (no
    # message requiring emphasis). The frontend uses
    # this to choose between error / warning / info
    # styling; the visible text never begins with
    # ``"Failure: "`` for an ``info`` or ``"warning"``
    # severity row.
    message_severity: str | None = None


class FindingRead(TimestampMixin):
    id: int
    scan_run_id: int
    repository_id: int
    rule_id: str
    category: str
    severity: str
    confidence: str
    title: str
    summary: str
    remediation: str | None = None
    evidence_json: str | None = None
    location_path: str | None = None
    location_start_line: int | None = None
    location_end_line: int | None = None
    stable_key: str
    status: str


class ProviderObservationRead(TimestampMixin):
    id: int
    scan_run_id: int
    # The logical request the row belongs to. Per-component
    # observations (OSV lookups, deps.dev enrichments) set it;
    # scan-level observations (Scorecard) leave it ``None``.
    # Consumers use it to keep retry semantics truthful: a
    # later row for the same request supersedes the earlier
    # attempt, while unrelated requests stay independent.
    component_id: int | None = None
    provider: str
    operation: str
    status: str
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    http_status: int | None = None
    records_returned: int
    cache_status: str | None = None
    retry_after: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None


class ComponentRead(SchemaModel):
    id: int
    scan_run_id: int
    manifest_id: int
    ecosystem: str | None = None
    package_name: str
    version: str | None = None
    version_source: str
    package_url: str | None = None
    scope: str | None = None
    relationship: str | None = None
    direct: bool
    development: bool
    optional: bool
    integrity: str | None = None


class ComponentEnrichment(SchemaModel):
    """Provider enrichment metadata attached to a component.

    The fields are always present; ``null`` means the
    provider was not queried (e.g. unsupported ecosystem or
    no concrete version). The frontend can render a "never
    queried" empty state without falling back to fixtures.
    """

    component_id: int
    ecosystem: str | None
    package_name: str
    version: str | None
    fetched_at: str | None
    cache_status: str
    provider_url: str | None
    source_provenance: str | None
    license_observations: list[str]
    dependency_count: int | None
    provider_status: str | None
    unavailable_reason: str | None
    # v0.4 honesty fix: the structured evidence envelope
    # persisted on the underlying ``provider_observations``
    # row. The column is the single source of truth for
    # successful provider data; ``error_summary`` is never
    # used to transport evidence. ``None`` means the
    # observation did not return a structured envelope
    # (failure, unsupported ecosystem, never queried).
    evidence: dict[str, Any] | None = None


class DependencyPathEntry(SchemaModel):
    id: int
    package_name: str
    version: str | None = None
    version_source: str
    ecosystem: str | None = None
    direct: bool
    development: bool


class DependencyPathRead(SchemaModel):
    components: list[DependencyPathEntry]
    edges: list[dict]
    truncated: bool


class AdvisoryRead(SchemaModel):
    id: int
    source: str
    source_advisory_id: str
    canonical_id: str | None = None
    summary: str | None = None
    details_url: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    withdrawn_at: datetime | None = None
    raw_payload_sha256: str | None = None


class ComponentAdvisoryRead(SchemaModel):
    id: int
    component_id: int
    advisory_id: int
    fixed_versions: list[str]
    severity_source: str | None = None
    # v0.4 honesty fix: confidence is a free-form string so
    # we can return ``None`` when the upstream provider did
    # not supply one. Lockverity never infers a confidence
    # value from severity, the presence of an advisory, or
    # the upstream name. The previous v0.4 implementation
    # substituted ``medium`` / ``high`` here; that was
    # removed. The frontend renders the ``None`` case as
    # "Not supplied" / "Unknown".
    confidence: str | None = None
    dependency_paths: list[dict]
    withdrawn: bool
    # Enriched join fields for the frontend.
    package_name: str | None = None
    package_version: str | None = None
    ecosystem: str | None = None
    direct: bool | None = None
    advisory_source: str | None = None
    advisory_external_id: str | None = None
    advisory_canonical_id: str | None = None
    advisory_summary: str | None = None
    advisory_details_url: str | None = None
    affected: bool
    severity_label: str | None = None
    severity_score: float | None = None
    # v0.4 additions: explicit provider provenance, aliases,
    # and freshness. A row is added to ``component_advisories``
    # only when the underlying provider returned data;
    # ``provider_provenance`` therefore always names a real
    # upstream.
    provider_provenance: str | None = None
    aliases: list[str] = field(default_factory=list)
    fetched_at: str | None = None


class WorkflowFindingRead(SchemaModel):
    id: int
    scan_run_id: int
    repository_id: int
    rule_id: str
    severity: str
    confidence: str
    workflow_path: str
    workflow_name: str
    title: str
    summary: str
    remediation: str | None = None
    permissions: list[str]
    triggers: list[str]
    unpinned_actions: list[str]
    yaml_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    stable_key: str
    limitations: list[str]


# The v0.5 scan-comparison schema lives in
# :file:`app/schemas/comparison.py`. The endpoint in
# :mod:`app.api.v0_3` returns :class:`comparison.ScanComparisonResponse`
# directly. The legacy ``ScanComparisonRead`` shape (a flat dict
# per row, with the old "added / removed / updated / persisting /
# resolved" vocabulary) has been removed: the new shape is
# strictly typed, preserves provenance, and uses the v0.5
# evidence-honest state vocabulary.
