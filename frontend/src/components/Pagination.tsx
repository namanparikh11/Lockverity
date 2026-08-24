import { ChevronLeft, ChevronRight } from "lucide-react";

import type { PageMeta } from "@/api/types";

export function Pagination({
  meta,
  onPageChange,
}: {
  meta: PageMeta;
  onPageChange: (page: number) => void;
}) {
  if (meta.total === 0) {
    return null;
  }
  const canPrev = meta.page > 1;
  const canNext = meta.page < meta.total_pages;
  return (
    <nav
      className="flex items-center justify-between gap-4 border-t border-ink-200 pt-3 text-sm text-ink-600"
      aria-label="Pagination"
    >
      <span>
        Page {meta.page} of {meta.total_pages} ({meta.total} items)
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          disabled={!canPrev}
          onClick={() => canPrev && onPageChange(meta.page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
          Previous
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!canNext}
          onClick={() => canNext && onPageChange(meta.page + 1)}
          aria-label="Next page"
        >
          Next
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
    </nav>
  );
}
