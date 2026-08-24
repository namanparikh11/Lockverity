import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, describeError } from "@/api/client";
import { isNotImplemented } from "@/api/fallback";
import type {
  Component,
  ComponentEvidenceResponse,
  ComponentEvidenceSummaryFacets,
  ComponentEvidenceSummaryItem,
  ComponentEvidenceSummaryPagination,
  ComponentEvidenceSummaryResponse,
  DependencyPath,
  SummaryFilterBool,
  SummaryFilterEdges,
  SummaryFilterPresent,
  SummaryFilterPurl,
  SummarySort,
} from "@/api/types";
import { ComponentIdentity, DependencyPathView } from "@/components/DependencyPath";
import { DetailsDrawer } from "@/components/DetailsDrawer";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, SelectFilter } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";

const DIRECT_OPTIONS = [
  { value: "all", label: "All" },
  { value: "yes", label: "Direct only" },
  { value: "no", label: "Transitive only" },
];

// Single source of truth for the default filter state.
// ``hasActiveFilters`` compares the current ``filters``
// against this constant to decide whether the Clear
// button is meaningful.
type FilterState = {
  search: string;
  ecosystem: string;
  direct: SummaryFilterBool;
  version: SummaryFilterPresent;
  licence_evidence: SummaryFilterPresent;
  provider_evidence: SummaryFilterPresent;
  purl: SummaryFilterPurl;
  dependency_edges: SummaryFilterEdges;
  cyclonedx_version_omitted: SummaryFilterBool;
  sort: SummarySort;
};

const DEFAULT_FILTERS: FilterState = {
  search: "",
  ecosystem: "",
  direct: "all",
  version: "all",
  licence_evidence: "all",
  provider_evidence: "all",
  purl: "all",
  dependency_edges: "all",
  cyclonedx_version_omitted: "all",
  sort: "package_name",
};

const PRESENT_OPTIONS = [
  { value: "all", label: "All" },
  { value: "present", label: "Present" },
  { value: "missing", label: "Missing" },
];

const PURL_OPTIONS = [
  { value: "all", label: "All" },
  { value: "persisted", label: "Persisted" },
  { value: "constructible", label: "Constructible" },
  { value: "omitted", label: "Omitted" },
];

const EDGES_OPTIONS = [
  { value: "all", label: "All" },
  { value: "present", label: "Edges observed" },
  { value: "none_observed", label: "No persisted edges" },
];

