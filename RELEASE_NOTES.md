# Lockverity — Release Notes

## What Lockverity is

Lockverity is an **evidence-first, defensive-only, read-only**
analyzer for the software supply chain of public GitHub
repositories and uploaded source archives. It never executes
analyzed code, never calls a Makefile / shell script / install
hook, and never claims that an absence of evidence is a clean
bill of health. Every claim the UI makes is backed by a file
path, a manifest, a provider response, or an explicit omission
marker.

The product is built around three guarantees:

- **Non-execution.** Lockverity never invokes `npm install`,
  `pip install`, `poetry install`, `yarn install`,
  `pnpm install`, `setup.py`, or any Makefile / shell script
  from an analyzed repository. See
  [`docs/security-boundaries.md`](docs/security-boundaries.md)
  for the full boundary statement.
- **Provider-honesty.** A missing or unavailable provider is
  recorded as such and surfaced explicitly; the UI never
  converts absence into "no findings". See
  [`docs/provider-honesty.md`](docs/provider-honesty.md).
- **Source-honest claims.** Severity, confidence, provider
  availability, and data completeness are independent
  dimensions in the data model and in the UI. A partial
  dependency graph is reported as `partial`, never
  `complete`.

## Status — v2.1.3 public release (current)

The current **published** release is **Lockverity v2.1.3**, on tag `v2.1.3` (2026-09-17), with artifact source commit `2ddb0e2d8f3771c711e98809beab409d468bebdd`. The Windows installer uses the dedicated WebView2 desktop shell and owned loopback runtime lifecycle. Provider choice/privacy state, archive intake, lifecycle handling, evidence semantics, packaging provenance, and UI appearance were hardened for this release.

The Windows installer is unsigned. Verify SHA-256 `831264757dccde2c8feef0422390d9812f053fcad811455bde868756c7757bfd` before running it. The release page is the canonical asset index: https://github.com/namanparikh11/Lockverity/releases/tag/v2.1.3.

The v2.1.3 portable ZIP is built, verified, and now available as a release asset on the v2.1.3 release page from the same artifact source commit.

## Status — v2.1.1 public release (previous)

The historical v2.1.1 release is **Lockverity v2.1.1**, on the annotated tag
``checkpoint-v2.1.1-public-release`` (2026-08-04).
The v2.1.1 hotfix is a code-only correction: it ships
the v2.1.0 public-repository scan-intake repair, the
actionable error taxonomy, and the repository-intake
consistency closure. The v2.1.0 release tag,
``checkpoint-v2.1.0-public-release``, and its six
release assets remain unchanged and continue to
resolve from the original tag URL; v2.1.1 publishes
on its own tag and does not republish the v2.1.0
assets.

What v2.1.1 fixes:

- **Public self-scan of the Lockverity repository
  now starts successfully.** The v2.1.0 scan intake
  failed on a self-scan of
  ``https://github.com/namanparikh11/lockverity``
  (and on any public repository whose long-named
  extracted directory pushed the destination path
  past Windows ``MAX_PATH``). The v2.1.0 user-visible
  result was "Could not start a scan (Unknown
  error.) / An internal error occurred." The
  hotfix routes the workspace root through the
  runtime home and adds Windows long-path support,
  so a public self-scan starts successfully and the
  operator gets a clean result.
- **Actionable error taxonomy for the scan intake.**
  The v2.1.0 user-facing error messages for
  repository-not-accessible, rate-limited, denied,
  invalid-ref, and archive-rejection conditions were
  generic ("Unknown error", "Archive was rejected.").
  The hotfix replaces these with category-specific,
  actionable messages, and adds a non-PII
  correlation id for unhandled internal failures so
  the operator always knows whether to retry,
  re-upload, or open an issue.
- **Repository-intake consistency.** Both bundled
  repository-intake pages — ``/analyze`` and
  ``/repositories/new`` — now submit through the
  same canonical GitHub intake endpoint
  (``POST /api/v1/repositories/github``) and render
  the same classified error taxonomy. The legacy
  ``POST /api/v1/repositories`` endpoint is
  retained for backwards compatibility and is
  wrapped with the same ``INTERNAL_UNEXPECTED``
  boundary as defence in depth.
