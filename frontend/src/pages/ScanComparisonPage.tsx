import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import type {
  ObservationState,
  ProviderStateName,
  ScanComparison,
  ScanComparisonComponentObservation,
  ScanComparisonCoverageSummary,
  ScanComparisonLicenceObservation,
  ScanComparisonOpenSSFObservation,
  ScanComparisonProviderCoverage,
  ScanComparisonVulnerabilityObservation,
  ScanComparisonWorkflowObservation,
  ScanComparisonManifestObservation,
  ScanComparisonDependencyPathChange,
} from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import { formatTimestamp } from "@/utils/time";

/**
 * v0.5 evidence-aware scan comparison.
 *
 * The page renders a deterministic diff of two terminal scans
 * belonging to the same workspace/repository. The state
 * vocabulary is the v0.5 evidence-honest one:
 *
 *   newly_observed           - present in the head scan only
 *   still_observed           - present in both with no material change
 *   no_longer_observed       - present in the base scan only
 *   changed_observation      - present in both with a material change
 *   coverage_changed         - provider availability / freshness moved
 *   comparison_indeterminate - evidence was insufficient to compare
 *
 * The page deliberately avoids the words "fixed", "resolved",
 * "secure", "clean", or any global pass/fail verdict. A row
 * that disappeared from the head scan is shown as
 * "no longer observed", never as "fixed" or "resolved". The
 * "no differences observed" summary is always qualified by a
 * coverage statement so the operator can never mistake a
 * quiet comparison for an all-clear.
 *
 * v1.8: the page also accepts optional ``breadcrumbs`` so
 * the repository-scoped selection page can render the same
 * comparison body with a "Repository → Compare" trail.
 * When the props are not supplied, the page falls back to
 * the URL params ``:scanId`` (head) and ``:baseScanId`` for
 * backward compatibility with the existing
 * ``/scans/:scanId/compare/:baseScanId`` route and the
 * existing v0.5 tests.
 */
export function ScanComparisonPage(props: {
  headId?: number;
  baseId?: number;
  breadcrumbs?: { label: string; to?: string }[];
} = {}) {
  const params = useParams<{
    scanId: string;
    baseScanId: string;
  }>();
  const headId =
    props.headId ?? Number.parseInt(params.scanId ?? "", 10);
  const baseId =
    props.baseId ?? Number.parseInt(params.baseScanId ?? "", 10);
  const breadcrumbs =
    props.breadcrumbs ?? [
      { label: "Scan", to: `/scans/${headId}` },
      { label: `Compare with #${baseId}` },
    ];
  const [data, setData] = useState<ScanComparison | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isFinite(headId) || !Number.isFinite(baseId)) {
      setError(new Error("Invalid scan ids."));
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setData(null);
    setError(null);
    setLoading(true);
    api
      .compareScans(baseId, headId)
      .then((d) => {
        if (controller.signal.aborted) return;
        setData(d);
        setLoading(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        // The v0.5 endpoint is the canonical implementation; the
        // legacy "not implemented" fallback is preserved for
        // development fixtures only.
        if (isNotImplemented(err)) {
          setError(err);
          setLoading(false);
          return;
        }
        setError(err);
        setLoading(false);
      });
    return () => controller.abort();
  }, [baseId, headId]);

  return (
    <>
      <PageHeader
        title={`Compare scans #${baseId} → #${headId}`}
        description="A read-only diff of two terminal scans. Rows are described in evidence-honest terms (newly observed, still observed, no longer observed, changed observation, coverage changed, comparison indeterminate). A row is never marked fixed or resolved."
        breadcrumbs={breadcrumbs}
      />
      {loading ? (
        <Skeleton rows={6} />
      ) : error ? (
        <ComparisonError error={error} headId={headId} baseId={baseId} />
      ) : data === null ? (
        <Skeleton rows={6} />
      ) : (
        <ComparisonBody data={data} />
      )}
    </>
  );
}

function ComparisonError({
  error,
  headId,
  baseId,
}: {
  error: unknown;
  headId: number;
  baseId: number;
}) {
  return (
    <div className="space-y-4">
      <DataCompletenessNotice
        title="Comparison could not be loaded"
        tone="warn"
        description="The comparator could not return a diff for the two selected scans. See the error below for the precise reason."
      />
      <ErrorState error={error} title="Could not compare scans" />
      <div className="text-sm text-ink-500 dark:text-surface-dark-text-muted">
        <Link to={`/scans/${headId}`} className="hover:text-accent-700 dark:text-accent-dark-300">
          Open scan #{headId}
        </Link>
        {" · "}
        <Link to={`/scans/${baseId}`} className="hover:text-accent-700 dark:text-accent-dark-300">
          Open scan #{baseId}
        </Link>
      </div>
    </div>
  );
}

