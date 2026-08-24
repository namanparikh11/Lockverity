import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { isNotImplemented } from "@/api/fallback";
import type { FindingSeverity, PageMeta, WorkflowFinding } from "@/api/types";
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
import { findingSeverityLabel } from "@/utils/labels";

const SEVERITY_OPTIONS = [
  { value: "all", label: "All severities" },
  { value: "critical", label: findingSeverityLabel.critical },
  { value: "high", label: findingSeverityLabel.high },
  { value: "medium", label: findingSeverityLabel.medium },
  { value: "low", label: findingSeverityLabel.low },
  { value: "informational", label: findingSeverityLabel.informational },
  { value: "unknown", label: findingSeverityLabel.unknown },
];

/**
 * Workflow findings.
 *
 * Lists GitHub Actions rule observations. The detail view
 * surfaces permissions, triggers, unpinned actions, the
 * YAML path / line range, remediation, and limitations.
 * Secret-like values are NEVER rendered in this view.
 */
export function WorkflowFindingsPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const [items, setItems] = useState<WorkflowFinding[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [filters, setFilters] = useState<{
    rule_id: string;
    severity: "all" | FindingSeverity;
  }>({ rule_id: "", severity: "all" });
  const [selected, setSelected] = useState<WorkflowFinding | null>(null);
  const [notImpl, setNotImpl] = useState(false);

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
      .listWorkflowFindings(sid, {
        page,
        page_size: 25,
        rule_id: filters.rule_id || undefined,
        severity: filters.severity,
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
        title={`Workflow findings · scan #${sid}`}
        description="Rule-based observations on GitHub Actions workflows. Permissions, triggers, and unpinned actions are surfaced explicitly; secret-like values are never shown."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "Workflow findings" },
        ]}
      />
      {notImpl ? (
        <div className="mb-4">
          <DataCompletenessNotice
            title="Workflow endpoint not yet implemented"
            description="The shape of this page mirrors the future backend: a per-rule list of workflow observations with the data points below."
            tone="info"
          />
        </div>
      ) : null}
      <div className="mb-4">
        <FilterBar
          search={filters.rule_id}
          onSearchChange={(v) => setFilters((f) => ({ ...f, rule_id: v }))}
          searchPlaceholder="Filter by rule id (e.g. LOCK-WORKFLOW-001)"
          resultCount={meta?.total}
          resultLabel="workflow findings"
        >
          <SelectFilter
            id="severity"
            label="Severity"
            value={filters.severity}
            onChange={(v) => setFilters((f) => ({ ...f, severity: v as "all" | FindingSeverity }))}
            options={SEVERITY_OPTIONS}
          />
        </FilterBar>
      </div>
      {error ? (
        <ErrorState error={error} />
      ) : items === null || meta === null ? (
        <Skeleton rows={5} />
      ) : items.length === 0 ? (
        <EmptyState
          title={notImpl ? "Workflow endpoint not exposed" : "No workflow findings recorded"}
          description={
            notImpl
              ? "When the backend exposes a paginated workflow findings endpoint, this table will appear automatically."
              : "No workflow rule produced an observation in this scan. The workflow-analysis stage may not have run yet."
          }
        />
      ) : (
        <>
          <ResponsiveTable
            headers={["Rule", "Workflow", "Title", "Severity", "Confidence", "YAML path"]}
          >
            {items.map((wf) => (
              <tr
                key={wf.id}
                className="table-row cursor-pointer hover:bg-ink-50"
                onClick={() => setSelected(wf)}
              >
                <td className="table-cell font-mono text-xs text-ink-500">
                  {wf.rule_id}
                </td>
                <td className="table-cell font-mono text-xs text-ink-700">
                  {wf.workflow_path}
                </td>
                <td className="table-cell text-ink-800">
                  <p className="font-medium">{wf.title}</p>
                  <p className="line-clamp-2 text-xs text-ink-500">{wf.summary}</p>
                </td>
                <td className="table-cell">
                  <SeverityBadge severity={wf.severity} />
                </td>
                <td className="table-cell">
                  <ConfidenceBadge confidence={wf.confidence} />
                </td>
                <td className="table-cell text-ink-500">
                  {wf.yaml_path ?? "—"}
                </td>
              </tr>
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
        </>
      )}
      <DetailsDrawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.workflow_name}` : ""}
        ariaLabel="Workflow finding details"
      >
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-ink-500">{selected.rule_id}</span>
              <SeverityBadge severity={selected.severity} />
              <ConfidenceBadge confidence={selected.confidence} />
            </div>
            <section>
              <h3 className="label">Summary</h3>
              <p className="mt-1 text-sm text-ink-800">{selected.summary}</p>
            </section>
            <section>
              <h3 className="label">Location</h3>
              <div className="mt-1">
                <CodeLocation
                  path={selected.workflow_path}
                  startLine={selected.start_line}
                  endLine={selected.end_line}
                />
                {selected.yaml_path ? (
                  <p className="mt-1 text-xs text-ink-500">
                    YAML path: <span className="font-mono">{selected.yaml_path}</span>
                  </p>
                ) : null}
              </div>
            </section>
            <section>
              <h3 className="label">Permissions</h3>
              {selected.permissions.length > 0 ? (
                <ul className="mt-1 flex flex-wrap gap-1">
                  {selected.permissions.map((p) => (
                    <li
                      key={p}
                      className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[11px] text-ink-700"
                    >
                      {p}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-ink-500">No permissions recorded.</p>
              )}
            </section>
            <section>
              <h3 className="label">Triggers</h3>
              {selected.triggers.length > 0 ? (
                <ul className="mt-1 flex flex-wrap gap-1">
                  {selected.triggers.map((t) => (
                    <li
                      key={t}
                      className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[11px] text-ink-700"
                    >
                      {t}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-ink-500">No triggers recorded.</p>
              )}
            </section>
            <section>
              <h3 className="label">Unpinned actions</h3>
              {selected.unpinned_actions.length > 0 ? (
                <ul className="mt-1 list-disc pl-5 text-sm text-ink-800">
                  {selected.unpinned_actions.map((a) => (
                    <li key={a} className="font-mono text-xs">
                      {a}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-ink-500">
                  No unpinned actions recorded for this finding.
                </p>
              )}
            </section>
            {selected.remediation ? (
              <section>
                <h3 className="label">Remediation</h3>
                <p className="mt-1 text-sm text-ink-800">{selected.remediation}</p>
              </section>
            ) : null}
            {selected.limitations.length > 0 ? (
              <section>
                <h3 className="label">Limitations</h3>
                <ul className="mt-1 list-disc pl-5 text-xs text-ink-500">
                  {selected.limitations.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        ) : null}
      </DetailsDrawer>
    </>
  );
}
