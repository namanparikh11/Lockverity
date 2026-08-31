import { apiClient } from "@/api/client";
import type {
  Advisory,
  Component,
  ComponentAdvisory,
  ComponentEnrichment,
  ComponentEvidenceResponse,
  ComponentEvidenceSummaryResponse,
  CycloneDxPreviewResponse,
  DependencyPath,
  EvidenceReportPreviewResponse,
  ExportDescriptor,
  ExportFormat,
  Finding,
  FindingCategory,
  FindingConfidence,
  FindingSeverity,
  FindingStatus,
  HealthResponse,
  IntakeResult,
  LicenceAssertion,
  ListComponentEvidenceSummaryFilters,
  OpenSSFCheck,
  Paginated,
  ProviderHealthEntry,
  ProviderName,
  ProviderObservation,
  ProviderStatus,
  Repository,
  RepositoryCreatePayload,
  Scan,
  ScanCreatePayload,
  ScanComparison,
  ScanRunPayload,
  ScanStage,
  ScanStatus,
  SystemInfoResponse,
  WorkflowFinding,
} from "@/api/types";

export interface ListRepositoriesFilters {
  page?: number;
  page_size?: number;
  search?: string;
  provider?: string;
  source_type?: string;
  archived?: "all" | "archived" | "active";
}

export interface ListScansFilters {
  page?: number;
  page_size?: number;
  status?: ScanStatus | "all";
  trigger_type?: string;
  // Explicit seeded-dataset provenance marker (e.g. "demo").
  // Selects rows the demo loader stamped; a numeric scan id
  // never implies demo identity. Supported by the cross-repo
  // ``GET /scans`` rollup.
  seeded_dataset?: string;
}

export interface ListFindingsFilters {
  page?: number;
  page_size?: number;
  category?: FindingCategory | "all";
  severity?: FindingSeverity | "all";
  confidence?: FindingConfidence | "all";
  rule_id?: string;
  path?: string;
  status?: FindingStatus | "all";
  provider?: string;
  // v1.7: free-text search across title, summary,
  // rule id, and evidence_json. The backend route
  // accepts this as the ``q`` query parameter; the
  // API client remaps the friendly ``q`` field name
  // to the wire form.
  q?: string;
  // v1.7: bounded sort field. Bounded to:
  // id, rule_id, category, severity, confidence,
  // status, updated_at. Invalid values map to id.
  sort?: string;
}

export interface ListComponentsFilters {
  page?: number;
  page_size?: number;
  ecosystem?: string;
  scope?: "all" | "direct" | "transitive";
  development?: "all" | "production" | "development";
  vulnerable_only?: "all" | "vulnerable";
  search?: string;
}

export interface ListVulnerabilitiesFilters {
  page?: number;
  page_size?: number;
  ecosystem?: string;
  direct_transitive?: "all" | "direct" | "transitive";
  search?: string;
}

export interface ListWorkflowFindingsFilters {
  page?: number;
  page_size?: number;
  rule_id?: string;
  severity?: FindingSeverity | "all";
}

export interface ListOpenSSFFilters {
  page?: number;
  page_size?: number;
  check_id?: string;
}

export interface ListLicenceFilters {
  page?: number;
  page_size?: number;
  provider?: string;
  review_status?: string;
  search?: string;
  direct_transitive?: "all" | "direct" | "transitive";
}

function buildQuery<T extends object>(obj: T): Record<string, string | number | boolean | undefined | null> {
  const out: Record<string, string | number | boolean | undefined | null> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    if (typeof value === "string" && value === "all") continue;
    out[key] = value as string | number | boolean;
  }
  return out;
}