function ComparisonBody({ data }: { data: ScanComparison }) {
  return (
    <>
      <ScanIdentity data={data} />
      <CoverageNotice data={data} />
      <IndeterminateReasons data={data} />
      <NoDifferencesNotice data={data} />
      <SectionHeading>Local evidence</SectionHeading>
      <ComponentsSection rows={data.components} />
      <ManifestsSection rows={data.manifests} />
      <DependencyPathsSection rows={data.dependency_paths} />
      <WorkflowsSection rows={data.workflows} />
      <SectionHeading>Provider-derived evidence</SectionHeading>
      <VulnerabilitiesSection rows={data.vulnerabilities} />
      <LicencesSection rows={data.licences} />
      <OpenSSFSection rows={data.openssf} />
      <ProvidersSection rows={data.providers} />
      <p className="mt-6 text-xs text-ink-500 dark:text-surface-dark-text-muted">
        Comparison generated at {formatTimestamp(data.generated_at)}.{" "}
        <Timestamp value={data.generated_at} mode="relative" />
      </p>
    </>
  );
}

function ScanIdentity({ data }: { data: ScanComparison }) {
  return (
    <section className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <SummaryCard label="Base scan" tone="muted">
        <p className="text-sm font-semibold text-ink-900 dark:text-surface-dark-text">
          <Link to={`/scans/${data.base_scan_id}`} className="hover:text-accent-700 dark:text-accent-dark-300">
            #{data.base_scan_id}
          </Link>{" "}
          <span className="text-xs font-normal text-ink-500 dark:text-surface-dark-text-muted">
            (repository #{data.repository_id})
          </span>
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Status: <StatusBadge status={data.coverage.base_scan_status} />
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Completed: {formatTimestamp(data.base_completed_at)}
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Trigger: {data.base_trigger_type ?? "—"}
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Analyzer: {data.base_analyzer_version ?? "—"}
        </p>
        {data.base_resolved_commit_sha ? (
          <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
            Resolved commit:{" "}
            <span className="font-mono">{data.base_resolved_commit_sha}</span>
          </p>
        ) : null}
      </SummaryCard>
      <SummaryCard label="Head scan" tone="muted">
        <p className="text-sm font-semibold text-ink-900 dark:text-surface-dark-text">
          <Link to={`/scans/${data.head_scan_id}`} className="hover:text-accent-700 dark:text-accent-dark-300">
            #{data.head_scan_id}
          </Link>{" "}
          <span className="text-xs font-normal text-ink-500 dark:text-surface-dark-text-muted">
            (repository #{data.repository_id})
          </span>
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Status: <StatusBadge status={data.coverage.head_scan_status} />
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Completed: {formatTimestamp(data.head_completed_at)}
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Trigger: {data.head_trigger_type ?? "—"}
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Analyzer: {data.head_analyzer_version ?? "—"}
        </p>
        {data.head_resolved_commit_sha ? (
          <p className="mt-1 text-xs text-ink-500 dark:text-surface-dark-text-muted">
            Resolved commit:{" "}
            <span className="font-mono">{data.head_resolved_commit_sha}</span>
          </p>
        ) : null}
      </SummaryCard>
    </section>
  );
}

function CoverageNotice({ data }: { data: ScanComparison }) {
  const c: ScanComparisonCoverageSummary = data.coverage;
  const baseCounts = [
    `${c.components_in_base} components`,
    `${c.findings_in_base} findings`,
    `${c.vulnerabilities_in_base} vulnerabilities`,
    `${c.workflows_in_base} workflows`,
    `${c.manifests_in_base} manifests`,
    `${c.licence_assertions_in_base} licence assertions`,
    `${c.openssf_checks_in_base} OpenSSF checks`,
  ];
  const headCounts = [
    `${c.components_in_head} components`,
    `${c.findings_in_head} findings`,
    `${c.vulnerabilities_in_head} vulnerabilities`,
    `${c.workflows_in_head} workflows`,
    `${c.manifests_in_head} manifests`,
    `${c.licence_assertions_in_head} licence assertions`,
    `${c.openssf_checks_in_head} OpenSSF checks`,
  ];
  return (
    <section className="mb-6">
      <h2 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Evidence coverage
      </h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="card">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-500 dark:text-surface-dark-text-muted">
            Base scan #{data.base_scan_id} observed
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm text-ink-700 dark:text-surface-dark-text">
            {baseCounts.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
        <div className="card">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-500 dark:text-surface-dark-text-muted">
            Head scan #{data.head_scan_id} observed
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm text-ink-700 dark:text-surface-dark-text">
            {headCounts.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function IndeterminateReasons({ data }: { data: ScanComparison }) {
  if (data.indeterminate_reasons.length === 0) return null;
  return (
    <div className="mb-6">
      <DataCompletenessNotice
        title="Some rows are marked 'Comparison indeterminate'"
        tone="warn"
        description="The comparator could not make a determination for these rows because the underlying evidence was insufficient. They are NOT counted as 'no longer observed' or 'newly observed'."
      >
        <ul className="mt-2 list-disc pl-5 text-xs">
          {data.indeterminate_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </DataCompletenessNotice>
    </div>
  );
}

function NoDifferencesNotice({ data }: { data: ScanComparison }) {
  const anyDifferences = countDifferences(data) > 0;
  if (anyDifferences) return null;
  return (
    <div className="mb-6">
      <DataCompletenessNotice
        title="No differences observed across the available evidence"
        tone="muted"
        description={
          "This message is qualified by the evidence-coverage summary above. " +
          "It does not mean the software is secure, clean, or free of risk; it " +
          "means the comparator found no evidence change within the data the " +
          "providers actually returned. Where provider coverage was unavailable, " +
          "partial, or stale, the comparison for that domain is marked " +
          "'Comparison indeterminate' rather than 'no differences observed'."
        }
      />
    </div>
  );
}

function countDifferences(data: ScanComparison): number {
  const isChange = (state: ObservationState) =>
    state !== "still_observed" && state !== "comparison_indeterminate";
  return (
    data.components.filter((row) => isChange(row.state)).length +
    data.manifests.filter((row) => isChange(row.state)).length +
    data.dependency_paths.filter((row) => isChange(row.state)).length +
    data.workflows.filter((row) => isChange(row.state)).length +
    data.vulnerabilities.filter((row) => isChange(row.state)).length +
    data.licences.filter((row) => isChange(row.state)).length +
    data.openssf.filter((row) => isChange(row.state)).length +
    data.providers.filter((row) => isChange(row.state)).length
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 mt-6 text-sm font-semibold uppercase tracking-wide text-ink-700 dark:text-surface-dark-text">
      {children}
    </h2>
  );
}

// ---------------------------------------------------------------------------
// Local evidence
// ---------------------------------------------------------------------------

function ComponentsSection({
  rows,
}: {
  rows: ScanComparisonComponentObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Components and versions
      </h3>
      <p className="mb-2 text-xs text-ink-500 dark:text-surface-dark-text-muted">
        The concrete version is part of each component&apos;s
        identity, so the same package at different versions
        appears as separate rows. No row is ever labelled
        &ldquo;fixed&rdquo; or &ldquo;resolved&rdquo;.
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No components in either scan.</p>
      ) : (
        <ResponsiveTable
          headers={[
            "Package",
            "Ecosystem",
            "Version",
            "State",
            "Direct (base → head)",
          ]}
        >
          {rows.map((row) => (
            <tr
              key={`${row.ecosystem ?? "?"}/${row.package_name}/${row.version ?? "?"}`}
              className="table-row"
            >
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.package_name}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">{row.ecosystem ?? "—"}</td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted font-mono text-xs">
                {row.version ?? "(unresolved)"}
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.direct_base === null
                  ? "—"
                  : row.direct_base
                  ? "yes"
                  : "no"}
                {" → "}
                {row.direct_head === null
                  ? "—"
                  : row.direct_head
                  ? "yes"
                  : "no"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function ManifestsSection({
  rows,
}: {
  rows: ScanComparisonManifestObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">Manifests</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No manifests observed.</p>
      ) : (
        <ResponsiveTable
          headers={["Manifest", "Ecosystem", "State", "Base SHA", "Head SHA"]}
        >
          {rows.map((row) => (
            <tr key={row.manifest_path} className="table-row">
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.manifest_path}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">{row.ecosystem ?? "—"}</td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell font-mono text-[10px] text-ink-500 dark:text-surface-dark-text-muted">
                {row.content_sha256_base ? row.content_sha256_base.slice(0, 12) : "—"}
              </td>
              <td className="table-cell font-mono text-[10px] text-ink-500 dark:text-surface-dark-text-muted">
                {row.content_sha256_head ? row.content_sha256_head.slice(0, 12) : "—"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function DependencyPathsSection({
  rows,
}: {
  rows: ScanComparisonDependencyPathChange[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Dependency path changes
      </h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">
          No parent-chain changes detected for components present in both scans.
        </p>
      ) : (
        <ResponsiveTable
          headers={["Package", "Version", "State", "Base parents", "Head parents"]}
        >
          {rows.map((row) => (
            <tr
              key={`${row.ecosystem ?? "?"}/${row.package_name}@${row.version ?? "?"}`}
              className="table-row"
            >
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.package_name}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">{row.version ?? "—"}</td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.parent_chain_base.length === 0
                  ? "—"
                  : row.parent_chain_base.join(", ")}
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.parent_chain_head.length === 0
                  ? "—"
                  : row.parent_chain_head.join(", ")}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function WorkflowsSection({
  rows,
}: {
  rows: ScanComparisonWorkflowObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Workflow findings
      </h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No workflow findings observed.</p>
      ) : (
        <ResponsiveTable
          headers={["Rule", "Workflow", "State", "Severity (base → head)", "Confidence (base → head)"]}
        >
          {rows.map((row) => (
            <tr key={row.stable_key} className="table-row">
              <td className="table-cell font-mono text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.rule_id}
              </td>
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.workflow_path}
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.severity_base ?? "—"} → {row.severity_head ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.confidence_base ?? "—"} → {row.confidence_head ?? "—"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Provider-derived evidence
// ---------------------------------------------------------------------------

function VulnerabilitiesSection({
  rows,
}: {
  rows: ScanComparisonVulnerabilityObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Vulnerabilities
      </h3>
      <p className="mb-2 text-xs text-ink-500 dark:text-surface-dark-text-muted">
        A row that disappeared from the head scan is shown as
        &quot;no longer observed&quot;; it is not described as fixed
        or resolved. When head provider coverage for the affected
        ecosystem was unavailable, partial, or stale, the row is
        shown as &quot;comparison indeterminate&quot; instead.
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No vulnerability rows observed.</p>
      ) : (
        <ResponsiveTable
          headers={[
            "Advisory",
            "Package",
            "State",
            "Provider (base → head)",
            "Fetched (base → head)",
            "Note",
          ]}
        >
          {rows.map((row) => (
            <tr
              key={[
                row.advisory_source ?? "",
                row.advisory_external_id ?? "",
                row.advisory_canonical_id ?? "",
                row.package_name ?? "",
                row.package_version_head ?? row.package_version_base ?? "",
              ].join("|")}
              className="table-row"
            >
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.advisory_canonical_id ?? row.advisory_external_id ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                <span className="font-mono text-xs">
                  {row.package_name ?? "—"}
                </span>{" "}
                <span className="text-[10px] text-ink-400 dark:text-surface-dark-text-subtle">
                  ({row.ecosystem ?? "—"})
                </span>
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.provider_provenance_base ?? "—"} →{" "}
                {row.provider_provenance_head ?? "—"}
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.fetched_at_base ?? "—"} → {row.fetched_at_head ?? "—"}
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.ambiguity_reason ?? "—"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function LicencesSection({
  rows,
}: {
  rows: ScanComparisonLicenceObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Licence and package observations
      </h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No licence assertions observed.</p>
      ) : (
        <ResponsiveTable
          headers={[
            "Package",
            "Version",
            "State",
            "Licence (base → head)",
            "Provider (base → head)",
            "Review (base → head)",
          ]}
        >
          {rows.map((row) => (
            <tr
              key={[
                row.package_name ?? "",
                row.package_version_base ?? row.package_version_head ?? "",
                row.licence_base ?? row.licence_head ?? "",
              ].join("|")}
              className="table-row"
            >
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.package_name ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.package_version_base ?? row.package_version_head ?? "—"}
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.licence_base ?? "—"} → {row.licence_head ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.provider_base ?? "—"} → {row.provider_head ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.review_status_base ?? "—"} →{" "}
                {row.review_status_head ?? "—"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function OpenSSFSection({
  rows,
}: {
  rows: ScanComparisonOpenSSFObservation[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        OpenSSF observations
      </h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No OpenSSF observations recorded.</p>
      ) : (
        <ResponsiveTable
          headers={["Check", "State", "Score (base → head)", "Source"]}
        >
          {rows.map((row) => (
            <tr key={row.check_id} className="table-row">
              <td className="table-cell">
                <p className="text-sm font-semibold text-ink-900 dark:text-surface-dark-text">{row.name}</p>
                <p className="font-mono text-[10px] text-ink-500 dark:text-surface-dark-text-muted">
                  {row.check_id}
                </p>
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.score_base ?? "—"} → {row.score_head ?? "—"}
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">{row.source}</td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

function ProvidersSection({
  rows,
}: {
  rows: ScanComparisonProviderCoverage[];
}) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 text-sm font-semibold text-ink-700 dark:text-surface-dark-text">
        Provider coverage
      </h3>
      <p className="mb-2 text-xs text-ink-500 dark:text-surface-dark-text-muted">
        A &quot;successful&quot; provider returned a structured
        evidence envelope. &quot;Cached&quot;, &quot;stale&quot;,
        &quot;partial&quot;, &quot;unavailable&quot;,
        &quot;unsupported&quot;, &quot;not_requested&quot;, and
        &quot;unknown&quot; states are all kept explicit so the
        operator can see what the comparator was actually
        looking at. Successful evidence is never carried in the
        &quot;error_summary&quot; field.
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted">No providers recorded for either scan.</p>
      ) : (
        <ResponsiveTable
          headers={[
            "Provider",
            "Change state",
            "Base state",
            "Head state",
            "Records (base → head)",
            "Error summary (base → head)",
          ]}
        >
          {rows.map((row) => (
            <tr key={row.provider} className="table-row">
              <td className="table-cell font-mono text-xs text-ink-700 dark:text-surface-dark-text">
                {row.provider}
              </td>
              <td className="table-cell">
                <ObservationStateBadge state={row.state} />
              </td>
              <td className="table-cell">
                <ProviderStateBadge state={row.state_base} />
              </td>
              <td className="table-cell">
                <ProviderStateBadge state={row.state_head} />
              </td>
              <td className="table-cell text-ink-500 dark:text-surface-dark-text-muted">
                {row.records_returned_base ?? "—"} →{" "}
                {row.records_returned_head ?? "—"}
              </td>
              <td className="table-cell text-xs text-ink-500 dark:text-surface-dark-text-muted">
                {row.error_summary_base ?? "—"} →{" "}
                {row.error_summary_head ?? "—"}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Local state badge that uses the v0.5 vocabulary
// ---------------------------------------------------------------------------

const STATE_TONE: Record<ObservationState, "ok" | "warn" | "danger" | "muted" | "info"> = {
  newly_observed: "info",
  still_observed: "muted",
  no_longer_observed: "warn",
  changed_observation: "info",
  coverage_changed: "warn",
  comparison_indeterminate: "muted",
};

const STATE_LABEL: Record<ObservationState, string> = {
  newly_observed: "Newly observed",
  still_observed: "Still observed",
  no_longer_observed: "No longer observed",
  changed_observation: "Changed observation",
  coverage_changed: "Coverage changed",
  comparison_indeterminate: "Comparison indeterminate",
};

function ObservationStateBadge({ state }: { state: ObservationState }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${
        toneClass(STATE_TONE[state])
      }`}
      aria-label={`Observation state: ${STATE_LABEL[state]}`}
    >
      {STATE_LABEL[state]}
    </span>
  );
}

function toneClass(tone: "ok" | "warn" | "danger" | "muted" | "info"): string {
  switch (tone) {
    case "ok":
      return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "warn":
      return "bg-amber-50 text-amber-700 border-amber-200";
    case "danger":
      return "bg-rose-50 text-rose-700 border-rose-200";
    case "muted":
      return "bg-ink-50 dark:bg-surface-dark-app text-ink-500 dark:text-surface-dark-text-muted border-ink-200 dark:border-surface-dark-border";
    case "info":
      return "bg-accent-50 dark:bg-surface-dark-raised text-accent-700 dark:text-accent-dark-300 border-accent-200";
  }
}

function ProviderStateBadge({ state }: { state: ProviderStateName }) {
  return (
    <div className="flex flex-col items-start gap-1">
      <span
        className="inline-flex items-center rounded-full border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface px-2 py-0.5 text-xs font-medium text-ink-700 dark:text-surface-dark-text"
        aria-label={`Provider state: ${state}`}
      >
        {state}
      </span>
      {state === "stale" || state === "partial" || state === "unavailable" ? (
        <span className="text-[10px] text-amber-700">
          Coverage was not fully trustworthy; the comparison for this
          domain is bounded by what the provider returned.
        </span>
      ) : null}
    </div>
  );
}
