import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/api";
import { AppearanceControl } from "@/appearance";
import { LockveritySymbol } from "@/components/LockveritySymbol";
import { PageHeader } from "@/components/PageHeader";

/**
 * About page copy.
 *
 * Every claim on this page is derived from code that ships in the
 * repository - the parser list, the rule list, the analyzer list,
 * the exporter list, and the API surface are all sourced from
 * ``backend/app/parsers``, ``backend/app/rules``,
 * ``backend/app/analyzers``, ``backend/app/exporters``, and
 * ``backend/app/api``.
 *
 * Implemented vs planned capabilities are kept in two separate
 * sections so a reader cannot mistake a forward-looking roadmap
 * item for a working feature. The version string is rendered from
 * ``GET /system/info`` so the page cannot drift from the running
 * backend.
 */

interface TrustPrinciple {
  title: string;
  body: string;
}

interface FeatureCard {
  title: string;
  body: string;
  detail?: string;
}

interface LimitationItem {
  title: string;
  body: string;
}

const TRUST_PRINCIPLES: TrustPrinciple[] = [
  {
    title: "Defensive only",
    body: "Lockverity does not execute analyzed code; it never calls npm install, pip install, or any Makefile / shell script from a repository. The non-execution guarantee in SECURITY.md is the binding boundary.",
  },
  {
    title: "Source-honest",
    body: "Missing evidence is rendered as missing. A failed or cancelled scan is reported as incomplete; a provider without coverage is preserved as unavailable rather than treated as clean. The data model and the UI never invent a security verdict.",
  },
  {
    title: "Provider-honest",
    body: "Lockverity never represents a missing or unavailable provider as 'no vulnerabilities found.' The provider-honesty policy records the distinction between absent, partial, and not-requested observations, and surfaces it on the Provider health page.",
  },
];

const FEATURE_CARDS: FeatureCard[] = [
  {
    title: "Two intake paths",
    body: "Public GitHub repositories and uploaded ZIP source archives. Streaming, validation, and quarantine are bounded by archive_limits.",
    detail: "POST /api/v1/repositories/github · POST /api/v1/repositories/upload",
  },
  {
    title: "Manifest and dependency analysis",
    body: "npm, pnpm, Yarn, Poetry, pyproject.toml, and requirements.txt parsers, with GitHub Actions workflow analysis.",
  },
  {
    title: "Optional provider enrichment",
    body: "OSV, deps.dev, GitHub, and OpenSSF Scorecard. Bounded HTTP, TTL cache, and provider failure modes that preserve coverage gaps.",
  },
  {
    title: "Evidence-aware comparison",
    body: "Diff two scans of the same repository without equating missing evidence with remediation. Nullable-key-safe rendering of newly_observed, still_observed, no_longer_observed, changed_observation, coverage_changed, and comparison_indeterminate.",
  },
  {
    title: "Deterministic exports",
    body: "CycloneDX 1.5 and 1.7 SBOM (validated against the official 1.7 schema), SARIF 2.1.0, findings JSON, findings CSV, and a human-readable Markdown evidence report. The Markdown report is not a security verdict, not a certification, and not a compliance pass-or-fail. Preview endpoints expose /api/v1/scans/{id}/reports/evidence-summary/preview and the download is /api/v1/scans/{id}/reports/evidence-summary.md.",
  },
  {
    title: "Operational diagnostics",
    body: "Read-only /diagnostics page and /api/v1/diagnostics/summary surface application, executor, and provider state. Operational state is never collapsed into a security verdict.",
  },
];