- **Failed-intake transaction cleanup.** A scan that
  fails before transitioning to ``READY`` is not left
  as a misleading running scan; the new
  ``INTERNAL_UNEXPECTED`` path bubbles the failure
  to the API envelope without leaving a partial
  workspace state.

What v2.1.1 does **not** do (preserved contracts):

- v2.1.1 does not add private-repository support.
- v2.1.1 does not change the v2.1.0 release assets.
  The ``checkpoint-v2.1.0-public-release`` tag and
  its six assets are unchanged and remain
  downloadable from the original release URL.
- v2.1.1 does not convert any Partial scan to
  Completed. A missing or unavailable provider is
  still surfaced as such; the OpenSSF Scorecard
  repository-posture case remains Partial.
- v2.1.1 does not add a code signature. The Windows
  build remains unsigned; operators may see
  ``Unknown publisher`` or SmartScreen warnings.
  Verify the SHA-256 hash before installing.

## Status — v2.1.2 public release (previous)

The previous **published** release is **Lockverity v2.1.2**,
on the annotated tag
``checkpoint-v2.1.2-public-release``
(2026-08-06). The v2.1.2 hotfix is a narrow Windows-only
correction plus Authenticode signing-readiness infrastructure.
v2.1.1 remains on the
``checkpoint-v2.1.1-public-release`` tag; v2.1.0
remains on ``checkpoint-v2.1.0-public-release``.
Neither is re-published or replaced.

What v2.1.2 fixes:

- **Settings → Installed apps shows the Lockverity
  icon.** The v2.1.0 and v2.1.1 installers declared
  ``UninstallDisplayIcon={app}\Lockverity.exe`` --
  a path that does not exist on disk (the launcher
  is installed under ``{app}\app\``) and that
  omitted the explicit ``,0`` icon index. v2.1.2
  fixes the path to ``{app}\app\Lockverity.exe,0``
  and rebuilds the canonical Windows ICO with the
  full size set the Windows shell queries
  (16/24/32/48/64/128/256).
- **Consistent installer, executable, shortcut and
  uninstaller branding.** The same canonical ICO is
  used as ``SetupIconFile``, bundled at the
  install root, and referenced by the Start Menu
  and desktop shortcuts via the ``IconFilename``
  directive. The uninstaller EXE icon is inherited
  from ``SetupIconFile`` (Inno Setup 6.7.3
  contract).
- **Authenticode signing-readiness hooks.** A
  **disabled-by-default** ``_authenticode_sign.py``
  helper exposes the documented env-var contract
  for a future trusted Authenticode provider
  (``LOCKVERITY_SIGNTOOL_PATH``,
  ``LOCKVERITY_SIGNTOOL_PFX``,
  ``LOCKVERITY_SIGNTOOL_PFX_PASSWORD``,
  ``LOCKVERITY_SIGNTOOL_TIMESTAMP_URL``, ...). The
  helper signs ``Lockverity.exe`` ->
  ``lockverity-cli.exe`` -> ``unins000.exe`` ->
  installer EXE, with SHA-256 and RFC 3161
  timestamping. When all env vars are unset the
  build is unchanged: still functional, still
  unsigned, no code-signing integration.

What v2.1.2 does **not** do (preserved contracts):

- v2.1.2 does **not** add a code signature. The
  Windows build remains unsigned; operators may
  see ``Unknown publisher`` or SmartScreen
  warnings. Verify the SHA-256 hash before
  installing.
- v2.1.2 does not change the v2.1.0 or v2.1.1
  release assets. Both tags and their six assets
  each remain unchanged and continue to resolve
  from the original release URLs.
- v2.1.2 does not claim private-repository support
  and does not claim the Windows build is
  code-signed.

## Status — v2.1.0 local-first release (historical)

The repository is a **local-first release**, not a production
SaaS, not a hosted service, and not a CI vendor. v2.1.0 was
the previous published release; v2.1.1 supersedes it on
``checkpoint-v2.1.1-public-release`` while keeping the v2.1.0
tag and its six assets intact at the original release URL.
v2.1.0 was a focused additive release that ships:

