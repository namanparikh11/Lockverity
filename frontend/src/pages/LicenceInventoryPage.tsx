import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import {
  providerWasDisabledByOperator,
  useProviderObservation,
} from "@/api/useProviderObservation";
import type {
  ComponentEnrichment,
  LicenceAssertion,
  PageMeta,
  ProviderStatus,
} from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ProviderStatusBadge } from "@/components/ProviderStatusBadge";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { formatTimestamp } from "@/utils/time";

const SCOPE_OPTIONS = [
  { value: "all", label: "All" },
  { value: "direct", label: "Direct" },
  { value: "transitive", label: "Transitive" },
];

const REVIEW_OPTIONS = [
  { value: "all", label: "All review states" },
  { value: "unreviewed", label: "Unreviewed" },
  { value: "review_required", label: "Review required" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "unknown", label: "Unknown" },
];

/**
 * Licence inventory.
 *
 * Per-component licence assertions, with review status,
 * provider attribution, and a non-legal-advice notice.
 */
export function LicenceInventoryPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const [items, setItems] = useState<LicenceAssertion[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [filters, setFilters] = useState<{
    search: string;
    provider: string;
    review_status: string;
    direct_transitive: "all" | "direct" | "transitive";
  }>({
    search: "",
    provider: "",
    review_status: "all",
    direct_transitive: "all",
  });
  const [notImpl, setNotImpl] = useState(false);
  const depsObservation = useProviderObservation(sid, "deps_dev");
  const depsDisabled = providerWasDisabledByOperator(depsObservation);

  useEffect(() => {
    setPage(1);
  }, [filters]);

  useEffect(() => {
    if (!Number.isFinite(sid)) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .listLicences(sid, {
        page,
        page_size: 50,
        search: filters.search || undefined,
        provider: filters.provider || undefined,
        review_status: filters.review_status === "all" ? undefined : filters.review_status,
        direct_transitive: filters.direct_transitive,
      })
      .then((r) => {
        if (controller.signal.aborted) return;
        setItems(r.items);
        setMeta(r.pagination);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setItems([]);
          setMeta({ page: 1, page_size: 0, total: 0, total_pages: 0 });
          setNotImpl(true);
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [sid, page, filters]);

  return (
    <>
      <PageHeader
        title={`Licence inventory · scan #${sid}`}
        description="Detected licence assertions per component, with provider attribution and review status."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "Licences" },
        ]}
      />
      <div className="mb-4">
        <DataCompletenessNotice
          title="Not legal advice"
          tone="warn"
          description="Lockverity surfaces detected licence assertions and their provider source. It does not interpret licence compatibility, provide legal advice, or substitute for human review by qualified counsel."
        />
      </div>
      <div className="mb-4">
        <FilterBar
          search={filters.search}
          onSearchChange={(v) => setFilters((f) => ({ ...f, search: v }))}
          searchPlaceholder="Search by package or licence"
          resultCount={meta?.total}
          resultLabel="assertions"
        >
          <div className="flex items-center gap-2">
            <label htmlFor="provider" className="text-xs text-ink-500">
              Provider
            </label>
            <input
              id="provider"
              className="input w-32 font-mono text-xs"
              placeholder="deps.dev, ..."
              value={filters.provider}
              onChange={(e) => setFilters((f) => ({ ...f, provider: e.target.value }))}
            />
          </div>
          <SelectFilter
            id="review"
            label="Review"
            value={filters.review_status}
            onChange={(v) => setFilters((f) => ({ ...f, review_status: v }))}
            options={REVIEW_OPTIONS}
          />
          <SelectFilter
            id="scope"
            label="Scope"
            value={filters.direct_transitive}
            onChange={(v) => setFilters((f) => ({ ...f, direct_transitive: v as "all" | "direct" | "transitive" }))}
            options={SCOPE_OPTIONS}
          />
        </FilterBar>
      </div>
      {error ? (
        <ErrorState error={error} />
      ) : items === null || meta === null ? (
        <Skeleton rows={6} />
      ) : items.length === 0 ? (
        <EmptyState
          title={
            notImpl
              ? "Licence endpoint not exposed"
              : depsDisabled
                ? "deps.dev was not requested"
                : "No licence assertions recorded"
          }
          description={
            notImpl
              ? "When the backend exposes a paginated licence endpoint, this table will appear automatically."
              : depsDisabled
                ? "deps.dev package metadata was disabled by the operator for this scan. No deps.dev request or cache lookup was made; local licence analysis may still be available."
              : "No licence assertions were recorded for this scan. The dependency-enrichment stage may not have run yet."
          }
        />
      ) : (
        <>
          <ResponsiveTable
            headers={["Package", "Version", "Licence", "Provider", "Direct?", "Review", "Unknown?"]}
          >
            {items.map((assertion) => (
              <tr key={assertion.id} className="table-row">
                <td className="table-cell font-mono text-xs text-ink-700">
                  {assertion.package_name}
                </td>
                <td className="table-cell text-ink-500">
                  {assertion.version ?? "—"}
                </td>
                <td className="table-cell text-ink-700">
                  {assertion.licence}
                </td>
                <td className="table-cell text-ink-500">
                  {assertion.provider}
                </td>
                <td className="table-cell">
                  <StatusBadge status={assertion.direct ? "available" : "transitive"} />
                </td>
                <td className="table-cell">
                  <StatusBadge status={assertion.review_status} />
                </td>
                <td className="table-cell">
                  <StatusBadge status={assertion.unknown_licence ? "unknown" : "completed"} />
                </td>
              </tr>
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
          <div className="mt-4">
            <EnrichmentSummary scanId={sid} disabledByOperator={depsDisabled} />
          </div>
        </>
      )}
    </>
  );
}

function EnrichmentSummary({
  scanId,
  disabledByOperator,
}: {
  scanId: number;
  disabledByOperator: boolean;
}) {
  const [items, setItems] = useState<ComponentEnrichment[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .listEnrichments(scanId, { page: 1, page_size: 200 })
      .then((r) => {
        if (controller.signal.aborted) return;
        setItems(r.items);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (!isNotImplemented(err)) {
          setError(err);
        } else {
          setItems([]);
        }
      });
    return () => controller.abort();
  }, [scanId]);
  if (error) {
    return (
      <DataCompletenessNotice
        title="Could not load enrichment metadata"
        description="The deps.dev enrichment endpoint did not respond. The licence inventory above still reflects the rule engine's findings; the missing enrichment data is logged on the provider status page."
        tone="warn"
      />
    );
  }
  if (items === null) {
    return <Skeleton rows={2} />;
  }
  if (items.length === 0) {
    return (
      <DataCompletenessNotice
        title={
          disabledByOperator
            ? "deps.dev was disabled by the operator"
            : "No deps.dev enrichment for this scan"
        }
        description={
          disabledByOperator
            ? "No deps.dev request or cache lookup was made. The licence inventory above still reflects local rule-engine findings."
            : "This scan's components were not enriched. The licence inventory above still reflects the rule engine's findings; provider status is on the provider page."
        }
        tone="info"
      />
    );
  }
  const fresh = items.filter((i) => i.provider_status === "available");
  const cached = items.filter((i) => i.provider_status === "cached");
  const unavailable = items.filter(
    (i) => i.provider_status === "unavailable" || i.provider_status === "partial"
  );
  return (
    <div className="rounded-md border border-ink-200 bg-ink-50 p-3 text-sm text-ink-700">
      <p className="font-semibold">deps.dev enrichment summary</p>
      <p className="mt-1 text-xs text-ink-500">
        {items.length} components observed · {fresh.length} fresh · {cached.length} cached ·{" "}
        {unavailable.length} unavailable
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {items.slice(0, 5).map((i) => (
          <li key={i.component_id} className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-ink-800">{i.package_name}</span>
            <span className="text-ink-500">@ {i.version ?? "—"}</span>
            {i.provider_status ? (
              <ProviderStatusBadge status={i.provider_status as ProviderStatus} />
            ) : null}
            {i.cache_status && i.cache_status !== "miss" ? (
              <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-600">
                cache: {i.cache_status}
              </span>
            ) : null}
            {i.fetched_at ? (
              <span className="text-ink-500">{formatTimestamp(i.fetched_at)}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
