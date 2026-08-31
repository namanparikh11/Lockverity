import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  AlertOctagon,
  EyeOff,
  FileSearch,
  PlayCircle,
  Sparkles,
  XCircle,
} from "lucide-react";

import { api } from "@/api/api";
import type { Scan } from "@/api/types";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import {
  DEMO_SEEDED_DATASET,
  hasDemoData,
  resolveDemoScenarios,
  type DemoScenarios,
} from "@/pages/demoDataset";

/**
 * In-app demo walkthrough.
 *
 * The page resolves the seeded demo dataset at runtime: it asks
 * the API for scans carrying the explicit ``seeded_dataset``
 * provenance marker and constructs every link from the resolved
 * records' actual ids. It never assumes that a particular
 * numeric id (1, 3, 4, ...) is a demo scan — a real user scan
 * can occupy any id — and when no marker-carrying rows exist it
 * renders a bounded neutral state instead of dead links.
 *
 * The copy describes product features, not internal
 * development milestones, and contains no development-only
 * instructions (ports, dev servers, loader scripts).
 */
export function DemoHomePage() {
  const [scenarios, setScenarios] = useState<DemoScenarios | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const page = await api.listAllScans(
          { seeded_dataset: DEMO_SEEDED_DATASET, page_size: 50 },
          { signal: controller.signal }
        );
        if (controller.signal.aborted) return;
        // resolveDemoScenarios re-checks the marker client-side
        // so an unfiltered response can never leak real scans
        // into the demo scenarios.
        setScenarios(resolveDemoScenarios(page.items as Scan[]));
      } catch (err) {
        if (!controller.signal.aborted) setError(err);
      }
    })();
    return () => controller.abort();
  }, []);

  return (
    <>
      <PageHeader
        title="Local demo"
        description="A guided walkthrough of the seeded demo dataset. Each step opens a live page; the live pages are the canonical evidence."
        breadcrumbs={[{ label: "Demo" }]}
      />
      <div className="space-y-6">
        {error !== null ? (
          <div className="card">
            <ErrorState error={error} />
          </div>
        ) : scenarios === null ? (
          <div className="card">
            <LoadingState label="Resolving demo dataset" />
          </div>
        ) : hasDemoData(scenarios) ? (
          <DemoDatasetSections scenarios={scenarios} />
        ) : (
          <DemoAbsentSection />
        )}

        <section aria-labelledby="demo-beyond" className="card">
          <h2
            id="demo-beyond"
            className="flex items-center gap-2 text-base font-semibold text-ink-900"
          >
            <Sparkles aria-hidden="true" className="h-4 w-4 text-accent-700" />
            Analyze your own repository
          </h2>
          <p className="mt-2 text-sm text-ink-700">
            The guided Analyze page lets you analyze a public GitHub
            repository or upload a <code>.zip</code> source archive
            alongside any existing data. It reuses the same intake
            endpoints the rest of the application uses; it does not
            duplicate business logic in the frontend.
          </p>
          <ul className="mt-2 ml-5 list-disc space-y-1 text-sm text-ink-700">
            <li>
              <Link
                to="/analyze"
                className="font-mono text-accent-700 hover:text-accent-800"
              >
                /analyze
              </Link>{" "}
              &mdash; Public GitHub URL or ZIP upload, navigate to the new scan.
            </li>
            <li>
              Public repositories only. Lockverity never asks for,
              stores, or sends a personal access token from the
              browser.
            </li>
            <li>
              Archives are treated as hostile input. Entries are
              validated before extraction; uploaded code is never
              executed.
            </li>
          </ul>
        </section>

        <DataCompletenessNotice
          title="This is a demo, not a hosted service"
          description="The application has no user accounts or login authentication, no multi-tenancy, and no hosted control plane. Lockverity makes no provider calls while you explore the seeded dataset. Lockverity is a local-first desktop application. Deployment and repository guidance is documented in the project documentation."
          tone="muted"
        />
      </div>
    </>
  );
}

