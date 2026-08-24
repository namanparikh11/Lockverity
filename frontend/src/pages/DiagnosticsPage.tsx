import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/api";
import type {
  DiagnosticsApplication,
  DiagnosticsExecutor,
  DiagnosticsSummary,
} from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { ProviderStatusBadge } from "@/components/ProviderStatusBadge";
import { RefreshButton } from "@/components/RefreshButton";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import { useRefresh } from "@/hooks/useRefresh";
import { providerNameLabel, scanStatusLabel } from "@/utils/labels";
import { formatTimestamp } from "@/utils/time";

/**
 * v1.9 — Operational diagnostics page.
 *
 * Renders the read-only diagnostics summary as five
 * independent bounded cards so the reviewer can
 * distinguish:
 *
 * - application reachability and version;
 * - executor / worker state (queued, running, and an
 *   honest "Heartbeat not exposed" notice);
 * - per-provider persisted observations, with cache
 *   state and evidence presence kept as separate
 *   fields;
 * - bounded recent partial / failed / cancelled scan
 *   issues with cross-links to the workbench;
 * - aggregated persisted stage-state counts.
 *
 * The page never triggers an external provider call,
 * never claims a clean / secure / healthy verdict, and
 * renders the explicit boundary notice that
 * operational state is not security state.
 *
 * Polling is manual-only by default. The refresh
 * button is the shared :class:`RefreshButton`
 * component which blocks duplicate clicks (a
 * synchronous guard), preserves the last known
 * payload on transient failure, and renders the
 * ``Refreshing…`` label while the request is in
 * flight. v2.1.3 promoted the historical inline
 * implementation to the shared component so the
 * same guard / accessibility / failure-preservation
 * semantics apply to every application-data
 * surface in the product.
 */

type ProviderStateFilter = "all" | "available" | "partial" | "rate_limited" | "unavailable" | "not_requested" | "cached" | "unknown";
type IssueStatusFilter = "all" | "partial" | "failed" | "cancelled";

const PROVIDER_STATE_OPTIONS: { value: ProviderStateFilter; label: string }[] = [
  { value: "all", label: "All states" },
  { value: "available", label: "Available" },
  { value: "partial", label: "Partial" },
  { value: "rate_limited", label: "Rate limited" },
  { value: "unavailable", label: "Unavailable" },
  { value: "cached", label: "Cached" },
  { value: "not_requested", label: "Not requested" },
  { value: "unknown", label: "Unknown" },
];

const ISSUE_STATUS_OPTIONS: { value: IssueStatusFilter; label: string }[] = [
  { value: "all", label: "All issue statuses" },
  { value: "partial", label: scanStatusLabel.partial },
  { value: "failed", label: scanStatusLabel.failed },
  { value: "cancelled", label: scanStatusLabel.cancelled },
];

