import { formatRelative, formatTimestamp } from "@/utils/time";

/**
 * A small inline timestamp that shows a relative phrase and a
 * full timestamp in the title attribute. Falls back to "—" for
 * nulls. A `prefix` is shown in a softer colour for things like
 * "started 3m ago".
 */
export function Timestamp({
  value,
  prefix,
  fallback = "—",
  mode = "relative",
}: {
  value: string | null | undefined;
  prefix?: string;
  fallback?: string;
  mode?: "relative" | "absolute" | "both";
}) {
  if (!value) {
    return (
      <span className="text-xs text-ink-400">
        {prefix ? <span className="text-ink-500">{prefix} </span> : null}
        {fallback}
      </span>
    );
  }
  const absolute = formatTimestamp(value);
  const relative = formatRelative(value);
  const label = mode === "absolute" ? absolute : mode === "both" ? `${relative} (${absolute})` : relative;
  return (
    <span className="text-xs text-ink-700" title={absolute}>
      {prefix ? <span className="text-ink-500">{prefix} </span> : null}
      <time dateTime={value}>{label}</time>
    </span>
  );
}
