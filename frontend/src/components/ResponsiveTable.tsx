import type { ReactNode } from "react";

/**
 * A horizontally scrollable table that keeps column headers visible
 * on small screens. Use semantic <table> markup; the wrapper just
 * adds overflow behaviour and a focusable container.
 */
export function ResponsiveTable({
  caption,
  headers,
  children,
  empty,
}: {
  caption?: string;
  headers: ReactNode[];
  children: ReactNode;
  empty?: ReactNode;
}) {
  return (
    <div className="card overflow-x-auto">
      <table className="min-w-full divide-y divide-ink-100">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead className="bg-ink-50 dark:bg-surface-dark-app">
          <tr>
            {headers.map((header, idx) => (
              <th key={idx} scope="col" className="table-head">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {empty ? (
            <tr>
              <td
                colSpan={headers.length}
                className="table-cell text-center text-ink-500 dark:text-surface-dark-text-muted"
              >
                {empty}
              </td>
            </tr>
          ) : (
            children
          )}
        </tbody>
      </table>
    </div>
  );
}
