import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, categorizeError } from "@/api/client";
import { ConfirmationDialog } from "@/components/ConfirmationDialog";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { Notification } from "@/components/Notification";
import { PageHeader } from "@/components/PageHeader";

const MAX_FILE_BYTES = 100 * 1024 * 1024; // Mirror backend's archive_max_compressed_bytes default.

export function UploadArchivePage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [successId, setSuccessId] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
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
      setError(new Error(`Archive is larger than ${(MAX_FILE_BYTES / 1024 / 1024).toFixed(0)} MB.`));
      setFile(null);
      return;
    }
    setFile(next);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setConfirmOpen(true);
  }

  async function performUpload() {
    if (!file) return;
    setConfirmOpen(false);
    setSubmitting(true);
    setError(null);
    try {
      // v1.5: the upload route now returns the full
      // ``IntakeResultRead`` shape (repository + scan +
      // workspace + summary). We navigate to the new
      // scan detail page so the reviewer lands on the
      // running scan, not a registry row.
      const result = await api.createRepositoryUpload(file);
      setSuccessId(result.scan.id);
      window.setTimeout(() => navigate(`/scans/${result.scan.id}`), 600);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Upload source archive"
        description="Upload a .zip archive of repository source. Archives are validated before extraction; the bytes are never executed."
        breadcrumbs={[
          { label: "Repositories", to: "/repositories" },
          { label: "Upload" },
        ]}
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <form
          onSubmit={handleSubmit}
          className="card space-y-4 lg:col-span-2"
          aria-label="Upload archive form"
        >
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
                <span className="text-ink-500">({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
              </p>
            ) : null}
          </div>
          {successId !== null ? (
            <Notification
              tone="ok"
              title="Archive uploaded"
              description="Redirecting to the new repository."
            />
          ) : null}
          {error ? (
            <div role="alert">
              {error instanceof ApiClientError ? (
                <ErrorState
                  error={error}
                  title={errorTitleFor(categorizeError(error))}
                />
              ) : (
                <ErrorState error={error} title="Could not upload archive" />
              )}
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/repositories")}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={!file || submitting}
            >
              {submitting ? "Uploading..." : "Upload"}
            </button>
          </div>
        </form>
        <aside className="space-y-3">
          <DataCompletenessNotice
            title="Archive safety"
            tone="warn"
            description="Archives are treated as hostile input. The backend rejects zip slips, absolute paths, path traversal, and decompression bombs. Uploaded archives are not exposed over the API; their contents are parsed inside a private workspace."
          />
          <DataCompletenessNotice
            title="Limits"
            description={`Maximum compressed size: ${(MAX_FILE_BYTES / 1024 / 1024).toFixed(0)} MB. The backend may impose additional limits per the system info endpoint.`}
            tone="muted"
          />
        </aside>
      </div>
      <ConfirmationDialog
        open={confirmOpen}
        title="Upload archive?"
        description={`Upload ${file?.name ?? "this archive"} and register it as a new repository? The file is read once and never executed.`}
        confirmLabel="Upload"
        busy={submitting}
        onConfirm={performUpload}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}

function errorTitleFor(category: ReturnType<typeof categorizeError>): string {
  switch (category) {
    case "validation":
      return "Archive rejected by the server";
    case "rate_limited":
      return "Upload throttled";
    case "provider_unavailable":
      return "Upload service unavailable";
    case "network":
    case "timeout":
      return "Network problem";
    case "duplicate":
      return "An archive with this content is already registered";
    case "forbidden":
    case "unauthorized":
      return "Upload not permitted";
    case "internal_unexpected":
      return "An internal error occurred";
    case "server":
      return "Server error";
    case "cancelled":
      return "Upload cancelled";
    default:
      // v2.1.1: never claim "Unknown error" when the
      // backend has supplied a classified message.
      return "Could not upload archive";
  }
}
