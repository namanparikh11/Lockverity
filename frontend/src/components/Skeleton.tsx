import type { ReactNode } from "react";

/**
 * The skeleton placeholder for loading content. Renders an
 * accessible progress bar (`aria-busy`) with subtle placeholders.
 * No animation, no decorative motion - respects reduced-motion
 * automatically.
 */
export function Skeleton({
  rows = 3,
  width = "w-full",
  className = "",
}: {
  rows?: number;
  width?: string;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col gap-2 ${className}`}
      role="progressbar"
      aria-busy="true"
      aria-label="Loading"
    >
      {Array.from({ length: rows }).map((_, idx) => (
        <div
          key={idx}
          className={`h-3 ${width} rounded bg-ink-100 dark:bg-surface-dark-raised motion-safe:animate-pulse`}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div
      className="flex flex-col gap-2"
      role="progressbar"
      aria-busy="true"
      aria-label="Loading"
    >
      {Array.from({ length: rows }).map((_, idx) => (
        <div
          key={idx}
          className="h-3 w-full rounded bg-ink-100 dark:bg-surface-dark-raised motion-safe:animate-pulse"
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

export function SkeletonTable({
  columns = 4,
  rows = 5,
}: {
  columns?: number;
  rows?: number;
}): ReactNode {
  return (
    <div
      className="card overflow-hidden"
      role="progressbar"
      aria-busy="true"
      aria-label="Loading table"
    >
      <div className="grid gap-px bg-ink-100 dark:bg-surface-dark-raised" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {Array.from({ length: columns * rows }).map((_, idx) => (
          <div
            key={idx}
            className="h-4 bg-white dark:bg-surface-dark-surface"
            aria-hidden="true"
          />
        ))}
      </div>
    </div>
  );
}
