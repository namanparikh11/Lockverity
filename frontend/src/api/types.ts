// API response type definitions. Kept narrow on purpose - the
// frontend never assumes a field is present unless the backend
// guarantees it in the OpenAPI schema.
//
// The v0.1 backend already exposes repositories, scans, stages,
// findings, and provider observations. The additional types below
// are forward-compatible declarations for the v0.2+/v0.3+ endpoints
// the frontend pages already use. The frontend gracefully renders
// "not yet available" empty states when a backend endpoint is not
// yet implemented (HTTP 404 or 501 is mapped to an empty list).

export type ScanStatus =
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type ScanTriggerType = "manual" | "upload" | "scheduled" | "api";

export type StageType =
  | "repository_intake"
  | "archive_validation"
  | "manifest_discovery"
  | "dependency_parsing"
  | "dependency_enrichment"
  | "vulnerability_query"
  | "workflow_analysis"
  | "repository_posture"
  | "finding_reconciliation"
  | "export_generation";

export type StageStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "skipped";

export type ProviderStatus =
  | "available"
  | "partial"
  | "rate_limited"
  | "unavailable"
  | "not_requested"
  | "cached"
  | "unknown";

export type FindingSeverity =
  | "informational"
  | "low"
  | "medium"
  | "high"
  | "critical"
  | "unknown";

export type FindingConfidence =
  | "low"
  | "medium"
  | "high"
  | "confirmed"
  | "unknown";

export type FindingStatus = "open" | "resolved" | "accepted" | "suppressed";

export type FindingCategory =
  | "dependency"
  | "vulnerability"
  | "workflow"
  | "repository_posture"
  | "licence"
  | "provider"
  | "data_quality";

export type RepositorySourceType = "github" | "uploaded_archive";
export type RepositoryProvider = "github" | "local_upload";
export type RepositoryVisibility = "public" | "private" | "unknown";

export type ComponentVersionSource =
  | "manifest"
  | "lockfile"
  | "override"
  | "unresolved"
  | "unknown";

export type ComponentScope = "runtime" | "development" | "build" | "test" | "optional" | "unknown";

export type DependencyScopeFilter = "all" | "direct" | "transitive";
export type DevelopmentScopeFilter = "all" | "production" | "development";
export type VulnerableOnlyFilter = "all" | "vulnerable";

// ---- System ----

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  version: string;
  environment: string;
  timestamp: string;
}

export interface SystemInfoResponse {
  name: string;
  version: string;
  tagline: string;
  environment: string;
  api_prefix: string;
  archive_limits: Record<string, number>;
  pagination: Record<string, number>;
  provider_safety: Record<string, number>;
}

// ---- Repositories ----

export interface Repository {
  id: number;
  source_type: RepositorySourceType;
  provider: RepositoryProvider;
  owner: string;
  name: string;
  canonical_url: string | null;
  default_branch: string | null;
  description: string | null;
  visibility: RepositoryVisibility;
  archived: boolean;
  last_provider_sync_at: string | null;
  created_at: string;
  updated_at: string;
  // v2.0.5: basename of the original uploaded filename, or
  // null for GitHub rows. The repository list uses this as
  // the primary human-readable label for uploaded rows; the
  // operator never has to read the opaque ``upload/<key>``
  // canonical URL to know which row is which.
  original_filename: string | null;
}

// v2.0.5: per-row summary. The list endpoint returns a
// ``RepositoryWithSummary`` shape (see below); a single
// ``GET /repositories/{id}`` continues to return the bare
// ``Repository`` shape so other surfaces that look up one
// repository by id are unchanged.
export interface RepositoryLatestScan {
  id: number;
  status: ScanStatus;
  trigger_type: ScanTriggerType;
  created_at: string;
  completed_at: string | null;
}

export interface RepositorySummary {
  scan_count: number;
  // Number of scans that the comparator will accept
  // (completed or partial; the same set the comparison
  // page uses).
  eligible_comparison_scan_count: number;
  latest_scan: RepositoryLatestScan | null;
}

