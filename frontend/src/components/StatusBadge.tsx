import type { ReactNode } from "react";

/**
 * Status badge for scan / stage states.
 *
 * The status word is always rendered as text - color is decoration,
 * not signal. The contract is "screen reader can parse the status
 * without relying on color".
 */

type Tone = "neutral" | "info" | "ok" | "warn" | "danger" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-ink-100 dark:bg-surface-dark-raised text-ink-700 dark:text-surface-dark-text border-ink-200 dark:border-surface-dark-border",
  info: "bg-accent-50 dark:bg-surface-dark-raised text-accent-700 dark:text-accent-dark-300 border-accent-200",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warn: "bg-amber-50 text-amber-700 border-amber-200",
  danger: "bg-rose-50 text-rose-700 border-rose-200",
  muted: "bg-ink-50 dark:bg-surface-dark-app text-ink-500 dark:text-surface-dark-text-muted border-ink-200 dark:border-surface-dark-border",
};

function toneFor(status: string): Tone {
  switch (status) {
    case "queued":
    case "pending":
      return "muted";
    case "running":
      return "info";
    case "completed":
    case "available":
    case "resolved":
      return "ok";
    case "partial":
    case "rate_limited":
    case "accepted":
      return "warn";
    case "failed":
    case "cancelled":
    case "unavailable":
      return "danger";
    case "skipped":
    case "not_requested":
    case "cached":
    case "unknown":
      return "muted";
    default:
      return "neutral";
  }
}

export function StatusBadge({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  const tone = toneFor(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
      aria-label={`Status: ${status}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {children ?? status}
    </span>
  );
}
