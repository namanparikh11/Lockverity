import { PageHeader } from "@/components/PageHeader";

export function PrivacyPage() {
  return (
    <>
      <PageHeader
        title="Privacy policy"
        description="What Lockverity processes locally and what it sends to external services."
        breadcrumbs={[{ label: "Privacy" }]}
      />
      <article className="card max-w-4xl space-y-5 text-sm text-ink-700">
        <PolicySection title="Local runtime and storage">
          Lockverity runs locally. Repository workspaces, scan results, provider observations,
          and bounded provider caches are stored on the operator&apos;s machine according to the
          configured database and workspace paths. Repository content may contain personal or
          confidential information; Lockverity does not classify it as non-personal data.
        </PolicySection>
        <PolicySection title="GitHub repository retrieval">
          Submitting a GitHub repository contacts GitHub before analysis to resolve the owner,
          repository, requested branch/tag/commit, metadata, and commit SHA, and to download the
          repository tarball. This retrieval is required for GitHub scans and has no disable
          control.
        </PolicySection>
        <PolicySection title="OSV">
          When selected and applicable, Lockverity sends package ecosystem, package name, and
          the observed version when available to OSV for vulnerability evidence.
        </PolicySection>
        <PolicySection title="deps.dev">
          When selected and applicable, Lockverity sends package ecosystem, package name, and
          concrete version to deps.dev for package and dependency metadata.
        </PolicySection>
        <PolicySection title="OpenSSF Scorecard">
          When selected for a supported GitHub repository, Lockverity sends the GitHub owner and
          repository name to the OpenSSF Scorecard API. Scorecard is not applicable to archive
          uploads.
        </PolicySection>
        <PolicySection title="Archive uploads">
          Archive bytes are validated, extracted, and analyzed locally and are not uploaded to
          GitHub, OSV, deps.dev, or OpenSSF. Package coordinates discovered inside an archive may
          be sent to OSV or deps.dev only when the operator leaves those providers selected.
        </PolicySection>
        <PolicySection title="Optional GitHub token">
          An operator may configure a GitHub token in the local backend environment. The token is
          used only to authenticate GitHub API requests, is not requested from the browser, and
          is not written to scan observations or evidence exports.
        </PolicySection>
        <PolicySection title="Telemetry and analytics">
          Lockverity does not include product telemetry, analytics, advertising identifiers,
          accounts, or cloud inference. Network activity is limited to operator-requested GitHub
          retrieval and the external evidence providers selected for a scan.
        </PolicySection>
      </article>
    </>
  );
}

function PolicySection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h2 className="text-base font-semibold text-ink-900">{title}</h2>
      <p className="mt-1 leading-6">{children}</p>
    </section>
  );
}