function DemoDatasetSections({ scenarios }: { scenarios: DemoScenarios }) {
  const scenarioRows: Array<{ label: string; scan: Scan | null }> = [
    { label: "Completed demo scan", scan: scenarios.completed },
    { label: "Partial demo scan", scan: scenarios.partial },
    { label: "Failed demo scan", scan: scenarios.failed },
    { label: "Cancelled demo scan", scan: scenarios.cancelled },
  ];
  const presentScenarios = scenarioRows.filter(
    (row): row is { label: string; scan: Scan } => row.scan !== null
  );
  return (
    <>
      <section aria-labelledby="demo-dataset-status" className="card">
        <h2
          id="demo-dataset-status"
          className="flex items-center gap-2 text-base font-semibold text-ink-900"
        >
          <Sparkles aria-hidden="true" className="h-4 w-4 text-accent-700" />
          Demo dataset status
        </h2>
        <p className="mt-2 text-sm text-ink-700">
          The seeded scans below are <strong>synthetic persisted
          evidence</strong> identified by an explicit provenance
          marker, and their ids are resolved from your local data
          at runtime:
        </p>
        <ul className="mt-3 space-y-2 text-sm text-ink-700">
          {presentScenarios.map(({ label, scan }) => (
            <DemoScenarioRow key={label} label={label} scan={scan} />
          ))}
        </ul>
        <ul className="mt-4 ml-5 list-disc space-y-1 text-sm text-ink-700">
          <li>
            The fixture repository is the canonical{" "}
            <code>example-org/lockverity-fixture</code>; the resolved
            commit SHA is the <code>deadbeef</code> repeated fill.
          </li>
          <li>
            Every package name is from the documented six-name set:{" "}
            <code>alpha</code>, <code>beta</code>, <code>gamma</code>,{" "}
            <code>left-pad</code>, <code>right-pad</code>, <code>stay</code>.
          </li>
          <li>
            <strong>No provider calls were made when the dataset was
            generated.</strong>{" "}
            The provider observations on the demo scans are explicit
            seeded rows, not live provider responses.
          </li>
          <li>
            <strong>No analyzed repository code is executed.</strong> The
            scanner never runs installers, build scripts, setup
            scripts, or Makefiles from the analyzed source.
          </li>
        </ul>
      </section>

      <section aria-labelledby="demo-flow" className="card">
        <h2
          id="demo-flow"
          className="flex items-center gap-2 text-base font-semibold text-ink-900"
        >
          <PlayCircle aria-hidden="true" className="h-4 w-4 text-accent-700" />
          Reviewer flow
        </h2>
        <p className="mt-2 text-sm text-ink-700">
          Open the pages below in order. Each one surfaces one product
          surface; the live data is the canonical evidence behind
          every claim.
        </p>
        <ol className="mt-3 ml-5 list-decimal space-y-2 text-sm text-ink-700">
          <li>
            <Link to="/" className="font-mono text-accent-700 hover:text-accent-800">
              /
            </Link>{" "}
            — <strong>Scan list</strong>. The seeded demo scans with the
            demo-dataset notice above them. Status badges: emerald
            (completed), amber (partial), rose (failed), neutral
            (cancelled).
          </li>
          {scenarios.completed !== null ? (
            <>
              <li>
                <Link
                  to={`/scans/${scenarios.completed.id}/dependencies`}
                  className="font-mono text-accent-700 hover:text-accent-800"
                >
                  /scans/{scenarios.completed.id}/dependencies
                </Link>{" "}
                — <strong>Dependency Explorer</strong>. Evidence filter
                fields and facet counts. Type <code>left</code> in the
                search box to narrow the table to{" "}
                <code>left-pad</code>; click <em>View evidence</em> to
                open the per-component evidence drilldown.
              </li>
              <li>
                <Link
                  to={`/scans/${scenarios.completed.id}/exports`}
                  className="font-mono text-accent-700 hover:text-accent-800"
                >
                  /scans/{scenarios.completed.id}/exports
                </Link>{" "}
                — <strong>Export Center</strong>. Click{" "}
                <em>Show CycloneDX 1.7 evidence preview</em> for the
                eligibility verdict, then click{" "}
                <em>Show report summary</em> for the Markdown evidence
                report preview. Both download flows are deterministic.
              </li>
            </>
          ) : null}
          {scenarios.failed !== null ? (
            <li>
              <Link
                to={`/scans/${scenarios.failed.id}/exports`}
                className="font-mono text-accent-700 hover:text-accent-800"
              >
                /scans/{scenarios.failed.id}/exports
              </Link>{" "}
              — <strong>Failed scan bounded empty state</strong>. No
              components, <code>not_applicable</code> coverage, the
              bounded &ldquo;no components recorded&rdquo; wording. The
              UI does <strong>not</strong> fabricate a clean verdict for
              a failed scan.
            </li>
          ) : null}
          {scenarios.cancelled !== null ? (
            <li>
              <Link
                to={`/scans/${scenarios.cancelled.id}/exports`}
                className="font-mono text-accent-700 hover:text-accent-800"
              >
                /scans/{scenarios.cancelled.id}/exports
              </Link>{" "}
              — <strong>Cancelled scan bounded empty state</strong>.
              Same bounded <code>not_applicable</code> empty state.
            </li>
          ) : null}
          <li>
            <Link to="/about" className="font-mono text-accent-700 hover:text-accent-800">
              /about
            </Link>{" "}
            — <strong>About page + product boundaries</strong>. The
            evidence report scope, the product boundaries section,
            the provider-honesty and non-execution guarantees.
          </li>
        </ol>
      </section>

      <section aria-labelledby="demo-look-for" className="card">
        <h2
          id="demo-look-for"
          className="flex items-center gap-2 text-base font-semibold text-ink-900"
        >
          <FileSearch aria-hidden="true" className="h-4 w-4 text-accent-700" />
          What to look for
        </h2>
        <ul className="mt-2 ml-5 list-disc space-y-1 text-sm text-ink-700">
          <li>
            The evidence filter fields and facet counts on the
            Dependency Explorer; the vocabulary itself is
            evidence-honest.
          </li>
          <li>
            The per-component evidence drilldown with its sections and
            the evidence-honesty markers list at the bottom.
          </li>
          <li>
            The CycloneDX 1.7 evidence preview surfaces the eligibility
            verdict <em>before</em> the SBOM is downloaded.
          </li>
          <li>
            The Markdown evidence report with the bounded disclaimer:{" "}
            <em>
              &ldquo;This is an evidence report, not a security verdict, not
              a certification, and not a compliance pass-or-fail.&rdquo;
            </em>
          </li>
          <li>
            The bounded <code>not_applicable</code> empty state on the
            failed and cancelled demo scans; the UI does not fabricate
            a clean verdict for a failed or cancelled scan.
          </li>
          <li>
            The demo-dataset notice above the scan list; the notice
            does not appear on real repositories.
          </li>
        </ul>
      </section>

      <section aria-labelledby="demo-not-claim" className="card">
        <h2
          id="demo-not-claim"
          className="flex items-center gap-2 text-base font-semibold text-ink-900"
        >
          <AlertOctagon aria-hidden="true" className="h-4 w-4 text-rose-700" />
          What not to claim
        </h2>
        <ul className="mt-2 ml-5 list-disc space-y-2 text-sm text-ink-700">
          <li>
            <EyeOff aria-hidden="true" className="-mt-1 mr-1 inline h-4 w-4 text-ink-500" />
            Do <strong>not</strong> call the report{" "}
            <em>&ldquo;a security scan result&rdquo;</em>. It is an evidence report.
          </li>
          <li>
            <XCircle aria-hidden="true" className="-mt-1 mr-1 inline h-4 w-4 text-ink-500" />
            Do <strong>not</strong> call the SBOM{" "}
            <em>&ldquo;a certified bill of materials&rdquo;</em>. It is evidence;
            the schema validates, but the export is not signed and
            does not carry a trust assertion.
          </li>
          <li>
            <XCircle aria-hidden="true" className="-mt-1 mr-1 inline h-4 w-4 text-ink-500" />
            Do <strong>not</strong> say{" "}
            <em>&ldquo;the dependency graph is complete&rdquo;</em> unless a
            positive persisted signal exists. The coverage helper
            returns <code>partial</code> for the demo dataset.
          </li>
          <li>
            <XCircle aria-hidden="true" className="-mt-1 mr-1 inline h-4 w-4 text-ink-500" />
            Do <strong>not</strong> say{" "}
            <em>&ldquo;no findings&rdquo;</em> because a provider was unavailable,
            rate-limited, or skipped. The demo&rsquo;s provider
            observations are explicit <code>AVAILABLE</code> or{" "}
            <code>RATE_LIMITED</code> rows, never silent omissions.
          </li>
          <li>
            <XCircle aria-hidden="true" className="-mt-1 mr-1 inline h-4 w-4 text-ink-500" />
            Do <strong>not</strong> call the demo dataset a real
            provider scan result. The fixture repository is
            <code>example-org/lockverity-fixture</code> and the
            resolved commit SHA is the <code>deadbeef</code> fill.
          </li>
        </ul>
      </section>
    </>
  );
}

