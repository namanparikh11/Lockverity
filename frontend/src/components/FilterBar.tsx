import { Search, X } from "lucide-react";
import type { ChangeEvent, ReactNode } from "react";

/**
 * Lockverity filter bar.
 *
 * Renders a search input, a row of optional child filters, and a
 * "clear" affordance. The component is purely presentational; the
 * owning page owns state. Status is announced via `aria-live` so
 * screen-reader users know when filters change.
 *
 * Two layouts are supported:
 *
 * - ``layout="inline"`` (default, backward-compatible): the search
 *   input and the child filters share a single wrapping row. The
 *   count and clear button sit on the right of that row. This is
 *   the layout the v0.1–v0.8 pages already use.
 * - ``layout="card"``: a card-style header with an optional
 *   ``title``, the count, and the clear button on a dedicated
 *   header row; a single search input below; the child filters in
 *   a responsive grid (1 / 2 / 3 / 4 columns at sm / lg / xl).
 *   This is the v0.9 evidence-filter layout.
 */
export function FilterBar({
  search,
  onSearchChange,
  searchPlaceholder = "Search…",
  children,
  onClear,
  resultCount,
  resultLabel = "results",
  ariaLabel = "Filters",
  title,
  layout = "inline",
}: {
  search: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;
  children?: ReactNode;
  onClear?: () => void;
  resultCount?: number;
  resultLabel?: string;
  ariaLabel?: string;
  title?: string;
  layout?: "inline" | "card";
}) {
  if (layout === "card") {
    return (
      <div
        className="rounded-md border border-ink-200 bg-surface p-4 shadow-sm"
        role="region"
        aria-label={ariaLabel}
        data-testid="filterbar-card"
      >
        <div className="flex flex-col gap-1 border-b border-ink-100 pb-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-baseline gap-2">
            {title ? (
              <h2
                className="text-sm font-semibold text-ink-800"
                data-testid="filterbar-title"
              >
                {title}
              </h2>
            ) : null}
            {typeof resultCount === "number" ? (
              <span
                aria-live="polite"
                data-testid="filterbar-result-count"
                className="text-xs text-ink-500"
              >
                <span className="font-mono font-semibold text-ink-800">
                  {resultCount}
                </span>{" "}
                {resultLabel}
              </span>
            ) : null}
          </div>
          {onClear ? (
            <button
              type="button"
              onClick={onClear}
              className="btn-secondary self-start sm:self-auto"
              aria-label="Clear filters"
              data-testid="filterbar-clear"
            >
              <X aria-hidden="true" className="h-4 w-4" />
              Clear
            </button>
          ) : null}
        </div>
        <div className="mt-3">
          <div className="relative w-full min-w-[200px] sm:max-w-sm">
            <label htmlFor="filterbar-search" className="sr-only">
              Search
            </label>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
            />
            <input
              id="filterbar-search"
              type="search"
              className="input w-full pl-8"
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                onSearchChange(e.target.value)
              }
              autoComplete="off"
            />
          </div>
        </div>
        <div
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="filterbar-grid"
        >
          {children}
        </div>
      </div>
    );
  }
  return (
    <div
      className="flex flex-col gap-3 rounded-md border border-ink-200 bg-surface p-3 sm:flex-row sm:items-center sm:justify-between"
      role="region"
      aria-label={ariaLabel}
    >
      <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative min-w-[200px] flex-1 sm:min-w-[240px] sm:max-w-xs">
          <label htmlFor="filterbar-search" className="sr-only">
            Search
          </label>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
          />
          <input
            id="filterbar-search"
            type="search"
            className="input pl-8"
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onSearchChange(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">{children}</div>
      </div>
      <div className="flex items-center gap-3 text-sm text-ink-500">
        {typeof resultCount === "number" ? (
          <span aria-live="polite">
            {resultCount} {resultLabel}
          </span>
        ) : null}
        {onClear ? (
          <button
            type="button"
            onClick={onClear}
            className="btn-secondary"
            aria-label="Clear filters"
          >
            <X aria-hidden="true" className="h-4 w-4" />
            Clear
          </button>
        ) : null}
      </div>
    </div>
  );
}

export interface SelectFilterOption {
  value: string;
  label: string;
}

export function SelectFilter({
  id,
  label,
  value,
  onChange,
  options,
  className = "",
  stacked = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectFilterOption[];
  className?: string;
  /**
   * When ``true``, the label is rendered above the select
   * (block layout) and the select takes the full width of
   * its container. This is the layout the v0.9
   * evidence-filter grid uses. The default ``false``
   * preserves the v0.1–v0.8 inline layout (label beside
   * the select).
   */
  stacked?: boolean;
}) {
  const containerClass = stacked
    ? `flex flex-col gap-1 ${className}`
    : `flex items-center gap-2 ${className}`;
  // The hand-rolled select chrome is replaced by the shared
  // ``.select`` primitive. Filter controls were the last form
  // surface still declaring their own background, which is why the
  // Repositories and Diagnostics filter rows stayed white in Dark
  // mode while the page around them was dark.
  const selectClass = stacked ? "select w-full" : "select";
  return (
    <div className={containerClass} data-testid={`select-filter-${id}`}>
      <label
        htmlFor={id}
        className="text-xs font-medium text-ink-500"
      >
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={selectClass}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
