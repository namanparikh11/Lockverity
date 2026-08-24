import { Info } from "lucide-react";

import type { ReactNode } from "react";

/**
 * Banner that surfaces the difference between "no findings" and
 * "data not available". Both look like an empty list to a casual
 * observer; this component is the only place the difference is
 * communicated to the user.
 */
export function DataCompletenessNotice({
  title,
  description,
  tone = "info",
  children,
  detail,
}: {
  title: string;
  description?: string;
  tone?: "info" | "warn" | "muted" | "danger";
  children?: ReactNode;
  detail?: string | null;
}) {
  const toneClass =
    tone === "warn"
      ? "border-amber-200 bg-amber-50 text-amber-800"
      : tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800"
      : tone === "muted"
      ? "border-ink-200 bg-ink-50 text-ink-700"
      : "border-accent-200 bg-accent-50 text-accent-800";
  return (
    <div
      className={`flex items-start gap-3 rounded-md border p-3 text-sm ${toneClass}`}
      role="status"
    >
      <Info aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
      <div>
        <p className="font-semibold">{title}</p>
        {description ? <p className="mt-1">{description}</p> : null}
        {detail ? <p className="mt-1 text-xs">{detail}</p> : null}
        {children}
      </div>
    </div>
  );
}
