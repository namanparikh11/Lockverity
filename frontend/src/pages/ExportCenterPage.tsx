import { Download, FileText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, describeError } from "@/api/client";
import { isNotImplemented } from "@/api/fallback";
import type {
  CycloneDxPreviewCoverage,
  CycloneDxPreviewInventory,
  CycloneDxPreviewResponse,
  CycloneDxPreviewSbomOutput,
  EvidenceReportPreviewResponse,
  ExportDescriptor,
  ExportFormat,
} from "@/api/types";
import { ConfirmationDialog } from "@/components/ConfirmationDialog";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { Notification } from "@/components/Notification";
import { PageHeader } from "@/components/PageHeader";
import { ResponsiveTable } from "@/components/ResponsiveTable";
import { Skeleton } from "@/components/Skeleton";

const KNOWN_FORMATS: ExportFormat[] = [
  "cyclonedx_json",
  "cyclonedx_1_7",
  "findings_json",
  "findings_csv",
  "sarif_json",
];

const FALLBACK_DESCRIPTORS: ExportDescriptor[] = [
  {
    format: "cyclonedx_json",
    label: "CycloneDX SBOM (JSON)",
    description: "CycloneDX 1.5 SBOM of every discovered component.",
    supported: true,
    not_supported_reason: null,
    content_type: "application/vnd.cyclonedx+json",
    filename_hint: "lockverity-sbom.cdx.json",
  },
  {
    format: "cyclonedx_1_7",
    label: "CycloneDX 1.7 SBOM (JSON)",
    description:
      "CycloneDX 1.7 software bill of materials as JSON. Generated against the official 1.7 schema. Only completed and partial scans with persisted local inventory are eligible.",
    supported: true,
    not_supported_reason: null,
    content_type: "application/vnd.cyclonedx+json; version=1.7",
    filename_hint: "lockverity-scan.cdx.json",
  },
  {
    format: "findings_json",
    label: "Findings (JSON)",
    description: "Raw findings list as JSON, including evidence and location.",
    supported: true,
    not_supported_reason: null,
    content_type: "application/json",
    filename_hint: "lockverity-findings.json",
  },
  {
    format: "findings_csv",
    label: "Findings (CSV)",
    description: "One finding per row. Severity and confidence remain separate columns.",
    supported: true,
    not_supported_reason: null,
    content_type: "text/csv",
    filename_hint: "lockverity-findings.csv",
  },
  {
    format: "sarif_json",
    label: "SARIF",
    description: "Static Analysis Results Interchange Format, for IDE and CI integration.",
    supported: true,
    not_supported_reason: null,
    content_type: "application/sarif+json",
    filename_hint: "lockverity-findings.sarif.json",
  },
];

/**
 * Export center.
 *
 * Lists every export the backend supports, with a truthful
 * "not supported" state when a format is unavailable. The user
 * is never given a download link that will 404 silently.
 */
