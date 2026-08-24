import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
  breadcrumbs,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumbs?: { label: string; to?: string }[];
}) {
  return (
    <header className="mb-6 border-b border-ink-200 pb-4">
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <nav
          aria-label="Breadcrumb"
          className="mb-2 text-xs text-ink-500"
        >
          <ol className="flex flex-wrap items-center gap-1">
            {breadcrumbs.map((crumb, index) => (
              <li
                key={`${crumb.label}-${index}`}
                className="flex items-center gap-1"
              >
                {crumb.to ? (
                  <a href={crumb.to} className="hover:text-ink-700">
                    {crumb.label}
                  </a>
                ) : (
                  <span className="text-ink-700">{crumb.label}</span>
                )}
                {index < breadcrumbs.length - 1 ? (
                  <span aria-hidden="true">/</span>
                ) : null}
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-ink-900">{title}</h1>
          {description ? (
            <p className="mt-1 text-sm text-ink-600">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
