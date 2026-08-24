import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "@/api/api";
import type { FindingCategory, Scan, ScanStage } from "@/api/types";
import { CopyableIdentifier } from "@/components/CopyableIdentifier";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { PipelineFailureAlert, ScanTimeline } from "@/components/ScanTimeline";
import { ScanActions } from "@/components/ScanActions";
import {
  ScanStatusExplanation,
  StageProgressSummary,
} from "@/components/ScanWorkbenchHelpers";
import { useWorkbenchPolling } from "@/api/scanPolling";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { SummaryCard } from "@/components/SummaryCard";
import { Timestamp } from "@/components/Timestamp";
import { findingCategoryLabel, scanStatusLabel, scanTriggerLabel } from "@/utils/labels";
import { formatTimestamp } from "@/utils/time";

/**
 * v1.6 scan workbench.
 *
 * Renders the truthful scan identity, status, pipeline,
 * and exploration links for a single scan. The page is
 * the destination for the v1.5 guided intake page once
 * execution starts, and the source of truth for the
 * reviewer's "what is this scan doing right now?"
 * question.
 *
 * The page uses the existing ``usePolling`` hook via
 * the ``useWorkbenchPolling`` wrapper so the polling
 * interval, terminal-set, and abort-on-unmount
 * behaviour are consistent with the rest of the
 * application.
 */