export function DiagnosticsPage() {
  // The shared :class:`useRefresh` hook is the
  // single chokepoint for the manual refresh
  // behaviour: one click = one request, the last
  // known payload is preserved on failure, the
  // visible label flips to ``"Refreshing…"`` while
  // the request is in flight, and the synchronous
  // guard inside the hook blocks duplicate
  // concurrent invocations. v2.1.3 promoted the
  // historical inline implementation to the
  // shared hook so the same guard / accessibility
  // / failure-preservation semantics apply to
  // every application-data surface in the product.
  const refreshState = useRefresh<DiagnosticsSummary>({
    fetcher: () => api.diagnosticsSummary(),
  });
  const data = refreshState.data;
  const initialLoadFailed =
    refreshState.error !== undefined && data === undefined;
  const refreshFailed = refreshState.error !== undefined && data !== undefined;
  const loading = !refreshState.hasLoaded && !refreshState.isRefreshing;
  const [providerState, setProviderState] = useState<ProviderStateFilter>("all");
  const [issueStatus, setIssueStatus] = useState<IssueStatusFilter>("all");
  const [stageFilter, setStageFilter] = useState("all");

  // Fire the initial fetch on mount. The hook
  // blocks duplicate clicks while in flight so
  // the operator can never cause a stack. The
  // effect intentionally runs only on mount; the
  // hook's own guards prevent duplicate in-flight
  // requests, and re-running on every state
  // change would create a feedback loop. The
  // ``refreshState.refresh`` reference is captured
  // in a ref so the lint dependency array does not
  // need to enumerate ``refreshState`` (which
  // would change every render and create a
  // feedback loop).
  const refreshRef = useRef(refreshState.refresh);
  refreshRef.current = refreshState.refresh;
  useEffect(() => {
    refreshRef.current();
  }, []);

  const providers = useMemo(
    () => data?.providers ?? [],
    [data]
  );
  const recentIssues = useMemo(
    () => data?.recent_scan_issues ?? [],
    [data]
  );
  const stageSummary = useMemo(
    () => data?.stage_summary ?? [],
    [data]
  );

  const filteredProviders = useMemo(() => {
    if (providerState === "all") return providers;
    return providers.filter((p) => p.last_observed_state === providerState);
  }, [providers, providerState]);

  const filteredIssues = useMemo(() => {
    if (issueStatus === "all") return recentIssues;
    return recentIssues.filter((i) => i.status === issueStatus);
  }, [recentIssues, issueStatus]);

  const stagesWithFailures = useMemo(() => {
    if (stageFilter === "all") return stageSummary;
    return stageSummary.filter((s) => s.stage === stageFilter);
  }, [stageSummary, stageFilter]);

  return (
    <>
      <PageHeader
        title="Operational diagnostics"
        description="A read-only view of runtime reachability, executor health, persisted provider observations, recent partial / failed / cancelled scans, and aggregated persisted stage states. Operational state is not security state; provider availability is not vulnerability absence."
        breadcrumbs={[{ label: "Diagnostics" }]}
        actions={
          <RefreshButton
            state={refreshState}
            testId="diagnostics-refresh"
            ariaLabel="Refresh operational diagnostics"
            label="Refresh"
          />
        }
      />
      <DataCompletenessNotice
        title="Operational state is not security state"
        description="The diagnostics surface is read-only and is composed from persisted state. A reachable backend does not imply providers are available; a provider unavailable does not imply a vulnerability is absent; cached evidence is not the same as live evidence; a successful provider request does not prove a repository is safe; a completed scan may still contain partial or degraded provider evidence."
        tone="muted"
      />
      {initialLoadFailed && refreshState.error ? (
        <div className="mt-4" data-testid="diagnostics-error">
          <ErrorState
            error={refreshState.error}
            title="Diagnostics could not be loaded. The last known state is shown where available."
          />
        </div>
      ) : null}
      {refreshFailed ? (
        <div className="mt-4" data-testid="diagnostics-refresh-error">
          <DataCompletenessNotice
            title="Refresh failed. The last known diagnostics state is shown below."
            tone="warn"
            description="The diagnostics endpoint did not respond. The application kept the last known payload so the reviewer can continue to read the previous state."
          />
        </div>
      ) : null}
      {loading ? (
        <Skeleton rows={6} />
      ) : data === undefined ? null : (
        <>
          <section
            className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2"
            aria-label="Application and executor"
          >
            <ApplicationCard application={data.application} />
            <ExecutorCard executor={data.executor} />
          </section>
          <section className="mt-6" aria-label="Provider diagnostics">
            <h2 className="mb-2 text-sm font-semibold text-ink-700">
              Provider diagnostics
            </h2>
            <p className="mb-2 text-xs text-ink-500" data-testid="provider-boundary">
              Provider diagnostics describe persisted collection state.
              They do not establish that a repository has or does not
              have vulnerabilities. Cache state, evidence presence,
              and provider availability are kept as separate fields
              and are not collapsed into a single verdict.
            </p>
            <div className="mb-3">
              <FilterBar
                search=""
                onSearchChange={() => undefined}
                searchPlaceholder=""
                ariaLabel="Provider filters"
                resultCount={filteredProviders.length}
                resultLabel="providers"
              >
                <SelectFilter
                  id="provider-state-filter"
                  label="Observed state"
                  value={providerState}
                  onChange={(v) => setProviderState(v as ProviderStateFilter)}
                  options={PROVIDER_STATE_OPTIONS}
                />
              </FilterBar>
            </div>
            {filteredProviders.length === 0 ? (
              <EmptyState
                title="No provider observations match the current filter"
                description="Loosen the filter to see more provider rows. The four known provider names are always surfaced by the diagnostics endpoint."
              />
            ) : (
              <ResponsiveTable
                headers={[
                  "Provider",
                  "Observed state",
                  "Cache state",
                  "Last attempt",
                  "Outcome reason/code",
                ]}
              >
                {filteredProviders.map((p) => (
                  <tr
                    key={p.provider}
                    className="table-row"
                    data-testid={`diagnostics-provider-row-${p.provider}`}
                  >
                    <td className="table-cell font-medium text-ink-900">
                      {providerNameLabel[
                        p.provider as keyof typeof providerNameLabel
                      ] ?? p.provider}
                    </td>
                    <td className="table-cell">
                      <ProviderStatusBadge
                        status={p.last_observed_state as never}
                      />
                    </td>
                    <td className="table-cell text-ink-600">
                      {p.cache_status ?? "Unknown"}
                    </td>
                    <td className="table-cell text-ink-600">
                      {p.last_attempt_at ? (
                        <Timestamp value={p.last_attempt_at} mode="relative" />
                      ) : (
                        "Unknown"
                      )}
                    </td>
                    <td className="table-cell font-mono text-xs text-ink-600">
                      {p.last_error_code === "disabled_by_operator"
                        ? "Disabled by operator"
                        : p.last_error_code ?? "—"}
                    </td>
                  </tr>
                ))}
              </ResponsiveTable>
            )}
          </section>
          <section className="mt-6" aria-label="Recent scan issues">
            <h2 className="mb-2 text-sm font-semibold text-ink-700">
              Recent scan issues
            </h2>
            <p className="mb-2 text-xs text-ink-500">
              Bounded recent partial, failed, and cancelled scans.
              Completed scans are intentionally excluded.
            </p>
            <div className="mb-3">
              <FilterBar
                search=""
                onSearchChange={() => undefined}
                searchPlaceholder=""
                ariaLabel="Recent issue filters"
                resultCount={filteredIssues.length}
                resultLabel="issues"
              >
                <SelectFilter
                  id="issue-status-filter"
                  label="Status"
                  value={issueStatus}
                  onChange={(v) => setIssueStatus(v as IssueStatusFilter)}
                  options={ISSUE_STATUS_OPTIONS}
                />
              </FilterBar>
            </div>
            {recentIssues.length === 0 ? (
              <EmptyState
                title="No matching partial, failed, or cancelled scans were found."
                description="The diagnostics endpoint excludes completed scans. A zero count is rendered as a bounded empty state, never as a 'healthy' label."
              />
            ) : filteredIssues.length === 0 ? (
              <EmptyState
                title="No matching partial, failed, or cancelled scans were found."
                description="Loosen the filter to see more issues."
              />
            ) : (
              <ResponsiveTable
                headers={["Scan", "Status", "Failure code", "Updated", "Action"]}
              >
                {filteredIssues.map((issue) => (
                  <tr
                    key={issue.scan_id}
                    className="table-row"
                    data-testid={`diagnostics-issue-row-${issue.scan_id}`}
                  >
                    <td className="table-cell font-mono text-xs text-ink-500">
                      #{issue.scan_id} (repo #{issue.repository_id})
                    </td>
                    <td className="table-cell">
                      <StatusBadge status={issue.status} />
                    </td>
                    <td className="table-cell font-mono text-xs text-ink-700">
                      {issue.failure_code ?? "—"}
                    </td>
                    <td className="table-cell text-ink-500">
                      {formatTimestamp(issue.updated_at)}
                    </td>
                    <td className="table-cell">
                      <Link
                        to={`/scans/${issue.scan_id}`}
                        className="text-xs text-accent-700 hover:text-accent-800"
                        data-testid={`diagnostics-issue-link-${issue.scan_id}`}
                      >
                        Open workbench →
                      </Link>
                    </td>
                  </tr>
                ))}
              </ResponsiveTable>
            )}
          </section>
          <section className="mt-6" aria-label="Stage diagnostics">
            <h2 className="mb-2 text-sm font-semibold text-ink-700">
              Stage diagnostics
            </h2>
            <p className="mb-2 text-xs text-ink-500">
              Aggregated persisted stage-state counts across all
              scans. A zero count is rendered as &ldquo;No matching
              persisted stage failures were found in the selected
              window.&rdquo; — never as &ldquo;All stages are healthy.&rdquo;
            </p>
            <div className="mb-3">
              <FilterBar
                search=""
                onSearchChange={() => undefined}
                searchPlaceholder=""
                ariaLabel="Stage filters"
                resultCount={stagesWithFailures.length}
                resultLabel="stages"
              >
                <SelectFilter
                  id="stage-filter"
                  label="Stage"
                  value={stageFilter}
                  onChange={(v) => setStageFilter(v)}
                  options={[
                    { value: "all", label: "All stages" },
                    ...stageSummary.map((s) => ({
                      value: s.stage,
                      label: s.stage,
                    })),
                  ]}
                />
              </FilterBar>
            </div>
            {stagesWithFailures.length === 0 ? (
              <EmptyState
                title="No matching persisted stage failures were found in the selected window."
                description="The diagnostics endpoint returns one row per stage type. The selected window may be empty for the chosen stage."
              />
            ) : (
              <ResponsiveTable
                headers={["Stage", "Completed", "Partial", "Failed", "Skipped", "Running", "Pending"]}
              >
                {stagesWithFailures.map((row) => (
                  <tr
                    key={row.stage}
                    className="table-row"
                    data-testid={`diagnostics-stage-row-${row.stage}`}
                  >
                    <td className="table-cell font-mono text-xs text-ink-700">
                      {row.stage}
                    </td>
                    <td className="table-cell text-ink-600">
                      {row.completed}
                    </td>
                    <td className="table-cell text-ink-600">{row.partial}</td>
                    <td className="table-cell text-ink-600">{row.failed}</td>
                    <td className="table-cell text-ink-600">{row.skipped}</td>
                    <td className="table-cell text-ink-600">{row.running}</td>
                    <td className="table-cell text-ink-600">{row.pending}</td>
                  </tr>
                ))}
              </ResponsiveTable>
            )}
          </section>
          <p className="mt-6 text-xs text-ink-500" data-testid="diagnostics-generated-at">
            Generated at {formatTimestamp(data.generated_at)}.
          </p>
        </>
      )}
    </>
  );
}

