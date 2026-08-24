import { CornerDownRight, FileWarning, GitBranch } from "lucide-react";

import type { Component, DependencyPath } from "@/api/types";

/**
 * Render a dependency path as a textual chain. The chain shows
 * each component in resolution order, with the deepest
 * dependency indented. A truncation badge is rendered when the
 * path was cut off by the backend. No graph is drawn - this is
 * accessible, copy-pasteable, and prints well.
 */
export function DependencyPathView({
  path,
  fallbackToSingle = false,
}: {
  path: DependencyPath | null | undefined;
  fallbackToSingle?: boolean;
}) {
  if (!path || (path.components.length === 0 && path.edges.length === 0)) {
    if (fallbackToSingle) {
      return (
        <span className="text-xs text-ink-500">No dependency path recorded.</span>
      );
    }
    return null;
  }
  if (path.components.length === 0) {
    return (
      <span className="text-xs text-ink-500">Path not recorded.</span>
    );
  }
  return (
    <ol
      className="flex flex-col gap-1 text-xs"
      aria-label="Dependency path"
    >
      {path.components.map((component, idx) => (
        <li
          key={`${component.id}-${idx}`}
          className="flex items-start gap-2"
          style={{ paddingLeft: `${idx * 0.75}rem` }}
        >
          {idx > 0 ? (
            <CornerDownRight
              aria-hidden="true"
              className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-ink-400"
            />
          ) : (
            <GitBranch
              aria-hidden="true"
              className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-ink-400"
            />
          )}
          <span className="flex flex-wrap items-center gap-1">
            <span className="font-mono text-ink-700">
              {component.ecosystem ? `${component.ecosystem}:` : ""}
              {component.package_name}
            </span>
            <span className="text-ink-500">
              {component.version ? `@${component.version}` : "(version unknown)"}
            </span>
            {component.direct ? (
              <span className="rounded bg-accent-50 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-700">
                direct
              </span>
            ) : null}
          </span>
        </li>
      ))}
      {path.truncated ? (
        <li className="mt-1 flex items-center gap-1 text-amber-700">
          <FileWarning aria-hidden="true" className="h-3.5 w-3.5" />
          path was truncated by the analyzer
        </li>
      ) : null}
    </ol>
  );
}

/**
 * Render a single-component identity, useful for table cells.
 */
export function ComponentIdentity({ component }: { component: Component }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="font-mono text-ink-800">
        {component.ecosystem ? `${component.ecosystem}:` : ""}
        {component.package_name}
      </span>
      <span className="text-ink-500">
        {component.version ? `@${component.version}` : "(version unknown)"}
      </span>
    </span>
  );
}