export function ExportCenterPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const sid = Number.parseInt(scanId ?? "", 10);
  const [items, setItems] = useState<ExportDescriptor[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const [confirm, setConfirm] = useState<ExportFormat | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(sid)) {
      setError(new Error("Invalid scan id."));
      return;
    }
    const controller = new AbortController();
    setItems(null);
    setError(null);
    api
      .listExports(sid)
      .then((r) => {
        if (controller.signal.aborted) return;
        const seen = new Set(r.items.map((it) => it.format));
        const merged = [...r.items];
        for (const known of KNOWN_FORMATS) {
          if (!seen.has(known)) {
            merged.push({
              format: known,
              label: fallbackLabel(known),
              description: "",
              supported: false,
              not_supported_reason: "Not exposed by the API yet.",
              content_type: "application/octet-stream",
              filename_hint: "",
            });
          }
        }
        setItems(merged);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          setItems(
            FALLBACK_DESCRIPTORS.map((d) => ({
              ...d,
              supported: false,
              not_supported_reason: "Export endpoint not exposed by the API yet.",
            }))
          );
          return;
        }
        setError(err);
      });
    return () => controller.abort();
  }, [sid]);

  async function performDownload(format: ExportFormat) {
    if (!Number.isFinite(sid)) return;
    setConfirm(null);
    setLastError(null);
    setLastSuccess(null);
    setDownloading(format);
    try {
      const result = await api.downloadExport(sid, format);
      const blob = new Blob([result.body], { type: result.contentType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename ?? `lockverity-${format}.bin`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setLastSuccess(`Downloaded ${link.download}.`);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setLastError(describeError(err));
      } else {
        setLastError("Download failed.");
      }
    } finally {
      setDownloading(null);
    }
  }

  return (
    <>
      <PageHeader
        title={`Exports · scan #${sid}`}
        description="Generate a CycloneDX SBOM, SARIF, findings JSON, or findings CSV. Unsupported exports are disabled and explained - the UI never offers a download that 404s."
        breadcrumbs={[
          { label: "Scan", to: `/scans/${sid}` },
          { label: "Exports" },
        ]}
      />
      {lastSuccess ? (
        <div className="mb-4">
          <Notification tone="ok" title={lastSuccess} onDismiss={() => setLastSuccess(null)} />
        </div>
      ) : null}
      {lastError ? (
        <div className="mb-4">
          <Notification tone="danger" title="Export failed" description={lastError} onDismiss={() => setLastError(null)} />
        </div>
      ) : null}
      {error ? (
        <ErrorState error={error} />
      ) : items === null ? (
        <Skeleton rows={4} />
      ) : (
        <ResponsiveTable
          headers={["Format", "Description", "State", "Action"]}
        >
          {items.map((item) => (
            <tr key={item.format} className="table-row">
              <td className="table-cell">
                <p className="font-medium text-ink-900">{item.label}</p>
                <p className="font-mono text-[11px] text-ink-500">{item.format}</p>
              </td>
              <td className="table-cell text-ink-700">
                {item.description || "—"}
                {item.not_supported_reason ? (
                  <p className="mt-1 text-xs text-rose-700">{item.not_supported_reason}</p>
                ) : null}
              </td>
              <td className="table-cell">
                {item.supported ? (
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                    available
                  </span>
                ) : (
                  <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-medium text-ink-600">
                    not available
                  </span>
                )}
              </td>
              <td className="table-cell">
                {item.supported ? (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => setConfirm(item.format)}
                    disabled={downloading !== null}
                  >
                    <Download aria-hidden="true" className="h-4 w-4" />
                    {downloading === item.format ? "Preparing..." : "Download"}
                  </button>
                ) : (
                  <button type="button" className="btn-secondary" disabled aria-disabled="true">
                    <Download aria-hidden="true" className="h-4 w-4" />
                    Unavailable
                  </button>
                )}
              </td>
            </tr>
          ))}
        </ResponsiveTable>
      )}
      {!error ? (
        <>
          <CycloneDxPreviewPanel scanId={sid} />
          <EvidenceReportPanel scanId={sid} />
          <div className="mt-4">
            <DataCompletenessNotice
              title="About exports"
              description="Exports are generated from the current scan's database state. A scan that has not yet produced findings produces an empty-but-valid export. The bytes are downloaded directly in the browser; they are not sent to any third-party service."
              tone="muted"
            />
          </div>
        </>
      ) : null}
      <ConfirmationDialog
        open={confirm !== null}
        title="Download export?"
        description={
          confirm
            ? `Download the ${fallbackLabel(confirm)} file for scan #${sid}?`
            : "Download this export?"
        }
        confirmLabel="Download"
        onConfirm={() => confirm && performDownload(confirm)}
        onCancel={() => setConfirm(null)}
      />
    </>
  );
}

function fallbackLabel(format: ExportFormat): string {
  switch (format) {
    case "cyclonedx_json":
      return "CycloneDX SBOM (JSON)";
    case "cyclonedx_1_7":
      return "CycloneDX 1.7 SBOM (JSON)";
    case "findings_json":
      return "Findings (JSON)";
    case "findings_csv":
      return "Findings (CSV)";
    case "sarif_json":
      return "SARIF";
    default:
      return format;
  }
}

