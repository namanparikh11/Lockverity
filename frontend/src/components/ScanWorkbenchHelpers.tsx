import type { Scan } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";

/**
 * v1.6 stage summary block.
 *
 * Renders the bounded summary that the v1.6 workbench
 * promises: "N of M stages reached a terminal state"
 * with the actual terminal-set (completed / partial /
 * failed / skipped). Never equates "terminal stage"
 * with "successful stage".
 */
export function StageProgressSummary({ stages }: { stages: ReadonlyArray<{ status: string }> }) {
  const total = stages.length;
  const terminal = stages.filter((s) =>
    ["completed", "partial", "failed", "skipped"].includes(s.status)
  ).length;
  if (total === 0) {
    return (
      <p className="text-xs text-ink-500" data-testid="stage-progress-empty">
        No stages recorded.
      </p>
    );
  }
  return (
    <p className="text-xs text-ink-700" data-testid="stage-progress-summary">
      {terminal} of {total} stages reached a terminal state
      (completed, partial, failed, or skipped). Terminal does not imply
      successful; the per-stage rows below carry the truthful state.
    </p>
  );
}

/**
 * v1.6 status explanation block.
 *
 * Renders a single bounded paragraph that maps the
 * scan's persisted status to the truth the page can
 * safely show. Never claims security, certification,
 * or compliance; never invents percentage progress.
 */
export function ScanStatusExplanation({ scan }: { scan: Scan }) {
  const message = explanationFor(scan);
  const tone = toneFor(scan.status);
  const toneClass = {
    info: "border-accent-200 bg-accent-50 text-accent-900",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-900",
    ok: "border-emerald-200 bg-emerald-50 text-emerald-900",
    muted: "border-ink-200 bg-ink-50 text-ink-700",
  }[tone];
  return (
    <div
      className={`space-y-1 rounded-md border p-3 text-sm ${toneClass}`}
      role="status"
      aria-live="polite"
      data-testid="scan-status-explanation"
    >
      <p className="font-semibold">
        Status: <StatusBadge status={scan.status} /> {labelFor(scan.status)}
      </p>
      <p className="text-xs">{message}</p>
    </div>
  );
}

function explanationFor(scan: Scan): string {
  switch (scan.status) {
    case "queued":
      return "Waiting for execution. The repository and the queued scan are persisted. No result is claimed yet.";
    case "running":
      return "Analysis in progress. Evidence may be incomplete; do not draw conclusions from partial stage output.";
    case "completed":
      return "Analysis pipeline completed. This is an evidence export, not a security verdict, certification, or compliance pass-or-fail.";
    case "partial":
      return scan.failure_summary
        ? `Analysis completed with missing or degraded evidence. Bounded reason: ${scan.failure_summary}`
        : "Analysis completed with missing or degraded evidence. Some stages did not record their expected records.";
    case "failed":
      return scan.failure_summary
        ? `Scan did not complete. Bounded failure: ${scan.failure_code ?? "unspecified"} — ${scan.failure_summary}`
        : "Scan did not complete. The local worker reported a failure; check the scan and stage rows for the bounded reason.";
    case "cancelled":
      return scan.failure_summary
        ? `Scan cancelled. Bounded reason: ${scan.failure_summary}`
        : "Scan cancelled by operator request. Persisted evidence remains available.";
    default:
      return "";
  }
}

function toneFor(status: Scan["status"]): "info" | "warn" | "danger" | "ok" | "muted" {
  if (status === "completed") return "ok";
  if (status === "partial") return "warn";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "running") return "info";
  return "muted";
}

function labelFor(status: Scan["status"]): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "completed":
      return "Completed";
    case "partial":
      return "Partial";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return status;
  }
}
