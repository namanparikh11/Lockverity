import type { ReactNode } from "react";

/**
 * A summary card for the dashboard. Shows a label, headline value
 * or arbitrary children, an optional tone, and an optional
 * supporting caption. The label is always rendered as visible
 * text - never a colour cue alone.
 */
export function SummaryCard({
  label,
  tone = "neutral",
  children,
  caption,
  actions,
  className = "",
}: {
  label: string;
  tone?: "neutral" | "info" | "ok" | "warn" | "danger" | "muted";
  children: ReactNode;
  caption?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  const toneClass =
    tone === "info"
      ? "border-accent-200"
      : tone === "ok"
        ? "border-emerald-200"
        : tone === "warn"
          ? "border-amber-200"
          : tone === "danger"
            ? "border-rose-200"
            : tone === "muted"
              ? "border-ink-200"
              : "border-ink-200";
  return (
    <div className={`card flex flex-col gap-2 ${toneClass} ${className}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="label">{label}</p>
        {actions ? <div className="flex flex-wrap gap-1">{actions}</div> : null}
      </div>
      <div className="text-ink-900">{children}</div>
      {caption ? (
        <p className="text-xs text-ink-500">{caption}</p>
      ) : null}
    </div>
  );
}