const YESNO_OPTIONS = [
  { value: "all", label: "All" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

const SORT_OPTIONS: { value: SummarySort; label: string }[] = [
  { value: "package_name", label: "Package name" },
  { value: "ecosystem", label: "Ecosystem" },
  { value: "version_missing_first", label: "Version missing first" },
  { value: "licence_missing_first", label: "Licence missing first" },
  { value: "provider_missing_first", label: "Provider missing first" },
  { value: "dependency_edges_missing_first", label: "Dependency edges missing first" },
];

/**
 * Dependency explorer.
 *
 * v0.9 evidence-aware search and filtering surface. The
 * page renders the ``getComponentsEvidenceSummary`` response
 * and surfaces the v0.9 filter row, the inline evidence
 * badges, and the optional facet panel. The "View
 * evidence" button continues to fetch the v0.8 detail
 * endpoint lazily when the user opens the drawer.
 */
export function DependencyExplorerPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const [items, setItems] = useState<ComponentEvidenceSummaryItem[] | null>(null);
  const [meta, setMeta] = useState<ComponentEvidenceSummaryPagination | null>(null);
  const [facets, setFacets] = useState<ComponentEvidenceSummaryFacets | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  // The Clear button is only meaningful when the user has
  // moved at least one filter away from its default. The
  // ``hasActiveFilters`` derivation is the single source of
  // truth used by the FilterBar (``onClear`` is only
  // passed when true) and by the regression tests.
  const hasActiveFilters =
    filters.search !== "" ||
    filters.ecosystem !== "" ||
    filters.direct !== "all" ||
    filters.version !== "all" ||
    filters.licence_evidence !== "all" ||
    filters.provider_evidence !== "all" ||
    filters.purl !== "all" ||
    filters.dependency_edges !== "all" ||
    filters.cyclonedx_version_omitted !== "all" ||
    filters.sort !== "package_name";
  const resetFilters = () => setFilters(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<ComponentEvidenceSummaryItem | null>(null);
  const [path, setPath] = useState<DependencyPath | null>(null);
  const [pathLoading, setPathLoading] = useState(false);
  const [notImpl, setNotImpl] = useState(false);
  // v0.8 evidence drilldown: a second side drawer surfaces
  // the read-only evidence summary for the same component.
  // The evidence fetch is lazy: it only fires when the user
  // clicks the "View evidence" button on a row. The fetch
  // is aborted on unmount.
  const [evidence, setEvidence] = useState<ComponentEvidenceResponse | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);

  useEffect(() => {
    setPage(1);
  }, [filters]);

  useEffect(() => {
    if (!Number.isFinite(sid)) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .getComponentsEvidenceSummary(sid, {
        page,
        page_size: 50,
        search: filters.search || undefined,
        ecosystem: filters.ecosystem || undefined,
        direct: filters.direct,
        version: filters.version,
        licence_evidence: filters.licence_evidence,
        provider_evidence: filters.provider_evidence,
        purl: filters.purl,
        dependency_edges: filters.dependency_edges,
        cyclonedx_version_omitted: filters.cyclonedx_version_omitted,
        sort: filters.sort,
      })
      .then((r: ComponentEvidenceSummaryResponse) => {
        if (controller.signal.aborted) return;
        setItems(r.items);
        setMeta(r.pagination);
        setFacets(r.facets);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setItems([]);
          setMeta({ page: 1, page_size: 0, total: 0, total_pages: 0 });
          setFacets(null);
          setNotImpl(true);
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [sid, page, filters]);

  useEffect(() => {
    if (!selected) {
      setPath(null);
      return;
    }
    if (!Number.isFinite(sid)) return;
    const controller = new AbortController();
    setPathLoading(true);
    api
      .getDependencyPath(sid, selected.id)
      .then((p) => {
        if (controller.signal.aborted) return;
        setPath(p);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setPath({
            components: [selected as unknown as Component],
            edges: [],
            truncated: false,
          });
          return;
        }
        setPath(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setPathLoading(false);
      });
    return () => controller.abort();
  }, [sid, selected]);

  // v0.8 evidence drilldown: a separate, lazy fetch that
  // surfaces the read-only evidence summary for the
  // currently selected component. The fetch is aborted on
  // unmount and on component change.
  useEffect(() => {
    if (!selected) {
      setEvidence(null);
      setEvidenceError(null);
      return;
    }
    if (!Number.isFinite(sid)) return;
    const controller = new AbortController();
    setEvidenceLoading(true);
    setEvidenceError(null);
    api
      .getComponentEvidence(sid, selected.id)
      .then((response) => {
        if (controller.signal.aborted) return;
        setEvidence(response);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiClientError) {
          if (err.apiError.httpStatus === 404) {
            setEvidenceError("Evidence is not available for this component.");
            return;
          }
          setEvidenceError(describeError(err));
          return;
        }
        setEvidenceError("Evidence is not available for this component.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setEvidenceLoading(false);
      });
    return () => controller.abort();
  }, [sid, selected]);

  return (
    <>
      <PageHeader
        title={`Dependencies · scan #${sid}`}
        description="Inventory of every component discovered in this scan. Filters narrow the list; the dependency path viewer explains how a transitive component was reached."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "Dependencies" },
        ]}
      />
      <div className="mb-4">
        <FilterBar
          search={filters.search}
          onSearchChange={(v) => setFilters((f) => ({ ...f, search: v }))}
          searchPlaceholder="Search package name"
          resultCount={meta?.total}
          resultLabel="components"
          title="Evidence filters"
          layout="card"
          onClear={hasActiveFilters ? resetFilters : undefined}
        >
          <div className="flex flex-col gap-1" data-testid="select-filter-ecosystem">
            <label
              htmlFor="ecosystem"
              className="text-xs font-medium text-ink-500"
            >
              Ecosystem
            </label>
            <input
              id="ecosystem"
              className="w-full rounded-md border border-ink-200 bg-surface px-2 py-1 text-sm text-ink-700 shadow-sm focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500 font-mono"
              placeholder="npm, PyPI, ..."
              value={filters.ecosystem}
              onChange={(e) => setFilters((f) => ({ ...f, ecosystem: e.target.value }))}
            />
          </div>
          <SelectFilter
            id="direct"
            label="Direct"
            value={filters.direct}
            onChange={(v) => setFilters((f) => ({ ...f, direct: v as SummaryFilterBool }))}
            options={DIRECT_OPTIONS}
            stacked
          />
          <SelectFilter
            id="version"
            label="Version"
            value={filters.version}
            onChange={(v) => setFilters((f) => ({ ...f, version: v as SummaryFilterPresent }))}
            options={PRESENT_OPTIONS}
            stacked
          />
          <SelectFilter
            id="licence"
            label="Licence evidence"
            value={filters.licence_evidence}
            onChange={(v) => setFilters((f) => ({ ...f, licence_evidence: v as SummaryFilterPresent }))}
            options={PRESENT_OPTIONS}
            stacked
          />
          <SelectFilter
            id="provider"
            label="Provider evidence"
            value={filters.provider_evidence}
            onChange={(v) => setFilters((f) => ({ ...f, provider_evidence: v as SummaryFilterPresent }))}
            options={PRESENT_OPTIONS}
            stacked
          />
          <SelectFilter
            id="purl"
            label="PURL"
            value={filters.purl}
            onChange={(v) => setFilters((f) => ({ ...f, purl: v as SummaryFilterPurl }))}
            options={PURL_OPTIONS}
            stacked
          />
          <SelectFilter
            id="dep-edges"
            label="Dependency edges"
            value={filters.dependency_edges}
            onChange={(v) => setFilters((f) => ({ ...f, dependency_edges: v as SummaryFilterEdges }))}
            options={EDGES_OPTIONS}
            stacked
          />
          <SelectFilter
            id="cdx-omit"
            label="CycloneDX 1.7 version omitted"
            value={filters.cyclonedx_version_omitted}
            onChange={(v) => setFilters((f) => ({ ...f, cyclonedx_version_omitted: v as SummaryFilterBool }))}
            options={YESNO_OPTIONS}
            stacked
          />
          <SelectFilter
            id="sort"
            label="Sort"
            value={filters.sort}
            onChange={(v) => setFilters((f) => ({ ...f, sort: v as SummarySort }))}
            options={SORT_OPTIONS}
            stacked
          />
        </FilterBar>
      </div>
      {facets ? <FacetsPanel facets={facets} /> : null}
      {error ? (
        <ErrorState error={error} />
      ) : items === null || meta === null ? (
        <Skeleton rows={6} />
      ) : items.length === 0 ? (
        <EmptyState
          title={notImpl ? "Dependency endpoint not exposed" : "No components recorded"}
          description={
            notImpl
              ? "When the backend exposes a paginated components endpoint, this table will appear automatically."
              : "No components matched the current evidence filters. Try widening the filters or clearing the search box."
          }
        />
      ) : (
        <>
          <ResponsiveTable
            headers={["Package", "Ecosystem", "Version", "Direct?", "Evidence flags", "Evidence"]}
          >
            {items.map((component) => (
              <tr
                key={component.id}
                className="table-row cursor-pointer hover:bg-ink-50"
                onClick={() => setSelected(component)}
              >
                <td className="table-cell">
                  <ComponentIdentity component={component as unknown as Component} />
                </td>
                <td className="table-cell text-ink-500">
                  {component.ecosystem ?? "—"}
                </td>
                <td className="table-cell text-ink-500">
                  {component.version ?? "—"}
                </td>
                <td className="table-cell">
                  <span className="font-mono">{component.direct ? "yes" : "no"}</span>
                </td>
                <td className="table-cell">
                  <EvidenceFlagsCell flags={component.evidence} />
                </td>
                <td className="table-cell">
                  <button
                    type="button"
                    className="rounded border border-ink-300 bg-surface px-2 py-1 text-xs font-medium text-ink-800"
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelected(component);
                      // The drawer effect picks up the
                      // selected component and fetches
                      // the evidence summary.
                    }}
                    data-testid={`component-evidence-button-${component.id}`}
                    aria-label={`View evidence for ${component.package_name}`}
                  >
                    View evidence
                  </button>
                </td>
              </tr>
            ))}
          </ResponsiveTable>
          <div className="mt-4">
            <Pagination meta={meta} onPageChange={setPage} />
          </div>
        </>
      )}
      <DetailsDrawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.package_name}` : ""}
        ariaLabel="Dependency details"
      >
        {selected ? (
          <div className="space-y-4">
            <div>
              <h3 className="label">Identity</h3>
              <p className="mt-1 text-sm text-ink-800">
                <ComponentIdentity component={selected as unknown as Component} />
              </p>
              <p className="mt-1 text-xs text-ink-500">
                Ecosystem: <span className="font-mono">{selected.ecosystem ?? "—"}</span> ·
                Source: <span className="font-mono">{selected.version_source ?? "—"}</span>
              </p>
            </div>
            <div>
              <h3 className="label">Dependency path</h3>
              {pathLoading ? (
                <Skeleton rows={3} />
              ) : (
                <DependencyPathView path={path} />
              )}
            </div>
            <div>
              <h3 className="label">Component evidence</h3>
              <ComponentEvidencePanel
                evidence={evidence}
                error={evidenceError}
                loading={evidenceLoading}
              />
            </div>
          </div>
        ) : null}
      </DetailsDrawer>
    </>
  );
}

/**
 * v0.8 component evidence panel.
 *
 * Renders the read-only evidence summary for the currently
 * selected component inside the existing DetailsDrawer. The
 * panel surfaces every documented section (identity, manifest,
 * licence, provider, dependency, export implications, omissions)
 * with the evidence-honesty wording the contract guarantees.
 *
 * The panel never:
 *
 * - describes the component as clean / secure / fixed;
 * - claims the dependency graph is complete;
 * - claims the SBOM is a security verdict;
 * - fabricates missing values.
 */
function ComponentEvidencePanel({
  evidence,
  error,
  loading,
}: {
  evidence: ComponentEvidenceResponse | null;
  error: string | null;
  loading: boolean;
}) {
  if (error) {
    return (
      <p
        className="mt-1 rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-900"
        data-testid="component-evidence-error"
      >
        {error}
      </p>
    );
  }
  if (loading && !evidence) {
    return (
      <p
        className="mt-1 text-xs text-ink-500"
        data-testid="component-evidence-loading"
      >
        Loading evidence…
      </p>
    );
  }
  if (!evidence) {
    return (
      <p
        className="mt-1 text-xs text-ink-500"
        data-testid="component-evidence-empty"
      >
        Evidence is not yet available.
      </p>
    );
  }
  const c = evidence.component;
  const impl = evidence.export_implications;
  return (
    <div className="mt-1 space-y-3 text-xs" data-testid="component-evidence-panel">
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">Identity</p>
        <ul className="mt-1 space-y-0.5 text-ink-700">
          <li>
            <span className="text-ink-500">Package:</span>{" "}
            <span className="font-mono" data-testid="ce-package-name">
              {c.package_name}
            </span>
          </li>
          <li>
            <span className="text-ink-500">Ecosystem:</span>{" "}
            <span className="font-mono">{c.ecosystem ?? "—"}</span>
          </li>
          <li>
            <span className="text-ink-500">Version:</span>{" "}
            <span className="font-mono">{c.version ?? "—"}</span>{" "}
            <span className="text-ink-500">(source: {c.version_source ?? "—"})</span>
          </li>
          <li>
            <span className="text-ink-500">Direct:</span>{" "}
            <span className="font-mono">{c.direct ? "yes" : "no"}</span>
          </li>
          <li>
            <span className="text-ink-500">PURL:</span>{" "}
            <span className="font-mono" data-testid="ce-purl">
              {c.package_url ?? "—"}
            </span>
            {c.package_url_well_formed === false ? (
              <span className="ml-2 text-rose-700">persisted PURL malformed</span>
            ) : null}
            {c.package_url === null && c.purl_constructible ? (
              <span className="ml-2 text-ink-500">
                PURL omitted from persistence but constructible from ecosystem + name + version.
              </span>
            ) : null}
          </li>
        </ul>
      </div>
      <EvidenceManifestBlock manifest={evidence.manifest} />
      <EvidenceLicenceBlock licence={evidence.licence_evidence} />
      <EvidenceProviderBlock provider={evidence.provider_evidence} />
      <EvidenceDependencyBlock dependency={evidence.dependency_evidence} />
      <EvidenceExportImplications impl={impl} />
      <EvidenceOmissions omissions={evidence.omissions} />
    </div>
  );
}

function EvidenceManifestBlock({
  manifest,
}: {
  manifest: ComponentEvidenceResponse["manifest"];
}) {
  if (!manifest.available) {
    return (
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Manifest evidence
        </p>
        <p className="mt-1 text-ink-700" data-testid="ce-manifest-empty">
          No persisted manifest association available.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Manifest evidence
      </p>
      <ul className="mt-1 space-y-0.5 text-ink-700" data-testid="ce-manifest">
        <li>
          <span className="text-ink-500">Path:</span>{" "}
          <span className="font-mono">{manifest.path ?? "—"}</span>
        </li>
        <li>
          <span className="text-ink-500">Type:</span>{" "}
          <span className="font-mono">{manifest.manifest_type ?? "—"}</span>
        </li>
        <li>
          <span className="text-ink-500">Parse status:</span>{" "}
          <span className="font-mono">{manifest.parse_status ?? "—"}</span>
        </li>
        <li>
          <span className="text-ink-500">Warnings:</span>{" "}
          <span className="font-mono">{manifest.parse_warning_count ?? 0}</span>
        </li>
      </ul>
    </div>
  );
}

function EvidenceLicenceBlock({
  licence,
}: {
  licence: ComponentEvidenceResponse["licence_evidence"];
}) {
  if (!licence.available) {
    return (
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Licence evidence
        </p>
        <p className="mt-1 text-ink-700" data-testid="ce-licence-empty">
          No persisted licence evidence available.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Licence evidence
      </p>
      <ul className="mt-1 space-y-0.5 text-ink-700" data-testid="ce-licence">
        {licence.observations.map((o, idx) => (
          <li key={`${o.finding_id}-${idx}`}>
            <span className="font-mono">{o.value}</span>{" "}
            <span className="text-ink-500">
              ({o.classification}, {o.provenance})
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceProviderBlock({
  provider,
}: {
  provider: ComponentEvidenceResponse["provider_evidence"];
}) {
  if (!provider.available) {
    return (
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Provider / advisory evidence
        </p>
        <p className="mt-1 text-ink-700" data-testid="ce-provider-empty">
          No provider observations or advisories were recorded for this
          component.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Provider / advisory evidence
      </p>
      <ul className="mt-1 space-y-0.5 text-ink-700" data-testid="ce-provider">
        {provider.observations.map((o) => (
          <li key={o.id}>
            <span className="font-mono">{o.provider}</span>{" "}
            <span className="text-ink-500">({o.operation}):</span>{" "}
            <span className="font-mono">{o.status}</span>{" "}
            {o.http_status ? (
              <span className="text-ink-500">http {o.http_status}</span>
            ) : null}
          </li>
        ))}
        {provider.advisories.map((a) => (
          <li key={a.advisory_id}>
            <span className="font-mono">
              {a.canonical_id ?? a.source_advisory_id ?? a.advisory_id}
            </span>{" "}
            <span className="text-ink-500">
              ({a.severity_label ?? "unknown"})
            </span>{" "}
            {a.confidence === null ? (
              <span className="text-ink-500">no confidence supplied</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceDependencyBlock({
  dependency,
}: {
  dependency: ComponentEvidenceResponse["dependency_evidence"];
}) {
  const coverage = dependency.graph_coverage;
  if (dependency.no_edges_observed) {
    return (
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Dependency evidence
        </p>
        <p className="mt-1 text-ink-700" data-testid="ce-dependency-empty">
          No persisted dependency edges for this component. The
          dependency graph coverage is reported as <span className="font-mono">{coverage}</span>;
          a partial / unknown graph is not the same as &ldquo;no dependencies&rdquo;.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Dependency evidence
      </p>
      <p className="mt-1 text-ink-700" data-testid="ce-dependency">
        Incoming: {dependency.incoming.length}, outgoing:{" "}
        {dependency.outgoing.length}. Coverage:{" "}
        <span className="font-mono">{coverage}</span>.
      </p>
    </div>
  );
}

function EvidenceExportImplications({
  impl,
}: {
  impl: ComponentEvidenceResponse["export_implications"];
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Export implications
      </p>
      <ul className="mt-1 space-y-0.5 text-ink-700" data-testid="ce-export">
        <li>
          Appears in CycloneDX 1.7:{" "}
          <span className="font-mono">{impl.appears_in_cyclonedx_17 ? "yes" : "no"}</span>
        </li>
        <li>
          Version omitted from export:{" "}
          <span className="font-mono">{impl.version_omitted ? "yes" : "no"}</span>
          {impl.version_omitted ? (
            <span className="text-ink-500">
              {" "}
              — no concrete version was persisted, so the export
              leaves the version field empty.
            </span>
          ) : null}
        </li>
        <li>
          PURL emitted in export:{" "}
          <span className="font-mono">{impl.purl_emitted ? "yes" : "no"}</span>
        </li>
        <li>
          Dependency relationships emitted:{" "}
          <span className="font-mono">
            {impl.dependency_relationships_emitted ? "yes" : "no"}
          </span>{" "}
          <span className="text-ink-500">
            (graph coverage {impl.graph_coverage})
          </span>
        </li>
      </ul>
    </div>
  );
}

function EvidenceOmissions({ omissions }: { omissions: string[] }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        Evidence-honesty markers
      </p>
      <ul
        className="mt-1 list-disc space-y-0.5 pl-5 text-ink-700"
        data-testid="ce-omissions"
      >
        {omissions.map((marker) => (
          <li key={marker}>
            <span className="font-mono">{marker}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * v0.9 inline evidence badges for the dependency table.
 *
 * The cell renders a small badge per evidence flag the
 * v0.9 summary exposes. The wording is evidence-honest:
 * missing evidence is rendered as "missing" (not "none"),
 * dependency edges are rendered as "no persisted edges"
 * (not "no dependencies"), and the PURL state is rendered
 * verbatim so the consumer can distinguish a deliberately
 * omitted PURL from a reconstructed one.
 */
function EvidenceFlagsCell({
  flags,
}: {
  flags: {
    version_present: boolean;
    licence_observed: boolean;
    provider_observed: boolean;
    purl_state: "persisted" | "constructible" | "omitted";
    edges_observed: boolean;
    appears_in_cyclonedx_17: boolean;
    version_omitted_from_cyclonedx_17: boolean;
    dependency_relationships_emitted_in_cyclonedx_17: boolean;
  };
}) {
  return (
    <div
      className="flex flex-wrap gap-1"
      data-testid="evidence-flags-cell"
    >
      <EvidenceBadge
        tone={flags.version_present ? "ok" : "warn"}
        label={flags.version_present ? "version present" : "version missing"}
      />
      <EvidenceBadge
        tone={flags.licence_observed ? "ok" : "warn"}
        label={
          flags.licence_observed
            ? "licence observed"
            : "licence not persisted"
        }
      />
      <EvidenceBadge
        tone={flags.provider_observed ? "ok" : "warn"}
        label={
          flags.provider_observed
            ? "provider observed"
            : "provider not persisted"
        }
      />
      <EvidenceBadge
        tone={flags.purl_state === "persisted" ? "ok" : "muted"}
        label={`purl ${flags.purl_state}`}
      />
      <EvidenceBadge
        tone={flags.edges_observed ? "ok" : "muted"}
        label={
          flags.edges_observed
            ? "edges observed"
            : "no persisted edges"
        }
      />
      {flags.version_omitted_from_cyclonedx_17 ? (
        <EvidenceBadge tone="warn" label="version omitted from cdx 1.7" />
      ) : null}
    </div>
  );
}

function EvidenceBadge({
  tone,
  label,
}: {
  tone: "ok" | "warn" | "muted";
  label: string;
}) {
  const classes =
    tone === "ok"
      ? "rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700"
      : tone === "warn"
        ? "rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700"
        : "rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-medium text-ink-600";
  return <span className={classes}>{label}</span>;
}

/**
 * v0.9 facet panel. Surfaces the aggregate counts the
 * backend computes over the full filtered set. The
 * panel is informational; it never renders a verdict.
 */
function FacetsPanel({
  facets,
}: {
  facets: ComponentEvidenceSummaryFacets;
}) {
  const ecosystems = Object.entries(facets.ecosystems);
  return (
    <section
      className="mb-4 grid grid-cols-2 gap-3 rounded-md border border-ink-200 bg-ink-50 p-3 text-xs sm:grid-cols-3 lg:grid-cols-5"
      data-testid="facets-panel"
    >
      <FacetItem
        label="Total components"
        value={String(
          ecosystems.reduce((acc, [, n]) => acc + n, 0)
        )}
      />
      <FacetItem
        label="Ecosystems"
        value={
          ecosystems.length === 0
            ? "—"
            : ecosystems.map(([e]) => e).join(", ")
        }
      />
      <FacetItem
        label="Missing version"
        value={String(facets.missing_version)}
      />
      <FacetItem
        label="Missing licence evidence"
        value={String(facets.missing_licence_evidence)}
      />
      <FacetItem
        label="Missing provider evidence"
        value={String(facets.missing_provider_evidence)}
      />
      <FacetItem
        label="PURL persisted"
        value={String(facets.purl_persisted)}
      />
      <FacetItem
        label="PURL constructible"
        value={String(facets.purl_constructible)}
      />
      <FacetItem
        label="PURL omitted"
        value={String(facets.purl_omitted)}
      />
      <FacetItem
        label="Edges observed"
        value={String(facets.edges_observed)}
      />
      <FacetItem
        label="No persisted edges"
        value={String(facets.edges_none_observed)}
      />
      <FacetItem
        label="Direct"
        value={String(facets.direct_yes)}
      />
      <FacetItem
        label="Transitive"
        value={String(facets.direct_no)}
      />
      <FacetItem
        label="CycloneDX 1.7 version omitted"
        value={String(facets.cyclonedx_version_omitted)}
      />
    </section>
  );
}

function FacetItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">
        {label}
      </p>
      <p className="mt-0.5 font-mono text-sm text-ink-900">{value}</p>
    </div>
  );
}
