import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 py-12 text-center">
      {icon ? <div className="text-ink-400">{icon}</div> : null}
      <h2 className="text-base font-semibold text-ink-700">{title}</h2>
      {description ? (
        <p className="max-w-md text-sm text-ink-500">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