function ApplicationCard({
  application,
}: {
  application: DiagnosticsApplication;
}) {
  return (
    <SummaryCard
      label="Application"
      tone={application.database === "available" ? "ok" : "warn"}
    >
      <p className="text-sm font-semibold text-ink-900" data-testid="diagnostics-application-version">
        Lockverity {application.version}
      </p>
      <p className="mt-1 text-xs text-ink-500">
        Environment: <span className="font-mono">{application.environment}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500">
        Runtime: <span className="font-mono">{application.status}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500" data-testid="diagnostics-database-state">
        Database:{" "}
        <span className="font-mono">{application.database}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500">
        Generated: {formatTimestamp(application.generated_at)}
      </p>
    </SummaryCard>
  );
}

function ExecutorCard({
  executor,
}: {
  executor: DiagnosticsExecutor;
}) {
  return (
    <SummaryCard
      label="Executor"
      tone={executor.state === "available" ? "ok" : "warn"}
    >
      <p className="text-sm font-semibold text-ink-900" data-testid="diagnostics-executor-state">
        {executor.state}
      </p>
      <p className="mt-1 text-xs text-ink-500">
        Implementation:{" "}
        <span className="font-mono">{executor.implementation}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500" data-testid="diagnostics-executor-queued">
        Queued scans: <span className="font-mono">{executor.queued_scans}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500" data-testid="diagnostics-executor-running">
        Running scans: <span className="font-mono">{executor.running_scans}</span>
      </p>
      <p className="mt-1 text-xs text-ink-500" data-testid="diagnostics-executor-heartbeat">
        {executor.heartbeat_supported
          ? executor.last_heartbeat_at
            ? `Last heartbeat: ${formatTimestamp(executor.last_heartbeat_at)}`
            : "Last heartbeat: not available"
          : "Heartbeat not exposed by the current executor."}
      </p>
      {executor.notes.length > 0 ? (
        <ul className="mt-2 list-disc pl-5 text-xs text-ink-500">
          {executor.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </SummaryCard>
  );
}