export interface RepositoryWithSummary extends Repository {
  // The server-computed primary human-readable label.
  // GitHub: ``owner/repository``. Uploaded: the basename
  // of the original filename (or the bounded fallback for
  // historical rows where the filename is unavailable).
  display_name: string;
  // The server-computed secondary technical identifier.
  // GitHub: the canonical URL. Uploaded: ``upload/<short-key>``.
  canonical_identity: string;
  summary: RepositorySummary;
}

export interface RepositoryCreatePayload {
  canonical_url: string;
  requested_ref?: string;
}

// ---- Intake (v1.5) ----
//
// The /repositories/github and /repositories/upload intake
// endpoints return an ``IntakeResultRead`` shape that bundles
// the freshly-created repository, scan, workspace, and a
// free-form summary. The frontend types mirror the
// backend ``app.schemas.intake.IntakeResultRead``
// declaration; the response is read-only and never mutated
// on the client.

export type WorkspaceKind = "github" | "uploaded_archive";
export type WorkspaceState =
  | "quarantined"
  | "validating"
  | "ready"
  | "failed"
  | "cleaned_up";

export interface Workspace {
  id: number;
  scan_run_id: number;
  workspace_key: string;
  kind: WorkspaceKind;
  state: WorkspaceState;
  archive_filename: string | null;
  archive_sha256: string | null;
  archive_size: number;
  file_count: number;
  uncompressed_size: number;
  failure_code: string | null;
  failure_summary: string | null;
  ready_at: string | null;
  cleaned_up_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntakeResult {
  repository: Repository;
  scan: Scan;
  workspace: Workspace;
  intake_summary: Record<string, unknown>;
}

// ---- Scans ----

export interface Scan {
  id: number;
  repository_id: number;
  status: ScanStatus;
  trigger_type: ScanTriggerType;
  requested_ref: string | null;
  resolved_commit_sha: string | null;
  analyzer_version: string | null;
  // Explicit seeded-dataset provenance. "demo" marks a row
  // written by the demo loader; every application-created
  // scan reports null. Demo surfaces identify demo rows by
  // this marker alone - never by numeric id, repository URL,
  // status, or creation order - so a real user scan can never
  // be treated as demo data.
  seeded_dataset?: string | null;
  started_at: string | null;
  completed_at: string | null;
  failure_code: string | null;
  failure_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScanCreatePayload {
  trigger_type?: ScanTriggerType;
  requested_ref?: string;
}

export interface ExternalEvidenceProviderSelection {
  osv: boolean;
  deps_dev: boolean;
  openssf: boolean;
}

export interface ScanRunPayload {
  force?: boolean;
  external_evidence_providers?: Partial<ExternalEvidenceProviderSelection>;
}

export interface ScanStage {
  id: number;
  scan_run_id: number;
  stage_type: StageType;
  status: StageStatus;
  started_at: string | null;
  completed_at: string | null;
  provider: string | null;
  provider_status: string | null;
  records_processed: number;
  failure_code: string | null;
  failure_summary: string | null;
  // v2.0.6: derived message severity. Computed at the
  // API boundary from the existing structured fields
  // (status, records_processed, failure_code,
  // failure_summary); never persisted. The frontend
  // uses this to choose between error / warning /
  // info / none styling. The visible text never
  // begins with "Failure: " when the severity is
  // "info" or "warning" - the failure label only
  // accompanies an actual "error" severity.
  message_severity: "error" | "warning" | "info" | "none";
  created_at: string;
  updated_at: string;
}

// ---- Findings ----

export interface Finding {
  id: number;
  scan_run_id: number;
  repository_id: number;
  rule_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  confidence: FindingConfidence;
  title: string;
  summary: string;
  remediation: string | null;
  evidence_json: string | null;
  location_path: string | null;
  location_start_line: number | null;
  location_end_line: number | null;
  stable_key: string;
  status: FindingStatus;
  created_at: string;
  updated_at: string;
}

// ---- Provider observations ----

export interface ProviderObservation {
  id: number;
  scan_run_id: number;
  /** Logical request key; null for scan-level observations. */
  component_id?: number | null;
  provider: string;
  operation: string;
  status: ProviderStatus;
  requested_at: string | null;
  completed_at: string | null;
  http_status: number | null;
  records_returned: number;
  cache_status: string | null;
  retry_after: string | null;
  error_code: string | null;
  error_summary: string | null;
  created_at: string;
  updated_at: string;
}

// ---- Components / dependencies (forward-compatible) ----

export interface Component {
  id: number;
  scan_run_id: number;
  manifest_id: number;
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  version_source: ComponentVersionSource;
  package_url: string | null;
  scope: string | null;
  relationship: string | null;
  direct: boolean;
  development: boolean;
  optional: boolean;
  integrity: string | null;
  created_at: string;
  updated_at: string;
}

export interface DependencyEdge {
  id: number;
  scan_run_id: number;
  parent_component_id: number;
  child_component_id: number;
  depth: number;
  resolved: boolean;
}

export interface DependencyPath {
  components: Component[];
  edges: DependencyEdge[];
  truncated: boolean;
}

// ---- Component enrichments (v0.4) ----

export interface ComponentEnrichment {
  component_id: number;
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  fetched_at: string | null;
  cache_status: string;
  provider_url: string | null;
  source_provenance: string | null;
  license_observations: string[];
  dependency_count: number | null;
  provider_status: ProviderStatus | null;
  unavailable_reason: string | null;
  // v0.4 honesty fix: the structured evidence envelope
  // from the underlying provider_observations row.
  // ``null`` for failed / never-queried rows; a JSON
  // object with ``licences`` and ``dependency_count`` for
  // successful rows. The UI never fabricates values.
  evidence: Record<string, unknown> | null;
}

// ---- Advisories / vulnerabilities (forward-compatible) ----

export interface Advisory {
  id: number;
  source: string;
  source_advisory_id: string;
  canonical_id: string | null;
  summary: string | null;
  details_url: string | null;
  published_at: string | null;
  modified_at: string | null;
  withdrawn_at: string | null;
  raw_payload_sha256: string | null;
  created_at: string;
  updated_at: string;
}

export interface ComponentAdvisory {
  id: number;
  component_id: number;
  advisory_id: number;
  fixed_versions: string[];
  severity_source: string | null;
  // v0.4 honesty fix: confidence is now nullable. The
  // previous v0.4 implementation substituted ``medium``
  // or ``high``; that was removed because the upstream
  // provider (OSV) does not supply a confidence. The
  // UI renders the ``null`` case as "Not supplied".
  confidence: FindingConfidence | null;
  dependency_paths: DependencyPath[];
  withdrawn: boolean;
  // v0.4 additions. The provider_provenance always
  // names a real upstream; ``local`` indicates a
  // rule-engine finding. Aliases cover the canonical
  // CVE / GHSA cross-references the provider
  // publishes. Fetched_at is the UTC ISO timestamp of
  // the provider call; ``null`` means the row was
  // written before v0.4 (legacy).
  provider_provenance?: string | null;
  aliases?: string[];
  fetched_at?: string | null;
  // The v0.3 read-side fields stay available for
  // backwards compatibility; new code can read the
  // renamed fields below.
  package_name?: string | null;
  package_version?: string | null;
  ecosystem?: string | null;
  direct?: boolean | null;
  advisory_source?: string | null;
  advisory_external_id?: string | null;
  advisory_canonical_id?: string | null;
  advisory_summary?: string | null;
  advisory_details_url?: string | null;
  affected: boolean;
  severity_label?: string | null;
  severity_score?: number | null;
}

// ---- Workflow findings (forward-compatible) ----

export interface WorkflowFinding {
  id: number;
  scan_run_id: number;
  repository_id: number;
  rule_id: string;
  severity: FindingSeverity;
  confidence: FindingConfidence;
  workflow_path: string;
  workflow_name: string;
  title: string;
  summary: string;
  remediation: string | null;
  permissions: string[];
  triggers: string[];
  unpinned_actions: string[];
  yaml_path: string | null;
  start_line: number | null;
  end_line: number | null;
  stable_key: string;
  limitations: string[];
  created_at: string;
  updated_at: string;
}

// ---- OpenSSF posture (forward-compatible) ----

export interface OpenSSFCheck {
  id: number;
  scan_run_id: number;
  repository_id: number;
  check_id: string;
  name: string;
  score: number | null;
  reason: string | null;
  details_url: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

// ---- Licence inventory (forward-compatible) ----

export type LicenceReviewStatus = "unreviewed" | "review_required" | "approved" | "rejected" | "unknown";

export interface LicenceAssertion {
  id: number;
  scan_run_id: number;
  component_id: number;
  package_name: string;
  version: string | null;
  licence: string;
  direct: boolean;
  provider: string;
  review_status: LicenceReviewStatus;
  unknown_licence: boolean;
  created_at: string;
  updated_at: string;
}

// ---- Scan comparison (v0.5 evidence-aware) ----

// The v0.5 state vocabulary. The comparator never claims a
// row was "fixed" or "resolved" - it only describes what the
// evidence shows between two scans. The frontend renders these
// as the only labels it ever uses for a comparison row.
export type ObservationState =
  | "newly_observed"
  | "still_observed"
  | "no_longer_observed"
  | "changed_observation"
  | "coverage_changed"
  | "comparison_indeterminate";

export type ProviderStateName =
  | "successful"
  | "cached"
  | "stale"
  | "partial"
  | "unavailable"
  | "unsupported"
  | "not_requested"
  | "unknown";

export interface ScanComparisonComponentObservation {
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  manifest_paths: string[];
  direct_base: boolean | null;
  direct_head: boolean | null;
  state: ObservationState;
}

export interface ScanComparisonManifestObservation {
  manifest_path: string;
  manifest_type: string | null;
  ecosystem: string | null;
  parse_status_base: string | null;
  parse_status_head: string | null;
  content_sha256_base: string | null;
  content_sha256_head: string | null;
  state: ObservationState;
}

export interface ScanComparisonDependencyPathChange {
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  parent_chain_base: string[];
  parent_chain_head: string[];
  state: ObservationState;
}

export interface ScanComparisonWorkflowObservation {
  rule_id: string;
  workflow_path: string;
  title: string;
  severity_base: string | null;
  severity_head: string | null;
  confidence_base: string | null;
  confidence_head: string | null;
  stable_key: string;
  state: ObservationState;
}

export interface ScanComparisonVulnerabilityObservation {
  component_id_base: number | null;
  component_id_head: number | null;
  ecosystem: string | null;
  package_name: string | null;
  package_version_base: string | null;
  package_version_head: string | null;
  advisory_source: string | null;
  advisory_external_id: string | null;
  advisory_canonical_id: string | null;
  severity_label_base: string | null;
  severity_score_base: number | null;
  severity_label_head: string | null;
  severity_score_head: number | null;
  state: ObservationState;
  provider_provenance_base: string | null;
  provider_provenance_head: string | null;
  fetched_at_base: string | null;
  fetched_at_head: string | null;
  ambiguity_reason: string | null;
}

export interface ScanComparisonLicenceObservation {
  ecosystem: string | null;
  package_name: string | null;
  package_version_base: string | null;
  package_version_head: string | null;
  licence_base: string | null;
  licence_head: string | null;
  provider_base: string | null;
  provider_head: string | null;
  review_status_base: string | null;
  review_status_head: string | null;
  state: ObservationState;
}

export interface ScanComparisonOpenSSFObservation {
  check_id: string;
  name: string;
  score_base: number | null;
  score_head: number | null;
  reason_base: string | null;
  reason_head: string | null;
  details_url: string | null;
  source: string;
  state: ObservationState;
}

export interface ScanComparisonProviderCoverage {
  provider: string;
  state_base: ProviderStateName;
  state_head: ProviderStateName;
  last_completed_at_base: string | null;
  last_completed_at_head: string | null;
  records_returned_base: number | null;
  records_returned_head: number | null;
  cache_status_base: string | null;
  cache_status_head: string | null;
  error_code_base: string | null;
  error_summary_base: string | null;
  error_code_head: string | null;
  error_summary_head: string | null;
  evidence_present_base: boolean;
  evidence_present_head: boolean;
  state: ObservationState;
}

export interface ScanComparisonCoverageSummary {
  base_scan_status: string;
  head_scan_status: string;
  components_in_base: number;
  components_in_head: number;
  findings_in_base: number;
  findings_in_head: number;
  vulnerabilities_in_base: number;
  vulnerabilities_in_head: number;
  workflows_in_base: number;
  workflows_in_head: number;
  manifests_in_base: number;
  manifests_in_head: number;
  licence_assertions_in_base: number;
  licence_assertions_in_head: number;
  openssf_checks_in_base: number;
  openssf_checks_in_head: number;
  providers_with_changed_state: number;
  providers_with_indeterminate_head: number;
}

export interface ScanComparison {
  base_scan_id: number;
  head_scan_id: number;
  repository_id: number;
  base_trigger_type: string | null;
  head_trigger_type: string | null;
  base_resolved_commit_sha: string | null;
  head_resolved_commit_sha: string | null;
  base_analyzer_version: string | null;
  head_analyzer_version: string | null;
  base_completed_at: string | null;
  head_completed_at: string | null;
  generated_at: string;
  coverage: ScanComparisonCoverageSummary;
  components: ScanComparisonComponentObservation[];
  manifests: ScanComparisonManifestObservation[];
  dependency_paths: ScanComparisonDependencyPathChange[];
  workflows: ScanComparisonWorkflowObservation[];
  vulnerabilities: ScanComparisonVulnerabilityObservation[];
  licences: ScanComparisonLicenceObservation[];
  openssf: ScanComparisonOpenSSFObservation[];
  providers: ScanComparisonProviderCoverage[];
  indeterminate_reasons: string[];
}

// ---- Provider health rollup (forward-compatible) ----

export type ProviderName = "github" | "osv" | "deps_dev" | "openssf";

export interface ProviderHealthEntry {
  provider: ProviderName;
  status: ProviderStatus;
  last_retrieved_at: string | null;
  records_returned: number;
  cache_status: string | null;
  redacted_failure_summary: string | null;
  last_error_code: string | null;
  scans_with_observations: number;
}

// ---- v1.9 Operational diagnostics ----

export type DiagnosticsDatabaseState = "available" | "unavailable" | "unknown";

export type DiagnosticsExecutorState = "available" | "unavailable" | "unknown";

export interface DiagnosticsApplication {
  status: string;
  version: string;
  environment: string;
  database: DiagnosticsDatabaseState;
  generated_at: string;
}

export interface DiagnosticsExecutor {
  state: DiagnosticsExecutorState;
  implementation: string;
  queued_scans: number;
  running_scans: number;
  last_heartbeat_at: string | null;
  heartbeat_supported: boolean;
  details_available: boolean;
  notes: string[];
}

export interface DiagnosticsProvider {
  provider: string;
  last_observed_state: string;
  configured_state: string;
  last_attempt_at: string | null;
  last_success_at: string | null;
  cache_status: string | null;
  evidence_present: boolean | null;
  last_error_code: string | null;
  last_error_summary: string | null;
  source_scan_id: number | null;
  source_observation_id: number | null;
}

export interface DiagnosticsRecentScanIssue {
  scan_id: number;
  repository_id: number;
  status: "partial" | "failed" | "cancelled";
  trigger_type: string | null;
  failure_code: string | null;
  failure_summary: string | null;
  updated_at: string;
  completed_at: string | null;
  started_at: string | null;
}

export interface DiagnosticsStageSummary {
  stage: string;
  completed: number;
  partial: number;
  failed: number;
  skipped: number;
  running: number;
  pending: number;
}

export interface DiagnosticsSummary {
  application: DiagnosticsApplication;
  executor: DiagnosticsExecutor;
  providers: DiagnosticsProvider[];
  recent_scan_issues: DiagnosticsRecentScanIssue[];
  stage_summary: DiagnosticsStageSummary[];
  generated_at: string;
}

// ---- Exports ----

export type ExportFormat =
  | "cyclonedx_json"
  | "cyclonedx_1_7"
  | "findings_json"
  | "findings_csv"
  | "sarif_json";

export interface ExportDescriptor {
  format: ExportFormat;
  label: string;
  description: string;
  supported: boolean;
  not_supported_reason: string | null;
  content_type: string;
  filename_hint: string;
}

// ---- v0.7 CycloneDX 1.7 preview / readiness summary ----

export interface CycloneDxPreviewScan {
  scan_id: number;
  repository_id: number;
  scan_status: ScanStatus;
  source_kind: string;
}

export interface CycloneDxPreviewEligibility {
  eligible: boolean;
  code: string;
  reason: string;
  limitations: string[];
  download_expected_to_succeed: boolean;
}

export interface CycloneDxPreviewInventory {
  component_count: number;
  manifest_count: number;
  ecosystems: string[];
  direct_count: number;
  transitive_count: number;
  missing_version_count: number;
  duplicate_observations_count: number;
}

export interface CycloneDxPreviewCoverage {
  inventory_coverage: string;
  dependency_graph_coverage: string;
  provider_coverage: string;
}

export interface CycloneDxPreviewSbomOutput {
  format: string;
  spec_version: string;
  media_type: string;
  filename_template: string;
  schema_uri: string;
  schema_validation: string;
  generation_source: string;
}

export interface CycloneDxPreviewResponse {
  scan: CycloneDxPreviewScan;
  eligibility: CycloneDxPreviewEligibility;
  inventory: CycloneDxPreviewInventory;
  evidence_coverage: CycloneDxPreviewCoverage;
  sbom_output: CycloneDxPreviewSbomOutput;
  omissions: string[];
  legacy_export_relationship: string;
}

// ---- v0.8 component evidence drilldown ----

export interface ComponentEvidenceScan {
  scan_id: number;
  repository_id: number;
  scan_status: ScanStatus;
}

export interface ComponentEvidenceIdentity {
  id: number;
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  version_source: string | null;
  direct: boolean;
  development: boolean;
  optional: boolean;
  scope: string | null;
  relationship: string | null;
  integrity: string | null;
  package_url: string | null;
  package_url_well_formed: boolean | null;
  purl_constructible: boolean;
  bom_ref: string;
}

export interface ComponentEvidenceManifest {
  available: boolean;
  id: number | null;
  path: string | null;
  manifest_type: string | null;
  ecosystem: string | null;
  parse_status: string | null;
  parse_warning_count: number | null;
}

export interface ComponentEvidenceLicenceObservation {
  value: string;
  classification: string;
  provenance: string;
  source: string | null;
  finding_id: number;
  rule_id: string;
}

export interface ComponentEvidenceLicenceBlock {
  available: boolean;
  reason: string | null;
  observations: ComponentEvidenceLicenceObservation[];
  sources: string[];
}

export interface ComponentEvidenceProviderObservation {
  id: number;
  provider: string;
  operation: string;
  status: string;
  cache_status: string | null;
  http_status: number | null;
  records_returned: number;
  requested_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_summary: string | null;
  evidence_keys: string[];
}

export interface ComponentEvidenceAdvisory {
  advisory_id: number;
  available: boolean;
  reason: string | null;
  canonical_id: string | null;
  source_advisory_id: string | null;
  source: string | null;
  severity_label: string | null;
  severity_score: number | null;
  severity_source: string | null;
  fixed_versions: string[];
  aliases: string[];
  confidence: null;
  provider_provenance: string | null;
  affected: boolean;
}

export interface ComponentEvidenceProviderBlock {
  available: boolean;
  any_provider_queried: boolean;
  observations: ComponentEvidenceProviderObservation[];
  advisories: ComponentEvidenceAdvisory[];
}

export interface ComponentEvidenceDependencyEdge {
  edge_id: number;
  component_id: number;
  other_component_id: number;
  direction: "incoming" | "outgoing";
  relationship: string;
  depth: number;
}

export interface ComponentEvidenceDependencyBlock {
  graph_coverage: string;
  incoming: ComponentEvidenceDependencyEdge[];
  outgoing: ComponentEvidenceDependencyEdge[];
  no_edges_observed: boolean;
}

export interface ComponentEvidenceExportImplications {
  appears_in_cyclonedx_17: boolean;
  version_omitted: boolean;
  purl_emitted: boolean;
  dependency_relationships_emitted: boolean;
  graph_coverage: string;
}

export interface ComponentEvidenceResponse {
  scan: ComponentEvidenceScan;
  component: ComponentEvidenceIdentity;
  manifest: ComponentEvidenceManifest;
  licence_evidence: ComponentEvidenceLicenceBlock;
  provider_evidence: ComponentEvidenceProviderBlock;
  dependency_evidence: ComponentEvidenceDependencyBlock;
  export_implications: ComponentEvidenceExportImplications;
  omissions: string[];
}

// ---- v0.9 evidence search and filtering ----

export type SummaryFilterDirect = "all" | "yes" | "no";
export type SummaryFilterPresent = "all" | "present" | "missing";
export type SummaryFilterPurl = "all" | "persisted" | "constructible" | "omitted";
export type SummaryFilterEdges = "all" | "present" | "none_observed";
export type SummaryFilterBool = "all" | "yes" | "no";

export type SummarySort =
  | "package_name"
  | "ecosystem"
  | "version_missing_first"
  | "licence_missing_first"
  | "provider_missing_first"
  | "dependency_edges_missing_first";

export interface ComponentEvidenceSummaryFlags {
  version_present: boolean;
  licence_observed: boolean;
  provider_observed: boolean;
  purl_state: "persisted" | "constructible" | "omitted";
  edges_observed: boolean;
  appears_in_cyclonedx_17: boolean;
  version_omitted_from_cyclonedx_17: boolean;
  dependency_relationships_emitted_in_cyclonedx_17: boolean;
}

export interface ComponentEvidenceSummaryItem {
  id: number;
  scan_id: number;
  manifest_id: number;
  package_name: string;
  ecosystem: string | null;
  version: string | null;
  version_source: string | null;
  direct: boolean;
  package_url: string | null;
  evidence: ComponentEvidenceSummaryFlags;
}

export interface ComponentEvidenceSummaryFacets {
  ecosystems: Record<string, number>;
  missing_version: number;
  missing_licence_evidence: number;
  missing_provider_evidence: number;
  purl_persisted: number;
  purl_constructible: number;
  purl_omitted: number;
  edges_observed: number;
  edges_none_observed: number;
  direct_yes: number;
  direct_no: number;
  cyclonedx_version_omitted: number;
}

export interface ComponentEvidenceSummaryPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ComponentEvidenceSummaryResponse {
  items: ComponentEvidenceSummaryItem[];
  pagination: ComponentEvidenceSummaryPagination;
  facets: ComponentEvidenceSummaryFacets;
  omissions: string[];
}

export interface ListComponentEvidenceSummaryFilters {
  search?: string;
  ecosystem?: string;
  direct?: SummaryFilterDirect;
  version?: SummaryFilterPresent;
  licence_evidence?: SummaryFilterPresent;
  provider_evidence?: SummaryFilterPresent;
  purl?: SummaryFilterPurl;
  dependency_edges?: SummaryFilterEdges;
  cyclonedx_appears?: SummaryFilterBool;
  cyclonedx_version_omitted?: SummaryFilterBool;
  cyclonedx_relationships_emitted?: SummaryFilterBool;
  sort?: SummarySort;
  page?: number;
  page_size?: number;
}

// ---- Pagination ----

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface Paginated<T> {
  items: T[];
  pagination: PageMeta;
}

// ---- v1.0 human-readable evidence report ----

export interface EvidenceReportMetadata {
  report_name: string;
  generator: string;
  generator_version: string;
  report_format: string;
  report_format_version: string;
  generated_at_utc: string;
  scan_id: number;
  repository_id: number;
}

export interface EvidenceReportScan {
  scan_id: number;
  repository_id: number;
  repository_canonical_url: string | null;
  repository_source_type: string | null;
  repository_visibility: string | null;
  scan_status: ScanStatus;
  scan_trigger_type: string | null;
  resolved_commit_sha: string | null;
  analyzer_version: string | null;
}

export interface EvidenceReportSummary {
  component_count: number;
  manifest_count: number;
  ecosystems: Record<string, number>;
  direct_count: number;
  transitive_count: number;
  version_present_count: number;
  version_missing_count: number;
  licence_observed_count: number;
  licence_missing_count: number;
  provider_observed_count: number;
  provider_missing_count: number;
  edges_observed_count: number;
  edges_none_observed_count: number;
  purl_persisted_count: number;
  purl_constructible_count: number;
  purl_omitted_count: number;
  appears_in_cyclonedx_17_count: number;
  cyclonedx_version_omitted_count: number;
  cyclonedx_relationships_emitted_count: number;
}

export interface EvidenceReportEvidenceCoverage {
  inventory_coverage: string;
  dependency_graph_coverage: string;
  provider_coverage: string;
}

export interface EvidenceReportEvidenceGaps {
  missing_version_count: number;
  missing_licence_evidence_count: number;
  missing_provider_evidence_count: number;
  no_persisted_edges_count: number;
  purl_omitted_count: number;
}

export interface EvidenceReportComponentRow {
  id: number;
  ecosystem: string | null;
  package_name: string;
  version: string | null;
  version_source: string | null;
  direct: boolean;
  purl_state: "persisted" | "constructible" | "omitted";
  edges_evidence: string;
  licence_evidence: string;
  provider_evidence: string;
  appears_in_cyclonedx_17: boolean;
  cyclonedx_version_omitted: boolean;
  cyclonedx_relationships_emitted: boolean;
}

export interface EvidenceReportTruncation {
  truncated: boolean;
  shown: number;
  total: number;
  reason: string;
}

export interface EvidenceReportExportRelationship {
  cyclonedx_eligible: boolean;
  cyclonedx_eligibility_code: string;
  cyclonedx_eligibility_reason: string;
  appears_in_cyclonedx_17_count: number;
  cyclonedx_version_omitted_count: number;
  cyclonedx_relationships_emitted_count: number;
  cyclonedx_relationships_omitted_count: number;
  inventory_coverage: string;
  dependency_graph_coverage: string;
  provider_coverage: string;
}

export interface EvidenceReportPreviewResponse {
  metadata: EvidenceReportMetadata;
  scan: EvidenceReportScan;
  summary: EvidenceReportSummary;
  evidence_coverage: EvidenceReportEvidenceCoverage;
  evidence_gaps: EvidenceReportEvidenceGaps;
  components: EvidenceReportComponentRow[];
  truncated: EvidenceReportTruncation;
  export_relationship: EvidenceReportExportRelationship;
  omissions: string[];
  disclaimer: string;
}