function DemoScenarioRow({ label, scan }: { label: string; scan: Scan }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <StatusBadge status={scan.status} />
      <span>
        <strong>{label}</strong> — scan{" "}
        <Link
          to={`/scans/${scan.id}`}
          className="font-mono text-accent-700 hover:text-accent-800"
        >
          #{scan.id}
        </Link>
      </span>
      <span className="flex flex-wrap gap-x-3 text-xs">
        <Link
          to={`/scans/${scan.id}/dependencies`}
          className="font-mono text-accent-700 hover:text-accent-800"
        >
          /scans/{scan.id}/dependencies
        </Link>
        <Link
          to={`/scans/${scan.id}/exports`}
          className="font-mono text-accent-700 hover:text-accent-800"
        >
          /scans/{scan.id}/exports
        </Link>
      </span>
    </li>
  );
}

function DemoAbsentSection() {
  return (
    <section aria-labelledby="demo-dataset-absent" className="card">
      <h2
        id="demo-dataset-absent"
        className="flex items-center gap-2 text-base font-semibold text-ink-900"
      >
        <Sparkles aria-hidden="true" className="h-4 w-4 text-accent-700" />
        Demo dataset not loaded.
      </h2>
      <p className="mt-2 text-sm text-ink-700">
        The seeded demo dataset is not present in this installation,
        so there are no demo scans to walk through. This page never
        substitutes your own scans for the demo.
      </p>
      <p className="mt-2 text-sm text-ink-700">
        You can explore the same product surfaces by running your own
        analysis: open the{" "}
        <Link
          to="/analyze"
          className="font-mono text-accent-700 hover:text-accent-800"
        >
          /analyze
        </Link>{" "}
        page to analyze a public GitHub repository or upload a source
        archive. Every feature described in this guide — the
        Dependency Explorer, the evidence drilldown, the export
        previews, and the bounded empty states — works the same way on
        your own scans.
      </p>
    </section>
  );
}