/**
 * Read-only preview / readiness summary panel for the
 * CycloneDX 1.7 export. v0.7 surfaces the bounded
 * eligibility verdict, the inventory summary, the
 * evidence-coverage labels, and the omissions list before
 * the user downloads the SBOM. The panel never fetches a
 * full BOM; it consumes the dedicated preview endpoint.
 *
 * The component is a controlled sub-render of the export
 * page. The panel is collapsed by default and only fetches
 * the preview summary when the user clicks "Show preview";
 * this keeps the export page lightweight and avoids an
 * unconditional round-trip on every page load. The fetch
 * is aborted on unmount so a fast scan switch does not
 * leave a stale preview on screen.
 */
function CycloneDxPreviewPanel({ scanId }: { scanId: number }) {
  const [preview, setPreview] = useState<CycloneDxPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    if (!Number.isFinite(scanId)) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .previewCyclonedx17(scanId)
      .then((response) => {
        if (controller.signal.aborted) return;
        setPreview(response);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiClientError && err.apiError.httpStatus === 404) {
          setError("Preview is not available for this scan.");
          return;
        }
        setError(describeError(err));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, [expanded, scanId]);

  if (error) {
    return (
      <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
        <p className="font-semibold">CycloneDX 1.7 preview unavailable</p>
        <p className="mt-1">{error}</p>
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="mt-4">
        <button
          type="button"
          className="rounded border border-ink-300 bg-surface px-4 py-2 text-sm font-medium text-ink-800"
          onClick={() => setExpanded(true)}
          data-testid="preview-show-button"
        >
          <FileText aria-hidden="true" className="mr-2 inline h-4 w-4" />
          Show CycloneDX 1.7 evidence preview
        </button>
      </div>
    );
  }

  if (loading && !preview) {
    return (
      <div className="mt-4 rounded-md border border-ink-200 bg-ink-50 p-4 text-sm text-ink-700">
        <p className="font-semibold text-ink-900">CycloneDX 1.7 preview</p>
        <p className="mt-1 text-ink-600">Loading evidence summary…</p>
      </div>
    );
  }

  if (!preview || !preview.eligibility || !preview.inventory || !preview.evidence_coverage) {
    return (
      <div
        className="mt-4 rounded-md border border-ink-200 bg-ink-50 p-4 text-sm text-ink-700"
        data-testid="preview-panel"
      >
        <p className="font-semibold text-ink-900">CycloneDX 1.7 preview</p>
        <p className="mt-1 text-ink-600">Evidence summary is not yet available.</p>
      </div>
    );
  }

  return (
    <div data-testid="preview-panel">
      <PreviewBody preview={preview} expanded={expanded} onToggle={() => setExpanded(false)} />
    </div>
  );
}

function PreviewBody({
  preview,
  expanded,
  onToggle,
}: {
  preview: CycloneDxPreviewResponse;
  expanded: boolean;
  onToggle: () => void;
}) {
  const eligible = preview.eligibility.eligible;
  const providerDegraded = preview.eligibility.limitations.includes("provider_degraded");
  const providerOmitted = preview.eligibility.limitations.includes(
    "provider_omitted_by_operator"
  );
  return (
    <section
      aria-label="CycloneDX 1.7 evidence preview"
      className="mt-4 rounded-md border border-ink-200 bg-surface p-4 text-sm"
    >
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="flex items-center gap-2 font-semibold text-ink-900">
            <FileText aria-hidden="true" className="h-4 w-4" />
            CycloneDX 1.7 SBOM &mdash; evidence preview
          </p>
          <p className="mt-1 text-xs text-ink-600">
            Read-only summary generated from persisted scan evidence. The bytes are downloaded
            directly in the browser; they are not sent to a third party. This preview does not
            generate a full SBOM.
          </p>
        </div>
        <button
          type="button"
          className="rounded border border-ink-200 px-3 py-1 text-xs font-medium text-ink-700"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          {expanded ? "Hide details" : "Show details"}
        </button>
      </header>

      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
        <PreviewKvp label="Eligibility" value={eligible ? "eligible" : "not eligible"} />
        <PreviewKvp
          label="Verdict reason"
          value={preview.eligibility.reason}
          tone={eligible ? "ok" : "danger"}
          testId="preview-verdict-reason"
        />
        <PreviewKvp
          label="Download expected to succeed"
          value={preview.eligibility.download_expected_to_succeed ? "yes" : "no"}
        />
        <PreviewKvp label="Coverage: provider" value={preview.evidence_coverage.provider_coverage} />
      </div>

      {!eligible ? (
        <PreviewNotice tone="danger">
          This scan is not eligible for a CycloneDX 1.7 SBOM. The download endpoint would
          return a 422 for this scan state. Older legacy exports may still produce an
          empty-but-valid file for this scan.
        </PreviewNotice>
      ) : null}
      {providerDegraded ? (
        <PreviewNotice tone="warn">
          Provider-degraded scan: local inventory is complete, but vulnerability / enrichment
          evidence may be partial. The SBOM does not claim a complete dependency graph.
        </PreviewNotice>
      ) : null}
      {providerOmitted ? (
        <PreviewNotice tone="info">
          {providerDegraded
            ? "One or more providers were omitted by the operator while other requested provider evidence was degraded. The SBOM records both limitations without treating the omission as a failure."
            : "External provider evidence was not requested by the operator. Local inventory is still exportable; the SBOM records provider coverage as not requested, not degraded or successful."}
        </PreviewNotice>
      ) : null}

      {expanded ? (
        <div className="mt-3 space-y-3 text-xs text-ink-700">
          <InventorySummary inventory={preview.inventory} />
          <CoverageSummary coverage={preview.evidence_coverage} />
          <SbomOutputSummary sbom={preview.sbom_output} />
          <OmissionsList omissions={preview.omissions} />
          <LegacyNote note={preview.legacy_export_relationship} />
        </div>
      ) : null}
    </section>
  );
}