- **v2.1 Part A** — original Lockverity brand assets, favicon
  closure, concise About page, Findings filter alignment, and
  bounded visual polish. The brand mark is a hand-authored
  interlocking L and V that suggests an evidence link; it is not
  generated from a raster concept and is not derived from any
  third-party logo asset. See
  [`docs/brand-assets.md`](docs/brand-assets.md).
- **v2.1 Part B1** — single-port production runtime. The FastAPI
  app can host the built React UI from the same host and port as
  the API when `LOCKVERITY_SERVE_FRONTEND=true` is set in a
  production environment. The two-port development workflow is
  unchanged.
- **v2.1 Part B2** — cross-platform local runtime CLI. The
  `lockverity` command is the supported operator path for
  starting, stopping, and inspecting the local instance on
  Windows, macOS, and Linux. The CLI never shells out with
  `shell=True`; the default bind is loopback only.
- **v2.1 Part B3A** — Windows x64 portable package. A
  self-contained ZIP that bundles the FastAPI backend, the
  cross-platform `lockverity-cli` command, the React frontend,
  the Alembic migrations, and the approved Part A brand assets.
  No separately installed Python or Node.js is required; no
  administrator rights; no Windows service, scheduled task, or
  registry autorun. See
  [`docs/windows-portable.md`](docs/windows-portable.md).
- **v2.1 Part B3B** — Windows x64 per-user installer. A
  self-contained, per-user, no-UAC, no-admin EXE that installs
  the accepted Part B3A portable payload into
  `%LOCALAPPDATA%\Programs\Lockverity`. The installer does not
  modify the operator's `PATH`, does not install a Windows
  service, does not register an autorun entry, does not add a
  firewall rule, and does not require administrator privilege.
  See [`docs/windows-installer.md`](docs/windows-installer.md).

There is:

- **No multi-tenancy** and no authentication. A reviewer runs
  the application on their own laptop.
- **No hosted component.** The application is not deployed
  anywhere by the maintainers. There is no marketing site, no
  signup flow, and no telemetry.
- **No SaaS SLA.** This is a code-and-documentation baseline;
  the maintainers do not run the application on anyone else's
  behalf.
- **No production hardening.** The configuration defaults are
  safe for local development but the application has not been
  penetration-tested for multi-tenant production deployment.

If a future release changes this status (e.g. adds hosted
deployment), this section will be the first thing rewritten
and the change will be called out in `CHANGELOG.md`.

## What the demo can show today

A single engineer on a laptop can demonstrate every v0.5–v1.2
surface in under five minutes without any provider credentials
or hosted services. The flow is:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\load_demo.py --reset-demo-db
$env:LOCKVERITY_DATABASE_URL = "sqlite:///var/demo/lockverity-demo.sqlite"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765

cd frontend
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:8765"
npm install
npm run dev
```

The five demo URLs a reviewer opens are:

- `http://127.0.0.1:5173/` — scan list (4 scans, all four
  terminal states).
- `http://127.0.0.1:5173/scans/1/dependencies` — Dependency
  Explorer, 6 components, evidence filters.
- `http://127.0.0.1:5173/scans/1/exports` — Export Center,
  CycloneDX 1.7 preview, evidence report.
- `http://127.0.0.1:5173/scans/3/exports` — bounded empty
  state for a failed scan.
- `http://127.0.0.1:5173/about` — About page + product
  boundaries.

The loader's success-path output already prints all of the
above plus the cross-platform startup commands. The full
reviewer walkthrough is in
[`docs/demo-walkthrough.md`](docs/demo-walkthrough.md). The
screenshot checklist is in
[`docs/screenshots.md`](docs/screenshots.md).

The demo dataset is **synthetic persisted evidence**: the
fixture repository is
`https://github.com/example-org/lockverity-fixture`, the
resolved commit SHA is the `deadbeef` repeated fill, every
package name is from a documented six-name set
(`alpha`, `beta`, `gamma`, `left-pad`, `right-pad`, `stay`),
and Lockverity makes no provider calls during the demo. The
demo is not a real provider scan result.

## Evidence boundaries — what Lockverity will not claim

Lockverity is **not**:

- A **security verdict**. The CycloneDX 1.7 SBOM, the
  evidence report, the component evidence drilldown, the
  search results, and the dependency-edge counts are evidence
  exports, not certifications.
