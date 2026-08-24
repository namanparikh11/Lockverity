import { Plus, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import {
  defaultExternalEvidenceProviderSelection,
  providerSelectionPayload,
} from "@/api/providerSelection";
import type {
  ExternalEvidenceProviderSelection,
  PageMeta,
  Repository,
  Scan,
  ScanStatus,
  ScanTriggerType,
} from "@/api/types";
import { repositoryDisplayName } from "@/utils/repositoryLabels";
import { CopyableIdentifier } from "@/components/CopyableIdentifier";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ExternalEvidenceProviderSelector } from "@/components/ExternalEvidenceProviderSelector";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import {
  repositoryProviderLabel,
  repositorySourceLabel,
  repositoryVisibilityLabel,
  scanStatusLabel,
  scanTriggerLabel,
} from "@/utils/labels";
import { formatTimestamp } from "@/utils/time";

/**
 * v1.8 — Repository history, rescan, and comparison workflow.
 *
 * Upgrades the existing repository detail page into a coherent
 * scan-history surface. The page:
 *
 * - Renders the persisted repository identity (name, source
 *   type, provider, default branch, visibility, archive flag,
 *   creation timestamp) without inventing any verdict.
 * - Lists the scan history newest-first with status, ref,
 *   timestamps, and cross-links to workbench / findings /
 *   dependencies / exports.
 * - Exposes a "Run another scan" action that uses the v1.6.1
 *   workspace-preserving rescan endpoint and never falls back
 *   to the low-level scan-record creator. Source-unavailable
 *   errors are rendered as bounded guidance.
 * - Exposes a "Compare two scans" selector that uses URL query
 *   state (``?baseline=&comparison=``) so the selection
 *   survives reload and is shareable.
 * - Renders a bounded partial / failed / cancelled notice
 *   when the user filters to a non-completed status. A
 *   repository with no scans never claims a clean result.
 */

const STATUS_OPTIONS: { value: "all" | ScanStatus; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "completed", label: scanStatusLabel.completed },
  { value: "partial", label: scanStatusLabel.partial },
  { value: "failed", label: scanStatusLabel.failed },
  { value: "cancelled", label: scanStatusLabel.cancelled },
  { value: "running", label: scanStatusLabel.running },
  { value: "queued", label: scanStatusLabel.queued },
];

const TRIGGER_OPTIONS: { value: "all" | ScanTriggerType; label: string }[] = [
  { value: "all", label: "All triggers" },
  { value: "manual", label: scanTriggerLabel.manual },
  { value: "upload", label: scanTriggerLabel.upload },
  { value: "scheduled", label: scanTriggerLabel.scheduled },
  { value: "api", label: scanTriggerLabel.api },
];

