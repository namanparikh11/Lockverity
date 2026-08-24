// `ELIGIBLE_COMPARE_SCAN_STATUSES` below is built with
// `new Set([...])`. The cycle 1-6 `eslint-plugin-react-refresh@0.4.26`
// considered this a constant export; the cycle 7
// `0.5.3` only allows `Literal`, `UnaryExpression`,
// `TemplateLiteral`, `ArrayExpression`, `ObjectExpression`,
// and `Identifier` (createContext) as constants. To keep the
// public constant co-located with the page that owns it, the
// `react-refresh/only-export-components` rule is disabled for
// this file. (Refactoring the constant into a separate file
// is the next-step recommendation once we have a follow-up
// cycle for that change.)
/* eslint-disable react-refresh/only-export-components */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "@/api/api";
import type { Scan, ScanStatus } from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { StatusBadge } from "@/components/StatusBadge";
import { formatRelative } from "@/utils/time";

/**
 * Scans eligible to appear in the comparison-selector table.
 *
 * Mirrors the v0.5 backend rule: the comparator is built
 * over persisted local-analysis evidence, so the only
 * scans that produce a trustworthy diff are the ones that
 * finished their work. ``completed`` and ``partial`` are
 * eligible; ``failed`` and ``cancelled`` had no trustworthy
 * local evidence and are rejected by the backend with
 * ``409 illegal_transition``. ``queued`` and ``running`` are
 * obviously not eligible either.
 *
 * The selector additionally excludes the current scan itself
 * (a scan cannot be compared with itself) and any scan from a
 * different repository.
 */
export const ELIGIBLE_COMPARE_SCAN_STATUSES: ReadonlySet<ScanStatus> = new Set<ScanStatus>([
  "completed",
  "partial",
]);

/**
 * Pick a comparison partner for the given head scan.
 *
 * Only other eligible terminal scans belonging to the same
 * repository are shown. The route is refresh-safe: the head
 * scan id is taken from the URL, and the picked partner ends
 * up in the direct comparison route
 * ``/scans/:headId/compare/:baseId``.
 */
export function ScanCompareSelectPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const headId = Number.parseInt(scanId ?? "", 10);
  const valid = Number.isFinite(headId);
  const [head, setHead] = useState<Scan | null>(null);
  const [candidates, setCandidates] = useState<Scan[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!valid) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setError(null);
    setHead(null);
    setCandidates(null);
    api
      .getScan(headId, { signal: controller.signal })
      .then((scan) => {
        if (controller.signal.aborted) return;
        setHead(scan);
        return api
          .listScansForRepository(scan.repository_id, {
            page: 1,
            page_size: 50,
          })
          .then((r) => {
            if (controller.signal.aborted) return;
            const eligible = r.items.filter(
              (s) =>
                s.id !== headId &&
                s.repository_id === scan.repository_id &&
                ELIGIBLE_COMPARE_SCAN_STATUSES.has(s.status)
            );
            eligible.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
            setCandidates(eligible);
          });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, [headId, valid]);

  if (error) {
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Scan", to: `/scans/${headId}` },
            { label: "Compare" },
          ]}
        />
        <ErrorState error={error} title="Could not load eligible scans" />
      </>
    );
  }

  if (head === null || candidates === null) {
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Scan", to: `/scans/${headId}` },
            { label: "Compare" },
          ]}
        />
        <LoadingState label="Loading eligible scans" />
      </>
    );
  }

  if (candidates.length === 0) {
    return (
      <>
        <PageHeader
          title={`Compare scan #${head.id} with another scan`}
          description="Only other completed or partial scans in the same repository are eligible. Failed and cancelled scans are not eligible because they did not produce trustworthy local-analysis evidence."
          breadcrumbs={[
            { label: "Scan", to: `/scans/${head.id}` },
            { label: "Compare" },
          ]}
        />
        <EmptyState
          title="No eligible scans to compare"
          description="Comparison requires a second completed or partial scan in the same repository. This scan is the only eligible one we have so far."
          action={
            <Link to={`/scans/${head.id}`} className="btn-primary">
              Back to scan
            </Link>
          }
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={`Compare scan #${head.id} with another scan`}
        description="Pick another completed or partial scan in the same repository. The comparator never compares scans across repositories, never compares a scan to itself, and never compares against a failed or cancelled scan."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${head.id}` },
          { label: "Compare" },
        ]}
      />
      <ResponsiveTable headers={["Base scan", "Status", "Created", "Action"]}>
        {candidates.map((scan) => (
          <tr key={scan.id} className="table-row">
            <td className="table-cell">
              <Link
                to={`/scans/${scan.id}`}
                className="text-ink-900 hover:text-accent-700"
              >
                #{scan.id}
              </Link>
            </td>
            <td className="table-cell">
              <StatusBadge status={scan.status} />
            </td>
            <td className="table-cell text-ink-500">
              {formatRelative(scan.created_at)}
            </td>
            <td className="table-cell">
              <Link
                to={`/scans/${head.id}/compare/${scan.id}`}
                className="btn-primary"
                data-compare-base={scan.id}
                data-compare-head={head.id}
              >
                Use as base
              </Link>
            </td>
          </tr>
        ))}
      </ResponsiveTable>
    </>
  );
}
