# Lockverity security boundaries

This document is the public-facing statement of what Lockverity
will and will not do. It is intentionally short and credible; the
internal threat model lives in `threat-model.md` and the
provider-honesty policy in `provider-honesty.md`.

## Source archives are hostile

Uploaded source archives are treated as hostile untrusted input
at every layer of the application. The application:

- Validates every archive entry before extraction.
- Rejects path traversal, absolute paths, drive letters, UNC
  paths, symlinks, hard links, duplicate normalized entries,
  excessive depth, oversized entries, suspicious compression
  ratios, excessive file counts, and excessive cumulative size.
- Quarantines extracted content under a workspace root that is
  not served by the API or frontend.

See `archive-safety.md` for the full model.

## Lockverity must not execute analyzed repository code

Lockverity never invokes any of the following against an
analyzed repository or upload:

- `npm install`, `npm` scripts, or any Node.js CLI
- `pip install`, `pip` scripts, or any Python install entrypoint
- `poetry install`, `yarn install`, `pnpm install`
- `setup.py`, `setup.cfg`, `pyproject.toml` build scripts
- `Makefile` targets
- Repository shell scripts
- Arbitrary subprocess commands derived from repository content

Manifests, lockfiles, workflows, and source metadata are read
as data, never executed as code. Adding a code-execution path
to the application requires an explicit, reviewable change
that updates this document and `SECURITY.md`.

## Dependency installers and build scripts are not run

Lockverity uses persisted manifests and lockfiles such as
`package.json`, `package-lock.json`, `pyproject.toml`,
`requirements.txt`, and other supported files to inventory
dependencies. It does not resolve the lockfile, install
packages, or run build scripts. Resolution is the job of the
provider integrations (OSV, deps.dev) and is bounded by the
provider-cache TTL.

## Provider failures are represented as unavailable or degraded

Lockverity never reports "no vulnerabilities found" because a
provider was unavailable, rate-limited, or skipped. The
provider-observation table records every call with its outcome,
and the UI surfaces `unavailable`, `rate_limited`, `partial`,
and `cached` states explicitly. The CycloneDX 1.7 preview and
the v1.0 evidence report render the bounded
`provider_coverage` label as `ok`, `degraded`, or
`not_applicable`. See `provider-honesty.md`.

## Missing evidence remains missing

Lockverity never converts the absence of evidence into a
positive verdict:

- A missing version is rendered as `version missing` (not
  "latest version").
- A missing PURL is rendered as `omitted` when neither the
  persisted PURL is well-formed nor the v0.6 reconstruction
  rule applies. It is rendered as `constructible` when the
  reconstruction rule would have built one.
- A missing licence observation is rendered as
  `licence_not_persisted` (not "no licence required").
- A missing provider observation is rendered as
  `provider_not_persisted` (not "no vulnerabilities").
- A missing dependency edge is rendered as
  `no_persisted_edges` (not "no dependencies"). A partial
  dependency graph is rendered as `partial` (not "complete").
- A missing provider confidence is rendered as `null` (not
  `medium` or `high`).

## Reports and SBOMs are evidence exports, not certifications

Lockverity is **not**:

- A **security verdict**. The CycloneDX 1.7 export, the v1.0
  evidence report, the v0.8 component evidence drilldown, and
  the v0.9 search results are evidence exports, not
  certifications.
- A **certification**. No export is signed; no export carries a
  trust assertion; no export is a substitute for human review.
- A **compliance pass / fail**. Lockverity does not score a
  repository against a regulatory framework.
- A **complete dependency-graph claim** unless a positive
  persisted signal exists. The v0.6 dependency-graph coverage
  helper returns `partial` or `empty`; it never returns
  `complete`.
- A **"no findings" verdict** when a provider was unavailable.
- A **remediation workflow**. Lockverity reports findings; it
  does not stage pull requests, open issues, or contact
  maintainers on the operator's behalf.

## Listening on loopback is not a caller boundary

Lockverity binds loopback only, but that is a *network* boundary,
not a *caller* boundary: any process on the machine can reach the
port, and so can any web page the user has open, because a browser
will issue a cross-site request to `http://127.0.0.1:<port>`
without asking.

Every state-changing API request therefore has to present a
control token:

- The backend mints one unguessable token per process. It lives in
  memory for the lifetime of that process. It is never written to
  disk, never logged, never placed in a URL, and never appears in
  diagnostics, exports, evidence, or the CLI state file — whose
  secret-free schema is a separate documented contract.
- The token reaches the frontend by being injected into the
  `index.html` that same process serves. A page on any other
  origin cannot read that document, so it cannot learn the token.
- The token is accepted in the `X-Lockverity-Control` request
  header and nowhere else. A custom header cannot be sent
  cross-origin without a CORS preflight, and no permissive CORS
  policy is shipped.
- `Host` is checked on every API request, so a DNS-rebinding page
  cannot reach the port under a name it controls. `Origin`, when
  present, must be same-origin or explicitly configured.
- `GET /api/v1/health` stays unauthenticated because the launcher
  waits on it before opening the window. It performs no mutation
  and returns no sensitive value.

**What this does not claim.** This is strong protection against
arbitrary websites and against accidental unauthenticated callers.
It is *not* a boundary against malware already running as the same
user: such a process can read this process's memory, fetch the
served document over the same loopback port, or simply act as the
user. No in-process measure changes that, and none is claimed.

## Local dev and demo is not a hosted SaaS security boundary

The local development setup (the manual-review SQLite database,
the Vite dev server on `127.0.0.1:5173`, the uvicorn process on
`127.0.0.1:8765`) is intended for a single engineer running
the product on a laptop. It is **not** a hosted SaaS security
boundary. Production deployments are expected to:

- Run uvicorn behind a TLS-terminating reverse proxy.
- Run the Vite-built static bundle behind the same reverse
  proxy.
- Use PostgreSQL via `LOCKVERITY_DATABASE_URL` instead of the
  local SQLite default.
- Set `LOCKVERITY_CORS_ORIGINS` to the deployed origin.
- Set `LOCKVERITY_GITHUB_TOKEN` only for public-rate-limit
  increases; private GitHub repositories are out of scope.

## No secrets should be committed

The application reads every credential from an environment
variable. The repository never contains a real secret:

- The `fixtures/` directory contains only synthetic test data
  designed to fail any credential-leak scanner.
- `.env` files are git-ignored.
- The frontend source never embeds a token; `credentials: "omit"`
  is the only mode the API client uses. The one token the client
  does send is the local control token described below, which is
  minted per process at runtime and never written to disk.

If you find a committed secret, treat it as compromised. Rotate
the credential and remove the file from history. See
`SECURITY.md` for the disclosure policy.

## Reviewing this document

When a new feature is proposed, update this document if the
feature:

- Touches a new code path that handles untrusted input.
- Adds a new outbound network call.
- Adds a new persisted field that could carry sensitive data.
- Changes the state machine for a scan or a stage.
- Adds a new export format or a new public-facing surface.

The threat model in `threat-model.md` is the internal companion
to this document; the two should agree on the trust boundaries.
