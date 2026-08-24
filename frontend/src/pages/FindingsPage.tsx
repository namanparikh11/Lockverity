import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import type {
  Finding,
  FindingCategory,
  FindingConfidence,
  FindingSeverity,
  FindingStatus,
  PageMeta,
  Repository,
  Scan,
} from "@/api/types";
import { CodeLocation } from "@/components/CodeLocation";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { DetailsDrawer } from "@/components/DetailsDrawer";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { SeverityBadge } from "@/components/SeverityBadge";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { Timestamp } from "@/components/Timestamp";
import {
  findingCategoryLabel,
  findingConfidenceLabel,
  findingSeverityLabel,
  findingStatusLabel,
  repositorySourceLabel,
  scanStatusLabel,
} from "@/utils/labels";

const CATEGORY_OPTIONS = [
  { value: "all", label: "All categories" },
  { value: "dependency", label: findingCategoryLabel.dependency },
  { value: "vulnerability", label: findingCategoryLabel.vulnerability },
  { value: "workflow", label: findingCategoryLabel.workflow },
  { value: "repository_posture", label: findingCategoryLabel.repository_posture },
  { value: "licence", label: findingCategoryLabel.licence },
  { value: "provider", label: findingCategoryLabel.provider },
  { value: "data_quality", label: findingCategoryLabel.data_quality },
];

const SEVERITY_OPTIONS = [
  { value: "all", label: "All severities" },
  { value: "critical", label: findingSeverityLabel.critical },
  { value: "high", label: findingSeverityLabel.high },
  { value: "medium", label: findingSeverityLabel.medium },
  { value: "low", label: findingSeverityLabel.low },
  { value: "informational", label: findingSeverityLabel.informational },
  { value: "unknown", label: findingSeverityLabel.unknown },
];

const CONFIDENCE_OPTIONS = [
  { value: "all", label: "All confidences" },
  { value: "confirmed", label: findingConfidenceLabel.confirmed },
  { value: "high", label: findingConfidenceLabel.high },
  { value: "medium", label: findingConfidenceLabel.medium },
  { value: "low", label: findingConfidenceLabel.low },
  { value: "unknown", label: findingConfidenceLabel.unknown },
];

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "open", label: findingStatusLabel.open },
  { value: "resolved", label: findingStatusLabel.resolved },
  { value: "accepted", label: findingStatusLabel.accepted },
  { value: "suppressed", label: findingStatusLabel.suppressed },
];

// v1.7: bounded sort vocabulary. We never invent a
// "Lockverity risk ranking"; the sort field is
// restricted to columns that are persisted and
// trustworthy. The backend normalises invalid
// values to "id" with a deterministic id
// tiebreaker for stable paging.
const SORT_OPTIONS = [
  { value: "id", label: "Default order" },
  { value: "rule_id", label: "Rule id" },
  { value: "category", label: "Category" },
  { value: "severity", label: "Severity" },
  { value: "confidence", label: "Confidence" },
  { value: "status", label: "Status" },
  { value: "updated_at", label: "Updated" },
];

interface FindingsFilters {
  q: string;
  category: "all" | FindingCategory;
  severity: "all" | FindingSeverity;
  confidence: "all" | FindingConfidence;
  rule_id: string;
  path: string;
  status: "all" | FindingStatus;
  provider: string;
  sort: string;
}

/**
 * v1.7 findings triage and evidence review workbench.
 *
 * Upgrades the existing findings page to:
 * - render a scan context header (scan id, repository,
 *   status, source type, finding count, partial / failed /
 *   cancelled notice, links to workbench / dependencies /
 *   exports);
 * - perform a server-side search across title, summary,
 *   rule id, and evidence_json;
 * - apply server-side filters for confidence, status,
 *   provider, rule id, path, category, severity;
 * - sort by bounded fields (id, rule_id, category,
 *   severity, confidence, status, updated_at);
 * - persist filter / search / sort state in the URL
 *   so the analyst queue is shareable;
 * - open an evidence detail drawer that fetches the
 *   freshest payload via the single-finding endpoint,
 *   shows advisory identity, provider attribution,
 *   evidence provenance, and a bounded boundary
 *   notice;
 * - never claim a clean / secure / safe / vulnerability-
 *   free state.
 */
