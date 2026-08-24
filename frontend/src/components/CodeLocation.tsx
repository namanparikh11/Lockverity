import { Code } from "lucide-react";

/**
 * Code location indicator. Renders a file path, an optional
 * line range, and a small inline "view source" affordance that
 * opens the canonical URL in a new tab. Never renders the line
 * range as just a number; the format "L42–L57" or "L42" is
 * always present and announced.
 */
export function CodeLocation({
  path,
  startLine,
  endLine,
  canonicalUrl,
}: {
  path: string | null;
  startLine: number | null;
  endLine: number | null;
  canonicalUrl?: string | null;
}) {
  if (!path) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-ink-400">
        <Code aria-hidden="true" className="h-3.5 w-3.5" />
        no file location
      </span>
    );
  }
  const rangeLabel =
    startLine != null
      ? endLine != null && endLine !== startLine
        ? `L${startLine}–L${endLine}`
        : `L${startLine}`
      : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-700">
      <Code aria-hidden="true" className="h-3.5 w-3.5 text-ink-400" />
      <code className="break-all font-mono">{path}</code>
      {rangeLabel ? (
        <span className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[11px] text-ink-600">
          {rangeLabel}
        </span>
      ) : null}
      {canonicalUrl ? (
        <a
          href={canonicalUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="text-accent-700 hover:text-accent-800"
        >
          view source
        </a>
      ) : null}
    </span>
  );
}
