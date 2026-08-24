import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock,
  PauseCircle,
  PlayCircle,
  XCircle,
} from "lucide-react";

import type { ScanStage, StageStatus } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { Timestamp } from "@/components/Timestamp";
import { stageTypeLabel as labelStage } from "@/components/ScanTimeline.helpers";

function StageIcon({ status }: { status: StageStatus }) {
  const cls = "h-5 w-5 flex-shrink-0";
  switch (status) {
    case "pending":
      return <Circle aria-hidden="true" className={`${cls} text-ink-400`} />;
    case "running":
      return <PlayCircle aria-hidden="true" className={`${cls} text-accent-600`} />;
    case "completed":
      return <CheckCircle2 aria-hidden="true" className={`${cls} text-emerald-600`} />;
    case "partial":
      return <AlertTriangle aria-hidden="true" className={`${cls} text-amber-600`} />;
    case "failed":
      return <XCircle aria-hidden="true" className={`${cls} text-rose-600`} />;
    case "skipped":
      return <PauseCircle aria-hidden="true" className={`${cls} text-ink-400`} />;
    default:
      return <Circle aria-hidden="true" className={`${cls} text-ink-400`} />;
  }
}

/**
 * Render the full ordered scan pipeline. Each row carries the
 * stage status, provider status (when present), records
 * processed, start and completion times, and a redacted failure
 * summary. The component never claims a stage ran if it only
 * saw it as "pending"; the status is always shown as text.
 */
export function ScanTimeline({ stages }: { stages: ScanStage[] }) {
  if (stages.length === 0) {
    return (
      <p className="text-sm text-ink-500">
        No stages recorded for this scan.
      </p>
    );
  }
  return (
    <ol
      className="relative space-y-3 border-l border-ink-200 pl-5"
      aria-label="Scan stage timeline"
    >
      {stages.map((stage) => (
        <li
          key={stage.id}
          className="relative"
          data-stage-status={stage.status}
        >
          <span
            className="absolute -left-[1.42rem] top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-surface"
            aria-hidden="true"
          >
            <StageIcon status={stage.status} />
          </span>
          <div className="card flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-ink-900">
                  {labelStage(stage.stage_type)}
                </h3>
                <StatusBadge status={stage.status} />
                {stage.provider ? (
                  <span className="text-xs text-ink-500">
                    provider: <span className="font-mono">{stage.provider}</span>
                  </span>
                ) : null}
                {stage.provider_status ? (
                  <StatusBadge status={stage.provider_status} />
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-ink-500">
                <span>
                  <span className="font-medium text-ink-700">
                    {stage.records_processed}
                  </span>{" "}
                  records
                </span>
                <Timestamp prefix="started" value={stage.started_at} />
                <Timestamp prefix="ended" value={stage.completed_at} />
              </div>
            </div>
            {stage.failure_summary ? (
              <StageMessage
                severity={stage.message_severity}
                summary={stage.failure_summary}
              />
            ) : null}
            {stage.failure_code ? (
              <p className="text-xs text-rose-700">
                <span className="font-mono">{stage.failure_code}</span>
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/**
 * Compact horizontal summary of the pipeline. Used on the
 * dashboard and in headers where space is tight. The summary
 * always shows a count by status; it never shows a single
 * coloured dot alone.
 */
export function PipelineSummary({ stages }: { stages: ScanStage[] }) {
  const counts = stages.reduce<Record<StageStatus, number>>(
    (acc, stage) => {
      acc[stage.status] = (acc[stage.status] ?? 0) + 1;
      return acc;
    },
    {
      pending: 0,
      running: 0,
      completed: 0,
      partial: 0,
      failed: 0,
      skipped: 0,
    }
  );
  return (
    <div
      className="flex flex-wrap items-center gap-2 text-xs text-ink-600"
      role="group"
      aria-label="Pipeline status counts"
    >
      {(Object.entries(counts) as [StageStatus, number][])
        .filter(([, count]) => count > 0)
        .map(([status, count]) => (
          <span
            key={status}
            className="inline-flex items-center gap-1 rounded-full border border-ink-200 bg-surface px-2 py-0.5"
          >
            <StatusBadge status={status} />
            <span className="font-medium text-ink-700">{count}</span>
          </span>
        ))}
      {stages.length === 0 ? (
        <span className="inline-flex items-center gap-1 text-ink-400">
          <Clock aria-hidden="true" className="h-3.5 w-3.5" />
          no stages
        </span>
      ) : null}
    </div>
  );
}

/**
 * Visual "alert" wrapper for pipeline failures. Used on the
 * scan-detail page to call out a stage that needs attention.
 */
export function PipelineFailureAlert({ stages }: { stages: ScanStage[] }) {
  const failed = stages.filter((s) => s.status === "failed");
  if (failed.length === 0) return null;
  return (
    <div
      className="flex items-start gap-3 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"
      role="alert"
    >
      <AlertOctagon aria-hidden="true" className="mt-0.5 h-5 w-5 text-rose-600" />
      <div>
        <p className="font-semibold">
          {failed.length} {failed.length === 1 ? "stage failed" : "stages failed"}
        </p>
        <ul className="mt-1 list-disc pl-5">
          {failed.map((s) => (
            <li key={s.id}>
              <span className="font-medium">{labelStage(s.stage_type)}</span>:{" "}
              {s.failure_summary ?? s.failure_code ?? "no failure summary recorded"}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * v2.0.6: render a stage's residual ``failure_summary`` with
 * the appropriate severity styling. The decision is sourced
 * from the backend's ``message_severity`` field; the
 * frontend never invents a severity from the message text.
 *
 * - ``"error"`` - red ``Failure:`` prefix, ``role="alert"``.
 * - ``"warning"`` - amber ``Partial:`` prefix, ``role="status"``.
 * - ``"info"`` - neutral, no failure prefix, ``role="status"``.
 * - ``"none"`` - no message block.
 */
export function StageMessage({
  severity,
  summary,
}: {
  severity: ScanStage["message_severity"];
  summary: string;
}) {
  if (severity === "none") {
    return null;
  }
  if (severity === "error") {
    return (
      <p
        className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800"
        role="alert"
      >
        <span className="font-semibold">Failure: </span>
        {summary}
      </p>
    );
  }
  if (severity === "warning") {
    return (
      <p
        className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        role="status"
      >
        <span className="font-semibold">Partial output: </span>
        {summary}
      </p>
    );
  }
  // info: neutral styling, no failure prefix
  return (
    <p
      className="rounded-md border border-ink-200 bg-surface px-3 py-2 text-xs text-ink-700"
      role="status"
    >
      {summary}
    </p>
  );
}