- A **certification**. No export is signed; no export carries
  a trust assertion; no export is a substitute for human
  review.
- A **compliance pass / fail**. Lockverity does not score a
  repository against a regulatory framework.
- A **complete dependency-graph claim** unless a positive
  persisted signal exists. The dependency-graph coverage
  helper returns `partial` or `empty`; it never returns
  `complete` without proof.
- A **"no findings" verdict** when a provider was unavailable.
  Missing provider data is rendered as `not_persisted` /
  `not_observed`; the UI never converts absence into a clean
  bill of health.
- A **remediation workflow**. Lockverity reports findings; it
  does not stage pull requests, open issues, or contact
  maintainers on the operator's behalf.

The full boundary statement is in
[`docs/security-boundaries.md`](docs/security-boundaries.md).
The provider-honesty policy is in
[`docs/provider-honesty.md`](docs/provider-honesty.md).

## What is implemented

- **Persistent data model** for repositories, scans, stages,
  findings, advisories, components, dependency edges,
  provider observations, scan jobs, workspaces, and provider
  cache.
- **Two intake paths:** `POST /api/v1/repositories/github`
  (public GitHub URL) and `POST /api/v1/repositories/upload`
  (ZIP archive upload with streaming, validation, and
  quarantine).
- **Manifest parsers** for npm (`package.json`,
  `package-lock.json`), pnpm, Yarn, Poetry, `pyproject.toml`,
  and `requirements.txt`. Rust / Go parsers are intentionally
  not implemented; the security boundary document was
  corrected in v1.0.1 to remove the v0.1-era claim.
- **Vulnerability / licence / workflow / repository-posture
  rules** with bounded evidence JSON, deterministic
  `stable_key`, and explicit severity / confidence.
- **Provider integrations** for GitHub, OSV, deps.dev, and
  OpenSSF Scorecard, with a bounded HTTP client and a
  provider-cache layer.
- **Exports:** CycloneDX 1.5 and 1.7 SBOM (JSON, validated
  against the official 1.7 schema), SARIF 2.1.0 (JSON),
  findings JSON, findings CSV, and a deterministic Markdown
  evidence report.
- **Evidence search and filtering** at
  `GET /api/v1/scans/{id}/components/evidence-summary` (v0.9)
  and a **component evidence drilldown** at
  `GET /api/v1/scans/{id}/components/{cid}/evidence` (v0.8).
- **Evidence-aware scan comparison** at
  `GET /api/v1/scans/{head}/compare/{base}` (v0.5).
- **Local scan worker** with a 10-stage pipeline, per-stage
  status, scan cancellation, and per-scan heartbeat
  monitoring.
- **Frontend** shell with a typed API client, request
  cancellation, structured error parsing, reduced-motion
  support, and explicit first-run empty states.

## What is intentionally out of scope

- **Authentication, multi-tenancy, billing, or self-service
  signup.** The application runs on a single engineer's
  laptop.
- **Continuous / scheduled scans.** Scans are explicit
  operator actions (manual trigger, archive upload, or
  `api`).
- **Private GitHub repository analysis.** v2.1.0 is
  public-only; the `LOCKVERITY_GITHUB_TOKEN` environment
  variable is honoured for public rate limits but private
  endpoints are out of scope.
- **LLM-driven analysis, exploit generation, or any other
  offensive feature.** The `No offensive feature policy`
  in `CONTRIBUTING.md` codifies this; any change that
  weakens the non-execution guarantee in `SECURITY.md` is
  closed without merging.
- **PDF, DOCX, HTML, signed attestations, or certification
  exports** for the human-readable evidence report. The
  Markdown report is the only evidence-report export; the
  others are explicitly out of scope by design.
- **Hosted deployment of the application itself.** The
  application is a local-first, portfolio-ready
  baseline; there is no production deployment, no
  hosted control plane, and no multi-tenant service.
  There is no marketing site, no signup flow, and no
  public deployment.

## Local development commands

```powershell
# Backend
cd backend
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m ruff check app tests scripts
.\.venv\Scripts\python.exe -m ruff format --check app tests scripts

# Frontend
cd frontend
npm.cmd test -- --run
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
```

## License

MIT. See [`LICENSE`](LICENSE).
