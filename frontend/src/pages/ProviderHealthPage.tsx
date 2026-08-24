import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import type { PageMeta, ProviderName, ProviderObservation, ProviderStatus } from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ProviderStatusBadge } from "@/components/ProviderStatusBadge";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import { providerNameLabel, providerStatusLabel } from "@/utils/labels";
import { formatTimestamp } from "@/utils/time";

const PROVIDER_STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "available", label: providerStatusLabel.available },
  { value: "partial", label: providerStatusLabel.partial },
  { value: "rate_limited", label: providerStatusLabel.rate_limited },
  { value: "unavailable", label: providerStatusLabel.unavailable },
  { value: "not_requested", label: providerStatusLabel.not_requested },
  { value: "cached", label: providerStatusLabel.cached },
  { value: "unknown", label: providerStatusLabel.unknown },
];

/**
 * Provider health.
 *
 * Two layers:
 *  - A per-scan observation list (rows from `provider_observations`)
 *  - A per-provider rollup (the most-recent state per provider
 *    across scans, with retrieval time, records, cache status,
 *    and redacted failure summary).
 *
 * The per-provider rollup is what makes provider honesty
 * visible at a glance.
 */
export function ProviderHealthPage() {
  const { scanId } = useParams<{ scanId?: string }>();
  const sid = scanId ? Number.parseInt(scanId, 10) : null;
  const [observations, setObservations] = useState<ProviderObservation[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState<"all" | ProviderStatus>("all");
  const [provider, setProvider] = useState<string>("");
  const [rollup, setRollup] = useState<{
    providers: ProviderName[];
    entries: { provider: ProviderName; status: ProviderStatus; last_retrieved_at: string | null; records_returned: number; cache_status: string | null; redacted_failure_summary: string | null; last_error_code: string | null; scans_with_observations: number }[];
  } | null>(null);
  const [rollupNotImpl, setRollupNotImpl] = useState(false);

  useEffect(() => {
    if (sid === null || !Number.isFinite(sid)) return;
    const controller = new AbortController();
    setObservations(null);
    setError(null);
    api
      .listProviderObservations(sid, {
        page,
        page_size: 25,
        status,
        provider: provider || undefined,
      })
      .then((r) => {
        if (controller.signal.aborted) return;
        setObservations(r.items);
        setMeta(r.pagination);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setObservations([]);
          setMeta({ page: 1, page_size: 0, total: 0, total_pages: 0 });
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [sid, page, status, provider]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .listProviderHealth()
      .then((r) => {
        if (controller.signal.aborted) return;
        setRollup(r);
        setRollupNotImpl(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setRollupNotImpl(true);
          return;
        }
        // non-fatal: keep going
      });
    return () => controller.abort();
  }, []);

  return (
    <>
      <PageHeader
        title={
          sid !== null && Number.isFinite(sid)
            ? `Provider status · scan #${sid}`
            : "Provider health"
        }
        description="Every provider call is recorded. 'No data' is never represented as 'no findings'."
        breadcrumbs={
          sid !== null
            ? [
                { label: "Scan", to: `/scans/${sid}` },
                { label: "Providers" },
              ]
            : undefined
        }
      />
      <section className="mb-6">
        <h2 className="mb-2 text-sm font-semibold text-ink-700">Per-provider rollup</h2>
        {rollupNotImpl ? (
          <DataCompletenessNotice
            title="Per-provider rollup endpoint not yet implemented"
            description="The per-scan observation list below is the source of truth. A rollup that aggregates by provider across all scans will appear here once the backend exposes it."
            tone="info"
          />
        ) : rollup === null ? (
          <Skeleton rows={2} />
        ) : rollup.entries.length === 0 ? (
          <EmptyState
            title="No provider activity recorded"
            description="No provider has been queried for any scan yet. Run a scan that touches a provider to populate this view."
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
            {rollup.entries.map((entry) => (
              <SummaryCard
                key={entry.provider}
                label={providerNameLabel[entry.provider]}
                tone={rollupTone(entry.status)}
                caption={
                  entry.last_retrieved_at
                    ? `Last seen ${formatTimestamp(entry.last_retrieved_at)}`
                    : "No retrievals recorded"
                }
              >
                <div className="flex items-center gap-2">
                  <ProviderStatusBadge status={entry.status} />
                  <span className="text-xs text-ink-500">
                    {entry.records_returned} records
                  </span>
                </div>
                {entry.cache_status ? (
                  <p className="mt-1 text-xs text-ink-500">cache: {entry.cache_status}</p>
                ) : null}
                {entry.redacted_failure_summary ? (
                  <p
                    className="mt-1 truncate text-xs text-rose-700"
                    title={entry.redacted_failure_summary}
                  >
                    {entry.redacted_failure_summary}
                  </p>
                ) : null}
                {entry.last_error_code === "disabled_by_operator" ? (
                  <p className="mt-1 text-xs text-ink-500">Disabled by operator</p>
                ) : null}
                <p className="mt-1 text-xs text-ink-400">
                  observed across {entry.scans_with_observations} scans
                </p>
              </SummaryCard>
            ))}
          </div>
        )}
      </section>
      {sid !== null && Number.isFinite(sid) ? (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-ink-700">Observations for scan #{sid}</h2>
          <div className="mb-3">
            <FilterBar
              search={provider}
              onSearchChange={setProvider}
              searchPlaceholder="Filter by provider"
              resultCount={meta?.total}
              resultLabel="observations"
            >
              <SelectFilter
                id="status"
                label="Status"
                value={status}
                onChange={(v) => setStatus(v as "all" | ProviderStatus)}
                options={PROVIDER_STATUS_OPTIONS}
              />
            </FilterBar>
          </div>
          {error ? (
            <ErrorState error={error} />
          ) : observations === null || meta === null ? (
            <Skeleton rows={5} />
          ) : observations.length === 0 ? (
            <EmptyState
              title="No provider observations"
              description="No external calls have been made for this scan yet. Each call will produce a row with its availability, status, and bounded error summary."
            />
          ) : (
            <>
              <ResponsiveTable
                headers={["Provider", "Operation", "Status", "HTTP", "Records", "Outcome detail", "Updated"]}
              >
                {observations.map((obs) => (
                  <tr key={obs.id} className="table-row">
                    <td className="table-cell font-mono text-xs text-ink-700">
                      {obs.provider}
                    </td>
                    <td className="table-cell text-ink-500">{obs.operation}</td>
                    <td className="table-cell">
                      <ProviderStatusBadge status={obs.status} />
                    </td>
                    <td className="table-cell text-ink-500">
                      {obs.http_status ?? "—"}
                    </td>
                    <td className="table-cell text-ink-500">
                      {obs.records_returned}
                    </td>
                    <td className="table-cell max-w-md">
                      <p className="text-xs text-ink-500">
                        {providerObservationDetail(obs)}
                      </p>
                    </td>
                    <td className="table-cell text-xs text-ink-500">
                      <Timestamp value={obs.completed_at ?? obs.created_at} mode="both" />
                    </td>
                  </tr>
                ))}
              </ResponsiveTable>
              <div className="mt-4">
                <Pagination meta={meta} onPageChange={setPage} />
              </div>
            </>
          )}
        </section>
      ) : null}
    </>
  );
}

function providerObservationDetail(observation: ProviderObservation): string {
  if (observation.error_code === "disabled_by_operator") {
    return "Disabled by operator";
  }
  if (observation.error_code === "not_applicable") {
    return "Not applicable";
  }
  if (observation.error_code === "no_components") {
    return "No applicable components";
  }
  return observation.error_summary ?? "—";
}

function rollupTone(status: ProviderStatus) {
  switch (status) {
    case "available":
    case "cached":
      return "ok" as const;
    case "partial":
    case "rate_limited":
      return "warn" as const;
    case "unavailable":
      return "danger" as const;
    case "not_requested":
    case "unknown":
    default:
      return "muted" as const;
  }
}
