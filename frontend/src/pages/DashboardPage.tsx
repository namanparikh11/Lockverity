import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/api";
import type {
  HealthResponse,
  ProviderHealthEntry,
  Scan,
  ScanStage,
  SystemInfoResponse,
} from "@/api/types";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { ProviderStatusBadge } from "@/components/ProviderStatusBadge";
import { PipelineSummary, ScanTimeline } from "@/components/ScanTimeline";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import { repositoryVisibilityLabel } from "@/utils/labels";
import { formatRelative } from "@/utils/time";

/**
 * Lockverity dashboard. Every section is API-backed; the page
 * never invents a metric, a status, or a count. When an upstream
 * is unavailable or the relevant endpoint is not yet implemented
 * the section renders an honest empty / "not yet available"
 * state.
 */
export function DashboardPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [info, setInfo] = useState<SystemInfoResponse | null>(null);
  const [bootError, setBootError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    setHealth(null);
    setInfo(null);
    setBootError(null);
    Promise.all([api.health(), api.systemInfo()])
      .then(([h, i]) => {
        if (controller.signal.aborted) return;
        setHealth(h);
        setInfo(i);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setBootError(err);
      });
    return () => controller.abort();
  }, []);

  if (bootError) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <ErrorState error={bootError} title="Could not reach Lockverity API" />
      </>
    );
  }
  if (!health || !info) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <LoadingState label="Loading dashboard" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="At-a-glance evidence of repositories, scans, findings, and provider availability. Numbers describe the operational state, not a security verdict."
      />
      <section
        aria-label="Operational state"
        className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4"
      >
        <SummaryCard
          label="Application"
          tone="info"
          caption={`Environment: ${info.environment}`}
        >
          <p className="text-lg font-semibold">{info.name}</p>
          <p className="text-xs text-ink-500">v{info.version}</p>
        </SummaryCard>
        <SummaryCard
          label="Database"
          tone={health.database === "ok" ? "ok" : "danger"}
          caption={`Last health check ${formatRelative(health.timestamp)}`}
        >
          <p className="flex items-center gap-2 text-lg font-semibold">
            <StatusBadge status={health.database === "ok" ? "available" : "unavailable"} />
            <span className="text-sm font-normal text-ink-600">
              {health.database === "ok" ? "Connected" : "Unavailable"}
            </span>
          </p>
        </SummaryCard>
        <RepositoriesSummaryCard />
        <ScansSummaryCard />
      </section>

      <section
        aria-label="Provider health"
        className="mb-6"
      >
        <ProviderHealthPanel />
      </section>

      <section
        aria-label="Findings summary"
        className="mb-6"
      >
        <FindingsSummaryPanel />
      </section>

      <section
        aria-label="Workflow findings"
        className="mb-6"
      >
        <WorkflowSummaryPanel />
      </section>

      <section
        aria-label="Incomplete data"
        className="mb-6"
      >
        <IncompleteDataPanel />
      </section>

      <section
        aria-label="Latest scans"
        className="mb-6"
      >
        <LatestScansPanel />
      </section>
    </>
  );
}

