import { Github, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, categorizeError } from "@/api/client";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import {
  intakeErrorDescriptionFor,
  intakeErrorTitleFor,
} from "@/components/intakeErrorFormatting";
import { Notification } from "@/components/Notification";
import { PageHeader } from "@/components/PageHeader";

const REF_PATTERN = /^[A-Za-z0-9._/-]{1,255}$/;
const GITHUB_URL_PATTERN =
  /^https:\/\/github\.com\/[A-Za-z0-9](?:[A-Za-z0-9-_.]{0,38}[A-Za-z0-9])?\/[A-Za-z0-9._-]{1,100}(?:\.git)?\/?$/;

export function NewRepositoryPage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [refError, setRefError] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<number | null>(null);

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
    if (!validate()) return;
    setSubmitting(true);
    try {
      // v2.1.1: submit through the canonical GitHub intake
      // endpoint so the page receives the same
      // ``IntakeResult`` shape, the same classified
      // error taxonomy, and the same ``internal_unexpected``
      // correlation-id envelope as the guided
      // ``/analyze`` page. The legacy
      // ``POST /repositories`` endpoint is retained for
      // backwards compatibility (other clients and
      // scripts) and is wrapped with the same
      // safe-error boundary as defence in depth; see
      // ``backend/app/api/repositories.py``.
      const result = await api.createRepositoryGithub({
        canonical_url: url.trim(),
        requested_ref: ref.trim() || undefined,
      });
      setSuccessId(result.repository.id);
      window.setTimeout(() => navigate(`/scans/${result.scan.id}`), 400);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Add repository"
        description="Register a public GitHub repository for analysis. Lockverity normalizes the URL, retrieves the repository from GitHub, and treats its contents as data that is never executed."
        breadcrumbs={[
          { label: "Repositories", to: "/repositories" },
          { label: "New" },
        ]}
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <form
          onSubmit={handleSubmit}
          className="card space-y-4 lg:col-span-2"
          aria-label="Add repository form"
          noValidate
        >
          <div>
            <label htmlFor="canonical_url" className="label">
              Public GitHub URL
            </label>
            <input
              id="canonical_url"
              name="canonical_url"
              type="url"
              required
              inputMode="url"
              placeholder="https://github.com/owner/repository"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="input mt-1"
              autoComplete="off"
              aria-describedby="url-help"
              aria-invalid={urlError ? "true" : undefined}
            />
            <p id="url-help" className="mt-1 text-xs text-ink-500">
              Only <code>https://github.com/owner/name</code> and the
              <code> .git</code> variant are accepted. Credentials, extra path
              segments, fragments, and query strings are rejected.
            </p>
            {urlError ? (
              <p className="mt-1 text-xs text-rose-700" role="alert">
                {urlError}
              </p>
            ) : null}
          </div>
          <div>
            <label htmlFor="requested_ref" className="label">
              Branch, tag, or commit (optional)
            </label>
            <input
              id="requested_ref"
              name="requested_ref"
              type="text"
              placeholder="main, v1.2.3, or a 40-character SHA"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              className="input mt-1 font-mono"
              autoComplete="off"
              aria-describedby="ref-help"
              aria-invalid={refError ? "true" : undefined}
            />
            <p id="ref-help" className="mt-1 text-xs text-ink-500">
              Defaults to the repository&apos;s default branch. If set, the
              scan will pin to this ref. Tags, branch names, and full 40-char
              SHAs are accepted.
            </p>
            {refError ? (
              <p className="mt-1 text-xs text-rose-700" role="alert">
                {refError}
              </p>
            ) : null}
          </div>
          {successId !== null ? (
            <Notification
              tone="ok"
              title="Repository registered"
              description="Redirecting to the repository detail page."
            />
          ) : null}
          {error ? (
            <div role="alert">
              {error instanceof ApiClientError ? (
                <ErrorState
                  error={error}
                  title={intakeErrorTitleFor(categorizeError(error))}
                  description={intakeErrorDescriptionFor(error)}
                />
              ) : (
                <ErrorState error={error} title="Could not add repository" />
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
              disabled={submitting}
            >
              {submitting ? "Adding..." : "Add repository"}
            </button>
          </div>
        </form>
        <aside className="space-y-3">
          <div className="card flex items-start gap-3">
            <Github aria-hidden="true" className="mt-0.5 h-5 w-5 text-ink-500" />
            <div>
              <p className="text-sm font-semibold text-ink-900">No GitHub token required</p>
              <p className="mt-1 text-xs text-ink-500">
                Public repositories are analysed using unauthenticated requests.
                Lockverity never asks for, stores, or sends a personal access
                token from the browser. Private repositories are not supported.
              </p>
            </div>
          </div>
          <div className="card flex items-start gap-3">
            <ShieldCheck aria-hidden="true" className="mt-0.5 h-5 w-5 text-ink-500" />
            <div>
              <p className="text-sm font-semibold text-ink-900">Repository code is never executed</p>
              <p className="mt-1 text-xs text-ink-500">
                Manifests, lockfiles, and workflow files are treated as
                untrusted text. Lockverity does not run <code>npm install</code>,
                <code> pip install</code>, Makefile targets, or repository
                scripts at any point.
              </p>
            </div>
          </div>
          <DataCompletenessNotice
            title="What happens next"
            description="Submitting this form immediately contacts GitHub to resolve repository metadata and download the requested repository tarball, then creates a queued scan. External evidence providers are not contacted until the queued scan is started, when the operator can select OSV, deps.dev, and OpenSSF Scorecard independently."
            tone="muted"
          />
          <p className="px-1 text-xs text-ink-500">
            See the <Link to="/privacy" className="link">Privacy policy</Link> for the
            coordinates sent to GitHub and optional evidence providers.
          </p>
        </aside>
      </div>
    </>
  );
}
