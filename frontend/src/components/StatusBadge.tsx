import type { ReactNode } from "react";

import { BADGE_BASE, TONE_CLASSES, type Tone } from "./tone";

/**
 * Status badge for scan / stage states.
 *
 * The status word is always rendered as text - color is decoration,
 * not signal. The contract is "screen reader can parse the status
 * without relying on color".
 *
 * The tint / foreground / border vocabulary lives in ``./tone`` so
 * every chip in the application shares one theme-aware definition.
 */

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
      className={`${BADGE_BASE} gap-1.5 ${TONE_CLASSES[tone]}`}
      aria-label={`Status: ${status}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {children ?? status}
    </span>
  );
}
