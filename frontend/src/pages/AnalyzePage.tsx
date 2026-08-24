import { Github, Loader2, ShieldCheck, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, categorizeError } from "@/api/client";
import { usePolling } from "@/api/hooks";
import {
  defaultExternalEvidenceProviderSelection,
  providerSelectionPayload,
} from "@/api/providerSelection";
import type { ExternalEvidenceProviderSelection, Scan } from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { ExternalEvidenceProviderSelector } from "@/components/ExternalEvidenceProviderSelector";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import {
  intakeErrorDescriptionFor,
  intakeErrorTitleFor,
} from "@/components/intakeErrorFormatting";

const REF_PATTERN = /^[A-Za-z0-9._/-]{1,255}$/;
const GITHUB_URL_PATTERN =
  /^https:\/\/github\.com\/[A-Za-z0-9](?:[A-Za-z0-9-_.]{0,38}[A-Za-z0-9])?\/[A-Za-z0-9._-]{1,100}(?:\.git)?\/?$/;
const MAX_FILE_BYTES = 100 * 1024 * 1024;

// Terminal scan statuses. The polling hook stops once the
// newly-created scan enters one of these states; the page
// then refreshes the routed scan detail view to fetch the
// final result.
const TERMINAL_SCAN_STATUSES: ReadonlySet<Scan["status"]> = new Set([
  "completed",
  "partial",
  "failed",
  "cancelled",
]);

/**
 * v1.5 guided intake page.
 *
 * Provides two clearly separated intake methods for the
 * existing backend APIs:
 *
 * - Public GitHub repository: ``POST /api/v1/repositories/github``
 * - Source archive upload:  ``POST /api/v1/repositories/upload``
 *
 * Both endpoints return the full ``IntakeResultRead``
 * shape, which includes the freshly-created scan. The
 * page navigates to ``/scans/{result.scan.id}`` on
 * success and renders the API error envelope on failure.
 * The page reuses the existing ``usePolling`` hook to
 * show the current scan status while the orchestrator
 * processes the intake.
 */
export function AnalyzePage() {
  return (
    <>
      <PageHeader
        title="Analyze"
        description="Register a public GitHub repository or upload a source archive. Lockverity reads the repository evidence and starts a scan automatically."
        breadcrumbs={[{ label: "Analyze" }]}
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <GitHubIntakeCard />
        <UploadIntakeCard />
      </div>
      <div className="mt-4">
        <DataCompletenessNotice
          title="What this page does and does not do"
          description="The page submits to the same intake endpoints the rest of the application uses. The GitHub URL must be public; private repositories are not supported. Uploaded archives are treated as hostile input. Lockverity never executes repository code, dependency installers, build scripts, or uploaded payloads. The resulting report and SBOM are evidence exports, not a security verdict, certification, or compliance pass-or-fail."
          tone="muted"
        />
      </div>
    </>
  );
}