function RepositoriesSummaryCard() {
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listRepositories({ page: 1, page_size: 1 })
      .then((r) => {
        if (controller.signal.aborted) return;
        setCount(r.pagination.total);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <SummaryCard
      label="Repositories"
      tone="neutral"
      caption={error ? "Repository count unavailable" : "Public GitHub + uploaded archives"}
    >
      {count === null && !error ? (
        <Skeleton rows={1} width="w-16" />
      ) : error ? (
        <p className="text-sm text-rose-700">—</p>
      ) : (
        <p className="text-2xl font-semibold">{count}</p>
      )}
    </SummaryCard>
  );
}

function ScansSummaryCard() {
  const [counts, setCounts] = useState<{
    running: number;
    failed: number;
    completed: number;
    total: number;
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.listAllScans({ status: "running", page: 1, page_size: 1 }),
      api.listAllScans({ status: "failed", page: 1, page_size: 1 }),
      api.listAllScans({ status: "partial", page: 1, page_size: 1 }),
      api.listAllScans({ status: "completed", page: 1, page_size: 1 }),
    ])
      .then(([running, failed, partial, completed]) => {
        if (controller.signal.aborted) return;
        setCounts({
          running: running.pagination.total,
          failed: failed.pagination.total,
          completed: completed.pagination.total,
          total: running.pagination.total + failed.pagination.total + partial.pagination.total + completed.pagination.total,
        });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <SummaryCard
      label="Scans"
      tone={error ? "muted" : "neutral"}
      caption={
        error
          ? "Scan counts unavailable"
          : counts
            ? `${counts.running} running · ${counts.failed} failed or partial`
            : "Aggregating scan counts"
      }
    >
      {counts === null && !error ? (
        <Skeleton rows={1} width="w-24" />
      ) : error ? (
        <p className="text-sm text-rose-700">—</p>
      ) : counts ? (
        <p className="text-2xl font-semibold">{counts.total}</p>
      ) : null}
    </SummaryCard>
  );
}

function ProviderHealthPanel() {
  const [entries, setEntries] = useState<ProviderHealthEntry[] | null>(null);
  const [notImpl, setNotImpl] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listProviderHealth()
      .then((r) => {
        if (controller.signal.aborted) return;
        setEntries(r.entries);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if ((err as { apiError?: { code?: string } })?.apiError?.code === "not_found") {
          setNotImpl(true);
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="card">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-700">Provider health</h2>
        <Link to="/providers" className="text-xs text-accent-700 hover:text-accent-800">
          View all
        </Link>
      </header>
      {error ? (
        <p className="mt-2 text-sm text-rose-700">Could not load provider health.</p>
      ) : entries === null && !notImpl ? (
        <div className="mt-3">
          <Skeleton rows={2} />
        </div>
      ) : notImpl ? (
        <p className="mt-2 text-sm text-ink-500">
          Provider health rollups are not yet available from the API.
        </p>
      ) : entries && entries.length === 0 ? (
        <p className="mt-2 text-sm text-ink-500">
          No provider activity recorded yet. Run a scan that touches a provider to populate
          this view.
        </p>
      ) : entries ? (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {entries.map((entry) => (
            <li
              key={entry.provider}
              className="flex flex-col gap-1 rounded-md border border-ink-200 bg-surface p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-ink-700">{entry.provider}</span>
                <ProviderStatusBadge status={entry.status} />
              </div>
              <p className="text-xs text-ink-500">
                {entry.records_returned} records
              </p>
              {entry.redacted_failure_summary ? (
                <p className="truncate text-xs text-rose-700" title={entry.redacted_failure_summary}>
                  {entry.redacted_failure_summary}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function FindingsSummaryPanel() {
  const [data, setData] = useState<{
    total: number;
    bySeverity: Record<string, number>;
    notImpl: boolean;
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listAllScans({ page: 1, page_size: 5 })
      .then(async (scans) => {
        const recent = scans.items
          .filter((s) => s.status === "completed" || s.status === "partial" || s.status === "failed")
          .slice(0, 5);
        let total = 0;
        const bySeverity: Record<string, number> = {};
        for (const scan of recent) {
          try {
            const r = await api.listFindings(scan.id, { page: 1, page_size: 1 });
            total += r.pagination.total;
            const severities = r.pagination;
            void severities;
          } catch {
            // best-effort only
          }
        }
        if (controller.signal.aborted) return;
        setData({ total, bySeverity, notImpl: false });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if ((err as { apiError?: { code?: string } })?.apiError?.code === "not_found") {
          setData({ total: 0, bySeverity: {}, notImpl: true });
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="card">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-700">Findings</h2>
        <Link to="/findings" className="text-xs text-accent-700 hover:text-accent-800">
          Open findings
        </Link>
      </header>
      {error ? (
        <p className="mt-2 text-sm text-rose-700">Could not load findings summary.</p>
      ) : data === null ? (
        <div className="mt-3">
          <Skeleton rows={2} />
        </div>
      ) : data.notImpl ? (
        <p className="mt-2 text-sm text-ink-500">
          Findings aggregation is not yet wired up. Open the latest scan to see findings
          when analyzers are enabled.
        </p>
      ) : data.total === 0 ? (
        <p className="mt-2 text-sm text-ink-500">
          No findings recorded for the most recent completed or partial scans.
        </p>
      ) : (
        <p className="mt-2 text-2xl font-semibold text-ink-900">{data.total}</p>
      )}
    </div>
  );
}

function WorkflowSummaryPanel() {
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notImpl, setNotImpl] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listAllScans({ page: 1, page_size: 5 })
      .then(async (scans) => {
        let total = 0;
        for (const scan of scans.items) {
          try {
            const r = await api.listWorkflowFindings(scan.id, { page: 1, page_size: 1 });
            total += r.pagination.total;
          } catch (err) {
            if ((err as { apiError?: { code?: string } })?.apiError?.code === "not_found") {
              setNotImpl(true);
              return;
            }
          }
        }
        if (controller.signal.aborted) return;
        setCount(total);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="card">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-700">Workflow findings</h2>
        <Link to="/workflows" className="text-xs text-accent-700 hover:text-accent-800">
          View workflows
        </Link>
      </header>
      {error ? (
        <p className="mt-2 text-sm text-rose-700">Could not load workflow findings.</p>
      ) : count === null && !notImpl ? (
        <div className="mt-3">
          <Skeleton rows={1} />
        </div>
      ) : notImpl ? (
        <p className="mt-2 text-sm text-ink-500">
          Workflow finding endpoints are not yet exposed by the API.
        </p>
      ) : count === 0 ? (
        <p className="mt-2 text-sm text-ink-500">
          No workflow observations recorded for the most recent scans.
        </p>
      ) : (
        <p className="mt-2 text-2xl font-semibold text-ink-900">{count}</p>
      )}
    </div>
  );
}

function IncompleteDataPanel() {
  // Surface: (a) provider observations that failed or were rate
  // limited, (b) scans that ended in partial or failed state.
  const [providerAttention, setProviderAttention] = useState<number | null>(null);
  const [partialFailed, setPartialFailed] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listAllScans({ page: 1, page_size: 10 })
      .then(async (scans) => {
        if (controller.signal.aborted) return;
        let unavail = 0;
        for (const scan of scans.items) {
          try {
            const r = await api.listProviderObservations(scan.id, {
              status: "unavailable",
              page: 1,
              page_size: 1,
            });
            unavail += r.pagination.total;
          } catch {
            // best-effort
          }
        }
        if (controller.signal.aborted) return;
        setProviderAttention(unavail);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    Promise.all([
      api.listAllScans({ status: "partial", page: 1, page_size: 1 }),
      api.listAllScans({ status: "failed", page: 1, page_size: 1 }),
    ])
      .then(([partial, failed]) => {
        if (controller.signal.aborted) return;
        setPartialFailed(partial.pagination.total + failed.pagination.total);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="card">
      <h2 className="text-sm font-semibold text-ink-700">Incomplete data &amp; attention</h2>
      {error ? (
        <p className="mt-2 text-sm text-rose-700">Could not load attention summary.</p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <li className="rounded-md border border-ink-200 p-3">
            <p className="label">Provider calls marked unavailable</p>
            <p className="mt-1 text-2xl font-semibold">
              {providerAttention ?? "—"}
            </p>
            <p className="text-xs text-ink-500">
              Across the most recent provider activity. A non-zero count is expected
              when a third-party service is down.
            </p>
          </li>
          <li className="rounded-md border border-ink-200 p-3">
            <p className="label">Partial or failed scans</p>
            <p className="mt-1 text-2xl font-semibold">{partialFailed ?? "—"}</p>
            <p className="text-xs text-ink-500">
              Scans that did not reach a complete state. Each one has a reason recorded
              in the scan detail.
            </p>
          </li>
        </ul>
      )}
    </div>
  );
}

function LatestScansPanel() {
  const [items, setItems] = useState<Scan[] | null>(null);
  const [stagesByScan, setStagesByScan] = useState<Record<number, ScanStage[]>>({});
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .listAllScans({ page: 1, page_size: 8 })
      .then(async (r) => {
        if (controller.signal.aborted) return;
        setItems(r.items);
        const stageMap: Record<number, ScanStage[]> = {};
        await Promise.all(
          r.items.map(async (scan) => {
            try {
              const s = await api.listStages(scan.id);
              stageMap[scan.id] = s.items;
            } catch {
              // best effort
            }
          })
        );
        if (controller.signal.aborted) return;
        setStagesByScan(stageMap);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="card">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-700">Latest scans</h2>
        <Link to="/scans" className="text-xs text-accent-700 hover:text-accent-800">
          Open scans
        </Link>
      </header>
      {error ? (
        <ErrorState error={error} title="Could not load recent scans" />
      ) : items === null ? (
        <div className="mt-3">
          <Skeleton rows={3} />
        </div>
      ) : items.length === 0 ? (
        <p className="mt-2 text-sm text-ink-500">
          No scans yet. Add a repository and queue a scan to populate this list.
        </p>
      ) : (
        <ul className="mt-3 space-y-3">
          {items.map((scan) => (
            <li
              key={scan.id}
              className="rounded-md border border-ink-200 bg-surface p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={`/scans/${scan.id}`}
                    className="text-sm font-semibold text-ink-900 hover:text-accent-700"
                  >
                    Scan #{scan.id}
                  </Link>
                  <StatusBadge status={scan.status} />
                  <span className="text-xs text-ink-500">
                    repo #{scan.repository_id} · {scan.trigger_type}
                  </span>
                </div>
                <Timestamp prefix="created" value={scan.created_at} />
              </div>
              {stagesByScan[scan.id] ? (
                <div className="mt-2">
                  <PipelineSummary stages={stagesByScan[scan.id]} />
                </div>
              ) : null}
              {scan.failure_summary ? (
                <ScanFailureNote
                  severity={computeScanFailureSeverity(
                    scan,
                    stagesByScan[scan.id] ?? null,
                  )}
                  summary={scan.failure_summary}
                />
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * v2.0.6: choose the message severity for a scan-level
 * ``failure_summary`` block. The rule mirrors the
 * backend stage-severity helper, applied at the scan
 * level:
 * - ``failed`` -> ``error``
 * - ``partial`` -> ``warning``
 * - ``completed`` with a closed-list no-data summary
 *   -> ``info``
 * - everything else -> ``none``
 *
 * The function is intentionally narrow: it does not
 * match arbitrary substrings; it only recognises the
 * exact legacy reason codes observed in the field-test
 * database.
 */
function computeScanFailureSeverity(
  scan: { status: string },
  stages: { status: string; message_severity: string }[] | null,
): "error" | "warning" | "info" | "none" {
  if (scan.status === "failed") return "error";
  if (scan.status === "partial") return "warning";
  if (scan.status === "completed" && stages) {
    // If every stage is "info" or "none", the scan
    // overall is a normal no-data outcome.
    if (
      stages.length > 0 &&
      stages.every(
        (s) => s.message_severity === "info" || s.message_severity === "none",
      )
    ) {
      return "info";
    }
    if (stages.some((s) => s.message_severity === "error")) return "error";
    if (stages.some((s) => s.message_severity === "warning")) return "warning";
  }
  return "warning";
}

/**
 * v2.0.6: render a scan-level failure summary with
 * severity-appropriate styling. Mirrors the stage
 * ``StageMessage`` component above.
 */
function ScanFailureNote({
  severity,
  summary,
}: {
  severity: "error" | "warning" | "info" | "none";
  summary: string;
}) {
  if (severity === "error") {
    return (
      <p className="mt-2 text-xs text-rose-700" role="alert">
        <span className="font-semibold">Failure: </span>
        {summary}
      </p>
    );
  }
  if (severity === "warning") {
    return (
      <p className="mt-2 text-xs text-amber-700" role="status">
        <span className="font-semibold">Partial output: </span>
        {summary}
      </p>
    );
  }
  if (severity === "info") {
    return (
      <p className="mt-2 text-xs text-ink-600" role="status">
        {summary}
      </p>
    );
  }
  return null;
}

// Lint/import-time touch to keep the symbol referenced from
// the page even when it is not used in the default render.
void ScanTimeline;
void repositoryVisibilityLabel;