function PreviewKvp({
  label,
  value,
  tone,
  testId,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "danger";
  testId?: string;
}) {
  const colour =
    tone === "ok"
      ? "text-emerald-700"
      : tone === "warn"
        ? "text-amber-700"
        : tone === "danger"
          ? "text-rose-700"
          : "text-ink-900";
  return (
    <div className="rounded border border-ink-100 bg-ink-50 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-ink-500">{label}</p>
      <p
        className={`mt-0.5 font-medium ${colour}`}
        data-testid={testId}
      >
        {value}
      </p>
    </div>
  );
}

function PreviewNotice({
  tone,
  children,
}: {
  tone: "info" | "warn" | "danger";
  children: React.ReactNode;
}) {
  const colour =
    tone === "info"
      ? "border-ink-200 bg-ink-50 text-ink-800"
      : tone === "warn"
        ? "border-amber-200 bg-amber-50 text-amber-900"
        : "border-rose-200 bg-rose-50 text-rose-900";
  return (
    <p
      className={`mt-3 rounded border px-3 py-2 text-xs ${colour}`}
      data-testid={`preview-notice-${tone}`}
    >
      {children}
    </p>
  );
}

function InventorySummary({ inventory }: { inventory: CycloneDxPreviewInventory }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">Inventory summary</p>
      <ul className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-3">
        <li>
          <span className="text-ink-500">Components:</span>{" "}
          <span className="font-medium text-ink-900" data-testid="preview-inventory-components">
            {inventory.component_count}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Manifests:</span>{" "}
          <span className="font-medium text-ink-900" data-testid="preview-inventory-manifests">
            {inventory.manifest_count}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Ecosystems:</span>{" "}
          <span className="font-medium text-ink-900">
            {inventory.ecosystems.length === 0 ? "—" : inventory.ecosystems.join(", ")}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Direct:</span>{" "}
          <span className="font-medium text-ink-900">{inventory.direct_count}</span>
        </li>
        <li>
          <span className="text-ink-500">Transitive:</span>{" "}
          <span className="font-medium text-ink-900">{inventory.transitive_count}</span>
        </li>
        <li>
          <span className="text-ink-500">Missing version:</span>{" "}
          <span
            className={`font-medium ${inventory.missing_version_count > 0 ? "text-amber-700" : "text-ink-900"}`}
            data-testid="preview-inventory-missing-version"
          >
            {inventory.missing_version_count}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Duplicate observations:</span>{" "}
          <span
            className={`font-medium ${inventory.duplicate_observations_count > 0 ? "text-amber-700" : "text-ink-900"}`}
          >
            {inventory.duplicate_observations_count}
          </span>
        </li>
      </ul>
    </div>
  );
}