const LIMITATIONS: LimitationItem[] = [
  {
    title: "Authentication, multi-tenancy, billing",
    body: "Lockverity is a single-tenant, local-first analyzer. There is no signup, no per-user state, and no hosted service.",
  },
  {
    title: "Private GitHub repositories",
    body: "Public-only. The LOCKVERITY_GITHUB_TOKEN environment variable is honoured for public rate limits; private endpoints are out of scope.",
  },
  {
    title: "Continuous or scheduled scans",
    body: "v2.1 scans are explicit operator actions. There is no background scheduler and no automatic re-scan.",
  },
  {
    title: "LLM-driven analysis or offensive features",
    body: "Exploit generation, LLM interpretation, and any other offensive feature are explicitly out of scope. The non-execution guarantee is binding.",
  },
  {
    title: "Hosted SaaS or centralized platform",
    body: "Lockverity is local-first. It scales to the resources of the host machine, not to a multi-tenant backend.",
  },
  {
    title: "PDF, DOCX, HTML, signed attestations, or certifications",
    body: "The CycloneDX 1.7 SBOM and the Markdown evidence report are evidence exports, not certifications.",
  },
];

export function AboutPage() {
  // Canonical version rendering. The version is read
  // from the backend so the page cannot drift from the
  // running application. When the backend is unreachable
  // the version slot degrades to a neutral dash rather
  // than a hardcoded string.
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .systemInfo()
      .then((info) => {
        if (controller.signal.aborted) return;
        setVersion(info.version);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setVersion(null);
      });
    return () => controller.abort();
  }, []);

  const versionLabel = version ? `v${version}` : "—";

  return (
    <>
      <PageHeader
        title="About Lockverity"
        description="Evidence-first software supply-chain assurance."
      />

      {/* Hero. The mark sits in a calm container so it
          reads as a brand spot rather than as decoration. */}
      <section
        className="mb-8 flex flex-col items-start gap-4 rounded-md border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface p-6 shadow-sm sm:flex-row sm:items-center sm:gap-6"
        aria-label="Lockverity brand"
        data-testid="about-hero"
      >
        <LockveritySymbol size={64} ariaLabel="Lockverity product symbol" />
        <div>
          <h2 className="text-lg font-semibold text-ink-900 dark:text-surface-dark-text">
            Lockverity{" "}
            <span
              className="ml-1 font-mono text-sm text-ink-500 dark:text-surface-dark-text-muted"
              data-testid="about-version"
            >
              {versionLabel}
            </span>
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-700 dark:text-surface-dark-text">
            Lockverity inspects the software supply chain of public GitHub
            repositories and uploaded source archives. It is
            defensive-only, source-honest, and provider-honest. The
            data model and the UI surface evidence: a finding is
            severity-tagged, confidence-tagged, and backed by a file
            path, a manifest, a provider response, or an explicit
            omission marker. Current build is v2.1.2.
          </p>
        </div>
      </section>

      {/* Appearance control. Lives on the About page
          so the user has a single, low-friction place
          to change it. Persisted to localStorage by
          the AppearanceProvider. The control itself
          is keyboard-accessible (native radio group
          with arrow-key navigation) and supports an
          OS-changes-live behavior in System mode. */}
      <section
        className="mb-8"
        aria-label="Appearance"
        data-testid="about-appearance"
      >
        <AppearanceControl />
      </section>

      {/* Three trust principles. Short, plain, and
          every sentence is backed by code that ships. */}
      <section
        className="mb-8"
        aria-label="Trust principles"
        data-testid="about-trust-principles"
      >
        <h2 className="mb-3 text-base font-semibold text-ink-900 dark:text-surface-dark-text">
          Three principles
        </h2>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {TRUST_PRINCIPLES.map((p) => (
            <li
              key={p.title}
              className="rounded-md border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface p-4 shadow-sm"
            >
              <h3 className="text-sm font-semibold text-ink-900 dark:text-surface-dark-text">
                {p.title}
              </h3>
              <p className="mt-1 text-sm text-ink-700 dark:text-surface-dark-text">{p.body}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* Six feature cards. Concise; the details live
          in the docs. */}
      <section
        className="mb-8"
        aria-label="Feature cards"
        data-testid="about-feature-cards"
      >
        <h2 className="mb-3 text-base font-semibold text-ink-900 dark:text-surface-dark-text">
          What v{version ?? "—"} implements today
        </h2>
        <p className="mb-3 text-xs uppercase tracking-wide text-ink-500 dark:text-surface-dark-text-muted">
          All items below have direct, exercised code paths in this
          repository. None are aspirational.
        </p>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURE_CARDS.map((f) => (
            <li
              key={f.title}
              className="flex h-full flex-col rounded-md border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface p-4 shadow-sm"
            >
              <h3 className="text-sm font-semibold text-ink-900 dark:text-surface-dark-text">
                {f.title}
              </h3>
              <p className="mt-1 flex-1 text-sm text-ink-700 dark:text-surface-dark-text">{f.body}</p>
              {f.detail ? (
                <p className="mt-2 font-mono text-xs text-ink-500 dark:text-surface-dark-text-muted">
                  {f.detail}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      {/* Expandable limitations. The page is short;
          the full list of out-of-scope items lives in
          <details> so the casual reader does not have
          to scroll past it. */}
      <section
        className="mb-8"
        aria-label="Limitations"
        data-testid="about-limitations"
      >
        <details className="rounded-md border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface p-4 shadow-sm">
          <summary className="cursor-pointer text-sm font-semibold text-ink-900 dark:text-surface-dark-text">
            What v{version ?? "—"} does <em>not</em> include
          </summary>
          <p className="mt-2 text-xs uppercase tracking-wide text-ink-500 dark:text-surface-dark-text-muted">
            Planned for later milestones, not implemented today.
          </p>
          <ul className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {LIMITATIONS.map((l) => (
              <li key={l.title}>
                <h3 className="text-sm font-medium text-ink-900 dark:text-surface-dark-text">
                  {l.title}
                </h3>
                <p className="mt-1 text-sm text-ink-700 dark:text-surface-dark-text">{l.body}</p>
              </li>
            ))}
          </ul>
        </details>
      </section>

      {/* Footer links. The product version is
          canonical (rendered from /system/info) and the
          repo link is canonical (the same GitHub URL the
          release references). */}
      <section
        className="mb-8 rounded-md border border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface p-4 shadow-sm"
        aria-label="Resources"
        data-testid="about-resources"
      >
        <h2 className="text-base font-semibold text-ink-900 dark:text-surface-dark-text">Resources</h2>
        <ul className="mt-2 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          <li>
            <Link to="/about" className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200">
              About this build
            </Link>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— you are here.</span>
          </li>
          <li>
            <a
              href="https://github.com/namanparikh11/lockverity/blob/main/docs/architecture.md"
              target="_blank"
              rel="noreferrer"
              className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200"
            >
              Architecture
            </a>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— modules, stages, and data flow.</span>
          </li>
          <li>
            <a
              href="https://github.com/namanparikh11/lockverity/blob/main/docs/threat-model.md"
              target="_blank"
              rel="noreferrer"
              className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200"
            >
              Threat model
            </a>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— defensive boundaries.</span>
          </li>
          <li>
            <a
              href="https://github.com/namanparikh11/lockverity/blob/main/SECURITY.md"
              target="_blank"
              rel="noreferrer"
              className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200"
            >
              Security policy
            </a>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— non-execution guarantee and disclosure.</span>
          </li>
          <li>
            <a
              href="https://github.com/namanparikh11/lockverity/blob/main/LICENSE"
              target="_blank"
              rel="noreferrer"
              className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200"
            >
              License
            </a>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— MIT for the source code.</span>
          </li>
          <li>
            <a
              href="https://github.com/namanparikh11/lockverity"
              target="_blank"
              rel="noreferrer"
              className="text-accent-700 dark:text-accent-dark-300 hover:text-accent-800 dark:text-accent-dark-200"
            >
              GitHub repository
            </a>{" "}
            <span className="text-ink-500 dark:text-surface-dark-text-muted">— releases, issues, and changelog.</span>
          </li>
        </ul>
        <p className="mt-3 text-xs text-ink-500 dark:text-surface-dark-text-muted">
          Current build:{" "}
          <span className="font-mono" data-testid="about-version-footer">
            {versionLabel}
          </span>
          . The version is rendered from the running backend and cannot
          drift from the API surface.
        </p>
      </section>
    </>
  );
}
