import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api } from "@/api/api";
import type { Scan, ScanStatus } from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { ScanComparisonPage } from "@/pages/ScanComparisonPage";
import { StatusBadge } from "@/components/StatusBadge";
import { formatRelative } from "@/utils/time";

/**
 * v1.8 — Repository-scoped scan comparison selection page.
 *
 * Route: ``/repositories/:repositoryId/compare``
 *
 * URL query state:
 *  - ``baseline=<scanId>``   the comparison baseline
 *  - ``comparison=<scanId>`` the comparison head
 *
 * The page is a thin wrapper over the v0.5
 * ``ScanComparisonPage`` that enforces repository
 * scope and URL-persisted selection. The comparator
 * itself is the existing v0.5 engine; v1.8 does not
 * introduce a new comparison algorithm.
 *
 * Validation rules (mirrored from the backend):
 *  - The two scan ids must be distinct.
 *  - Both scans must belong to this repository.
 *  - Both scans must be in a terminal state
 *    (``completed`` or ``partial``). Failed and
 *    cancelled scans are not eligible as a comparison
 *    baseline, per the v0.5 comparator.
 *  - Invalid or cross-repository ids are rendered as
 *    a bounded error rather than silently excluded.
 */

const ELIGIBLE_STATUSES: ReadonlySet<ScanStatus> = new Set<ScanStatus>([
  "completed",
  "partial",
]);

type InvalidReason =
  | { kind: "missing-selection" }
  | { kind: "duplicate-selection"; scanId: number }
  | { kind: "not-eligible"; scanId: number; status: ScanStatus }
  | { kind: "cross-repository"; scanId: number; actualRepositoryId: number }
  | { kind: "unknown"; scanId: number };

function checkInvalid(
  baseline: Scan | null,
  comparison: Scan | null,
  expectedRepositoryId: number
): InvalidReason | null {
  if (baseline === null || comparison === null) {
    return { kind: "missing-selection" };
  }
  if (baseline.id === comparison.id) {
    return { kind: "duplicate-selection", scanId: baseline.id };
  }
  if (baseline.repository_id !== expectedRepositoryId) {
    return {
      kind: "cross-repository",
      scanId: baseline.id,
      actualRepositoryId: baseline.repository_id,
    };
  }
  if (comparison.repository_id !== expectedRepositoryId) {
    return {
      kind: "cross-repository",
      scanId: comparison.id,
      actualRepositoryId: comparison.repository_id,
    };
  }
  if (!ELIGIBLE_STATUSES.has(baseline.status)) {
    return { kind: "not-eligible", scanId: baseline.id, status: baseline.status };
  }
  if (!ELIGIBLE_STATUSES.has(comparison.status)) {
    return { kind: "not-eligible", scanId: comparison.id, status: comparison.status };
  }
  return null;
}