function GitHubIntakeCard() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [refError, setRefError] = useState<string | null>(null);
  const [scanId, setScanId] = useState<number | null>(null);
  const [startError, setStartError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState(false);
  const [providerSelection, setProviderSelection] =
    useState<ExternalEvidenceProviderSelection>(
      defaultExternalEvidenceProviderSelection
    );

  function validate(): boolean {
    let ok = true;
    setUrlError(null);
    setRefError(null);
    if (!url.trim()) {
      setUrlError("A repository URL is required.");
      ok = false;
    } else if (!GITHUB_URL_PATTERN.test(url.trim())) {
      setUrlError(
        "Only public GitHub repository URLs are accepted. Use https://github.com/owner/name or the .git variant. No extra path, query, or fragment."
      );
      ok = false;
    }
    if (ref.trim() && !REF_PATTERN.test(ref.trim())) {
      setRefError("Use a branch, tag, or commit SHA made of letters, digits, '.', '_', '-', or '/'.");
      ok = false;
    }
    return ok;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setStartError(null);
    setStarted(false);
    if (!validate()) return;
    setSubmitting(true);
    try {
      const result = await api.createRepositoryGithub({
        canonical_url: url.trim(),
        ...(ref.trim() ? { requested_ref: ref.trim() } : {}),
      });
      setScanId(result.scan.id);
      // v1.6: the intake route creates a queued scan but
      // does not start execution. The frontend must call
      // ``/scans/{id}/run`` explicitly so the work is
      // scheduled on the local worker.
      setStarting(true);
      try {
        await api.runScan(result.scan.id, providerSelectionPayload(providerSelection));
        setStarted(true);
      } catch (err) {
        setStartError(err);
      } finally {
        setStarting(false);
      }
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  async function retryStart() {
    if (scanId === null || starting || started) return;
    setStartError(null);
    setStarting(true);
    try {
      await api.runScan(scanId, providerSelectionPayload(providerSelection));
      setStarted(true);
    } catch (err) {
      setStartError(err);
    } finally {
      setStarting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="card space-y-4"
      aria-label="Analyze public GitHub repository form"
      noValidate
    >
      <header className="flex items-start gap-3">
        <Github aria-hidden="true" className="mt-0.5 h-5 w-5 text-ink-700" />
        <div>
          <h2 className="text-base font-semibold text-ink-900">
            Public GitHub repository
          </h2>
          <p className="mt-1 text-sm text-ink-600">
            Lockverity resolves the commit SHA, downloads the
            repository tarball through a defensive quarantine,
            and starts a scan.
          </p>
        </div>
      </header>
      <div>
        <label htmlFor="analyze-canonical-url" className="label">
          Public GitHub URL
        </label>
        <input
          id="analyze-canonical-url"
          name="canonical_url"
          type="url"
          required
          inputMode="url"
          placeholder="https://github.com/owner/repository"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="input mt-1"
          autoComplete="off"
          aria-describedby="analyze-url-help"
          aria-invalid={urlError ? "true" : undefined}
          disabled={submitting || scanId !== null}
        />
        <p id="analyze-url-help" className="mt-1 text-xs text-ink-500">
          Only <code>https://github.com/owner/name</code> and the
          <code> .git</code> variant are accepted. Credentials, extra
          path segments, fragments, and query strings are rejected.
        </p>
        {urlError ? (
          <p className="mt-1 text-xs text-rose-700" role="alert">
            {urlError}
          </p>
        ) : null}
      </div>
      <div>
        <label htmlFor="analyze-requested-ref" className="label">
          Branch, tag, or commit (optional)
        </label>
        <input
          id="analyze-requested-ref"
          name="requested_ref"
          type="text"
          placeholder="main, v1.2.3, or a 40-character SHA"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          className="input mt-1 font-mono"
          autoComplete="off"
          aria-describedby="analyze-ref-help"
          aria-invalid={refError ? "true" : undefined}
          disabled={submitting || scanId !== null}
        />
        <p id="analyze-ref-help" className="mt-1 text-xs text-ink-500">
          Defaults to the repository&apos;s default branch. When set,
          the scan pins to this ref.
        </p>
        {refError ? (
          <p className="mt-1 text-xs text-rose-700" role="alert">
            {refError}
          </p>
        ) : null}
      </div>
      <DataCompletenessNotice
        title="GitHub retrieval happens before analysis"
        description="Submitting this repository contacts GitHub to resolve repository metadata and the requested ref, then downloads the repository tarball. GitHub retrieval is required for a GitHub repository scan and is separate from the optional evidence providers below."
        tone="muted"
      />
      <ExternalEvidenceProviderSelector
        idPrefix="analyze-github-provider"
        value={providerSelection}
        onChange={setProviderSelection}
        disabled={submitting || starting || started}
      />
      {error ? (
        <div role="alert">
          {error instanceof ApiClientError ? (
            <ErrorState
              error={error}
              title={intakeErrorTitleFor(categorizeError(error))}
              description={intakeErrorDescriptionFor(error)}
            />
          ) : (
            <ErrorState
              error={error}
              title="Could not register the repository"
            />
          )}
        </div>
      ) : null}
      {scanId !== null && starting ? (
        <div
          className="rounded-md border border-accent-200 bg-accent-50 p-3 text-sm text-accent-900"
          role="status"
          aria-live="polite"
        >
          <p className="font-semibold">
            Repository registered. Starting scan #{scanId}&hellip;
          </p>
        </div>
      ) : null}
      {scanId !== null && started ? (
        <ScanStatusPanel
          scanId={scanId}
          onOpenScan={(id) => navigate(`/scans/${id}`)}
        />
      ) : null}
      {scanId !== null && startError ? (
        <div
          className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
          role="alert"
          aria-live="assertive"
        >
          <p className="font-semibold">
            Repository intake completed, but scan execution did not start.
          </p>
          <p className="text-xs text-amber-800">
            The repository and the queued scan are persisted. The local
            worker did not accept the run request.
          </p>
          {startError instanceof ApiClientError ? (
            <ErrorState
              error={startError}
              title="Could not start the scan"
              description={intakeErrorDescriptionFor(startError)}
            />
          ) : (
            <ErrorState
              error={startError}
              title="Could not start the scan"
            />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate(`/scans/${scanId}`)}
            >
              Open scan
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={retryStart}
              disabled={starting}
            >
              {starting ? "Retrying&hellip;" : "Retry start"}
            </button>
          </div>
        </div>
      ) : null}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            setUrl("");
            setRef("");
            setUrlError(null);
            setRefError(null);
            setError(null);
            setScanId(null);
            setProviderSelection(defaultExternalEvidenceProviderSelection());
          }}
          disabled={submitting}
        >
          Reset
        </button>
        <button
          type="submit"
          className="btn-primary"
          disabled={submitting || scanId !== null}
        >
          {submitting ? (
            <>
              <Loader2 aria-hidden="true" className="mr-1 inline h-4 w-4 animate-spin" />
              Registering&hellip;
            </>
          ) : (
            "Analyze repository"
          )}
        </button>
      </div>
    </form>
  );
}

function UploadIntakeCard() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [scanId, setScanId] = useState<number | null>(null);
  const [startError, setStartError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [providerSelection, setProviderSelection] =
    useState<ExternalEvidenceProviderSelection>(
      defaultExternalEvidenceProviderSelection
    );
  const inputRef = useRef<HTMLInputElement | null>(null);

  function onSelect(next: File | null) {
    setError(null);
    if (!next) {
      setFile(null);
      return;
    }
    if (!/^[^/\\]+\.zip$/i.test(next.name)) {
      setError(new Error("Only .zip archives are accepted."));
      setFile(null);
      return;
    }
    if (next.size > MAX_FILE_BYTES) {
      setError(
        new Error(
          `Archive is larger than ${(MAX_FILE_BYTES / 1024 / 1024).toFixed(0)} MB.`
        )
      );
      setFile(null);
      return;
    }
    setFile(next);
  }

  async function retryStart() {
    if (scanId === null || starting || started) return;
    setStartError(null);
    setStarting(true);
    try {
      await api.runScan(scanId, providerSelectionPayload(providerSelection));
      setStarted(true);
    } catch (err) {
      setStartError(err);
    } finally {
      setStarting(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError(new Error("Select a .zip archive first."));
      return;
    }
    setSubmitting(true);
    setError(null);
    setStartError(null);
    setStarted(false);
    try {
      // v1.5: the upload route returns the full
      // ``IntakeResultRead`` shape. v1.6: the page then
      // calls ``/scans/{id}/run`` to schedule execution
      // on the local worker.
      const result = await api.createRepositoryUpload(file);
      setScanId(result.scan.id);
      setStarting(true);
      try {
        await api.runScan(result.scan.id, providerSelectionPayload(providerSelection));
        setStarted(true);
      } catch (err) {
        setStartError(err);
      } finally {
        setStarting(false);
      }
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="card space-y-4"
      aria-label="Analyze uploaded source archive form"
    >
      <header className="flex items-start gap-3">
        <Upload aria-hidden="true" className="mt-0.5 h-5 w-5 text-ink-700" />
        <div>
          <h2 className="text-base font-semibold text-ink-900">
            Source archive upload
          </h2>
          <p className="mt-1 text-sm text-ink-600">
            Upload a <code>.zip</code> archive of repository
            source. Entries are validated before extraction; the
            bytes are never executed.
          </p>
        </div>
      </header>
      <div
        className={`flex flex-col items-center justify-center gap-3 rounded-md border-2 border-dashed p-6 text-center ${
          dragOver ? "border-accent-400 bg-accent-50" : "border-ink-200"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const dropped = e.dataTransfer.files?.[0];
          if (dropped) onSelect(dropped);
        }}
      >
        <p className="text-sm text-ink-700">
          Drag a <code>.zip</code> archive here, or
        </p>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => inputRef.current?.click()}
          disabled={submitting || scanId !== null}
        >
          Choose file
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".zip,application/zip"
          className="sr-only"
          onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <p className="mt-2 text-sm text-ink-700">
            Selected: <span className="font-mono">{file.name}</span>{" "}
            <span className="text-ink-500">
              ({(file.size / 1024 / 1024).toFixed(1)} MB)
            </span>
          </p>
        ) : null}
      </div>
      <ExternalEvidenceProviderSelector
        idPrefix="analyze-archive-provider"
        value={providerSelection}
        onChange={setProviderSelection}
        openssfApplicable={false}
        disabled={submitting || starting || started}
      />
      {error ? (
        <div role="alert">
          {error instanceof ApiClientError ? (
            <ErrorState
              error={error}
              title={intakeErrorTitleFor(categorizeError(error))}
              description={intakeErrorDescriptionFor(error)}
            />
          ) : (
            <ErrorState
              error={error}
              title="Could not upload the archive"
            />
          )}
        </div>
      ) : null}
      {scanId !== null && starting ? (
        <div
          className="rounded-md border border-accent-200 bg-accent-50 p-3 text-sm text-accent-900"
          role="status"
          aria-live="polite"
        >
          <p className="font-semibold">
            Archive registered. Starting scan #{scanId}&hellip;
          </p>
        </div>
      ) : null}
      {scanId !== null && started ? (
        <ScanStatusPanel
          scanId={scanId}
          onOpenScan={(id) => navigate(`/scans/${id}`)}
        />
      ) : null}
      {scanId !== null && startError ? (
        <div
          className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
          role="alert"
          aria-live="assertive"
        >
          <p className="font-semibold">
            Archive intake completed, but scan execution did not start.
          </p>
          <p className="text-xs text-amber-800">
            The repository and the queued scan are persisted. The local
            worker did not accept the run request.
          </p>
          {startError instanceof ApiClientError ? (
            <ErrorState
              error={startError}
              title="Could not start the scan"
              description={intakeErrorDescriptionFor(startError)}
            />
          ) : (
            <ErrorState
              error={startError}
              title="Could not start the scan"
            />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate(`/scans/${scanId}`)}
            >
              Open scan
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={retryStart}
              disabled={starting}
            >
              {starting ? "Retrying&hellip;" : "Retry start"}
            </button>
          </div>
        </div>
      ) : null}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            setFile(null);
            setError(null);
            setScanId(null);
            setProviderSelection(defaultExternalEvidenceProviderSelection());
          }}
          disabled={submitting}
        >
          Reset
        </button>
        <button
          type="submit"
          className="btn-primary"
          disabled={!file || submitting || scanId !== null}
        >
          {submitting ? (
            <>
              <Loader2 aria-hidden="true" className="mr-1 inline h-4 w-4 animate-spin" />
              Uploading&hellip;
            </>
          ) : (
            "Analyze archive"
          )}
        </button>
      </div>
    </form>
  );
}

/**
 * Inline status panel rendered after intake succeeds. The
 * panel polls the scan using the same ``usePolling`` hook
 * the scan detail page uses, then offers a button to open
 * the scan detail page. Duplicate submission is already
 * blocked at the parent via the disabled state on the
 * submit button.
 */
function ScanStatusPanel({
  scanId,
  onOpenScan,
}: {
  scanId: number;
  onOpenScan: (id: number) => void;
}) {
  const { data: scan, error: pollError } = usePolling<Scan>(
    (signal) => api.getScan(scanId, signal ? { signal } : undefined),
    [scanId],
    {
      intervalMs: 2000,
      maxPolls: 600,
      isTerminal: (value) => TERMINAL_SCAN_STATUSES.has(value.status),
    }
  );

  // When the scan reaches a terminal state, the reviewer's
  // expected action is to open the scan detail page so the
  // bounded terminal-state UI is rendered. We expose a
  // button rather than auto-navigating so the reviewer
  // can choose to wait for additional context.
  useEffect(() => {
    // No-op effect retained for future terminal-state UX
    // such as a "View report" link. The polling hook
    // already stops on terminal status; we only need
    // this to ensure the effect re-runs when the scan
    // identity changes.
  }, [scanId]);

  if (pollError) {
    return (
      <ErrorState
        error={pollError}
        title="Could not read the scan status"
      />
    );
  }

  return (
    <div
      className="space-y-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <ShieldCheck aria-hidden="true" className="h-4 w-4" />
        <p className="font-semibold">
          Repository registered. Scan #{scanId} started.
        </p>
      </div>
      {scan ? (
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-emerald-700">
            Status
          </span>
          <StatusBadge status={scan.status} />
        </div>
      ) : (
        <p className="text-xs text-emerald-700">Reading scan status&hellip;</p>
      )}
      <div className="flex justify-end">
        <button
          type="button"
          className="btn-primary"
          onClick={() => onOpenScan(scanId)}
        >
          Open scan
        </button>
      </div>
    </div>
  );
}