export function ScanDetailsPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const valid = Number.isFinite(sid);
  // The scan object is polled. We keep a separate
  // ``stages`` cache that is refreshed once per poll.
  // The stages are not polled separately because the
  // orchestrator updates both rows in the same
  // transaction; the scan poll is the single source of
  // truth for "is the work done yet?".
  const {
    data: scan,
    error: pollError,
    polls,
  } = useWorkbenchPolling(sid);
  const [stages, setStages] = useState<ScanStage[] | null>(null);
  const [stagesError, setStagesError] = useState<unknown>(null);
  const error = valid ? pollError || stagesError : new Error("Invalid scan id.");

  useEffect(() => {
    if (!valid) return;
    if (!scan) return;
    const controller = new AbortController();
    api
      .listStages(scan.id)
      .then((st) => {
        if (controller.signal.aborted) return;
        setStages(st.items);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setStagesError(err);
      });
    return () => controller.abort();
    // We intentionally depend on ``scan?.id`` (not
    // the whole ``scan`` object) so the effect
    // re-runs only when the scan id changes or the
    // polling hook reports a new poll, not on every
    // scan field update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valid, scan?.id, polls]);

  if (error) {
    return (
      <>
        <PageHeader
          title="Scan"
          breadcrumbs={[{ label: "Scan", to: "/scans" }, { label: "Not found" }]}
        />
        <ErrorState error={error} title="Could not load scan" />
      </>
    );
  }
  if (!scan || stages === null) {
    return (
      <>
        <PageHeader title="Scan" />
        <Skeleton rows={6} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={`Scan #${scan.id}`}
        description={`Repository #${scan.repository_id} · ${scanTriggerLabel[scan.trigger_type]}`}
        breadcrumbs={[
          {
            label: "Repository",
            to: `/repositories/${scan.repository_id}`,
          },
          { label: `Scan #${scan.id}` },
        ]}
      />
      <div className="mb-4">
        <ScanStatusExplanation scan={scan} />
      </div>
      <section className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <SummaryCard
          label="Status"
          tone={statusTone(scan.status)}
          caption={scanStatusLabel[scan.status]}
        >
          <p className="flex items-center gap-2">
            <StatusBadge status={scan.status} />
            <span className="text-sm text-ink-600">{scanStatusLabel[scan.status]}</span>
          </p>
        </SummaryCard>
        <SummaryCard label="Identifiers" tone="muted">
          <CopyableIdentifier label="scan id" value={String(scan.id)} />
          <p className="mt-1 text-xs text-ink-500">
            Requested ref: <span className="font-mono">{scan.requested_ref ?? "—"}</span>
          </p>
          <p className="text-xs text-ink-500">
            Resolved commit: <span className="font-mono">{scan.resolved_commit_sha ?? "—"}</span>
          </p>
        </SummaryCard>
        <SummaryCard label="Timing" tone="muted">
          <p className="text-xs text-ink-700">
            Started: {formatTimestamp(scan.started_at)}
          </p>
          <p className="text-xs text-ink-700">
            Completed: {formatTimestamp(scan.completed_at)}
          </p>
          <Timestamp prefix="Created" value={scan.created_at} mode="both" />
        </SummaryCard>
      </section>
      {scan.failure_summary ? (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800" role="alert">
          <p className="font-semibold">
            Scan failure {scan.failure_code ? `(${scan.failure_code})` : ""}
          </p>
          <p className="mt-1">{scan.failure_summary}</p>
        </div>
      ) : null}
      <div className="mb-4">
        <ScanActions scan={scan} refreshKey={polls} />
      </div>
      <PipelineFailureAlert stages={stages} />
      <h2 className="mb-2 mt-4 text-sm font-semibold text-ink-700">Pipeline</h2>
      <DataCompletenessNotice
        title="Stages show intent and outcome"
        description="Every stage records its status, provider, records processed, and any failure summary. The pipeline below is the truth of what ran; nothing is marked complete without a stage record."
        tone="muted"
      />
      <div className="mt-2">
        <StageProgressSummary stages={stages} />
      </div>
      <div className="mt-4">
        <ScanTimeline stages={stages} />
      </div>
      <h2 className="mb-2 mt-8 text-sm font-semibold text-ink-700">Explore this scan</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {EXPLORE_CARDS.map((card) => (
          <Link
            key={card.to}
            to={card.to.replace(":scanId", String(scan.id))}
            className="card flex flex-col gap-1 hover:border-accent-300"
          >
            <p className="text-sm font-semibold text-ink-900">{card.title}</p>
            <p className="text-xs text-ink-500">{card.description}</p>
            {card.categories ? (
              <ul className="mt-1 flex flex-wrap gap-1">
                {card.categories.map((c) => (
                  <li
                    key={c}
                    className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-600"
                  >
                    {findingCategoryLabel[c]}
                  </li>
                ))}
              </ul>
            ) : null}
          </Link>
        ))}
      </div>
    </>
  );
}

const EXPLORE_CARDS: ReadonlyArray<{
  title: string;
  to: string;
  description: string;
  categories?: ReadonlyArray<FindingCategory>;
}> = [
  {
    title: "Findings",
    to: "/scans/:scanId/findings",
    description: "Every rule-evaluated observation in this scan.",
    categories: [
      "dependency",
      "vulnerability",
      "workflow",
      "repository_posture",
      "licence",
      "data_quality",
    ],
  },
  {
    title: "Vulnerabilities",
    to: "/scans/:scanId/vulnerabilities",
    description: "Component matches against advisories, with severity sources and fixed versions.",
  },
  {
    title: "Dependencies",
    to: "/scans/:scanId/dependencies",
    description: "Component inventory, ecosystem filter, direct / transitive view, vulnerable-only filter.",
  },
  {
    title: "Workflow findings",
    to: "/scans/:scanId/workflows",
    description: "GitHub Actions rule observations, with permissions, triggers, and unpinned actions.",
  },
  {
    title: "OpenSSF posture",
    to: "/scans/:scanId/openssf",
    description: "Imported OpenSSF Scorecard results, displayed as externally sourced observations.",
  },
  {
    title: "Licence inventory",
    to: "/scans/:scanId/licences",
    description: "Detected licences per component, with review status and provider attribution.",
  },
  {
    title: "Provider status",
    to: "/scans/:scanId/providers",
    description: "Provider availability, partial results, rate limits, and redacted failure summaries.",
  },
  {
    title: "Exports",
    to: "/scans/:scanId/exports",
    description: "CycloneDX SBOM, findings JSON, findings CSV, and SARIF exports.",
  },
];

function statusTone(status: Scan["status"]) {
  if (status === "completed") return "ok" as const;
  if (status === "partial") return "warn" as const;
  if (status === "failed" || status === "cancelled") return "danger" as const;
  if (status === "running") return "info" as const;
  return "muted" as const;
}
