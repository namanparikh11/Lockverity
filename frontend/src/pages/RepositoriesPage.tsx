import { GitBranch, GitCompare, ExternalLink, Plus, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/api";
import type {
  PageMeta,
  RepositoryProvider,
  RepositorySourceType,
  RepositoryWithSummary,
  ScanStatus,
} from "@/api/types";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { Timestamp } from "@/components/Timestamp";
import { repositoryProviderLabel, repositorySourceLabel, repositoryVisibilityLabel, scanStatusLabel } from "@/utils/labels";

const PROVIDER_OPTIONS = [
  { value: "all", label: "All providers" },
  { value: "github", label: repositoryProviderLabel.github },
  { value: "local_upload", label: repositoryProviderLabel.local_upload },
];

const SOURCE_OPTIONS = [
  { value: "all", label: "All sources" },
  { value: "github", label: repositorySourceLabel.github },
  { value: "uploaded_archive", label: repositorySourceLabel.uploaded_archive },
];

const ARCHIVE_OPTIONS = [
  { value: "all", label: "All" },
  { value: "active", label: "Active only" },
  { value: "archived", label: "Archived only" },
];

/**
 * v2.0.5 repository list. Each row surfaces the human-readable
 * primary label (the original filename for uploaded archives,
 * ``owner/repository`` for GitHub), a secondary technical
 * identifier, the latest scan with status, the total scan
 * count, the last-scanned timestamp, and three actions
 * ("Open latest scan", "View history", "Compare"). The
 * "Compare" action is hidden when fewer than two eligible
 * scans exist; "Open latest scan" is hidden when no scan
 * has ever run. Search accepts repository names, original
 * filenames, canonical URLs, canonical upload identifiers,
 * and exact scan IDs (with or without a leading ``#``).
 */
export function RepositoriesPage() {
  const [repos, setRepos] = useState<RepositoryWithSummary[] | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState<"all" | RepositoryProvider>("all");
  const [source, setSource] = useState<"all" | RepositorySourceType>("all");
  const [archive, setArchive] = useState<"all" | "archived" | "active">("all");
  const [error, setError] = useState<unknown>(null);

  // Reset to page 1 when filters change.
  useEffect(() => {
    setPage(1);
  }, [search, provider, source, archive]);

  useEffect(() => {
    const controller = new AbortController();
    setRepos(null);
    setError(null);
    api
      .listRepositories({
        page,
        page_size: 25,
        search: search || undefined,
        provider: provider === "all" ? undefined : provider,
        source_type: source === "all" ? undefined : source,
        archived: archive,
      })
      .then((r) => {
        if (controller.signal.aborted) return;
        setRepos(r.items as RepositoryWithSummary[]);
        setMeta(r.pagination);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err);
      });
    return () => controller.abort();
  }, [page, search, provider, source, archive]);

  const filtersActive = Boolean(
    search || provider !== "all" || source !== "all" || archive !== "all"
  );

  function clearAll() {
    setSearch("");
    setProvider("all");
    setSource("all");
    setArchive("all");
  }

  return (
    <>
      <PageHeader
        title="Repositories"
        description="Public GitHub repositories and uploaded source archives registered for analysis. Each row shows the source, the latest scan, the total scan count, and the per-repository actions."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link to="/repositories/upload" className="btn-secondary">
              <Upload aria-hidden="true" className="h-4 w-4" />
              Upload archive
            </Link>
            <Link to="/repositories/new" className="btn-primary">
              <Plus aria-hidden="true" className="h-4 w-4" />
              Add repository
            </Link>
          </div>
        }
      />
      <div className="mb-4">
        <FilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search by repository, filename, canonical URL, or scan ID (#15)"
          onClear={filtersActive ? clearAll : undefined}
          resultCount={meta?.total}
          resultLabel="repositories"
        >
          <SelectFilter
            id="provider-filter"
            label="Provider"
            value={provider}
            onChange={(v) => setProvider(v as "all" | RepositoryProvider)}
            options={PROVIDER_OPTIONS}
          />
          <SelectFilter
            id="source-filter"
            label="Source"
            value={source}
            onChange={(v) => setSource(v as "all" | RepositorySourceType)}
            options={SOURCE_OPTIONS}
          />
          <SelectFilter
            id="archive-filter"
            label="Archive state"
            value={archive}
            onChange={(v) => setArchive(v as "all" | "archived" | "active")}
            options={ARCHIVE_OPTIONS}
          />
        </FilterBar>
      </div>
      {error ? (
        <ErrorState error={error} title="Could not load repositories" />
      ) : !repos || !meta ? (
        <Skeleton rows={6} />
      ) : repos.length === 0 ? (
        filtersActive ? (
          <EmptyState
            title="No repositories match your filters"
            description="Clear the filters to see every registered repository, or refine the search."
            action={
              <button type="button" className="btn-secondary" onClick={clearAll}>
                Clear filters
              </button>
            }
          />
        ) : (
          <EmptyState
            title="No repositories yet"
            description="Add a public GitHub repository or upload a source archive to register it for analysis. GitHub repositories are retrieved for local analysis; repository code and uploaded contents are treated as data and never executed."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link to="/repositories/new" className="btn-primary">
                  <Plus aria-hidden="true" className="h-4 w-4" />
                  Add your first repository
                </Link>
                <Link to="/repositories/upload" className="btn-secondary">
                  <Upload aria-hidden="true" className="h-4 w-4" />
                  Upload archive
                </Link>
              </div>
            }
          />
        )
      ) : (
        <>
          <ResponsiveTable
            headers={["Repository", "Source", "Latest scan", "Scans", "Last scanned", "Actions"]}
          >
            {repos.map((repo) => (
              <RepositoryRow key={repo.id} repo={repo} />
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
        </>
      )}
    </>
  );
}

function RepositoryRow({ repo }: { repo: RepositoryWithSummary }) {
  const latestScan = repo.summary.latest_scan;
  const eligibleForCompare = repo.summary.eligible_comparison_scan_count >= 2;
  return (
    <tr className="table-row">
      <td className="table-cell">
        <Link
          to={`/repositories/${repo.id}`}
          className="block font-medium text-ink-900 hover:text-accent-700"
        >
          {repo.display_name}
          {repo.archived ? (
            <span className="ml-2 inline-block rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
              archived
            </span>
          ) : null}
        </Link>
        <p className="mt-0.5 text-xs text-ink-500">
          {repo.canonical_identity}
        </p>
        <p className="mt-0.5 text-xs text-ink-400">
          {repositorySourceLabel[repo.source_type]} ·{" "}
          {repositoryVisibilityLabel[repo.visibility]}
        </p>
      </td>
      <td className="table-cell text-ink-500">
        {repositoryProviderLabel[repo.provider]}
      </td>
      <td className="table-cell">
        {latestScan ? (
          <div className="flex flex-col">
            <Link
              to={`/scans/${latestScan.id}`}
              className="text-sm font-medium text-ink-900 hover:text-accent-700"
            >
              #{latestScan.id}
            </Link>
            <StatusBadge status={latestScan.status}>
              {scanStatusLabel[latestScan.status as ScanStatus] || latestScan.status}
            </StatusBadge>
          </div>
        ) : (
          <span className="text-xs text-ink-500">No scans</span>
        )}
      </td>
      <td className="table-cell text-ink-500">
        {repo.summary.scan_count === 0 ? "0" : repo.summary.scan_count}
        {eligibleForCompare ? (
          <p className="text-xs text-ink-400">
            {repo.summary.eligible_comparison_scan_count} eligible to compare
          </p>
        ) : null}
      </td>
      <td className="table-cell text-ink-500">
        {latestScan?.completed_at ? (
          <Timestamp value={latestScan.completed_at} mode="relative" />
        ) : latestScan ? (
          <Timestamp value={latestScan.created_at} mode="relative" />
        ) : (
          <span className="text-xs text-ink-500">Never</span>
        )}
      </td>
      <td className="table-cell">
        <div className="flex flex-wrap items-center gap-2">
          {latestScan ? (
            <Link
              to={`/scans/${latestScan.id}`}
              className="btn-secondary"
              title={`Open latest scan #${latestScan.id}`}
            >
              <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
              Open latest
            </Link>
          ) : (
            <span
              className="btn-secondary opacity-50 pointer-events-none"
              aria-disabled="true"
              title="No scan has been run yet"
            >
              <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
              Open latest
            </span>
          )}
          <Link
            to={`/repositories/${repo.id}`}
            className="btn-secondary"
            title="View full scan history"
          >
            <GitBranch aria-hidden="true" className="h-3.5 w-3.5" />
            View history
          </Link>
          {eligibleForCompare ? (
            <Link
              to={`/repositories/${repo.id}/compare`}
              className="btn-secondary"
              title="Open the comparison selector"
            >
              <GitCompare aria-hidden="true" className="h-3.5 w-3.5" />
              Compare
            </Link>
          ) : (
            <span
              className="btn-secondary opacity-50 pointer-events-none"
              aria-disabled="true"
              title="Comparison requires at least two eligible scans"
            >
              <GitCompare aria-hidden="true" className="h-3.5 w-3.5" />
              Compare
            </span>
          )}
        </div>
      </td>
    </tr>
  );
}
