import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/**
 * Right-side details drawer. Used for showing the details of a
 * finding, vulnerability, dependency, or workflow observation
 * without leaving the list. The drawer traps focus, supports
 * `Escape` to close, and is portal-mounted so it escapes any
 * stacking context.
 */
export function DetailsDrawer({
  open,
  title,
  onClose,
  children,
  ariaLabel,
  widthClass = "max-w-xl",
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  ariaLabel?: string;
  widthClass?: string;
}) {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handle = window.setTimeout(() => {
      closeRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(handle);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-40 flex justify-end bg-ink-900/30 motion-safe:animate-in"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel ?? title}
        className={`flex h-full w-full ${widthClass} flex-col border-l border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface shadow-xl`}
      >
        <header className="flex items-center justify-between border-b border-ink-200 dark:border-surface-dark-border px-4 py-3">
          <h2 className="text-base font-semibold text-ink-900 dark:text-surface-dark-text">{title}</h2>
          <button
            ref={closeRef}
            type="button"
            className="rounded p-1 text-ink-500 dark:text-surface-dark-text-muted hover:bg-ink-100 dark:bg-surface-dark-raised"
            onClick={onClose}
            aria-label="Close details"
          >
            <X aria-hidden="true" className="h-5 w-5" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-4">{children}</div>
      </aside>
    </div>,
    document.body
  );
}