function CoverageSummary({ coverage }: { coverage: CycloneDxPreviewCoverage }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">Evidence coverage</p>
      <ul className="mt-1 grid grid-cols-1 gap-1 md:grid-cols-3">
        <li>
          <span className="text-ink-500">Inventory:</span>{" "}
          <span
            className="font-medium text-ink-900"
            data-testid="preview-coverage-inventory"
          >
            {coverage.inventory_coverage}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Dependency graph:</span>{" "}
          <span
            className="font-medium text-ink-900"
            data-testid="preview-coverage-graph"
          >
            {coverage.dependency_graph_coverage}
          </span>
        </li>
        <li>
          <span className="text-ink-500">Provider:</span>{" "}
          <span
            className={`font-medium ${coverage.provider_coverage === "degraded" ? "text-amber-700" : "text-ink-900"}`}
            data-testid="preview-coverage-provider"
          >
            {coverage.provider_coverage}
          </span>
        </li>
      </ul>
    </div>
  );
}

function SbomOutputSummary({ sbom }: { sbom: CycloneDxPreviewSbomOutput }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">SBOM output facts</p>
      <ul className="mt-1 grid grid-cols-1 gap-1 md:grid-cols-2">
        <li>
          <span className="text-ink-500">Format:</span>{" "}
          <span className="font-medium text-ink-900">{sbom.format}</span>
        </li>
        <li>
          <span className="text-ink-500">Spec version:</span>{" "}
          <span className="font-medium text-ink-900">{sbom.spec_version}</span>
        </li>
        <li>
          <span className="text-ink-500">Media type:</span>{" "}
          <span className="font-mono text-[11px] text-ink-700">{sbom.media_type}</span>
        </li>
        <li>
          <span className="text-ink-500">Filename template:</span>{" "}
          <span className="font-mono text-[11px] text-ink-700">
            {sbom.filename_template}
          </span>
        </li>
        <li className="md:col-span-2">
          <span className="text-ink-500">Schema validation:</span>{" "}
          <span className="font-medium text-ink-900">{sbom.schema_validation}</span>
        </li>
      </ul>
      <p className="mt-1 text-[11px] text-ink-500">
        Source: {sbom.generation_source}. The official CycloneDX 1.7 schema is bundled
        with the library; no network access occurs at export time.
      </p>
    </div>
  );
}

function OmissionsList({ omissions }: { omissions: string[] }) {
  // The omissions list is the bounded place where we
  // explicitly disclaim forbidden claims. Renaming any
  // marker here would change the evidence-honesty
  // contract; the frontend simply renders them.
  const human: Record<string, string> = {
    no_invented_versions: "No invented component versions.",
    no_inferred_dependency_edges:
      "No inferred dependency edges. Only persisted DependencyEdge rows are emitted.",
    no_dependency_graph_completeness_claim_without_positive_proof:
      "No dependency-graph completeness claim unless a positive persisted signal exists.",
    no_clean_or_security_verdict: "No clean / security / certification verdict.",
    no_repository_code_execution: "No repository code is executed.",
    unavailable_provider_data_is_not_converted_to_none:
      "Unavailable provider data is not converted to a 'no findings' verdict.",
    external_provider_evidence_omitted_by_operator:
      "External provider evidence omitted by the operator is recorded as not requested.",
  };
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ink-500">Omissions and limitations</p>
      <ul
        className="mt-1 list-disc space-y-0.5 pl-5 text-ink-700"
        data-testid="preview-omissions"
      >
        {omissions.map((marker) => (
          <li key={marker}>{human[marker] ?? marker}</li>
        ))}
      </ul>
    </div>
  );
}

function LegacyNote({ note }: { note: string }) {
  return (
    <p className="rounded border border-ink-100 bg-ink-50 p-2 text-[11px] text-ink-700">
      {note}
    </p>
  );
}

/**
 * v1.0 human-readable evidence report panel.
 *
 * Renders a card with a "Show evidence report" toggle, a
 * Markdown download button, and a lazy preview of the
 * report's evidence-only summary. The preview fetches
 * the JSON summary on first expand; the download fires
 * a separate request for the Markdown body.
 *
 * The panel is informational. It never claims the
 * report is a security verdict, a certification, or a
 * compliance pass-or-fail.
 */