export function FindingsPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const sid = Number.parseInt(scanId ?? "", 10);
  const validScanId = Number.isFinite(sid);

  // ---- URL <-> filter state ----
  const filters: FindingsFilters = useMemo(
    () => ({
      q: searchParams.get("q") ?? "",
      category:
        (searchParams.get("category") as "all" | FindingCategory) ?? "all",
      severity:
        (searchParams.get("severity") as "all" | FindingSeverity) ?? "all",
      confidence:
        (searchParams.get("confidence") as "all" | FindingConfidence) ?? "all",
      rule_id: searchParams.get("rule_id") ?? "",
      path: searchParams.get("path") ?? "",
      status:
        (searchParams.get("status") as "all" | FindingStatus) ?? "all",
      provider: searchParams.get("provider") ?? "",
      sort: searchParams.get("sort") ?? "id",
    }),
    [searchParams]
  );
  const page = Number(searchParams.get("page") ?? "1") || 1;

  function setFilter<K extends keyof FindingsFilters>(
    key: K,
    value: FindingsFilters[K]
  ) {
    const next = new URLSearchParams(searchParams);
    if (value === "" || value === "all") {
      next.delete(key);
    } else {
      next.set(key, String(value));
    }
    // Changing any filter resets the page cursor.
    next.delete("page");
    setSearchParams(next, { replace: true });
  }

  function setPage(p: number) {
    const next = new URLSearchParams(searchParams);
    if (p <= 1) {
      next.delete("page");
    } else {
      next.set("page", String(p));
    }
    setSearchParams(next, { replace: true });
  }

  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
  }

  // ---- Scan + repository context (top header) ----
  const [scan, setScan] = useState<Scan | null>(null);
  const [repository, setRepository] = useState<Repository | null>(null);
  const [contextError, setContextError] = useState<unknown>(null);
  useEffect(() => {
    if (!validScanId) {
      setContextError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setScan(null);
    setRepository(null);
    setContextError(null);
    api
      .getScan(sid, { signal: controller.signal })
      .then((s) => {
        if (controller.signal.aborted) return;
        setScan(s);
        return api.getRepository(s.repository_id);
      })
      .then((r) => {
        if (controller.signal.aborted || !r) return;
        setRepository(r);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setContextError(err);
      });
    return () => controller.abort();
  }, [sid, validScanId]);

  // ---- Findings list ----
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notImpl, setNotImpl] = useState(false);
  useEffect(() => {
    if (!validScanId) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setFindings(null);
    setError(null);
    api
      .listFindings(sid, {
        page,
        page_size: 25,
        q: filters.q || undefined,
        category: filters.category,
        severity: filters.severity,
        confidence: filters.confidence,
        rule_id: filters.rule_id || undefined,
        path: filters.path || undefined,
        status: filters.status,
        provider: filters.provider || undefined,
        sort: filters.sort,
      })
      .then((r) => {
        if (controller.signal.aborted) return;
        setFindings(r.items);
        setMeta(r.pagination);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setFindings([]);
          setMeta({ page: 1, page_size: 0, total: 0, total_pages: 0 });
          setNotImpl(true);
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [sid, validScanId, page, filters]);

  // ---- Selection / detail drawer ----
  const [selected, setSelected] = useState<Finding | null>(null);

  const activeFilterCount = useMemo(() => {
    return [
      filters.q,
      filters.category !== "all" ? filters.category : "",
      filters.severity !== "all" ? filters.severity : "",
      filters.confidence !== "all" ? filters.confidence : "",
      filters.rule_id,
      filters.path,
      filters.status !== "all" ? filters.status : "",
      filters.provider,
    ].filter(Boolean).length;
  }, [filters]);

  if (contextError && !scan) {
    return (
      <>
        <PageHeader
          title={`Findings · scan #${sid}`}
          breadcrumbs={[
            { label: "Scan", to: `/scans/${sid}` },
            { label: "Findings" },
          ]}
        />
        <ErrorState error={contextError} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={`Findings · scan #${sid}`}
        description="Each finding is evidence-backed, severity-tagged, and confidence-tagged. Severity and confidence are independent dimensions."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "Findings" },
        ]}
      />

      {/* Scan context header. v1.7: bounded wording.
          A partial / failed / cancelled notice is
          shown only when relevant. Finding count
          comes from the paginated total, not from a
          separate denormalised count. */}
      {scan ? (
        <section
          className="mb-4 rounded-md border border-ink-200 bg-surface p-4 shadow-sm"
          aria-label="Scan context"
          data-testid="findings-context-header"
        >
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h2 className="text-sm font-semibold text-ink-800" data-testid="findings-context-title">
              Scan #{scan.id}
            </h2>
            {repository ? (
              <Link
                to={`/repositories/${repository.id}`}
                className="text-sm text-accent-700 hover:text-accent-800"
                data-testid="findings-context-repository"
              >
                {repository.owner}/{repository.name}
              </Link>
            ) : (
              <span className="text-sm text-ink-500">Repository #{scan.repository_id}</span>
            )}
            <span className="text-xs text-ink-500" data-testid="findings-context-status">
              Status: <span className="font-medium text-ink-700">{scanStatusLabel[scan.status]}</span>
            </span>
            {repository ? (
              <span className="text-xs text-ink-500" data-testid="findings-context-source">
                Source: <span className="font-medium text-ink-700">{repositorySourceLabel[repository.source_type]}</span>
              </span>
            ) : null}
            {meta ? (
              <span className="text-xs text-ink-500" data-testid="findings-context-count">
                {meta.total} finding{meta.total === 1 ? "" : "s"} match the current filters
              </span>
            ) : null}
          </div>
          <nav
            className="mt-2 flex flex-wrap gap-2 text-xs"
            aria-label="Quick links"
            data-testid="findings-context-links"
          >
            <Link
              to={`/scans/${scan.id}`}
              className="rounded border border-ink-200 bg-surface px-2 py-1 text-ink-700 hover:border-accent-300"
            >
              Workbench
            </Link>
            <Link
              to={`/scans/${scan.id}/dependencies`}
              className="rounded border border-ink-200 bg-surface px-2 py-1 text-ink-700 hover:border-accent-300"
            >
              Dependencies
            </Link>
            <Link
              to={`/scans/${scan.id}/exports`}
              className="rounded border border-ink-200 bg-surface px-2 py-1 text-ink-700 hover:border-accent-300"
            >
              Exports
            </Link>
          </nav>
        </section>
      ) : (
        <Skeleton rows={2} />
      )}

      {/* Bounded scan-state notice. We do not
          imply the result set is complete for
          non-completed scans. */}
      {scan && scan.status === "partial" ? (
        <div className="mb-4">
          <DataCompletenessNotice
            title="This scan is partial"
            description="Some stages did not complete. The findings shown are limited to the stages that did complete; additional findings may be absent because those stages did not produce any record."
            tone="warn"
          />
        </div>
      ) : null}
      {scan && scan.status === "failed" ? (
        <div className="mb-4">
          <DataCompletenessNotice
            title="This scan did not complete"
            description="The scan failed before reaching a terminal pipeline state. Findings shown are limited to whatever the orchestrator persisted before the failure."
            tone="danger"
            detail={
              scan.failure_summary
                ? `Failure summary: ${scan.failure_summary}`
                : null
            }
          />
        </div>
      ) : null}
      {scan && scan.status === "cancelled" ? (
        <div className="mb-4">
          <DataCompletenessNotice
            title="This scan was cancelled"
            description="Findings shown are limited to whatever the orchestrator persisted before the scan was cancelled. The result set is not a complete review."
            tone="warn"
          />
        </div>
      ) : null}

      {notImpl ? (
        <div className="mb-4">
          <DataCompletenessNotice
            title="Findings list endpoint not yet implemented"
            description="The backend does not currently expose a paginated findings endpoint. The page renders the same shape it will once the endpoint is available, with the filter bar and detail drawer ready."
            tone="info"
          />
        </div>
      ) : null}
      <div className="mb-4 space-y-3">
        <FilterBar
          layout="card"
          title="Filters"
          search={filters.q}
          onSearchChange={(v) => setFilter("q", v)}
          searchPlaceholder="Search title, summary, rule id, evidence, or PURL"
          onClear={activeFilterCount > 0 ? clearFilters : undefined}
          resultCount={meta?.total}
          resultLabel="findings"
        >
          <SelectFilter
            id="category-filter"
            label="Category"
            value={filters.category}
            onChange={(v) => setFilter("category", v as "all" | FindingCategory)}
            options={CATEGORY_OPTIONS}
            stacked
          />
          <SelectFilter
            id="sort-filter"
            label="Sort"
            value={filters.sort}
            onChange={(v) => setFilter("sort", v)}
            options={SORT_OPTIONS}
            stacked
          />
          <SelectFilter
            id="severity-filter"
            label="Severity"
            value={filters.severity}
            onChange={(v) => setFilter("severity", v as "all" | FindingSeverity)}
            options={SEVERITY_OPTIONS}
            stacked
          />
          <SelectFilter
            id="confidence-filter"
            label="Confidence"
            value={filters.confidence}
            onChange={(v) => setFilter("confidence", v as "all" | FindingConfidence)}
            options={CONFIDENCE_OPTIONS}
            stacked
          />
          <SelectFilter
            id="status-filter"
            label="Status"
            value={filters.status}
            onChange={(v) => setFilter("status", v as "all" | FindingStatus)}
            options={STATUS_OPTIONS}
            stacked
          />
        </FilterBar>
        <details className="rounded-md border border-ink-200 bg-surface p-3 text-sm">
          <summary className="cursor-pointer text-ink-700">Advanced filters</summary>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <label htmlFor="rule-id" className="label">Rule id</label>
              <input
                id="rule-id"
                className="input mt-1 font-mono"
                placeholder="LOCK-SUPPLY-001"
                value={filters.rule_id}
                onChange={(e) => setFilter("rule_id", e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="path-filter" className="label">Path</label>
              <input
                id="path-filter"
                className="input mt-1 font-mono"
                placeholder="src/ or package.json"
                value={filters.path}
                onChange={(e) => setFilter("path", e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="provider-filter" className="label">Provider</label>
              <input
                id="provider-filter"
                className="input mt-1 font-mono"
                placeholder="osv, deps.dev, ..."
                value={filters.provider}
                onChange={(e) => setFilter("provider", e.target.value)}
              />
            </div>
          </div>
        </details>
      </div>
      {error ? (
        <ErrorState error={error} />
      ) : findings === null || meta === null ? (
        <Skeleton rows={5} />
      ) : findings.length === 0 ? (
        notImpl ? (
          <EmptyState
            title="Findings endpoint not exposed"
            description="When the backend exposes a paginated findings list, this table will appear automatically."
          />
        ) : activeFilterCount > 0 ? (
          <EmptyState
            title="No finding records match the current filters"
            description="No finding records are available for the current filters. This does not establish that the repository is vulnerability-free. Loosen the filters to see more results, or clear them entirely."
            action={
              <button type="button" className="btn-secondary" onClick={clearFilters}>
                Clear filters
              </button>
            }
          />
        ) : (
          <EmptyState
            title="No finding records available for this scan"
            description="Analyzers, rules, and vulnerability providers did not produce any finding records for this scan. This does not establish that the repository is vulnerability-free."
          />
        )
      ) : (
        <>
          <ResponsiveTable
            headers={["Rule", "Title", "Category", "Severity", "Confidence", "Status", "Path", "Updated"]}
          >
            {findings.map((finding) => (
              <tr
                key={finding.id}
                className="table-row cursor-pointer focus-within:bg-ink-50 hover:bg-ink-50"
                onClick={() => setSelected(finding)}
                data-testid={`finding-row-${finding.id}`}
              >
                <td className="table-cell font-mono text-xs text-ink-500">
                  <Link
                    to={`/scans/${finding.scan_run_id}/findings#finding-${finding.id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="hover:text-accent-700"
                  >
                    {finding.rule_id}
                  </Link>
                </td>
                <td className="table-cell">
                  <p className="font-medium text-ink-900">{finding.title}</p>
                  <p className="line-clamp-2 text-xs text-ink-500">{finding.summary}</p>
                </td>
                <td className="table-cell text-ink-500">
                  {findingCategoryLabel[finding.category]}
                </td>
                <td className="table-cell">
                  <SeverityBadge severity={finding.severity} />
                </td>
                <td className="table-cell">
                  <ConfidenceBadge confidence={finding.confidence} />
                </td>
                <td className="table-cell">
                  <StatusBadge status={finding.status} />
                </td>
                <td className="table-cell text-ink-500">
                  <CodeLocation
                    path={finding.location_path}
                    startLine={finding.location_start_line}
                    endLine={finding.location_end_line}
                  />
                </td>
                <td className="table-cell text-ink-500">
                  <Timestamp value={finding.updated_at} mode="relative" />
                </td>
              </tr>
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
        </>
      )}
      <FindingDrawer
        scanId={sid}
        finding={selected}
        onClose={() => setSelected(null)}
      />
    </>
  );
}

interface EvidenceView {
  provider?: string;
  purl?: string;
  advisory_id?: string;
  aliases?: string[];
  source_url?: string;
  version?: string;
  // Anything else from the evidence JSON is preserved
  // as raw extra fields. The drawer renders them
  // verbatim (text only) so we never parse as HTML.
  [k: string]: unknown;
}

function extractEvidence(finding: Finding): EvidenceView {
  if (!finding.evidence_json) return {};
  try {
    const parsed = JSON.parse(finding.evidence_json);
    if (parsed && typeof parsed === "object") {
      return parsed as EvidenceView;
    }
  } catch {
    // Fall through.
  }
  return {};
}

function FindingDrawer({
  scanId,
  finding,
  onClose,
}: {
  scanId: number;
  finding: Finding | null;
  onClose: () => void;
}) {
  // v1.7: re-fetch the freshest payload via the
  // single-finding endpoint so the drawer reflects
  // any updates since the list was rendered. The
  // displayed identity (rule id, stable key) is
  // bound to the row the user clicked, so even if
  // the row disappeared between render and open
  // we still render a bounded fallback.
  const [fresh, setFresh] = useState<Finding | null>(null);
  const [drawerError, setDrawerError] = useState<unknown>(null);
  useEffect(() => {
    if (!finding) {
      setFresh(null);
      setDrawerError(null);
      return;
    }
    const controller = new AbortController();
    setFresh(null);
    setDrawerError(null);
    api
      .getFinding(scanId, finding.id)
      .then((f) => {
        if (controller.signal.aborted) return;
        setFresh(f);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setDrawerError(err);
      });
    return () => controller.abort();
  }, [scanId, finding]);

  if (!finding) return null;
  // The identity comes from the row the user clicked
  // so the drawer always opens. The freshest payload
  // (used for evidence / advisory) is layered on
  // top when available.
  const f = fresh ?? finding;
  const evidence = extractEvidence(f);
  const hasAdvisory = Boolean(evidence.advisory_id) || (evidence.aliases?.length ?? 0) > 0;
  return (
    <DetailsDrawer
      open={finding !== null}
      onClose={onClose}
      title={f.title}
      ariaLabel={`Finding ${f.rule_id}`}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-ink-500" data-testid="drawer-rule-id">{f.rule_id}</span>
          <SeverityBadge severity={f.severity} />
          <ConfidenceBadge confidence={f.confidence} />
          <StatusBadge status={f.status} />
          <span className="text-xs text-ink-500">
            {findingCategoryLabel[f.category]}
          </span>
        </div>
        {drawerError ? (
          <DataCompletenessNotice
            title="Could not refresh evidence"
            description="The freshest evidence payload could not be loaded. The row below shows the values that were rendered in the list."
            tone="warn"
          />
        ) : null}
        <section>
          <h3 className="label">Summary</h3>
          <p className="mt-1 text-sm text-ink-800">{f.summary}</p>
        </section>
        <section>
          <h3 className="label">Location</h3>
          <div className="mt-1">
            <CodeLocation
              path={f.location_path}
              startLine={f.location_start_line}
              endLine={f.location_end_line}
            />
          </div>
        </section>

        {hasAdvisory ? (
          <section data-testid="drawer-advisory">
            <h3 className="label">Advisory identity</h3>
            <dl className="mt-1 grid grid-cols-3 gap-x-3 gap-y-1 text-xs">
              <dt className="text-ink-500">Primary id</dt>
              <dd className="col-span-2 font-mono text-ink-800">
                {evidence.advisory_id ?? "—"}
              </dd>
              <dt className="text-ink-500">Aliases</dt>
              <dd className="col-span-2 text-ink-700" data-testid="drawer-aliases">
                {evidence.aliases && evidence.aliases.length > 0
                  ? evidence.aliases.join(", ")
                  : "—"}
              </dd>
              <dt className="text-ink-500">Provider</dt>
              <dd className="col-span-2 text-ink-700" data-testid="drawer-provider">
                {evidence.provider ?? "—"}
              </dd>
              <dt className="text-ink-500">Source URL</dt>
              <dd className="col-span-2 text-ink-700">
                {evidence.source_url ? (
                  <a
                    href={evidence.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent-700 hover:text-accent-800"
                  >
                    {evidence.source_url}
                  </a>
                ) : (
                  "—"
                )}
              </dd>
              {evidence.purl ? (
                <>
                  <dt className="text-ink-500">Package</dt>
                  <dd className="col-span-2 font-mono text-ink-700">
                    {evidence.purl}
                  </dd>
                </>
              ) : null}
            </dl>
          </section>
        ) : null}

        {f.evidence_json ? (
          <section data-testid="drawer-evidence">
            <h3 className="label">Evidence</h3>
            <p className="mt-1 text-xs text-ink-500">
              Raw evidence as persisted. Lockverity does not parse this as
              HTML; it is rendered as text so a malicious payload cannot
              change the page.
            </p>
            <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-ink-200 bg-canvas p-2 font-mono text-xs text-ink-800">
              {safePrettyJson(f.evidence_json)}
            </pre>
          </section>
        ) : (
          <section data-testid="drawer-evidence-empty">
            <h3 className="label">Evidence</h3>
            <p className="mt-1 text-xs text-ink-500">
              No evidence record is attached to this finding. Missing
              evidence is not a clean result.
            </p>
          </section>
        )}

        {f.remediation ? (
          <section>
            <h3 className="label">Remediation</h3>
            <p className="mt-1 text-sm text-ink-800">{f.remediation}</p>
          </section>
        ) : null}

        <section>
          <h3 className="label">Severity &amp; confidence</h3>
          <p className="mt-1 text-sm text-ink-700">
            Severity: <strong>{findingSeverityLabel[f.severity]}</strong> ·
            Confidence: <strong>{findingConfidenceLabel[f.confidence]}</strong>
          </p>
          <p className="mt-1 text-xs text-ink-500">
            Severity and confidence are independent dimensions. A critical
            finding with low confidence is not a confirmed vulnerability; a
            medium finding with confirmed confidence is. Severity is
            provider-attributed; Lockverity does not invent a universal
            severity or risk ranking.
          </p>
        </section>

        <section>
          <h3 className="label">Cross-links</h3>
          <ul className="mt-1 list-disc pl-5 text-xs text-ink-700">
            <li>
              <Link
                to={`/scans/${f.scan_run_id}`}
                className="text-accent-700 hover:text-accent-800"
              >
                Scan workbench
              </Link>
            </li>
            <li>
              <Link
                to={`/scans/${f.scan_run_id}/dependencies`}
                className="text-accent-700 hover:text-accent-800"
              >
                Dependencies (component inventory)
              </Link>
            </li>
            <li>
              <Link
                to={`/scans/${f.scan_run_id}/vulnerabilities`}
                className="text-accent-700 hover:text-accent-800"
              >
                Vulnerabilities (advisory matches)
              </Link>
            </li>
            <li>
              <Link
                to={`/scans/${f.scan_run_id}/exports`}
                className="text-accent-700 hover:text-accent-800"
              >
                Exports (CycloneDX, findings JSON/CSV, SARIF)
              </Link>
            </li>
          </ul>
        </section>

        <section>
          <h3 className="label">Scan context</h3>
          <p className="mt-1 text-sm text-ink-700">
            Scan <Link to={`/scans/${f.scan_run_id}`} className="text-accent-700 hover:text-accent-800">#{f.scan_run_id}</Link>{" "}
            on repository #{f.repository_id}.
          </p>
          <p className="mt-1 text-xs text-ink-500 font-mono">
            stable key: {f.stable_key}
          </p>
          <Timestamp prefix="Recorded" value={f.created_at} mode="both" />
        </section>

        <section data-testid="drawer-boundary">
          <h3 className="label">Boundary</h3>
          <p className="mt-1 text-xs text-ink-500">
            This finding is an evidence record, not a security verdict.
            Applicability may remain partial or unknown when source or
            provider evidence is incomplete. Severity is provider-attributed;
            Lockverity does not invent a universal severity, risk score, or
            compliance label.
          </p>
        </section>
      </div>
    </DetailsDrawer>
  );
}

function safePrettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
