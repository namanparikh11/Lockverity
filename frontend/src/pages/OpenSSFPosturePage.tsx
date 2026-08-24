import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import {
  providerWasDisabledByOperator,
  useProviderObservation,
} from "@/api/useProviderObservation";
import type { OpenSSFCheck, PageMeta } from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";

/**
 * OpenSSF posture.
 *
 * Lockverity imports externally produced Scorecard results and
 * surfaces them as observations. The page is explicit that
 * Lockverity does not independently re-evaluate each check.
 */
export function OpenSSFPosturePage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const [items, setItems] = useState<OpenSSFCheck[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [checkId, setCheckId] = useState("");
  const [notImpl, setNotImpl] = useState(false);
  const openssfObservation = useProviderObservation(sid, "openssf");
  const openssfDisabled = providerWasDisabledByOperator(openssfObservation);
  const openssfNotApplicable =
    openssfObservation?.status === "not_requested" &&
    openssfObservation.error_code === "not_applicable";

  useEffect(() => {
    setPage(1);
  }, [checkId]);

  useEffect(() => {
    if (!Number.isFinite(sid)) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .listOpenSSF(sid, { page, page_size: 25, check_id: checkId || undefined })
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
  }, [sid, page, checkId]);

  return (
    <>
      <PageHeader
        title={`OpenSSF posture · scan #${sid}`}
        description="Externally sourced observations from the OpenSSF Scorecard project. Lockverity displays published results; it does not independently reproduce every check."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "OpenSSF posture" },
        ]}
      />
      <div className="mb-4">
        <DataCompletenessNotice
          title="External observations, not a Lockverity verdict"
          description="Each row is the result of an upstream OpenSSF Scorecard run. Lockverity records the source, score, and reason. A check that has not yet been imported remains unobserved, never 'clean'."
          tone="info"
        />
      </div>
      <div className="mb-4">
        <FilterBar
          search={checkId}
          onSearchChange={setCheckId}
          searchPlaceholder="Filter by check id (e.g. Binary-Artifacts, Code-Review)"
          resultCount={meta?.total}
          resultLabel="checks"
        />
      </div>
      {error ? (
        <ErrorState error={error} />
      ) : items === null || meta === null ? (
        <Skeleton rows={5} />
      ) : items.length === 0 ? (
        <EmptyState
          title={
            notImpl
              ? "OpenSSF endpoint not exposed"
              : openssfDisabled
                ? "OpenSSF Scorecard was not requested"
                : openssfNotApplicable
                  ? "OpenSSF Scorecard is not applicable"
                  : "No OpenSSF checks imported"
          }
          description={
            notImpl
              ? "When the backend exposes an OpenSSF endpoint, this table will appear automatically."
              : openssfDisabled
                ? "OpenSSF Scorecard was disabled by the operator for this scan. No Scorecard request or cache lookup was made."
                : openssfNotApplicable
                  ? "OpenSSF Scorecard repository posture applies only to supported GitHub repositories, not archive uploads."
              : "No OpenSSF Scorecard observations are attached to this scan yet."
          }
        />
      ) : (
        <>
          <ResponsiveTable
            headers={["Check id", "Name", "Score", "Source", "Reason"]}
          >
            {items.map((check) => (
              <tr key={check.id} className="table-row">
                <td className="table-cell font-mono text-xs text-ink-500">
                  {check.check_id}
                </td>
                <td className="table-cell text-ink-800">{check.name}</td>
                <td className="table-cell text-ink-700">
                  {check.score == null ? "—" : check.score.toFixed(1)}
                </td>
                <td className="table-cell text-xs text-ink-500">
                  {check.source}
                </td>
                <td className="table-cell text-xs text-ink-500">
                  {check.reason ?? "—"}
                </td>
              </tr>
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
        </>
      )}
    </>
  );
}