export function RepositoryComparePage() {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const repoId = Number.parseInt(repositoryId ?? "", 10);
  const validRepoId = Number.isFinite(repoId);

  const baselineParam = searchParams.get("baseline");
  const comparisonParam = searchParams.get("comparison");
  const baselineId = baselineParam ? Number.parseInt(baselineParam, 10) : NaN;
  const comparisonId = comparisonParam
    ? Number.parseInt(comparisonParam, 10)
    : NaN;
  const hasValidIds =
    Number.isFinite(baselineId) && Number.isFinite(comparisonId);

  const [scans, setScans] = useState<Scan[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [baselineDetail, setBaselineDetail] = useState<Scan | null>(null);
  const [headDetail, setHeadDetail] = useState<Scan | null>(null);

  // ---- Eligible scan list (always loaded) ----
  useEffect(() => {
    if (!validRepoId) {
      setError(new Error("Invalid repository id."));
      return;
    }
    const controller = new AbortController();
    setScans(null);
    setError(null);
    api
      .listScansForRepository(repoId, { page: 1, page_size: 50 })
      .then((r) => {
        if (controller.signal.aborted) return;
        setScans(r.items);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, [repoId, validRepoId]);

  const eligibleScans = useMemo(
    () => (scans ?? []).filter((s) => ELIGIBLE_STATUSES.has(s.status)),
    [scans]
  );

  // ---- Detail lookups (when the user supplies ids) ----
  useEffect(() => {
    if (!validRepoId) return;
    if (!hasValidIds) {
      setBaselineDetail(null);
      setHeadDetail(null);
      return;
    }
    const controller = new AbortController();
    setBaselineDetail(null);
    setHeadDetail(null);
    Promise.all([
      api
        .getScan(baselineId, { signal: controller.signal })
        .catch((err) => err),
      api
        .getScan(comparisonId, { signal: controller.signal })
        .catch((err) => err),
    ]).then(([b, h]) => {
      if (controller.signal.aborted) return;
      if (b instanceof Error) {
        setError(b);
        return;
      }
      if (h instanceof Error) {
        setError(h);
        return;
      }
      setBaselineDetail(b);
      setHeadDetail(h);
    });
    return () => controller.abort();
  }, [
    repoId,
    validRepoId,
    hasValidIds,
    baselineId,
    comparisonId,
  ]);

  function setSelection(baseline: number | null, comparison: number | null) {
    const next = new URLSearchParams(searchParams);
    if (baseline && baseline > 0) {
      next.set("baseline", String(baseline));
    } else {
      next.delete("baseline");
    }
    if (comparison && comparison > 0) {
      next.set("comparison", String(comparison));
    } else {
      next.delete("comparison");
    }
    setSearchParams(next, { replace: true });
  }

  function pickBaseline(scanId: number) {
    setSelection(scanId, comparisonId);
  }
  function pickComparison(scanId: number) {
    setSelection(baselineId, scanId);
  }
  function clearSelection() {
    setSelection(null, null);
  }

  if (error) {
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Repository", to: `/repositories/${repoId}` },
            { label: "Compare" },
          ]}
        />
        <ErrorState error={error} title="Could not load comparison" />
      </>
    );
  }

  if (!validRepoId) {
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Repositories", to: "/repositories" },
            { label: "Invalid" },
          ]}
        />
        <DataCompletenessNotice
          title="Invalid repository id"
          description="The URL does not reference a valid repository."
          tone="warn"
        />
      </>
    );
  }

  // ---- Render the comparison when both ids are
  //      present, valid, and eligible. ----
  if (hasValidIds) {
    if (baselineDetail === null || headDetail === null) {
      return (
        <>
          <PageHeader
            title="Compare scans"
            breadcrumbs={[
              { label: "Repository", to: `/repositories/${repoId}` },
              { label: "Compare" },
            ]}
          />
          <LoadingState label="Loading scans" />
        </>
      );
    }
    const invalid = checkInvalid(
      baselineDetail,
      headDetail,
      validRepoId ? repoId : -1
    );
    if (invalid === null) {
      // Defer the comparison rendering to the v0.5
      // page. The URL is intentionally the
      // repository-scoped one so the back button
      // returns the user to the selection page.
      return (
        <ScanComparisonPage
          headId={comparisonId}
          baseId={baselineId}
          breadcrumbs={[
            { label: "Repository", to: `/repositories/${repoId}` },
            { label: "Compare" },
          ]}
        />
      );
    }
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Repository", to: `/repositories/${repoId}` },
            { label: "Compare" },
          ]}
        />
        <InvalidSelectionNotice
          reason={invalid}
          onReset={clearSelection}
        />
      </>
    );
  }

  // ---- Render the selection UI when no ids are
  //      supplied. The selector mirrors the
  //      existing v0.5 rules: only completed and
  //      partial scans are eligible; the most
  //      recent eligible pair is pre-selected. ----
  if (scans === null) {
    return (
      <>
        <PageHeader
          title="Compare scans"
          breadcrumbs={[
            { label: "Repository", to: `/repositories/${repoId}` },
            { label: "Compare" },
          ]}
        />
        <LoadingState label="Loading scans" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Compare scans"
        description="Pick a baseline and a comparison scan from the same repository. The comparator never compares scans across repositories, never compares a scan with itself, and never compares against a failed or cancelled scan."
        breadcrumbs={[
          { label: "Repository", to: `/repositories/${repoId}` },
          { label: "Compare" },
        ]}
      />
      {eligibleScans.length < 2 ? (
        <EmptyState
          title={
            eligibleScans.length === 0
              ? "No eligible scans to compare"
              : "Only one eligible scan to compare"
          }
          description={
            "Comparison requires at least two completed or partial scans in the same repository. Run another scan to enable comparison."
          }
          action={
            <Link
              to={`/repositories/${repoId}`}
              className="btn-primary"
              data-testid="repository-compare-back"
            >
              Back to repository
            </Link>
          }
        />
      ) : (
        <>
          <p
            className="mb-3 text-xs text-ink-500"
            data-testid="repository-compare-help"
          >
            Newest eligible scans are listed first. Click a scan to set it as
            the comparison head; the baseline defaults to the previous eligible
            scan. You can swap them at any time.
          </p>
          <button
            type="button"
            className="btn-secondary mb-3"
            onClick={() => {
              // Pre-fill the most recent eligible pair.
              const sorted = [...eligibleScans].sort((a, b) =>
                a.created_at < b.created_at ? 1 : -1
              );
              const head = sorted[0];
              const base = sorted[1];
              if (head && base) {
                navigate(
                  `/repositories/${repoId}/compare?baseline=${base.id}&comparison=${head.id}`
                );
              }
            }}
            data-testid="repository-compare-prefill"
          >
            Use most recent eligible pair
          </button>
          <ResponsiveTable
            headers={[
              "Scan",
              "Status",
              "Ref",
              "Created",
              "Action",
            ]}
          >
            {eligibleScans.map((scan) => (
              <tr
                key={scan.id}
                className="table-row"
                data-testid={`compare-candidate-row-${scan.id}`}
              >
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
                <td className="table-cell font-mono text-xs text-ink-500">
                  {scan.requested_ref ?? "—"}
                </td>
                <td className="table-cell text-ink-500">
                  {formatRelative(scan.created_at)}
                </td>
                <td className="table-cell">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => pickBaseline(scan.id)}
                      data-testid={`compare-pick-baseline-${scan.id}`}
                    >
                      Use as baseline
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => pickComparison(scan.id)}
                      data-testid={`compare-pick-comparison-${scan.id}`}
                    >
                      Use as comparison
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </ResponsiveTable>
        </>
      )}
    </>
  );
}

function InvalidSelectionNotice({
  reason,
  onReset,
}: {
  reason: InvalidReason;
  onReset: () => void;
}) {
  if (reason.kind === "duplicate-selection") {
    return (
      <DataCompletenessNotice
        title="Same-scan comparison is not allowed"
        description={`You selected scan #${reason.scanId} as both the baseline and the comparison. Pick two distinct scans.`}
        tone="warn"
      >
        <button
          type="button"
          className="btn-secondary mt-3"
          onClick={onReset}
          data-testid="repository-compare-reset"
        >
          Reset selection
        </button>
      </DataCompletenessNotice>
    );
  }
  if (reason.kind === "not-eligible") {
    return (
      <DataCompletenessNotice
        title="This scan did not complete and is not eligible as a comparison baseline"
        description={`Scan #${reason.scanId} is in state ${reason.status}. The comparator only accepts completed or partial scans as a baseline; the head side may also be partial, with a completeness warning.`}
        tone="warn"
      >
        <button
          type="button"
          className="btn-secondary mt-3"
          onClick={onReset}
          data-testid="repository-compare-reset"
        >
          Reset selection
        </button>
      </DataCompletenessNotice>
    );
  }
  if (reason.kind === "cross-repository") {
    return (
      <DataCompletenessNotice
        title="Cross-repository comparison is not allowed"
        description={`Scan #${reason.scanId} belongs to repository #${reason.actualRepositoryId}, not the current repository. The comparator never crosses repository boundaries.`}
        tone="warn"
      >
        <button
          type="button"
          className="btn-secondary mt-3"
          onClick={onReset}
          data-testid="repository-compare-reset"
        >
          Reset selection
        </button>
      </DataCompletenessNotice>
    );
  }
  if (reason.kind === "unknown") {
    return (
      <DataCompletenessNotice
        title="Unknown scan"
        description={`Scan #${reason.scanId} could not be found.`}
        tone="warn"
      >
        <button
          type="button"
          className="btn-secondary mt-3"
          onClick={onReset}
          data-testid="repository-compare-reset"
        >
          Reset selection
        </button>
      </DataCompletenessNotice>
    );
  }
  return (
    <DataCompletenessNotice
      title="Pick two scans to compare"
      description="Use the selector below to set the baseline and the comparison scan."
      tone="info"
    >
      <button
        type="button"
        className="btn-secondary mt-3"
        onClick={onReset}
        data-testid="repository-compare-reset"
      >
        Reset selection
      </button>
    </DataCompletenessNotice>
  );
}