export function RepositoryDetailsPage() {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const repoId = Number.parseInt(repositoryId ?? "", 10);
  const validRepoId = Number.isFinite(repoId);

  // ---- URL <-> filter state ----
  const status = (searchParams.get("status") ?? "all") as "all" | ScanStatus;
  const triggerType = (searchParams.get("trigger_type") ?? "all") as
    | "all"
    | ScanTriggerType;
  const page = Number(searchParams.get("page") ?? "1") || 1;

  function setFilter<K extends "status" | "trigger_type">(key: K, value: string) {
    const next = new URLSearchParams(searchParams);
    if (!value || value === "all") {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    next.delete("page");
    setSearchParams(next, { replace: true });
  }

  function clearFilters() {
    const next = new URLSearchParams(searchParams);
    next.delete("status");
    next.delete("trigger_type");
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

  // ---- Repository + scans ----
  const [repo, setRepo] = useState<Repository | null>(null);
  const [scans, setScans] = useState<Scan[] | null>(null);
  const [scansMeta, setScansMeta] = useState<PageMeta | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [rescanning, setRescanning] = useState(false);
  const [rescanError, setRescanError] = useState<unknown>(null);
  const [providerSelection, setProviderSelection] =
    useState<ExternalEvidenceProviderSelection>(
      defaultExternalEvidenceProviderSelection
    );
  const rescanPendingRef = useRef(false);

  useEffect(() => {
    if (!validRepoId) {
      setError(new Error("Invalid repository id."));
      return;
    }
    const controller = new AbortController();
    setError(null);
    setRepo(null);
    api
      .getRepository(repoId)
      .then((r) => {
        if (controller.signal.aborted) return;
        setRepo(r);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, [repoId, validRepoId]);

  useEffect(() => {
    if (!validRepoId) return;
    const controller = new AbortController();
    setScans(null);
    api
      .listScansForRepository(repoId, {
        page,
        page_size: 10,
        status,
        trigger_type: triggerType,
      })
      .then((r) => {
        if (controller.signal.aborted) return;
        setScans(r.items);
        setScansMeta(r.pagination);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setScans([]);
          setScansMeta({
            page: 1,
            page_size: 0,
            total: 0,
            total_pages: 0,
          });
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [repoId, validRepoId, page, status, triggerType]);

  /**
   * v1.6.1 workspace-preserving rescan. The handler calls
   * ``api.rescanRepository`` (not the low-level
   * ``api.createScan``) so the new scan is always paired
   * with a fresh workspace. After a successful rescan, the
   * worker is asked to start the scan; the new workbench
   * handles partial-success UX. The handler refuses to fire
   * twice in quick succession (pendingRef is the
   * synchronous guard) so a reviewer cannot double-submit.
   */
  async function handleRescan() {
    if (!validRepoId) return;
    if (rescanning || rescanPendingRef.current) return;
    rescanPendingRef.current = true;
    setRescanning(true);
    setRescanError(null);
    try {
      const newScan = await api.rescanRepository(repoId);
      try {
        await api.runScan(newScan.id, providerSelectionPayload(providerSelection));
      } catch (err) {
        // Partial success: the new scan exists with a
        // fresh workspace, but the worker did not start
        // it. The new workbench surfaces a retry-start
        // button. We still navigate so the reviewer can
        // see the new scan and act on it.
        setRescanError(err);
      }
      navigate(`/scans/${newScan.id}`);
    } catch (err) {
      setRescanError(err);
    } finally {
      setRescanning(false);
      rescanPendingRef.current = false;
    }
  }

  const activeFilterCount = useMemo(() => {
    return [
      status !== "all" ? status : "",
      triggerType !== "all" ? triggerType : "",
    ].filter(Boolean).length;
  }, [status, triggerType]);

  if (error) {
    return (
      <>
        <PageHeader
          title="Repository"
          breadcrumbs={[
            { label: "Repositories", to: "/repositories" },
            { label: "Not found" },
          ]}
        />
        <ErrorState error={error} title="Could not load repository" />
      </>
    );
  }
  if (!repo) {
    return (
      <>
        <PageHeader title="Repository" />
        <Skeleton rows={6} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={repositoryDisplayName(repo)}
        description={repo.description ?? "No description."}
        breadcrumbs={[
          { label: "Repositories", to: "/repositories" },
          { label: repositoryDisplayName(repo) },
        ]}
        actions={
          <button
            type="button"
            className="btn-primary"
            onClick={handleRescan}
            disabled={rescanning}
            data-testid="repository-run-another-scan"
          >
            <Plus aria-hidden="true" className="h-4 w-4" />
            {rescanning ? "Preparing rescan..." : "Run another scan"}
          </button>
        }
      />
      <div className="mb-4 space-y-3">
        <ExternalEvidenceProviderSelector
          idPrefix={`repository-${repo.id}-provider`}
          value={providerSelection}
          onChange={setProviderSelection}
          openssfApplicable={repo.source_type !== "uploaded_archive"}
          disabled={rescanning}
        />
        {repo.source_type === "github" ? (
          <DataCompletenessNotice
            title="GitHub retrieval happens before analysis"
            description="Running another scan contacts GitHub to resolve and download this repository before analysis. This required retrieval is separate from the optional evidence providers above."
            tone="muted"
          />
        ) : null}
      </div>
      {rescanError ? (
        <div className="mb-4" data-testid="repository-rescan-error">
          <RescanErrorNotice error={rescanError} />
        </div>
      ) : null}

      <section className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <SummaryCard label="Identity" tone="muted">
          <p className="text-base font-semibold">
            {repositoryDisplayName(repo)}
          </p>
          {repo.original_filename ? (
            <p className="mt-1 text-xs text-ink-500">
              Original filename: <span className="font-mono">{repo.original_filename}</span>
            </p>
          ) : null}
          <p className="mt-1 break-all text-sm text-ink-700">
            {repo.canonical_url ?? "—"}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            Default branch: <span className="font-mono">{repo.default_branch ?? "—"}</span>
          </p>
        </SummaryCard>
        <SummaryCard label="Source &amp; provider" tone="muted">
          <p className="text-sm text-ink-700">
            {repositorySourceLabel[repo.source_type]} ·{" "}
            {repositoryProviderLabel[repo.provider]}
          </p>
          <p className="mt-1 flex items-center gap-2 text-sm text-ink-700">
            <StatusBadge status={repo.visibility} />
            <span className="text-xs text-ink-500">
              {repositoryVisibilityLabel[repo.visibility]}
            </span>
            {repo.archived ? (
              <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                archived
              </span>
            ) : null}
          </p>
        </SummaryCard>
        <SummaryCard label="Identifiers" tone="muted">
          <CopyableIdentifier label="id" value={String(repo.id)} />
          <p className="mt-1 text-xs text-ink-500">
            Last provider sync: {formatTimestamp(repo.last_provider_sync_at)}
          </p>
          <Timestamp prefix="Added" value={repo.created_at} mode="both" />
        </SummaryCard>
      </section>

      <DataCompletenessNotice
        title="About this view"
        tone="muted"
        description="Repository identity, scan history, and stage pipeline are sourced from the live API. Dependency, workflow, vulnerability, OpenSSF, and licence summaries populate as the corresponding scans and analyzers are enabled. The page never infers a clean result from an empty history."
      />

      <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <CompareScansCard repositoryId={repo.id} />
        <ExportShortcutsCard
          repositoryId={repo.id}
          scans={scans}
        />
      </section>

      <div className="mt-8 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink-700" data-testid="scan-history-heading">
          Scan history
          {scansMeta ? (
            <span className="ml-2 text-xs font-normal text-ink-500">
              {scansMeta.total} scan{scansMeta.total === 1 ? "" : "s"}
            </span>
          ) : null}
        </h2>
        <Link
          to={`/repositories/${repo.id}/compare`}
          className="text-xs text-accent-700 hover:text-accent-800"
          data-testid="repository-compare-link"
        >
          Open repository comparison selector →
        </Link>
      </div>

      {/* Bounded scan-state notice. The wording is
          deliberately evidence-honest: filtering to a
          non-completed status does not claim the result
          is complete, and a partial scan is never
          presented as a full baseline. */}
      {status === "partial" ? (
        <div className="mt-2">
          <DataCompletenessNotice
            title="Showing partial scans only"
            description="Partial scans did not complete the full pipeline. The history row counts, stage summaries, and downstream comparison all reflect the incomplete evidence."
            tone="warn"
          />
        </div>
      ) : null}
      {status === "failed" ? (
        <div className="mt-2">
          <DataCompletenessNotice
            title="Showing failed scans only"
            description="Failed scans do not produce trustworthy local-analysis evidence. They are listed for completeness; do not use a failed scan as a comparison baseline."
            tone="danger"
          />
        </div>
      ) : null}
      {status === "cancelled" ? (
        <div className="mt-2">
          <DataCompletenessNotice
            title="Showing cancelled scans only"
            description="Cancelled scans stopped before reaching a terminal pipeline state. They are not eligible as a comparison baseline."
            tone="warn"
          />
        </div>
      ) : null}

      <div className="mt-3">
        <FilterBar
          search=""
          onSearchChange={() => undefined}
          searchPlaceholder=""
          ariaLabel="History filters"
          onClear={activeFilterCount > 0 ? clearFilters : undefined}
          resultCount={scansMeta?.total}
          resultLabel="scans"
        >
          <SelectFilter
            id="history-status-filter"
            label="Status"
            value={status}
            onChange={(v) => setFilter("status", v)}
            options={STATUS_OPTIONS}
          />
          <SelectFilter
            id="history-trigger-filter"
            label="Trigger"
            value={triggerType}
            onChange={(v) => setFilter("trigger_type", v)}
            options={TRIGGER_OPTIONS}
          />
        </FilterBar>
      </div>

      <div className="mt-3">
        {scans === null || scansMeta === null ? (
          <Skeleton rows={3} />
        ) : scans.length === 0 ? (
          activeFilterCount > 0 ? (
            <EmptyState
              title="No scans match the filters"
              description="Loosen the filters to see more results, or clear them entirely. The repository may still have scans; the filters simply exclude them."
              action={
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={clearFilters}
                >
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              icon={<ScanSearch aria-hidden="true" className="h-8 w-8" />}
              title="No scans have been recorded for this repository"
              description="No scans have been recorded for this repository. Click Run another scan to prepare a fresh scan and workspace. A lack of scans does not establish that the repository is vulnerability-free."
              action={
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleRescan}
                  disabled={rescanning}
                >
                  {rescanning ? "Preparing rescan..." : "Run another scan"}
                </button>
              }
            />
          )
        ) : (
          <ResponsiveTable
            headers={[
              "Scan",
              "Status",
              "Trigger",
              "Ref",
              "Started",
              "Completed",
              "Updated",
            ]}
          >
            {scans.map((scan) => (
              <tr
                key={scan.id}
                className="table-row"
                data-testid={`scan-history-row-${scan.id}`}
              >
                <td className="table-cell">
                  <Link
                    to={`/scans/${scan.id}`}
                    className="text-ink-900 hover:text-accent-700"
                  >
                    #{scan.id}
                  </Link>
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] uppercase tracking-wide text-ink-500">
                    <Link
                      to={`/scans/${scan.id}/findings`}
                      className="hover:text-accent-700"
                    >
                      Findings
                    </Link>
                    <span aria-hidden="true">·</span>
                    <Link
                      to={`/scans/${scan.id}/dependencies`}
                      className="hover:text-accent-700"
                    >
                      Dependencies
                    </Link>
                    <span aria-hidden="true">·</span>
                    <Link
                      to={`/scans/${scan.id}/exports`}
                      className="hover:text-accent-700"
                    >
                      Exports
                    </Link>
                  </div>
                </td>
                <td className="table-cell">
                  <StatusBadge status={scan.status} />
                </td>
                <td className="table-cell text-ink-500">
                  {scanTriggerLabel[scan.trigger_type]}
                </td>
                <td className="table-cell font-mono text-xs text-ink-500">
                  {scan.requested_ref ?? "—"}
                </td>
                <td className="table-cell text-ink-500">
                  {formatTimestamp(scan.started_at)}
                </td>
                <td className="table-cell text-ink-500">
                  {formatTimestamp(scan.completed_at)}
                </td>
                <td className="table-cell text-ink-500">
                  <Timestamp value={scan.updated_at} mode="relative" />
                </td>
              </tr>
            ))}
          </ResponsiveTable>
        )}
      </div>
      {scansMeta && scansMeta.total_pages > 1 ? (
        <div className="mt-4">
          <Pagination meta={scansMeta} onPageChange={setPage} />
        </div>
      ) : null}
    </>
  );
}

function RescanErrorNotice({ error }: { error: unknown }) {
  // The v1.6.1 backend returns a
  // ``rescan_source_unavailable`` code when the
  // original upload source is no longer present.
  // The api client categorises it as
  // ``ErrorCategory.RescanSourceUnavailable``; we
  // surface the bounded copy and a "Back to
  // repository" action so the reviewer can recover.
  const code = readErrorCode(error);
  const message = error instanceof Error ? error.message : String(error);
  if (
    code === "rescan_source_unavailable" ||
    /rescan_source_unavailable/.test(message)
  ) {
    return (
      <DataCompletenessNotice
        title="Rescan source is no longer available"
        description="The original uploaded source is no longer available. Upload the archive again to create another scan. The historical scan remains unchanged."
        tone="warn"
      />
    );
  }
  return (
    <DataCompletenessNotice
      title="A new scan could not be prepared"
      description="The historical scan remains unchanged. You can retry the action from the workbench or upload a fresh archive."
      tone="danger"
      detail={message}
    />
  );
}

function readErrorCode(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  // ApiClientError stores the structured code on
  // ``apiError.code``; a plain thrown Error stores
  // the message on ``message``. We probe both so
  // the helper survives a thrown plain Error and a
  // thrown ApiClientError.
  const rec = error as {
    code?: unknown;
    apiError?: { code?: unknown };
    message?: unknown;
  };
  if (typeof rec.code === "string") return rec.code;
  if (rec.apiError && typeof rec.apiError.code === "string") {
    return rec.apiError.code;
  }
  return null;
}

const COMPARE_STATUS_OPTIONS: ReadonlySet<ScanStatus> = new Set<ScanStatus>([
  "completed",
  "partial",
]);

function CompareScansCard({
  repositoryId,
}: {
  repositoryId: number;
}) {
  const [searchParams] = useSearchParams();
  const [scans, setScans] = useState<Scan[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listScansForRepository(repositoryId, { page: 1, page_size: 50 })
      .then((r) => {
        if (controller.signal.aborted) return;
        setScans(r.items);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, [repositoryId]);

  const eligible = (scans ?? []).filter((s) =>
    COMPARE_STATUS_OPTIONS.has(s.status)
  );
  // v1.8 selection workflow: defaults mirror the
  // comparator's terminal-state rule. The user can
  // override the defaults through the repository
  // comparison selector at
  // ``/repositories/:id/compare?baseline=&comparison=``.
  const baseline = searchParams.get("baseline");
  const comparison = searchParams.get("comparison");
  const baselineValid = baseline
    ? eligible.some((s) => String(s.id) === baseline)
    : false;
  const comparisonValid = comparison
    ? eligible.some((s) => String(s.id) === comparison)
    : false;
  const ready = eligible.length >= 2 && baselineValid && comparisonValid;
  return (
    <div
      className="card"
      data-testid="repository-compare-card"
    >
      <h3 className="text-sm font-semibold text-ink-700">Compare two scans</h3>
      <p className="mt-1 text-xs text-ink-500">
        Pick two terminal scans of this repository. The comparator never
        compares across repositories, never compares a scan with itself, and
        never compares against a failed or cancelled scan.
      </p>
      {error ? (
        <p className="mt-2 text-xs text-rose-700">
          Could not load eligible scans.
        </p>
      ) : scans === null ? (
        <Skeleton rows={2} />
      ) : eligible.length < 2 ? (
        <p className="mt-2 text-xs text-ink-500" data-testid="repository-compare-empty">
          {eligible.length === 0
            ? "This repository has no completed or partial scans yet. Run another scan to create one."
            : "This repository has only one completed or partial scan so far. Run another scan to enable comparison."}
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-end gap-2 text-xs">
          <div>
            <div className="text-ink-500">Baseline</div>
            <div className="font-mono text-ink-700" data-testid="repository-compare-baseline">
              {baselineValid ? `#${baseline}` : "—"}
            </div>
          </div>
          <div>
            <div className="text-ink-500">Comparison</div>
            <div className="font-mono text-ink-700" data-testid="repository-compare-head">
              {comparisonValid ? `#${comparison}` : "—"}
            </div>
          </div>
        </div>
      )}
      <Link
        to={`/repositories/${repositoryId}/compare`}
        className="btn-primary mt-3 inline-flex"
        data-testid="repository-compare-open"
      >
        {ready ? "Open comparison" : "Open comparison selector"}
      </Link>
    </div>
  );
}

function ExportShortcutsCard({
  repositoryId,
  scans,
}: {
  repositoryId: number;
  scans: Scan[] | null;
}) {
  const headScan = scans?.find((s) =>
    COMPARE_STATUS_OPTIONS.has(s.status)
  );
  if (!headScan) {
    return (
      <div className="card">
        <h3 className="text-sm font-semibold text-ink-700">Exports</h3>
        <p className="mt-1 text-xs text-ink-500">
          Exports are available after at least one scan reaches a complete or
          partial state. Run another scan to begin.
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-ink-700">Exports</h3>
      <p className="mt-1 text-xs text-ink-500">
        Generate a CycloneDX SBOM, SARIF, findings JSON, or findings CSV from
        the most recent completed scan.
      </p>
      <Link
        to={`/scans/${headScan.id}/exports`}
        className="btn-primary mt-3 inline-flex"
        data-repository={repositoryId}
      >
        Open export center
      </Link>
    </div>
  );
}