function EvidenceReportPanel({ scanId }: { scanId: number }) {
  const [expanded, setExpanded] = useState(false);
  const [report, setReport] = useState<EvidenceReportPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [notImpl, setNotImpl] = useState(false);

  // The v1.0 preview fetch must not be aborted by a
  // re-render that only changes ``loading`` or
  // ``notImpl``. A previous version of this effect
  // included those values in the dependency array,
  // which made React call the cleanup
  // (``controller.abort()``) the moment
  // ``setLoading(true)`` ran, so the in-flight fetch was
  // cancelled before ``.then`` could fire. The fix is
  // to track the in-flight controller and the
  // already-fetched flag in refs, and to depend only
  // on ``expanded`` and ``scanId``. The unmount-only
  // effect below cancels the controller when the panel
  // leaves the tree.
  const reportRef = useRef<EvidenceReportPreviewResponse | null>(null);
  const notImplRef = useRef(false);
  const inFlightRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!expanded) return;
    if (reportRef.current || notImplRef.current || inFlightRef.current) return;
    inFlightRef.current = true;
    // Abort any previous in-flight controller (e.g. from
    // a different scanId the user has since navigated
    // away from).
    if (controllerRef.current) {
      controllerRef.current.abort();
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setError(null);
    api
      .previewEvidenceReport(scanId)
      .then((response) => {
        if (controller.signal.aborted) return;
        reportRef.current = response;
        setReport(response);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (isNotImplemented(err)) {
          notImplRef.current = true;
          setNotImpl(true);
          return;
        }
        setError(
          err instanceof ApiClientError ? describeError(err) : "Unable to load the report preview."
        );
      })
      .finally(() => {
        inFlightRef.current = false;
        // The UI infers the loading state from
        // ``report === null``; the ``report`` set in the
        // ``.then`` above is the only place the panel
        // exits the skeleton. No ``setLoading(false)`` is
        // needed because the Skeleton / error / notImpl
        // branching is driven by ``report`` and
        // ``error``.
      });
    // Intentionally no per-render cleanup: a re-render
    // caused by ``setLoading(true)`` or ``setReport(...)``
    // must not cancel the in-flight fetch. The
    // unmount-only effect below handles teardown.
  }, [expanded, scanId]);

  useEffect(() => {
    return () => {
      if (controllerRef.current) controllerRef.current.abort();
    };
  }, []);

  const onDownload = async () => {
    setDownloading(true);
    setDownloadError(null);
    try {
      const body = await api.downloadEvidenceReport(scanId);
      // The download endpoint returns the Markdown body
      // as a string; create a Blob and trigger a
      // browser download. The filename matches the
      // ``Content-Disposition`` the backend returns.
      const blob = new Blob([body], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `lockverity-scan-${scanId}.evidence-report.md`;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch (err) {
      if (isNotImplemented(err)) {
        setDownloadError(
          "Markdown download is not exposed by the API yet."
        );
        return;
      }
      setDownloadError(
        err instanceof ApiClientError ? describeError(err) : "Unable to download the report."
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section
      className="mt-4 rounded-md border border-ink-200 bg-surface p-4 shadow-sm"
      data-testid="evidence-report-panel"
    >
      <div className="flex flex-col gap-1 border-b border-ink-100 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-ink-800">
            Evidence report
          </h2>
          <p className="text-xs text-ink-500">
            Markdown. Evidence-only summary, not a security verdict,
            not a certification, not a compliance pass-or-fail.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={expanded}
            data-testid="evidence-report-toggle"
          >
            {expanded ? "Hide report summary" : "Show report summary"}
          </button>
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1"
            onClick={onDownload}
            disabled={downloading}
            data-testid="evidence-report-download"
            aria-label="Download Markdown report"
          >
            <Download aria-hidden="true" className="h-4 w-4" />
            {downloading ? "Preparing\u2026" : "Download Markdown"}
          </button>
        </div>
      </div>
      {downloadError ? (
        <p
          className="mt-2 rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-900"
          data-testid="evidence-report-download-error"
        >
          {downloadError}
        </p>
      ) : null}
      {expanded ? (
        <div className="mt-3" data-testid="evidence-report-summary">
          {notImpl ? (
            <p className="text-xs text-ink-500">
              The evidence-report endpoint is not exposed by the API yet.
            </p>
          ) : error ? (
            <p
              className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-900"
              data-testid="evidence-report-error"
            >
              {error}
            </p>
          ) : report === null ? (
            <Skeleton rows={4} />
          ) : (
            <EvidenceReportSummary report={report} />
          )}
        </div>
      ) : null}
    </section>
  );
}

/**
 * Renders the bounded evidence-only summary the v1.0
 * preview endpoint returns. The component never claims
 * the report is a security verdict, a certification, or
 * a compliance pass-or-fail. Missing evidence is rendered
 * as &ldquo;missing&rdquo; / &ldquo;no persisted
 * evidence&rdquo; / &ldquo;no persisted edges&rdquo;.
 */
function EvidenceReportSummary({
  report,
}: {
  report: EvidenceReportPreviewResponse;
}) {
  const summary = report.summary;
  const gaps = report.evidence_gaps;
  const coverage = report.evidence_coverage;
  const rel = report.export_relationship;
  return (
    <div className="space-y-3 text-xs" data-testid="evidence-report-body">
      <p
        className="rounded border border-ink-100 bg-ink-50 p-2 text-ink-700"
        data-testid="evidence-report-disclaimer"
      >
        {report.disclaimer}
      </p>
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Scan
        </p>
        <ul className="mt-1 space-y-0.5 text-ink-700">
          <li>
            Status:{" "}
            <span className="font-mono" data-testid="evidence-report-scan-status">
              {report.scan.scan_status}
            </span>
          </li>
          <li>
            Components:{" "}
            <span className="font-mono" data-testid="evidence-report-component-count">
              {summary.component_count}
            </span>
          </li>
          <li>
            Manifests:{" "}
            <span className="font-mono" data-testid="evidence-report-manifest-count">
              {summary.manifest_count}
            </span>
          </li>
        </ul>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Evidence gaps
        </p>
        <ul
          className="mt-1 space-y-0.5 text-ink-700"
          data-testid="evidence-report-gaps"
        >
          <li>
            Missing version:{" "}
            <span className="font-mono">{gaps.missing_version_count}</span>
          </li>
          <li>
            Missing licence evidence:{" "}
            <span className="font-mono">
              {gaps.missing_licence_evidence_count}
            </span>
          </li>
          <li>
            Missing provider evidence:{" "}
            <span className="font-mono">
              {gaps.missing_provider_evidence_count}
            </span>
          </li>
          <li>
            No persisted dependency edges:{" "}
            <span className="font-mono">
              {gaps.no_persisted_edges_count}
            </span>
          </li>
        </ul>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          CycloneDX 1.7 relationship
        </p>
        <ul
          className="mt-1 space-y-0.5 text-ink-700"
          data-testid="evidence-report-cyclonedx"
        >
          <li>
            Eligible:{" "}
            <span className="font-mono">
              {rel.cyclonedx_eligible ? "yes" : "no"}
            </span>
          </li>
          <li>
            Appear in BOM:{" "}
            <span className="font-mono">
              {rel.appears_in_cyclonedx_17_count}
            </span>
          </li>
          <li>
            Dependency relationships emitted:{" "}
            <span className="font-mono">
              {rel.cyclonedx_relationships_emitted_count}
            </span>
          </li>
        </ul>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wide text-ink-500">
          Evidence coverage
        </p>
        <ul
          className="mt-1 space-y-0.5 text-ink-700"
          data-testid="evidence-report-coverage"
        >
          <li>
            Inventory:{" "}
            <span className="font-mono">{coverage.inventory_coverage}</span>
          </li>
          <li>
            Dependency graph:{" "}
            <span className="font-mono">
              {coverage.dependency_graph_coverage}
            </span>
          </li>
          <li>
            Provider:{" "}
            <span className="font-mono">{coverage.provider_coverage}</span>
          </li>
        </ul>
      </div>
      <p className="text-[11px] text-ink-500">
        Not a certification. Not a security verdict. Not a compliance
        pass-or-fail.
      </p>
    </div>
  );
}