export const api = {
  // ---- System ----
  health: () => apiClient.get<HealthResponse>("/health"),
  systemInfo: () => apiClient.get<SystemInfoResponse>("/system/info"),

  // ---- Repositories ----
  listRepositories: (filters: ListRepositoriesFilters = {}) =>
    apiClient.get<Paginated<Repository>>("/repositories", {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        search: filters.search,
        provider: filters.provider,
        source_type: filters.source_type,
        archived: filters.archived && filters.archived !== "all" ? filters.archived : undefined,
      }),
    }),
  getRepository: (id: number) =>
    apiClient.get<Repository>(`/repositories/${id}`),
  createRepository: (payload: RepositoryCreatePayload | string) => {
    const body: RepositoryCreatePayload =
      typeof payload === "string" ? { canonical_url: payload } : payload;
    return apiClient.post<Repository>("/repositories", body);
  },
  // v1.5 guided-intake endpoint. ``POST /repositories/github``
  // accepts a public GitHub URL plus an optional ref and
  // returns the full ``IntakeResultRead`` shape (repository,
  // scan, workspace, summary). The frontend reads
  // ``result.scan.id`` to navigate to the new scan detail
  // page. The endpoint is the same one already used by the
  // backend tests; the v1.5 page is a thin wrapper.
  createRepositoryGithub: (payload: { canonical_url: string; requested_ref?: string }) =>
    apiClient.post<IntakeResult>("/repositories/github", {
      canonical_url: payload.canonical_url,
      ...(payload.requested_ref ? { requested_ref: payload.requested_ref } : {}),
    }),
  // v1.5 guided-intake endpoint. ``POST /repositories/upload``
  // accepts a ZIP file (multipart ``file`` field) and
  // returns the full ``IntakeResultRead`` shape. The
  // frontend reads ``result.scan.id`` to navigate to the
  // new scan detail page. The method is the same backend
  // route the older ``/repositories/upload`` page used;
  // v1.5 reuses it as the upload half of the guided
  // intake page.
  createRepositoryUpload: (file: File | Blob) =>
    apiClient.upload<IntakeResult>("/repositories/upload", file),

  // ---- Scans ----
  listScansForRepository: (
    repositoryId: number,
    filters: ListScansFilters = {}
  ) =>
    apiClient.get<Paginated<Scan>>(`/repositories/${repositoryId}/scans`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        status: filters.status,
        trigger_type: filters.trigger_type,
      }),
    }),
  listAllScans: (filters: ListScansFilters = {}, options: { signal?: AbortSignal } = {}) =>
    apiClient.get<Paginated<Scan>>("/scans", {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        status: filters.status,
        trigger_type: filters.trigger_type,
        seeded_dataset: filters.seeded_dataset,
      }),
      signal: options.signal,
    }),
  createScan: (repositoryId: number, payload: ScanCreatePayload = {}) =>
    apiClient.post<Scan>(`/repositories/${repositoryId}/scans`, payload),
  // v1.6.1: workspace-preserving rescan. The backend
  // route ``POST /api/v1/repositories/{id}/rescan``
  // creates a fresh scan row, a fresh workspace, and
  // re-materialises the source evidence (GitHub
  // tarball or upload workspace copy) before
  // returning. The historical scan and workspace
  // are never mutated. When the source is no longer
  // available, the route returns
  // ``rescan_source_unavailable`` and no queued row
  // is persisted.
  rescanRepository: (repositoryId: number, payload: ScanCreatePayload = {}) =>
    apiClient.post<Scan>(`/repositories/${repositoryId}/rescan`, payload),
  // v1.6: explicit scan start. The intake endpoints
  // create a queued scan but do not start execution; the
  // caller must POST to ``/scans/{id}/run`` to schedule
  // the work on the local worker. Already-running scans
  // are rejected with HTTP 409; the UI surfaces the
  // stable error envelope.
  runScan: (scanId: number, payload?: ScanRunPayload) =>
    apiClient.post<Scan>(`/scans/${scanId}/run`, payload),
  // v1.6: cancellation. The endpoint accepts an optional
  // ``reason`` field; the UI passes a non-empty string
  // when the user types a custom reason and the empty
  // default when they click the default Cancel action.
  cancelScan: (scanId: number, payload: { reason?: string } = {}) =>
    apiClient.post<Scan>(`/scans/${scanId}/cancel`, payload),
  getScan: (scanId: number, options: { signal?: AbortSignal } = {}) =>
    apiClient.get<Scan>(`/scans/${scanId}`, { signal: options.signal }),
  listStages: (scanId: number) =>
    apiClient.get<{ items: ScanStage[] }>(`/scans/${scanId}/stages`),

  // ---- Findings ----
  listFindings: (scanId: number, filters: ListFindingsFilters = {}) =>
    apiClient.get<Paginated<Finding>>(`/scans/${scanId}/findings`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        category: filters.category,
        severity: filters.severity,
        confidence: filters.confidence,
        rule_id: filters.rule_id,
        path: filters.path,
        status: filters.status,
        provider: filters.provider,
        q: filters.q,
        sort: filters.sort,
      }),
    }),
  // v1.7: scan-scoped single-finding lookup. The
  // backend enforces scan-scoped isolation so a
  // finding from one scan cannot be read through
  // another scan's URL. Used by the evidence detail
  // drawer to fetch the freshest evidence payload
  // without re-listing the page.
  getFinding: (scanId: number, findingId: number) =>
    apiClient.get<Finding>(`/scans/${scanId}/findings/${findingId}`),

  // ---- Provider observations ----
  listProviderObservations: (
    scanId: number,
    filters: { page?: number; page_size?: number; status?: ProviderStatus | "all"; provider?: string } = {}
  ) =>
    apiClient.get<Paginated<ProviderObservation>>(`/scans/${scanId}/providers`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        status: filters.status,
        provider: filters.provider,
      }),
    }),
  getProviderHealth: (scanId?: number) =>
    apiClient.get<{ entries: ProviderHealthEntry[] }>(
      scanId ? `/scans/${scanId}/provider-health` : "/provider-health"
    ),
  listProviderHealth: () =>
    apiClient.get<{ entries: ProviderHealthEntry[]; providers: ProviderName[] }>(
      "/provider-health"
    ),

  // ---- v1.9 operational diagnostics ----
  // Read-only summary of the application's runtime
  // state, executor health, per-provider persisted
  // observations, recent partial / failed /
  // cancelled scans, and aggregated stage states.
  // The endpoint never triggers an external provider
  // request and never exposes secrets, tokens,
  // environment values, connection strings, or
  // local filesystem paths.
  diagnosticsSummary: () =>
    apiClient.get<import("@/api/types").DiagnosticsSummary>(
      "/diagnostics/summary"
    ),

  // ---- Components / dependencies ----
  listComponents: (scanId: number, filters: ListComponentsFilters = {}) =>
    apiClient.get<Paginated<Component>>(`/scans/${scanId}/components`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 50,
        ecosystem: filters.ecosystem,
        scope: filters.scope,
        development: filters.development,
        vulnerable_only: filters.vulnerable_only,
        search: filters.search,
      }),
    }),
  getDependencyPath: (scanId: number, componentId: number) =>
    apiClient.get<DependencyPath>(`/scans/${scanId}/components/${componentId}/path`),
  // v0.8 component evidence drilldown. Read-only summary
  // of identity, manifest, licence, provider, dependency,
  // and CycloneDX 1.7 export implications for one
  // component. Returns 404 when the component is unknown
  // to the scan; the consumer renders the bounded error.
  getComponentEvidence: (scanId: number, componentId: number) =>
    apiClient.get<ComponentEvidenceResponse>(
      `/scans/${scanId}/components/${componentId}/evidence`
    ),
  // v0.9 evidence-aware component search and filtering.
  // Returns a paginated, sorted, faceted list of
  // components with their evidence flags. The consumer
  // renders the response as a discovery surface; the
  // endpoint never returns a verdict.
  getComponentsEvidenceSummary: (
    scanId: number,
    filters: ListComponentEvidenceSummaryFilters = {}
  ) =>
    apiClient.get<ComponentEvidenceSummaryResponse>(
      `/scans/${scanId}/components/evidence-summary`,
      {
        query: buildQuery({
          search: filters.search,
          ecosystem: filters.ecosystem,
          direct: filters.direct,
          version: filters.version,
          licence_evidence: filters.licence_evidence,
          provider_evidence: filters.provider_evidence,
          purl: filters.purl,
          dependency_edges: filters.dependency_edges,
          cyclonedx_appears: filters.cyclonedx_appears,
          cyclonedx_version_omitted: filters.cyclonedx_version_omitted,
          cyclonedx_relationships_emitted: filters.cyclonedx_relationships_emitted,
          sort: filters.sort,
          page: filters.page,
          page_size: filters.page_size,
        }),
      }
    ),

  // ---- Vulnerabilities ----
  listVulnerabilities: (scanId: number, filters: ListVulnerabilitiesFilters = {}) =>
    apiClient.get<Paginated<ComponentAdvisory>>(`/scans/${scanId}/vulnerabilities`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        ecosystem: filters.ecosystem,
        direct_transitive: filters.direct_transitive,
        search: filters.search,
      }),
    }),
  listAdvisories: (scanId: number) =>
    apiClient.get<Paginated<Advisory>>(`/scans/${scanId}/advisories`),

  // ---- Component enrichments (v0.4) ----
  listEnrichments: (
    scanId: number,
    filters: { page?: number; page_size?: number; ecosystem?: string; source_provenance?: string } = {}
  ) =>
    apiClient.get<Paginated<ComponentEnrichment>>(`/scans/${scanId}/enrichments`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 50,
        ecosystem: filters.ecosystem,
        source_provenance: filters.source_provenance,
      }),
    }),

  // ---- Workflow findings ----
  listWorkflowFindings: (scanId: number, filters: ListWorkflowFindingsFilters = {}) =>
    apiClient.get<Paginated<WorkflowFinding>>(`/scans/${scanId}/workflows`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        rule_id: filters.rule_id,
        severity: filters.severity,
      }),
    }),

  // ---- OpenSSF posture ----
  listOpenSSF: (scanId: number, filters: ListOpenSSFFilters = {}) =>
    apiClient.get<Paginated<OpenSSFCheck>>(`/scans/${scanId}/openssf`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 25,
        check_id: filters.check_id,
      }),
    }),

  // ---- Licence inventory ----
  listLicences: (scanId: number, filters: ListLicenceFilters = {}) =>
    apiClient.get<Paginated<LicenceAssertion>>(`/scans/${scanId}/licences`, {
      query: buildQuery({
        page: filters.page ?? 1,
        page_size: filters.page_size ?? 50,
        provider: filters.provider,
        review_status: filters.review_status,
        search: filters.search,
        direct_transitive: filters.direct_transitive,
      }),
    }),

  // ---- Scan comparison ----
  compareScans: (baseScanId: number, headScanId: number) =>
    apiClient.get<ScanComparison>(
      `/scans/${headScanId}/compare/${baseScanId}`
    ),

  // ---- Exports ----
  listExports: (scanId: number) =>
    apiClient.get<{ items: ExportDescriptor[] }>(`/scans/${scanId}/exports`),
  downloadExport: (scanId: number, format: ExportFormat) =>
    apiClient.getText(`/scans/${scanId}/exports/${format}`),
  // v0.7 preview / readiness summary for the CycloneDX 1.7
  // export. The endpoint always returns 200; the eligibility
  // verdict is in the body. The frontend renders the
  // preview summary panel from this response.
  previewCyclonedx17: (scanId: number) =>
    apiClient.get<CycloneDxPreviewResponse>(
      `/scans/${scanId}/exports/cyclonedx_1_7/preview`
    ),
  // v1.0 human-readable evidence report — preview
  // summary (lazy) and Markdown download. The download
  // route uses ``getText`` so the raw Markdown body
  // reaches the consumer unmodified; the response
  // carries a per-scan ``Content-Disposition`` filename
  // that the consumer can read from the headers when
  // present.
  previewEvidenceReport: (scanId: number) =>
    apiClient.get<EvidenceReportPreviewResponse>(
      `/scans/${scanId}/reports/evidence-summary/preview`
    ),
  downloadEvidenceReport: (scanId: number) =>
    apiClient
      .getText(`/scans/${scanId}/reports/evidence-summary.md`)
      .then((r) => r.body),
};

export type { ListRepositoriesFilters as _ListRepositoriesFilters };
