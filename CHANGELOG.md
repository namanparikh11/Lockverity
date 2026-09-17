# Changelog

All notable changes to Lockverity are documented here. Versions
follow [Semantic Versioning](https://semver.org/). Lockverity is
pre-1.0 in the sense that the public API may evolve; the
underlying data model and Alembic migrations are stable.

## v2.1.3 — Native Windows shell and release hardening (published 2026-09-17)

Published as `v2.1.3` from artifact source commit `2ddb0e2d8f3771c711e98809beab409d468bebdd`. The release adds the dedicated WebView2 Windows desktop shell, owned loopback runtime lifecycle, explicit per-scan provider selection and privacy/availability states, hardened archive intake, stronger mutation/lifecycle/logging behavior, preserved partial-evidence semantics, deterministic Windows packaging/provenance, native icon resources, and UI appearance/sidebar refinements. The published Windows installer is unsigned and must be verified by SHA-256.

The release tag remains pinned to the exact artifact source commit; later documentation-only commits do not move the tag or alter the published binary provenance.

## v2.1.2 — Windows application icon and signing readiness (published 2026-08-06)

The hotfix was tagged and published as
``checkpoint-v2.1.2-public-release`` after a hash-alignment
cycle that walked the tag through ``c76e8fe`` (docs-finalization
HEAD) → ``fe6ceec`` (first hash-alignment HEAD) → ``2aa8729``
(final main HEAD). The published release tag, the
GitHub release, and all six release assets are
authoritatively attached to
``2aa8729832ac94093966a67d2f3f76c0e2110d15``; the
``BUILD-MANIFEST.json`` ``source_commit``, the
``INSTALLER-MANIFEST.json`` ``installer_source_commit``, and
the ``INSTALLER-MANIFEST.json`` ``payload_source_commit``
field all carry ``2aa8729``. The moving of the tag between
hash-alignment commits was a publication-cycle self-induced
churn; later releases must not repeat it — see the
release-process rule in ``docs/release-checklist.md``.
Later README / CHANGELOG / install-document commits on
``main`` after the tag do not change the published v2.1.2
artefacts and do not require a rebuild.

A narrow Windows-only hotfix that fixes the **Settings →
Installed apps** icon (the registry ``DisplayIcon`` value the
Windows shell renders) and prepares the build for a future
trusted Authenticode signer. The hotfix is *not* a feature
release and ships no signed binaries. v2.1.1 is the previous
public release; v2.1.0 is the release before that; both
remain on their original tags with all six assets
unchanged and are not re-published.

### Icon regression fixed

The v2.1.0 and v2.1.1 installers declared
``UninstallDisplayIcon={app}\Lockverity.exe`` (no payload
subdir, no icon index). Two defects:

1. The path pointed at a file that does not exist on disk
   because the launcher is installed under ``{app}\app\``
   (the inner payload subdir, not the install root). The
   Windows shell silently fell back to the generic
   application icon.
2. The directive did not include an explicit ``,0`` icon
   index, so some user accounts rendered the wrong icon
   group from the PE.

v2.1.2 fixes both: the directive now reads
``{app}\app\Lockverity.exe,0`` and the underlying ICO has
the full canonical size set.

### Canonical Windows ICO (16/24/32/48/64/128/256)

The canonical Windows ICO at
``backend/pyinstaller/favicon-exe.ico`` is regenerated
from the approved brand assets
(``frontend/public/favicon-source.png`` 1024×1024 RGBA
plus the hand-tuned 16/32/48 brand-favicon entries).
The new entry set covers every size the documented
Windows 10/11 shell queries — taskbar, Start tile,
Installed apps, File Explorer, uninstaller UI — so
the shell never falls back to the generic icon for a
missing size.

### Authenticode signing readiness hooks

Disabled by default. The v2.1.2 hotfix ships the
**infrastructure** to sign the Lockverity Windows
binaries (``Lockverity.exe``, ``lockverity-cli.exe``,
``unins000.exe``, the final installer EXE) once a
trusted Authenticode certificate is provisioned via
the documented environment variables:

- ``LOCKVERITY_SIGNTOOL_PATH``
- ``LOCKVERITY_SIGNTOOL_PFX``
- ``LOCKVERITY_SIGNTOOL_PFX_PASSWORD``
- ``LOCKVERITY_SIGNTOOL_TIMESTAMP_URL``
- ``LOCKVERITY_SIGNTOOL_DESCRIPTION``
- ``LOCKVERITY_SIGNTOOL_URL``

When all env vars are unset the build remains
functional and unsigned. No certificate material is
tracked, no PFX is bundled, no private key is
committed, and no self-signed production certificate
is generated. The release remains unsigned until an
operator explicitly enables the hooks by setting the
documented env vars.

### Signing approach (when enabled)

SHA-256 + RFC 3161 timestamping. The default
timestamp authority is ``http://timestamp.digicert.com``.
The signing helper signs ``Lockverity.exe`` first,
then ``lockverity-cli.exe``, then ``unins000.exe``,
then the final installer EXE (the order matters
because the counter-signature chain anchors on the
first sign and later signs reference it). After
signing, ``signtool verify /pa`` confirms a single
valid signature on each file.

### Files changed

- ``backend/scripts/generate_exe_icon.py`` — produce
  the canonical 7-size set.
- ``backend/tests/test_exe_icon.py`` — assert the full
  canonical size set.
- ``backend/pyinstaller/lockverity.spec`` — point
  ``icon=`` at the canonical ICO; expanded
  explanatory comment.
- ``backend/installer/lockverity.iss`` — bump
  ``MyAppVersion`` to ``2.1.2``; fix
  ``UninstallDisplayIcon`` to ``{app}\app\Lockverity.exe,0``.
- ``backend/scripts/build_windows_installer.py`` — bump
  ``APP_VERSION`` to ``2.1.2``, payload name to
  ``Lockverity-2.1.2-windows-x64-portable``.
- ``backend/scripts/build_windows_portable.py`` — bump
  default portable name to ``Lockverity-2.1.2-windows-x64-portable``.
- ``backend/app/_version.py`` — bump to ``2.1.2``.
- ``backend/scripts/_authenticode_sign.py`` — **new**:
  disabled-by-default signing readiness helper.
- ``backend/tests/test_signing_readiness.py`` — **new**:
  16 deterministic tests for the signing hooks,
  certificate-material guard, and password-leak guard.

### What v2.1.2 does **not** claim

- **The release is not signed.** The signing hooks are
  *infrastructure*, not an integration. A signed
  v2.1.2 binary requires a future trusted Authenticode
  provider to be configured at build time.
- **The release is not a code-signing integration.**
  No certificate is procured, no signing provider is
  contacted, no PFX is bundled.
- **v2.1.1 and v2.1.0 are unchanged.** The v2.1.0
  release tag, release body, and six release assets
  remain on ``checkpoint-v2.1.0-public-release``;
  the v2.1.1 release tag, release body, and six
  release assets remain on
  ``checkpoint-v2.1.1-public-release``. Neither is
  re-published or replaced.

## v2.1.1 — Public-repository scan intake and error taxonomy

A targeted hotfix for the v2.1.0 public-repository scan
intake, published on 2026-08-04 alongside the
``checkpoint-v2.1.1-public-release`` tag. The hotfix is
code-only: no new feature, no behaviour change beyond
the bug fix, the actionable error taxonomy, and the
repository-intake consistency closure. The v2.1.0
release tag, release body, and six release assets
remain unchanged on
``checkpoint-v2.1.0-public-release`` and are not
re-published.

### Acceptance corrections (commit 2 of the v2.1.1 hotfix)

- **Frontend error taxonomy never renders "Unknown
  error."** The v2.1.1 first commit fixed the
  backend-side error envelope; this commit
  extends the fix to the browser. The frontend
  ``categorizeError`` helper now recognises
  ``not_found``, ``invalid_ref``, ``internal_unexpected``,
  and the ``archive_unsafe`` / ``path_unsafe``
  400-class envelopes, and the
  ``errorTitleFor`` / ``actionErrorTitleFor``
  helpers carry a case for every category. The
  default catch-all no longer claims
  "Could not start a scan (Unknown error.)" —
  it renders a generic "Could not start a scan"
  title with the backend's safe message in the
  body. The ``ErrorState`` component accepts an
  optional ``description`` override so callers
  can append the correlation id for
  ``internal_unexpected`` envelopes.

- **``internal_unexpected`` correlation id is shown
  to the operator.** The ``correlationIdFromError``
  helper extracts the 16-character lowercase hex
  id (``secrets.token_hex(8)`` produces 8 random
  bytes = 16 hex chars) from the response
  ``details`` envelope. The AnalyzePage,
  NewRepositoryPage, and ScanActions components
  render ``Reference: <id>. Open Diagnostics or
  inspect the local runtime log.`` so the
  operator can grep the local log for the same
  id.

- **``invalid_ref`` is a distinct, actionable
  failure mode from the repository-not-accessed
  case.** ``_resolve_ref_to_sha`` now raises
  ``github_invalid_ref`` (mapped to a new
  ``ApiErrorCode.INVALID_REF = "invalid_ref"``)
  when both the branch and tag APIs return 404
  on a known-existing public repository. The
  404 repository case remains mapped to
  ``ApiErrorCode.NOT_FOUND`` so a private
  repository is still classified as
  "Repository could not be accessed" (and the
  message intentionally does not reveal whether
  the private repository actually exists).

- **State machine: ``QUEUED → FAILED`` is a legal
  transition.** The historical
  ``_SCAN_TRANSITIONS`` table only allowed
  ``QUEUED → RUNNING`` or ``QUEUED → CANCELLED``;
  the hotfix adds ``QUEUED → FAILED`` so a
  failed intake can move the scan row to a
  terminal state alongside the workspace. The
  intake service's
  ``_transition_intake_scan_to_failed`` helper
  makes the move, mirroring the workspace
  ``failure_code`` / ``failure_summary`` so the
  operator sees the same diagnostic in both
  surfaces. The transition is a one-way move to
  a terminal state; the scan row never reverts
  to a non-terminal status.

- **Failed-start database cleanup is direct, not
  inferred.** New tests in
  ``tests/test_intake_service.py`` assert via
  direct SQLAlchemy queries that after a 404,
  an ``invalid_ref``, an
  ``INTERNAL_UNEXPECTED``, or a retried 404
  with the same canonical URL, the database
  contains no READY workspace and no scan row
  in a non-terminal state. The contract is
  "either the rows are absent (transaction
  rolled back) or they are in a terminal
  state (FAILED)"; both are acceptable and
  the tests cover both shapes.

- **Packaging surfaces aligned.** The Inno Setup
  ``MyAppVersion`` directive, the portable
  builder's ``DEFAULT_PORTABLE_NAME`` and
  documentation references, the installer
  builder's ``APP_VERSION`` and ``PAYLOAD_NAME``
  constants, and the
  ``test_installer.py`` / ``test_build_manifest.py``
  packaging tests are all updated from
  ``2.1.0`` to ``2.1.1``. The ``app/_version.py``
  constant remains the single source of truth
  for the runtime-reported version. The
  ``backend/pyproject.toml`` Python package
  version (``0.2.0``) and the
  ``frontend/package.json`` frontend package
  version (``0.4.0``) are intentionally NOT
  bumped: they track the package distribution
  version, not the product version.

- **Documentation updates.** The README
  "Current release" header and Version table
  cell, the ``frontend/src/pages/AboutPage.tsx``
  hero, the ``frontend/src/pages/DemoHomePage.tsx``
  description, the
  ``frontend/src/__tests__/version_about.test.tsx``
  test mock and assertions, the
  ``CHANGELOG.md`` v2.1.1 hotfix section, and
  the ``RELEASE_NOTES.md`` v2.1.1 Status section
  all reference ``v2.1.1``. The v2.1.0 download
  links, the v2.1.0 asset hashes, and the
  v2.1.0 historical references in the
  ``What v2.1.0 does not include`` section
  remain as historical context and are not
  rewritten.

### Preserved (commit 1 of the v2.1.1 hotfix)

- **OpenSSF Scorecard partial scans remain Partial.**

### Repository-intake consistency defect closure (commit 5 of the v2.1.1 hotfix)

The v2.1.0 / v2.1.0-only-cycle bundled UI had two
public repository-intake pages, ``/analyze`` and
``/repositories/new``. The v2.1.0 hotfix scope
covered only ``/analyze``; ``/repositories/new``
submitted through the legacy ``POST /repositories``
endpoint and rendered the legacy generic
``Server error`` envelope on an unexpected failure,
without the v2.1.1 correlation id. Both pages are
public repository-intake workflows and must behave
identically.

- **Canonical intake path for both pages.** The
  ``/repositories/new`` page now submits through
  ``api.createRepositoryGithub`` →
  ``POST /api/v1/repositories/github``, the same
  canonical GitHub intake endpoint that ``/analyze``
  uses. The legacy ``POST /api/v1/repositories``
  endpoint is retained for backwards compatibility
  (other clients, scripts, ``curl``) and is wrapped
  with the same ``INTERNAL_UNEXPECTED`` boundary as
  defence in depth via the new
  ``repository_service.safe_create_repository_from_url``
  helper. ``/repositories/new`` now navigates to
  ``/scans/{result.scan.id}`` on success, matching
  the ``/analyze`` post-intake flow.
- **Shared intake-error formatting helpers.** A new
  ``frontend/src/components/intakeErrorFormatting.ts``
  module exports ``intakeErrorTitleFor`` and
  ``intakeErrorDescriptionFor`` so every intake
  page renders the same category-specific title
  and the same ``Reference: <correlation-id>.
  Open Diagnostics or inspect the local runtime
  log.`` line for ``internal_unexpected`` envelopes.
  The per-page copies in ``AnalyzePage.tsx`` and
  ``NewRepositoryPage.tsx`` are removed.
- **Regression coverage.** New tests cover
  ``/repositories/new`` submitting through the
  canonical intake endpoint, rendering the
  private / not-found wording, the ``invalid_ref``
  wording, the rate-limit wording, the
  ``internal_unexpected`` correlation-id line,
  the no-``Unknown error``-and-no-legacy-``Server
  error``-title contract, and the reset / retry
  behaviour. New backend tests cover the
  ``safe_create_repository_from_url`` wrapper at
  the service level (sanitised
  ``internal_unexpected`` envelope, classified
  errors preserved, transaction rollback leaves no
  orphan rows) and end-to-end through the legacy
  route (HTTP 500, 16-char hex correlation id,
  ``kind=repository``, no exception class or
  traceback in the response).

### Fixed

- **Public self-scan of the Lockverity repository now
  starts successfully.** Two related defects in the
  v2.1.0 scan intake caused the published v2.1.0 build
  to fail with ``FileNotFoundError`` (surfaced to the
  user as "Unknown error / An internal error occurred.")
  on a self-scan of
  ``https://github.com/namanparikh11/lockverity``:
  1. ``backend/app/core/config.py:45``'s default
     ``workspace_root = "./var/workspace"`` is
     CWD-relative and resolved under the install
     directory's ``_internal`` PyInstaller support
     directory when the CLI launched the child server,
     instead of under the operator-controlled runtime
     home.
  2. Combined with the long-named extracted directory
     (``<repo>-<40-char-sha>\backend\alembic\versions\…``),
     the destination path can exceed Windows
     ``MAX_PATH`` (260) and ``Path.open("wb")`` would
     fail with ``FileNotFoundError`` even when the parent
     directory existed and was writable.
  The fix mirrors the v2.1 Part B3B ``database_url``
  approach: the runner computes an absolute,
  runtime-home-relative workspace root and publishes
  it on the supervisor's process environment
  (``LOCKVERITY_WORKSPACE_ROOT``). The new
  ``_open_for_write`` and ``_mkdir_parents`` helpers
  in ``backend/app/utils/zip_intake.py`` retry through
  the Windows long-path prefix (``\\?\``) for paths
  that exceed 260 characters. POSIX is a no-op.
- **Actionable error taxonomy for the scan intake.**
  Generic "Unknown error" / "Archive was rejected." /
  raw "Upstream returned 404 Not Found." messages are
  replaced with category-specific, user-facing
  messages. At minimum:
  - 404 / private repository: *"Repository could not
    be accessed. Confirm that the URL exists and is
    public. Private repositories are not supported in
    this version."* The message does not reveal
    whether a private repository actually exists.
  - 429 rate limit: *"GitHub rate limit reached. Wait
    a few minutes and retry. Configure
    ``LOCKVERITY_GITHUB_TOKEN`` to lift the
    unauthenticated limit. The Diagnostics page shows
    the current rate-limit state."*
  - 403 denial: *"GitHub denied the request. The
    repository may be private, the URL may be wrong,
    or the configured token may lack access. Private
    repositories are not supported in this version."*
  - Archive rejection: 11 category-specific actionable
    messages (``archive_unsafe_path``,
    ``archive_symlink_forbidden``,
    ``archive_too_many_files``,
    ``archive_entry_too_large``,
    ``archive_uncompressed_too_large``,
    ``archive_overwrite_forbidden``,
    ``archive_path_resolve_failed``,
    ``archive_path_escape``,
    ``archive_extract_failed``,
    ``archive_quarantine_write_failed``,
    ``archive_validation_failed``). The original
    rejection code and bounded diagnostic message
    remain in the API response ``details`` envelope
    for operator debugging.
  - Internal error: a new
    ``ApiErrorCode.INTERNAL_UNEXPECTED`` carries a
    short non-PII correlation id (``8-char hex``) in
    the response ``details`` envelope. The
    user-facing message points the operator at the
    Diagnostics page and the runtime log; the full
    stack trace is written to the local log only.
    The response never includes a stack trace, a
    filesystem path, a token, a credential, or the
    raw exception string.

### Preserved

- **OpenSSF Scorecard partial scans remain Partial.**
  When the OpenSSF Scorecard repository-posture
  provider is unavailable, the ScopeForge CLI scan
  (and any other affected repository) remains
  ``Partial``, not ``Completed``. A missing or
  unavailable provider is still recorded as such
  and surfaced explicitly; the UI does not convert
  absence into "no findings". The provider-honesty
  contract is unchanged.
- **Failed-start record cleanup.** A scan that fails
  before transitioning to ``READY`` is not left as a
  misleading running scan; the new
  ``INTERNAL_UNEXPECTED`` path bubbles the failure
  to the API envelope without leaving a partial
  workspace state.

### Not changed

- No new feature. No public-claim broadening. No
  design or visual change.
- No new credential, token, or private-repository
  authentication support.
- The v2.1.0 installer EXE, the v2.1.0 portable ZIP,
  the ``checkpoint-v2.1.0-public-release`` tag, the
  GitHub Release body, and the six v2.1.0 release
  assets are unchanged. The v2.1.0 release remains
  the published release until v2.1.1 is published
  on its own tag.

## v2.1.0 — Local runtime brand polish and single-port production runtime

Historical. v2.1.0 was the previous published release,
on the ``checkpoint-v2.1.0-public-release`` tag. v2.1.1
supersedes v2.1.0 on
``checkpoint-v2.1.1-public-release``; the v2.1.0 release
tag and its six assets remain on GitHub and continue to
resolve from the original tag URL.

A focused, additive release that ships the v2.1 Part A
milestone (original brand assets, favicon closure,
concise About page, Findings filter alignment, and
bounded visual polish), the v2.1 Part B1 milestone
(single-port production runtime), and the v2.1 Part B2
milestone (cross-platform local runtime CLI).

### v2.1 Part B2: cross-platform local runtime CLI

The v2.1 Part B2 milestone adds the ``lockverity``
command, a cross-platform local runtime CLI for the
single-port production runtime introduced in Part B1.
The CLI wraps the existing application factory and
the Part B1 settings; it is the supported operator
path for starting, stopping, and inspecting the
local instance on Windows, macOS, and Linux.

- **Public subcommands.** ``lockverity start``,
  ``lockverity stop``, ``lockverity status``,
  ``lockverity open``, ``lockverity doctor``, and
  ``lockverity logs``. Each subcommand is also
  accessible through ``python -m app.cli
  <subcommand>`` so source-based usage works without
  an editable install.
- **Console-script entry point.** A new
  ``[project.scripts]`` entry in
  ``backend/pyproject.toml`` installs
  ``lockverity = "app.cli.main:main"`` alongside the
  Python module form. The CLI never shells out with
  ``shell=True``; every subprocess is constructed
  with an explicit argument list.
- **Runtime home.** The CLI persists state under an
  operator-controlled runtime home, precedence
  ``--home`` > ``LOCKVERITY_HOME`` > OS-appropriate
  default (``%LOCALAPPDATA%\\Lockverity`` on Windows,
  ``~/Library/Application Support/Lockverity`` on
  macOS, ``${XDG_DATA_HOME:-~/.local/share}/lockverity``
  on Linux). The home has four sub-directories
  (``data/``, ``logs/``, ``run/``, ``config/``)
  created with safe permissions and never written
  to the source repository.
- **Atomic instance-state file.** The state file
  under ``run/lockverity.state.json`` records the PID
  + recorded process creation time + command-line
  fingerprint + module + instance UUID. The writer
  uses a ``tempfile + os.replace`` atomic write so a
  crash mid-write never produces a half-written
  file. The state file intentionally stores no
  secrets (tokens, passwords, full URLs).
- **Process identity and PID-reuse protection.** The
  cross-platform process identity check compares
  the recorded PID + creation time + command line
  against the live process via ``psutil``. The
  standard library alone cannot reliably identify a
  PID on Windows: ``/proc`` is not available on
  macOS, the ``wmic`` CLI is deprecated and may be
  missing on modern Windows, and ``tasklist`` does
  not return creation time or the full command
  line. ``psutil`` ships as a wheel on Windows,
  macOS, and Linux and gives a uniform API for
  every dimension the identity check needs (PID
  existence, creation time, command line, module
  extraction, zombie detection, termination). A PID
  that has been recycled for an unrelated process
  never matches the recorded identity; the
  ``stop`` and ``status`` commands refuse to
  terminate the unrelated process. The CLI never
  uses ``shell=True``, never shells out to ``wmic``
  or ``tasklist`` for normal operation, and never
  assumes ``/proc`` is available.
- **Cross-platform start lock.** The ``start``
  command acquires an advisory file lock under
  ``run/lockverity.start.lock`` so two simultaneous
  ``lockverity start`` invocations against the same
  runtime home cannot both launch servers. The
  lock uses ``O_CREAT | O_EXCL`` on POSIX and a
  matching atomic create on Windows; a stale lock
  whose owner PID has been gone for more than 30
  seconds is recovered automatically. A
  ``test_real_concurrency_two_start_attempts`` test
  exercises the start lock with two concurrent
  acquisitions and asserts that exactly one
  succeeds.
- **Secret-free state file.** The state file
  intentionally stores no full command line, no
  database URL, and no provider tokens. The CLI
  generates a non-secret ``UUID4`` ``instance_id``
  at start time, passes it to the private child
  serve entry point ``app.cli._serve`` as
  ``--instance-id <UUID>``, and stores only the
  UUID in the state file. The live-process
  identity check reads the live command line at
  verification time and confirms the
  ``--instance-id <UUID>`` token is present -- the
  live command line is *read* but never *written*
  to disk. A ``test_state_does_not_store_cmdline_or_db_url``
  regression test asserts that no full command
  line, no database URL, and no ``--log-level``
  string appear in the persisted state.
- **One new pip dependency: ``psutil``.** The
  process identity and termination paths depend
  on ``psutil>=5.9.0,<7.0.0``. ``psutil`` ships as
  a wheel on every supported host; the CLI uses
  it for process inspection and termination
  only. No other pip dependencies are added in the
  v2.1 cycle. The constraint is pinned in
  ``backend/pyproject.toml`` and a comment in the
  dependency list explains why ``psutil`` is
  preferred over a ``wmic`` / ``tasklist`` /
  ``/proc`` polyglot.
- **Alembic migrations before launch.** The
  ``start`` command runs ``alembic upgrade head`` in
  a clean subprocess before launching Uvicorn. The
  subprocess is constructed with an explicit
  argument list (no ``shell=True``); the database URL
  is passed through the documented
  ``LOCKVERITY_DATABASE_URL`` environment variable
  so the application's ``alembic/env.py`` does not
  override it.
- **Bounded rotating log.** The runtime log uses
  ``logging.handlers.RotatingFileHandler`` with
  ``maxBytes`` of 10 MiB and ``backupCount`` of 5
  (bounded total footprint ~50 MiB). The handler is
  UTF-8 and never logs provider tokens, request
  authorization headers, or other secrets.
- **Loopback bind by default.** The CLI binds to
  ``127.0.0.1`` by default and refuses to bind a
  non-loopback host without the explicit
  ``--allow-remote`` flag. A clear warning is printed
  when ``--allow-remote`` is supplied; the built-in
  server does not terminate TLS, so the operator is
  responsible for a reverse proxy in front of any
  remote exposure.
- **Diagnostic ``doctor`` command.** The doctor
  command runs a read-only checklist (Python version,
  Lockverity version, runtime-home resolution,
  directory writeability, database file, Alembic
  state, frontend dist, default port availability,
  state-file integrity, Node availability) and
  reports each check as PASS / WARN / FAIL with a
  clear message. Secret env values are redacted. The
  command exits non-zero only for blocking failures;
  warnings do not block operator workflow.
- **Status and ``--json`` schemas.** Every
  subcommand supports a ``--json`` output flag with
  a documented stable top-level schema suitable for
  future launchers. ``status`` returns the JSON
  schema ``{status, instance_id, pid, host, port,
  url, version, home, frontend_dist, log_file,
  started_at, uptime, health, state_file}`` so a
  wrapper script can introspect a running instance
  without parsing human-readable text.
- **Exit-code contract.** ``status`` returns
  ``0`` for running-and-healthy, ``1`` for stopped,
  and ``2`` for unhealthy, stale, or misconfigured.
  ``start`` returns ``0`` on success, ``1`` on
  any documented failure, ``2`` for the
  ``--allow-remote`` guard, and ``3`` for a
  health-timeout (the process is running but did
  not report healthy in time). ``stop`` returns
  ``0`` for ``stopped`` / ``force_killed`` /
  ``was_not_running`` and ``1`` for
  identity-mismatch / inaccessible / grace-period
  exceeded without ``--force``.
- **No system service / installer.** The Part B2
  CLI is a process supervisor for source-based
  installations; it does not create Windows
  services, systemd units, launchd agents, scheduled
  tasks, MSI / DMG / DEB / RPM packages, or any other
  packaging artefact. Those deliverables belong to
  later milestones (Part B3 and beyond).
- **Forward-compatible state schema.** The
  ``InstanceState`` schema version is bumped
  forward only on breaking changes; new keys are
  backward-compatible. The reader is tolerant of
  unknown keys; missing required keys raise
  ``ValueError`` so a corrupt state file is detected
  at the boundary.
- **Test coverage.** The Part B2 release adds the
  ``backend/tests/test_cli.py`` test module with
  focused tests for the runtime-home resolver, the
  atomic state-file writer/reader, the cross-platform
  process identity checks, the rotating log
  handler, the runner helpers (port probe, loopback
  check, log tail, migrations, health probe), the
  start / stop flow guards, the CLI argparse
  grammar, the per-subcommand behaviour, and the
  ``python -m app.cli`` public entry point.

### v2.1 Part B1: single-port production runtime

### v2.1 Part B1: single-port production runtime

The FastAPI app can now host the built React UI from the
same host and port as the API when
``LOCKVERITY_SERVE_FRONTEND=true`` is set in a production
environment. The two-port development workflow (Vite on
5173, FastAPI on 8000) is unchanged.

- **New configuration settings.** ``LOCKVERITY_SERVE_FRONTEND``
  (default ``false``, refused in development and test
  environments) and ``LOCKVERITY_FRONTEND_DIST`` (default
  ``frontend/dist``, relative to the repository root).
  Absolute paths are accepted. The settings validator
  rejects ``..`` traversal segments.
- **Build preparation script.** ``scripts/prepare_frontend_dist.py``
  is a dependency-light Python step that verifies the
  Node.js toolchain (``node >= 22.22.0``), runs
  ``npm ci`` and ``npm run build``, and confirms the
  Vite output exists with the required artefacts. The
  ``--skip-install`` flag skips ``npm ci`` for repeated
  local builds. The backend never executes npm itself.
- **Single-port routing.** The route order is: (1) existing
  API and operational routes, (2) docs and OpenAPI,
  (3) static assets (``/assets/``, ``/favicon.ico``,
  versioned favicon PNGs, ``/apple-touch-icon.png``,
  ``/brand/``), (4) SPA fallback that serves
  ``index.html`` for extension-less, non-API, non-dotfile
  paths. API, docs, health, and diagnostics routes are
  never shadowed.
- **Cache and security headers.** Every response carries
  ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy: same-origin``, ``X-Frame-Options: DENY``,
  and the existing ``X-Request-Id`` correlation header.
  ``index.html`` is served with
  ``no-cache, no-store, must-revalidate`` so every
  navigation reloads the manifest. Hashed Vite assets
  use ``public, max-age=31536000, immutable``. Favicon
  and brand PNGs use ``public, max-age=86400``.
- **Path-traversal protection.** The serving rejects
  ``..`` segments (forward-slash or backslash), URL-
  encoded traversal, dotfile probes, and any file
  outside the configured dist directory. The serving
  cannot expose workspace files, databases, logs, or
  arbitrary filesystem content. The resolver performs
  a containment check with ``Path.is_relative_to`` after
  symlink resolution.
- **Build-before-start requirement.** The dist is
  validated at startup. A missing or stale build aborts
  the process with a clear error so the operator
  notices immediately. The error message references
  ``scripts/prepare_frontend_dist.py``.
- **HTTPS / TLS boundary.** The runtime does not
  terminate TLS. HTTPS/TLS must be provided by a reverse
  proxy or the packaged desktop boundary when the
  application is exposed beyond localhost.
- **Repository-controlled code is never executed.** The
  serving is read-only. The backend never invokes
  ``npm``, never runs the Vite build, and never writes to
  the dist directory. The repository-controlled code
  path (executing analyzed repositories) is unchanged.
- **75 new backend tests.** Every documented behaviour
  is guarded: serving-disabled preserves API-only
  behaviour, valid synthetic dist, ``/`` serves
  ``index.html``, nested SPA route serves ``index.html``,
  backend routes are not shadowed, docs and OpenAPI are
  not shadowed, missing static asset returns 404,
  unknown API-like route does not return HTML, favicon
  and brand PNG assets are served, correct MIME types,
  index cache policy, hashed-asset immutable caching,
  path traversal rejection, encoded traversal rejection,
  backslash traversal rejection, dotfile probing
  rejection, missing dist startup failure, missing
  index startup failure, absolute dist override, and
  build-preparation output verification.
- **Documentation update.** ``docs/release-checklist.md``
  adds a "Single-port production runtime" section that
  documents the build-before-start requirement, the
  configuration settings, the default and override dist
  paths, the route order, the cache and security
  headers, the path-traversal protections, the
  HTTPS/TLS boundary, the deep-link behaviour, the
  missing-or-stale dist troubleshooting, the static-
  serving cannot expose workspace files invariant, and
  the repository-controlled code is never executed
  invariant.

### v2.1 Part A: original brand assets, favicon closure, concise About, Findings filter grid

A focused, additive release that ships the v2.1 Part A
milestone: original brand assets, favicon closure,
concise About page, Findings filter alignment, and
bounded visual polish.

- **Original Lockverity mark.** v2.1 ships a hand-authored
  SVG brand mark in ``frontend/public/brand/``:
  ``lockverity-mark.svg`` (primary, ``currentColor``
  stroke for inline use), ``lockverity-mark-mono-dark.svg``
  and ``lockverity-mark-mono-light.svg`` (fixed-colour
  monochrome variants for documented backgrounds),
  ``lockverity-app-icon.svg`` (rounded-square icon for
  application surfaces), and a simplified favicon in
  ``frontend/public/favicon.svg`` that drops the L and
  keeps the V for 16-32px legibility. The geometry is an
  interlocking L and V that suggests an evidence link.
  It is not generated from a raster concept and is not
  derived from any third-party logo asset. See
  ``docs/brand-assets.md`` for the originality and
  ownership note.
- **Favicon and application icon closure.** v2.1 closes
  the favicon 404 reported in the v2.0.6 field-test
  manual sweep. ``frontend/public/favicon.svg`` and
  ``frontend/public/apple-touch-icon.svg`` are the
  shipping assets; ``frontend/index.html`` references
  both. A new ``BrandMark`` React component renders the
  same geometry inline so the AppShell header, sidebar
  footer, and About hero share a single source of truth.
- **Concise About page.** The long v2.0.6 About copy is
  replaced by a hero, three trust principles, six
  feature cards, an expandable limitations section, and
  a resources footer. The version string is rendered
  from ``GET /system/info`` so the page cannot drift
  from the running backend. The page still documents
  the human-readable evidence report, the two evidence-
  summary endpoints, the bounded
  "not a security verdict / not a certification /
  not a compliance pass-or-fail" wording, the
  non-execution guarantee, the provider-honesty policy,
  and the limitations list (authentication, multi-
  tenancy, billing; private GitHub repos; continuous
  scans; LLM/offensive features; hosted SaaS; and
  PDF/DOCX/HTML/certifications).
- **Findings filter alignment.** The Sort control on the
  Findings page now sits inside the v0.9 coherent
  responsive filter grid (Card layout) alongside
  Category, Severity, Confidence, and Status. The grid
  renders as 1 / 2 / 3 / 4 columns at sm / lg / xl
  breakpoints and preserves URL state, the bounded sort
  vocabulary, the advanced filters details, the
  zero-result wording, and the partial / failed /
  cancelled scan notices. Two new tests in
  ``frontend/src/__tests__/findings_v2_1_filter_grid.test.tsx``
  guard the layout and the API plumbing.
- **Bounded UI polish.** The v2.1 release tightens
  spacing, typography, and card hierarchy across the
  AppShell, About, and Findings surfaces without
  changing the visual language: ink/accent palette
  unchanged, green/amber/red still reserved for status
  semantics, no decorative animation, no neon effects,
  no excessive gradients, and no hacker imagery.
  Reduced-motion and visible focus states are preserved.
  The ``Card`` component pattern in ``index.css`` is
  unchanged so the existing pages still render.
- **Brand and design-token documentation.** Two new
  documents join the existing ``docs/`` set:
  ``docs/brand-assets.md`` (asset inventory,
  originality, ownership, sizes, and trademark note)
  and ``docs/design-tokens.md`` (colour, typography,
  spacing, focus, and motion tokens). The trademark
  note records that Lockverity is currently an
  unregistered open-source brand and makes no
  trademark-registration claim.
- **7 new frontend tests.** 5 in
  ``frontend/src/components/BrandMark.test.tsx`` and 2
  in
  ``frontend/src/__tests__/findings_v2_1_filter_grid.test.tsx``.
  ``frontend/src/__tests__/version_about.test.tsx`` is
  updated to assert the dynamic version rendering and
  the v2.1.0 About copy.

### v2.1 Part B3A: Windows portable package

The v2.1 Part B3A milestone adds a reproducible
Windows x64 portable distribution of the Lockverity
local runtime. The portable is a self-contained
single-folder artefact that bundles the FastAPI
backend, the cross-platform ``lockverity-cli``
command, the React frontend, the Alembic migrations,
and the approved Part A brand assets into a ZIP
that an operator can extract to any user-controlled
directory and run without a separately installed
Python or Node.js runtime, without administrator
rights, and without a Windows service or scheduled
task.

- **PyInstaller 6.10.0 one-folder build.** A new
  ``[project.optional-dependencies].build`` group in
  ``backend/pyproject.toml`` pins ``pyinstaller`` and
  ``pip-licenses`` as build-only dependencies. The
  pinned versions are not part of the runtime
  dependency set; an operator who installs only the
  runtime extras never pulls in PyInstaller.
- **Two committed PyInstaller specs.** The graphical
  launcher is built from the new
  ``backend/pyinstaller/lockverity.spec`` (windowless
  ``console=False``) and the console CLI from
  ``backend/pyinstaller/cli.spec`` (``console=True``).
  Both specs are committed in source form, are the
  canonical inputs for the build, and are reviewed
  for hidden imports, datas, and excludes. UPX is
  forbidden; ``shell=True`` is never used.
- **Frozen-resource resolver.** A new
  ``backend/app/runtime_paths`` module is the single
  chokepoint for "where do I find resource X?" in
  both source and frozen modes. The resolver routes
  ``frontend/dist``, ``alembic.ini``,
  ``alembic/versions``, the approved ``favicon.ico``
  and brand PNGs, the ``LICENSE``, and the bundled
  ``README-PORTABLE.txt`` to the right path in each
  mode. The resolver raises ``RuntimeError`` on
  wrong-mode calls so a future maintainer cannot
  silently regress the contract.
- **Graphical launcher.** A new
  ``backend/app/launcher`` module hosts
  ``Lockverity.exe``. The launcher is a windowless
  Windows application that uses the approved Part A
  ``favicon.ico`` as its executable icon, calls the
  accepted Part B2 ``status`` logic to discover a
  running instance, starts a background instance via
  the accepted Part B2 ``start`` logic when none is
  running, and opens the trusted local URL in the
  default browser. A second double-click reuses the
  same running instance and does not start a second
  server. Failures show a native Windows message box
  with the log path and a ``lockverity-cli.exe
  doctor`` recommendation; secrets and tracebacks
  are never displayed to ordinary users.
- **Console CLI executable.** ``lockverity-cli.exe``
  wraps the existing ``app.cli.main:main`` entry
  point and exposes the documented Part B2
  subcommands (``start``, ``stop``, ``status``,
  ``open``, ``doctor``, ``logs``) plus ``--help``
  and ``--version``. The contract is identical to
  the source-based ``lockverity`` command.
- **Frozen-mode Alembic path resolution.** A new
  ``BACKEND_ROOT = frozen_root()`` branch in
  ``backend/alembic/env.py`` lets Alembic find the
  ``alembic.ini`` and ``alembic/versions`` bundle
  under ``sys._MEIPASS`` in frozen mode. The
  source-mode branch is unchanged.
- **Single canonical build command.** A new
  ``backend/scripts/build_windows_portable.py``
  script is the single source of truth for the
  portable. It verifies Windows x64 + Python 3.12,
  verifies the build dependencies, optionally runs
  ``scripts/prepare_frontend_dist.py``, runs both
  PyInstaller builds from the committed specs,
  assembles the portable directory, generates
  ``THIRD_PARTY_NOTICES.txt`` (via ``pip-licenses``),
  ``BUILD-MANIFEST.json`` (source commit, build
  timestamp UTC, Python/PyInstaller/Node/npm
  versions, Alembic head, approved brand-asset
  hashes, dependency inventory location), and
  ``SHA256SUMS.txt``, and zips the artefact to
  ``dist/windows/Lockverity-2.1.0-windows-x64-portable.zip``.
  Useful options: ``--clean``,
  ``--skip-frontend-build``, ``--skip-smoke``,
  ``--output-dir``, ``--keep-work``, ``--json-report``.
- **No installer, no service, no auto-update.** The
  Part B3A portable is a "drop anywhere" artefact.
  It is not yet a Windows installer (no MSI, no
  NSIS, no Squirrel), does not install a Windows
  service, scheduled task, registry autorun, or
  firewall rule, and does not include an automatic
  update mechanism. There is no telemetry; the
  runtime only makes network calls the operator
  explicitly configured. Code signing is not
  included; SmartScreen and antivirus false-positive
  guidance are in ``docs/windows-portable.md``.
- **27 new backend tests.** 18 in the new
  ``backend/tests/test_runtime_paths.py`` (7
  source-mode + 10 frozen-mode + 1 ``frozen_exe_dir``)
  and 9 in the new ``backend/tests/test_launcher.py``
  (runtime-home resolution, healthy-instance reuse,
  stopped-instance start, port-in-use, missing
  dist, duplicate double-click, health timeout,
  non-Windows message-box no-op, no-secrets /
  ``shell=True`` greps).
- **Operator reference.** A new
  ``docs/windows-portable.md`` documents the layout,
  the default runtime home, the graphical launcher
  contract, the CLI executable, the first-launch
  migration behaviour, troubleshooting,
  SmartScreen and antivirus guidance, the "not yet
  an installer" statement, the clean-uninstall
  procedure, and the build instructions for
  maintainers.
- **Not in Part B3A.** Windows installer, code
  signing, automatic update, Linux/macOS/Docker
  packaging, system service integration, backup /
  restore, cloud sync, and telemetry are explicit
  future work and are not included in this
  milestone.

## v2.0.6 — Historical upload identification and clearer stage-outcome presentation

A narrowly scoped, field-testing-driven patch that ships
two real usability defects uncovered by a v2.0.5 field-test
run. No new product feature, no new provider, no new
export standard, no new evidence contract, no migration.

- **Historical upload names are derived from
  trustworthy persisted workspace metadata.** v2.0.5
  surfaced a human-readable label for new uploads
  (the basename of the original filename) but
  historical v0.x-v2.0.4 uploaded rows had
  ``Repository.original_filename = NULL`` and rendered
  the bounded opaque fallback
  ``Uploaded archive · upload/<short-key>``. The
  ``Workspace.archive_filename`` rows for each scan
  still carry the original archive filename (basename-
  only, sanitised at intake). v2.0.6 introduces
  ``get_repository_historical_filenames`` in
  ``backend/app/repositories/repository_repo.py``: a
  single batched query that resolves a per-repository
  historical archive filename from the workspace
  metadata. The helper surfaces a single filename when
  every workspace for the repository agrees, flags a
  conflict (and retains the bounded opaque fallback)
  when multiple distinct filenames are present, and
  returns ``None`` when no filename is available. The
  list endpoint now reads the historical helper and
  uses the historical filename as the primary
  ``display_name`` for uploaded rows whose
  ``Repository.original_filename`` is null. The helper
  is read-only: no historical row is mutated, no
  ``Repository.original_filename`` is backfilled, no
  filesystem path is read. Repository #13 in the
  field-test database now renders as
  ``test-09-mixed-monorepo.zip`` instead of the
  bounded fallback.
- **Repository search now matches historical
  filenames.** The ``search`` parameter on
  ``GET /api/v1/repositories`` is extended to also
  match a repository whose ``Workspace.archive_filename``
  rows contain the term. A search for
  ``test-09-mixed-monorepo`` (or any substring) returns
  the historical repository 13 even though
  ``Repository.original_filename`` is null. The
  extension is purely additive: the existing
  filename, owner, name, canonical URL, and scan-ID
  modes are preserved; the existing mutually-exclusive
  scan-ID token mode is preserved.
- **Stage message severity is a derived, structured
  field, not a frontend string match.** v0.5-v2.0.5
  rendered every ``failure_summary`` string with the
  red ``"Failure: "`` prefix. Several normal no-data
  outcomes (``No OSV advisories were returned for
  this scan.``, ``No workflow files were discovered.``,
  ``not_github_or_no_url``, ``1 parser warnings``) are
  not stage-execution failures; they describe a
  completed stage that did not produce records
  because the input was honest. v2.0.6 adds an
  additive ``message_severity`` field to
  ``ScanStageRead`` (``"error"`` / ``"warning"`` /
  ``"info"`` / ``"none"``) computed at the API
  boundary from the existing structured fields
  (``status``, ``records_processed``, ``failure_code``,
  ``failure_summary``). The decision uses a closed
  allow-list of known legacy reason codes (never a
  broad substring rule): an unknown residual
  summary falls through to ``"none"`` rather than
  to ``"info"``. The visible text never begins with
  ``"Failure: "`` for an ``info`` or ``"warning"``
  severity row. The field is never persisted; it is
  a derived read-time concern only.
- **42 new tests.** 22 in
  ``backend/tests/test_repository_historical_filenames_v2_0_6.py``
  (historical-label precedence, conflict handling,
  batched query, search by historical filename, the
  field-test repro for repository 13, a query-count
  regression that pins the bounded list-endpoint
  query count) and 20 in
  ``backend/tests/test_stage_message_severity_v2_0_6.py``
  (closed-list decision coverage, ``stage_to_read``
  mapping, the ``/scans/{id}/stages`` endpoint
  contract, defensive fallback for unknown residual
  summaries). The frontend adds 5 in
  ``src/__tests__/repository_v2_0_6.test.tsx``
  (historical archive filename as the primary title,
  bounded opaque fallback, new-upload
  ``original_filename`` regression, no local path
  leak, scan count + latest scan + actions) and 10
  in ``src/__tests__/stage_outcome_v2_0_6.test.tsx``
  (info / warning / error / none rendering, no
  ``"Failure: "`` prefix on info or warning, parser
  warnings and provider degradation remain visible,
  accessibility role is ``status`` for non-error and
  ``alert`` for error).
- **Version.** Bumped ``__version__`` to ``2.0.6``.
  The frontend ``version_about`` test mock now
  expects ``2.0.6``.
- **Boundary preservation.** No new feature, no new
  endpoint, no new persisted field, no migration
  (Alembic head remains ``e5f6a7b8c9d0``), no new
  export, no new organisation, no new provider, no
  new dependency, no destructive change to the
  field-test database (the historical scans #6, #7,
  #8, #13, #15 are preserved as defect and reacceptance
  evidence), no global Git config change, no remote
  URL change, no destructive action, no production
  deployment, no source-file mutation, no
  ``original_filename`` backfill, no filesystem
  read, no broad substring severity rule, no security
  verdict, no claim that an empty scan is "clean" or
  "vulnerability-free".

## v2.0.5 — Comparison stability and repository identification

A narrowly scoped, field-testing-driven patch that ships
two real defects uncovered by a v2.0.4 field-test run.
No new product feature, no new provider, no new export
standard, no new evidence contract, no new ecosystem.
v2.0.5 does not introduce a new feature; it ships two
defect repairs and the migration that supports one of
them.

- **Nullable comparison sort-key repair.** v2.0.4
  shipped with the component comparator sorting its
  identity-key tuples with Python's default
  ``sorted``. The identity tuple is
  ``(ecosystem, package_name, version)`` and ``version``
  is legitimately ``None`` for an unresolved range. A
  sort that mixed ``None`` and a populated string
  raised ``TypeError: '<' not supported between
  instances of 'str' and 'NoneType'``. The field-test
  repro hit this twice on scans #13 and #15 in
  ``var/manual-review/lockverity-field-test.sqlite``:
  ``GET /api/v1/repositories/13/compare?baseline=13&comparison=15``
  returned 500 with that exact traceback. v2.0.5
  introduces a dedicated
  ``_nullable_key_sort_key`` helper that converts
  ``None`` to ``(0, "")`` and any non-None value to
  ``(1, str(value))``; the original identity tuple is
  not mutated (``None`` and ``""`` remain distinct in
  the underlying dict), and the four ``sorted(...)``
  call sites that mix ``None`` with strings now use the
  helper. The vulnerability and licence comparators
  were also repaired because they share the same
  nullable-key risk. Equality continues to use the
  original tuple; only the display ordering was
  affected. The v0.5 contract (``newly_observed`` /
  ``still_observed`` / ``no_longer_observed`` /
  ``changed_observation`` / ``coverage_changed`` /
  ``comparison_indeterminate``) is unchanged; the
  v0.5 forbidden wording (``security improved``,
  ``fixed``, ``remediated``, ``risk increased``,
  ``risk decreased``) is still absent from every
  response.
- **Repository identification: uploaded filename as
  the primary label.** v2.0.4 surfaced an opaque
  canonical upload identifier (e.g.
  ``upload/2ed7b06ed7d3d967``) as the primary row
  label on the repository list and provided no scan
  count, no latest-scan summary, and no per-row
  "Open latest scan" / "Compare" action. v2.0.5 adds
  a nullable ``original_filename`` column on
  ``repositories``, populated for new uploads with
  the basename of the client-supplied filename
  (sanitised via ``basename_safely`` so an absolute
  path the client sends never reaches the database).
  The list endpoint now returns a
  ``RepositoryWithSummary`` shape with
  ``display_name`` (``owner/repository`` for GitHub;
  the original-filename basename for uploaded rows;
  the bounded fallback
  ``Uploaded archive · upload/<short-key>`` for
  historical rows where the filename is unavailable),
  ``canonical_identity`` (the secondary technical
  identifier), and a per-row ``summary`` that
  includes ``scan_count``,
  ``eligible_comparison_scan_count`` (the number of
  scans the comparator accepts), and ``latest_scan``
  (the scan with the largest ``id``; ``None`` for
  repositories with no scans). The summary is
  computed by a single batched query
  (``get_repository_summaries``) so the list
  endpoint does not produce an N+1 request pattern.
  The list page renders "Open latest scan" (disabled
  when no scan exists), "View history" (always
  present), and "Compare" (disabled when fewer than
  two eligible scans exist).
- **Repository search: filename + scan ID.** The
  ``search`` parameter on ``GET /api/v1/repositories``
  now matches a bounded set of persisted fields
  (uploaded original filename, GitHub ``owner`` /
  ``name``, canonical URL, canonical upload
  identifier) and resolves pure-integer or
  ``#N`` tokens to the parent repository of scan
  ``N``. The free-text and scan-ID modes are
  mutually exclusive: a pure digit token does not
  also run the ``ilike`` predicate, so a search for
  ``15`` returns only the parent repository of
  scan 15 rather than every repository whose
  ``owner`` or ``name`` contains a ``1``. A search
  that matches multiple scans for the same
  repository returns one row, not one row per
  matching scan. The existing provider, source, and
  archive filters are preserved.
- **45 new tests.** 15 in
  ``tests/test_comparison_nullable_sort_v2_0_5.py``
  (the comparison sort-key repair + the field-test
  repro pinned against the in-memory test engine) and
  30 in
  ``tests/test_repository_identification_v2_0_5.py``
  (basename sanitisation, display-name resolution,
  list-summary correctness, deterministic
  latest-scan selection, search by filename / owner
  / canonical upload key / scan ID / ``#N``,
  pagination preservation, provider isolation, and
  a query-count assertion that pins no per-row N+1
  scan lookup). The frontend adds 7 in
  ``src/__tests__/repository_v2_0_5.test.tsx``
  (uploaded filename as the primary title, GitHub
  ``owner/repository``, the no-scan explicit state,
  the "Compare" action visibility, search by
  filename placeholder, no local path leak, eligible
  comparison count helper text).
- **Migration.** A new Alembic revision
  (``e5f6a7b8c9d0``) adds the nullable
  ``repositories.original_filename`` column and a
  covering index. The migration is reversible
  (``upgrade`` adds the column and index;
  ``downgrade`` drops both). No historical row is
  rewritten; v0.x-v2.0.4 rows are left with
  ``original_filename = NULL`` and the API surfaces
  the bounded fallback label for them. The
  ``test_load_demo.py`` expected-alembic-head
  constant is updated to the new head.
- **Version.** Bumped ``__version__`` to ``2.0.5``.
  The frontend ``version_about`` test mock now
  expects ``2.0.5``.
- **Boundary preservation.** No new feature, no new
  endpoint, no new persisted field beyond the
  additive nullable ``original_filename``, no new
  export, no new organisation, no new provider, no
  new dependency, no destructive change to the
  field-test database (the migration was applied
  normally; the historical scans #6, #7, #8, #13,
  #15 are preserved as defect and reacceptance
  evidence), no global Git config change, no remote
  URL change, no destructive action, no production
  deployment, no source-file mutation, no broad
  encoding fallback, no ``_display_name`` /
  ``_canonical_identity`` for editable aliases
  (a future-work item).

## v2.0.4 — UTF-8 BOM compatibility repair

A narrowly scoped testing-driven patch that ships one real
defect uncovered by a v2.0.3 field-test run. No new
product feature, no new provider, no new export standard,
no new evidence contract, no migration, no new dependency.

- **UTF-8 BOM accepted in JSON dependency manifests.**
  v2.0.3 shipped with the ``PackageJsonParser`` and
  ``PackageLockJsonParser`` decoding the manifest bytes
  as plain UTF-8: ``content.decode("utf-8")``. A leading
  UTF-8 BOM (``EF BB BF``) — produced by Notepad on
  Windows and many other editors — is preserved as a
  literal ``\ufeff`` in the decoded string, which
  ``json.loads`` then rejects as
  ``Expecting value: line 1 column 1 (char 0)``. The
  orchestrator records the manifest with
  ``parse_status="FAILED"`` and zero components are
  produced. The field-test repro saw this twice on
  ``test-06-package-json-only.zip``: one manifest
  discovered, one parser failure, zero components. v2.0.4
  changes the decode to ``utf-8-sig``, which transparently
  strips a single leading UTF-8 BOM. The BOM does not
  become part of any name, version, PURL, or source
  path. The no-BOM control path is unchanged. The
  fix is bounded to the two JSON parsers; the TOML and
  YAML parsers are intentionally untouched (they are
  not JSON-based and the field-test repro did not
  surface a BOM regression there).
- **11 new backend tests** in
  ``backend/tests/test_parsers_npm_bom_v2_0_4.py`` cover:
  package.json with UTF-8 BOM parses successfully;
  the two expected direct dependencies
  (``axios 1.7.9`` and ``lodash 4.17.21``) survive the
  BOM exactly; names, versions, and PURLs do not
  contain a leading BOM codepoint or percent-escape;
  the relationship is ``direct`` with no
  development/optional side effects; the source path
  is preserved unchanged; the no-BOM control path is
  unchanged; ``package-lock.json`` with a UTF-8 BOM
  parses successfully; a BOM followed by invalid JSON
  is still rejected as a bounded parser failure;
  a BOM-like codepoint inside a string value is
  preserved (we do not over-strip); an end-to-end
  orchestrator scan with a BOM-prefixed
  ``package.json`` persists two components and
  records the manifest as ``PARSED``; and a nested
  BOM-prefixed ``package.json`` is discovered by the
  orchestrator's basename-based manifest discovery
  (the v2.0.2 fix) and parsed by the v2.0.4 BOM
  fix.
- **Field-test reacceptance.** The v2.0.3 field-test
  database is preserved. The BOM scan now produces
  the expected two components; the no-BOM control
  scan is unchanged. The historical scans that
  demonstrated the v2.0.3 defect are untouched.
- **Version.** Bumped ``__version__`` to ``2.0.4``. The
  frontend ``version_about`` test mock now expects
  ``2.0.4``.
- **Boundary preservation.** Two-line code change
  (``content.decode("utf-8")`` →
  ``content.decode("utf-8-sig")`` in
  ``PackageJsonParser.parse`` and
  ``PackageLockJsonParser.parse`` in
  ``backend/app/parsers/npm.py``). No new feature, no
  new endpoint, no new persisted field, no new
  export, no new organisation, no new provider, no
  new dependency, no migration, no global Git config
  change, no remote URL change, no destructive
  action, no production deployment, no source-file
  mutation, no malformed-JSON weakening, no broad
  encoding fallback. The repair is the smallest
  safe BOM-stripping change at the narrowest
  shared JSON-manifest boundary.

## v2.0.3 — First-run reproducibility repair

A focused defect-repair release that ships one real
release-blocking defect discovered during the v2.0.2
clean-state acceptance gate. The one-command
release-verification script documented in
[`docs/release-checklist.md`](docs/release-checklist.md) and
[`README.md`](README.md) failed on a fresh clean checkout.
No new product feature, no new provider, no new
export standard, no new evidence contract, no migration.

- **Release-validation script now works on a clean
  checkout.** v2.0.2 shipped with
  ``"ruff>=0.4.0"`` in the ``[project.optional-dependencies.dev]``
  extras of `backend/pyproject.toml`. A clean
  ``pip install -e ".[dev]"`` resolved the latest ruff
  release, which produces a different format than the one
  the committed files were last formatted against. The
  ``scripts/verify_release.py`` script runs
  ``python -m ruff format --check app tests scripts`` and
  failed with
  ``196 files would be reformatted``. The defect was
  reproducible on a clean clone from
  ``ed07e2f100248f9ca8689e7d7f103b78b71d3e69`` (the
  v2.0.2 release commit). v2.0.3 pins
  ``"ruff==0.15.21"`` in the dev extras — the exact
  patch that produced the committed format. The one-command
  verification now passes on a clean checkout.
- **3 new backend tests** in
  ``backend/tests/test_pyproject_ruff_pin_v2_0_3.py`` cover:
  the ruff spec in the dev extras is an exact
  ``==``-pinned semver (not a range that would silently
  regress); the dev extras still contain ``pytest``,
  ``ruff``, and ``mypy``; and
  ``scripts/verify_release.py`` invokes
  ``ruff format --check``.
- **Version.** Bumped ``__version__`` to ``2.0.3``. The
  frontend ``version_about`` test mock now expects
  ``2.0.3``.
- **Boundary preservation.** No new feature, no new
  endpoint, no new persisted field, no new export, no
  new organisation, no new provider, no migration, no
  global Git config change, no remote URL change, no
  destructive action, no production deployment. The
  release is a small, obvious correctness repair on the
  v2.0.2 surface.

## v2.0.2 — Ecosystem compatibility repair

A focused defect-repair release that ships one real defect
discovered during the v2.0.1 ecosystem-and-scale acceptance
gate. No new product feature, no new provider, no new
export standard, no new evidence contract, no migration.

- **Nested-manifest discovery in monorepos now works.** The
  v2.0.1 orchestrator stage
  ``backend/app/services/orchestrator_service.py:_discover_manifest_files``
  checked ``if rel in _MANIFEST_NAMES`` where ``rel`` is the
  full relative path (e.g. ``frontend/package.json``) but
  ``_MANIFEST_NAMES`` keys are basenames (e.g.
  ``package.json``). The full-path check only matched
  root-level manifests; every nested manifest in a
  monorepository was silently dropped, so the pipeline
  recorded zero ``Manifest`` rows and the analysis
  returned zero components. v2.0.2 changes the membership
  check to ``if manifest_type_for(rel) != "generic"``,
  which is the same basename lookup the
  ``app.utils.manifest_scanner`` path uses.
- **6 new backend tests** in
  ``backend/tests/test_orchestrator_manifest_discovery_v2_0_2.py``
  cover: the basename lookup for known and unknown
  filenames; nested ``frontend/package.json`` discovery in
  a single-namespace monorepo; a mixed-ecosystem
  monorepository (``frontend/``, ``backend/``,
  ``nested/service/``, ``tools/``); unknown-file rejection;
  the orchestrator's permissive behaviour re: nested
  ``node_modules`` paths (which the v0.3 dedupe handles);
  and the manifest row insertion shape.
- **Re-acceptance after the fix:** the
  ``11-mixed-monorepo`` fixture returns 7 components, 13
  findings, ``inventory_coverage: complete`` (was 0/0/
  empty). The ``12-monorepo-duplicate-versions`` fixture
  returns 4 components, 6 findings, with distinct
  ``manifest_id`` per source path (was 0/0/empty).
- **Version.** Bumped ``__version__`` to ``2.0.2``. The
  frontend ``version_about`` test mock now expects
  ``2.0.2``.
- **Boundary preservation.** No new feature, no new
  endpoint, no new persisted field, no new export, no
  new organisation, no new provider, no migration, no
  global Git config change, no remote URL change, no
  destructive action, no production deployment. The
  release is a small, obvious correctness repair on the
  v2.0.1 surface; the wider v0.3 dedupe
  (``(package_name, version, source_path)``) already
  prevents the new discoveries from duplicating existing
  components.

## v2.0.1 — Acceptance repair

A focused defect-repair release that ships one real defect fix
discovered during the v2.0 acceptance gate. No new product
feature, no new provider, no new export standard, no new
evidence contract, no migration.

- **Per-repository scan-history filter is now wired up.**
  v2.0 shipped with the v1.8 URL-persisted status / trigger
  filters on `GET /repositories/{id}/scans` documented but
  silently ignored: the route signature only accepted
  `page` / `page_size`, the `DataCompletenessNotice` and the
  "No scans match the filters" empty state implied the filter
  was working, and the table rendered every scan regardless of
  the active filter. v2.0.1 accepts `status` and
  `trigger_type` as `Query` parameters on the route (rejected
  with a bounded 422 envelope for unknown values), forwards
  them to `scan_service.list_scans_for_repository`, and the
  service forwards them to `scan_repo.list_scans_for_repository`.
  The repo layer adds the matching `WHERE` clauses before the
  `ORDER BY id DESC LIMIT/OFFSET` and the `COUNT` is computed
  against the filtered subquery. The result is the rendered
  table now actually matches the URL state.
- **7 new backend tests** in
  `backend/tests/test_api_repository_scan_filter_v2_0_1.py`
  cover the route accept/reject boundary, the combined-filter
  AND semantics, the no-filter control, the unknown-value 422
  envelope, and the service-to-repo kwargs forwarding.
- **5 new frontend tests** in
  `frontend/src/__tests__/repository_v2_0_1.test.tsx` use a
  filter-sensitive mock to assert the rendered table contains
  only the rows the API returned, including the
  "No scans match the filters" empty state when the filter
  has no matches. The v1.8 test only checked the request URL,
  not the rendered table.
- **Version.** Bumped `__version__` to `2.0.1`. The frontend
  `version_about` test mock now expects `2.0.1`.
- **Boundary preservation.** No new feature, no new endpoint,
  no new persisted field, no new export, no new
  organisation, no new provider, no migration, no global
  Git config change, no remote URL change, no destructive
  action, no production deployment. The release is a
  small, obvious correctness repair on the v2.0 surface.

## v2.0.0 — Local-first release candidate

A non-feature release candidate. The v2.0 contract bundles
the v0.5–v1.9 surface area under a single bounded
release-validation script, with two real defect fixes that
were uncovered during the v1.9 audit. The version bump
signals that the prior milestones have been audited,
regression-tested, and verified end-to-end on a single
command; it does **not** introduce a new product feature
or a new provider.

- **Local-first release candidate.** v2.0 is the first
  release line that explicitly markets itself as a
  local-first release candidate. The supported review
  workflow is unchanged from v1.9; the new
  `docs/release-checklist.md` documents the bounded
  operator-facing checklist, the prerequisites, the
  full verification command, the core security
  boundaries, the release-validation step plan, the
  known limitations, and what v2.0 does not claim.
- **Single release-validation entry point.** New
  `backend/scripts/verify_release.py` runs the
  documented 10-step plan in order: backend pytest,
  Ruff check, Ruff format check, pip check, frontend
  tests, frontend typecheck, frontend lint, frontend
  build, `npm audit --omit=dev`, and full `npm audit`.
  The script uses argv-only subprocess construction
  (no shell metacharacter concatenation), exits
  non-zero immediately on the first failed step, and
  prints a concise per-step summary. The step plan is
  the single source of truth for the release
  verification command; there is no second
  copy-pasteable command list. The script does not
  install dependencies, does not delete files, and
  does not mutate Git.
- **Defect fix — rescan error-envelope mapping.** The
  v1.8 rescan route used exact-match
  `exc.code == "github_error"` to decide between
  `PROVIDER_UNAVAILABLE` and `RESCAN_SOURCE_UNAVAILABLE`,
  but the rescan service wraps real `GitHubIntakeError`
  values with codes such as `github_not_found`,
  `github_rate_limited`, `github_unauthorized`,
  `github_invalid_response`, and
  `github_no_default_branch`. The result was that
  genuine GitHub errors were being mapped to
  `RESCAN_SOURCE_UNAVAILABLE` (HTTP 422) instead of
  `PROVIDER_UNAVAILABLE` (HTTP 502). v2.0 widens the
  mapping to match any `github_*` code against
  `PROVIDER_UNAVAILABLE` while keeping
  `RESCAN_SOURCE_UNAVAILABLE` for genuine source-side
  problems. Two new regression tests
  (`test_rescan_github_codes_map_to_provider_unavailable`
  and
  `test_rescan_non_github_codes_map_to_source_unavailable`)
  pin the new behavior.
- **Defect fix — dead code in diagnostics service.** The
  v1.9 `diagnostics_service` shipped a dead
  `executor_metadata_snapshot` function and an unused
  `typing.Any` import. Both are removed; the live
  service composes its summary from
  `build_summary(session, database_status)` plus the
  three section builders and never references the
  removed helper.
- **Focused tests for the release-validation script.**
  `backend/tests/test_verify_release.py` covers
  helper logic only (step plan shape, argv-only
  construction, working directories, non-zero timeouts,
  python executable resolved relative to the backend,
  tail truncation, `run_step` capturing stdout / stderr,
  `run_step` handling non-zero exit,
  `run_step_plan` stopping on the first failure, and
  `render_summary` marking the failed step). It does
  not execute the full release suite from pytest; that
  is the operator's job.
- **Boundary preservation.** The audit confirmed that
  every existing boundary from v0.5–v1.9 is preserved:
  no execution of analyzed code, no new providers, no
  new export standards, no new evidence contracts, no
  scheduled scans, no authentication, no organisation
  model, no universal score, no `secure/clean/passed/
  certified` claim, no cached-equals-live claim, no
  provider-success-equals-security claim, no tracked
  secrets, no tracked runtime artifacts, no global Git
  config change, and no production deployment.
- **Version.** Bumped `__version__` to `2.0.0`. The
  frontend `version_about` test mock now expects
  `2.0.0`.

## v1.9.0 — Provider health and operational diagnostics

- **Operational diagnostics page.** A new read-only
  `/diagnostics` route surfaces runtime reachability,
  executor state, per-provider persisted observations,
  recent partial / failed / cancelled scan issues, and
  aggregated persisted stage-state counts.
- **`/api/v1/diagnostics/summary` endpoint.** A new
  additive, read-only endpoint that composes the
  bounded summary from persisted state plus the
  existing `SELECT 1` database probe. The endpoint
  never triggers an external provider request and
  never exposes secrets, tokens, environment values,
  connection strings, or local filesystem paths.
- **Bounded provider diagnostics.** Per-provider rows
  surface the most-recent observation with cache state,
  evidence presence, last attempt, last success, and
  bounded error code / summary as independent fields.
  The page keeps cache state, evidence presence, and
  provider availability separate and never collapses
  them into a single verdict.
- **Bounded recent-issue list.** At most 25 partial /
  failed / cancelled scans are surfaced, ordered
  newest-first. Completed scans are intentionally
  excluded.
- **Bounded stage aggregation.** One row per
  `StageType` (the enum is fixed and small) with
  completed / partial / failed / skipped / running /
  pending counts. No percentage is invented; a zero
  count is rendered as "No matching persisted stage
  failures were found in the selected window" — never
  as "All stages are healthy."
- **Honest executor state.** The in-process executor
  does not persist heartbeats, so the page renders
  the explicit "Heartbeat not exposed by the current
  executor" notice rather than inventing a heartbeat
  from a wall-clock guess. Queued and running counts
  come from the persisted `scan_jobs` table.
- **Manual refresh only.** The page polls no faster
  than user-driven refresh. The refresh button blocks
  duplicate clicks through a synchronous `pendingRef`
  guard and preserves the last known payload on
  transient failure.
- **Boundary notice.** The page renders an explicit
  "Operational state is not security state" notice:
  a reachable backend does not imply providers are
  available; a provider unavailable does not imply a
  vulnerability is absent; cached evidence is not
  the same as live evidence; a successful provider
  request does not prove a repository is safe; a
  completed scan may still contain partial or
  degraded provider evidence.
- **No new providers, no new evidence contracts, no
  new external integrations, no migration.** The
  summary is composed from existing persisted
  observations and the existing scan-jobs table.

## v1.8.0 — Repository history, rescan, and evidence comparison

- **Repository history workbench.** The
  `/repositories/:repositoryId` page is upgraded into a
  coherent scan-history surface: scan rows are listed
  newest-first with status, ref, started/completed
  timestamps, and per-row cross-links to workbench /
  findings / dependencies / exports.
- **URL-persisted filters.** Status, trigger type, and
  page number are reflected in the URL query string so
  the history view is shareable and survives reload.
- **Bounded scan-state notices.** Filtering to partial,
  failed, or cancelled surfaces a `DataCompletenessNotice`
  with copy that does not claim a clean or complete
  result.
- **Run another scan uses the v1.6.1 rescan endpoint.**
  The "Run another scan" action calls
  `api.rescanRepository` (workspace-preserving) and never
  the low-level `api.createScan` path. The new scan is
  then started via `/scans/{id}/run` and the page
  navigates to the new workbench. The historical scan
  is never mutated.
- **Bounded rescan error rendering.** A
  `rescan_source_unavailable` error renders bounded
  guidance rather than a generic failure.
- **Repository-scoped comparison selector.** A new
  `/repositories/:repositoryId/compare` page hosts the
  baseline / comparison selector with URL state
  (`?baseline=&comparison=`). The selector preserves the
  v0.5 eligibility rules: only completed and partial
  scans are listed; same-scan selection is blocked;
  cross-repository ids are rejected as bounded errors.
- **Reuses the v0.5 comparison engine.** The selector
  page defers to the existing `ScanComparisonPage`
  with a "Repository → Compare" breadcrumb. The
  comparator itself is unchanged; v1.8 does not
  introduce a new comparison algorithm.
- **Removed-finding disclaimer and zero-improvement
  claim.** The v0.5 wording ("is not described as fixed
  or resolved") is preserved; the v1.8 page never
  claims security improved, security worsened, risk
  increased, risk decreased, fixed, or remediated.
- **No new backend endpoints.** The existing
  `/api/v1/repositories/{id}/rescan`,
  `/api/v1/repositories/{id}/scans`, and
  `/api/v1/scans/{id}/compare/{base}` routes are
  reused. No migration.

## v1.7.0 — Findings triage and evidence review

- **Findings triage and evidence review workbench.**
  The existing `/scans/:scanId/findings` page
  becomes a scan-scoped, evidence-first review
  surface. Reviewers can:
  - read a scan context header (scan id,
    repository, status, source type, finding
    count) with links back to the workbench,
    dependencies, and exports;
  - search findings server-side across title,
    summary, rule id, and the raw `evidence_json`
    (so package names, PURLs, advisories, and
    aliases are reachable from one search box);
  - filter server-side by category, severity,
    confidence, status, provider, rule id, and
    path; previous client-only filters are wired
    to the API;
  - sort by bounded fields (`id`, `rule_id`,
    `category`, `severity`, `confidence`,
    `status`, `updated_at`); Lockverity never
    invents a universal risk ranking;
  - open an evidence detail drawer that fetches
    the freshest payload via the single-finding
    endpoint, renders advisory identity (primary
    id, aliases, provider, source URL), shows
    the evidence provenance block, and surfaces
    cross-links to the workbench, dependencies,
    vulnerabilities, and exports;
  - persist filter / search / sort state in the
    URL so the analyst queue is shareable;
  - read a partial / failed / cancelled scan
    notice whenever the scan did not complete
    normally; the page never implies the
    finding set is complete in those cases.
- **Zero-result wording is bounded.** Empty
  states use "No finding records are available
  for the current filters. This does not
  establish that the repository is
  vulnerability-free." Lockverity never claims a
  clean / secure / safe / certified / compliant
  state.
- **Backend bounded additions.** New query
  parameters on
  `GET /api/v1/scans/{id}/findings`: `q`,
  `confidence`, `status`, `provider`, `rule_id`,
  `path`, `sort`. Page size is capped at 100.
  Invalid sort values map to `id` so paging stays
  deterministic. New
  `GET /api/v1/scans/{id}/findings/{finding_id}`
  route enforces scan-scoped isolation. No
  migration; all additions reuse existing
  persisted columns.
- **Persistent analyst disposition is not
  implemented.** v1.7 is read-only; review
  decisions are intentionally not stored in
  localStorage or in any new database column.
  This is reported as future work.

## v1.6.1 — Workspace-preserving rescan repair

- **Workspace-preserving rescan.** New
  `POST /api/v1/repositories/{id}/rescan` route and
  `RescanService` create a fresh scan, a fresh
  workspace, and re-materialise the source
  evidence (re-download the GitHub tarball or
  safely copy the previous upload workspace)
  before returning. The historical scan and
  workspace are never mutated.
- **Source-unavailable guard.** When the original
  uploaded source is no longer available, the
  route returns a bounded
  `rescan_source_unavailable` error before any
  queued row is persisted. The frontend renders
  the bounded guidance; the workbench never
  navigates to a new scan that is known to be
  unrunnable.
- **Action error carries its pending state.** The
  `ScanActions` component now stores the error
  alongside the action that produced it, so the
  bounded error title survives the
  `finally` block that resets the pending flag.
- **Frontend update.** `api.rescanRepository`
  wraps the new route; the v1.6 `api.createScan`
  still wraps the low-level route that creates
  only a queued scan row (kept for the orchestrator
  tests).
- **No new providers, no new export standards, no
  new evidence contracts, no migration.**

## v1.6 — Scan execution controls + live stage progress

- **Scan workbench at `/scans/:scanId`.** The existing
  scan detail page is upgraded with a truthful
  `ScanStatusExplanation` block (queued / running /
  completed / partial / failed / cancelled) and a
  `StageProgressSummary` block ("N of M stages reached
  a terminal state — terminal does not imply
  successful") that derives from persisted stage rows
  only. No percentage progress is ever shown.
- **Execution controls.** A new `ScanActions` component
  surfaces Start scan (`POST /api/v1/scans/{id}/run`),
  Cancel scan (`POST /api/v1/scans/{id}/cancel` with
  a destructive-styled confirmation dialog), and
  Run another scan / Retry as new scan (creates a
  fresh scan for the same repository; the historical
  scan is never mutated). The actions disable while
  pending so duplicate submissions are blocked.
- **Intake-to-execution flow.** The v1.5 `/analyze`
  page now calls `/scans/{id}/run` immediately after
  successful intake. If the start fails, the page
  surfaces a bounded partial-success card: the
  repository and scan are persisted, the worker did
  not start the scan, and a Retry start button is
  offered. The card never claims the scan started.
- **API client additions.** `api.runScan(scanId)` and
  `api.cancelScan(scanId, payload)` wrap the existing
  executor endpoints. No new backend endpoints.
- **Polling.** The workbench reuses the existing
  `usePolling` hook (2s interval, terminal-set stop,
  abort on unmount) via a new `useWorkbenchPolling`
  wrapper.
- **No new providers, no new export standards, no new
  evidence contracts, no migration.**

## v1.5 — Guided intake / scan launch

- **New `/analyze` route.** `frontend/src/pages/AnalyzePage.tsx`
  wraps the existing intake APIs in a guided flow with two
  clearly separated methods: a public GitHub URL form and a
  source archive (`zip`) upload. The page does not duplicate
  business logic; it calls
  `POST /api/v1/repositories/github` and
  `POST /api/v1/repositories/upload` and reads the returned
  `IntakeResultRead.scan.id` to navigate to the new scan.
- **AppShell nav entry.** New `Analyze` entry in the primary
  navigation, between `Dashboard` and `Demo`. The link
  points at `/analyze` and uses the `Sparkles` icon.
- **Bounded non-execution and archive-hostility copy.** The
  page repeats the "Lockverity never executes repository
  code" and "archives are treated as hostile input"
  guarantees inline on the form, plus the bounded
  "evidence exports, not a security verdict, certification,
  or compliance pass-or-fail" wording.
- **First-run empty state.** The scan list empty state now
  offers two clear actions: `Analyze repository` (linking
  to `/analyze`) and `Open demo guide` (linking to `/demo`).
  The synthetic-dataset notice on the scan list is
  unchanged.
- **Upload field-name bug fix.** The frontend `requestUpload`
  helper used to send the ZIP under the multipart `archive`
  field; the backend route declared `file: UploadFile = File(...)`
  so every upload silently bound `None` and the endpoint
  returned `422`. The v1.5 fix pins the field name to `file`
  matching the backend contract, so the existing
  `/repositories/upload` route (used by the new `/analyze`
  page) and the older `/repositories/upload` legacy page
  both work end-to-end. The pre-existing test in
  `frontend/src/__tests__/exports.test.tsx` only checks the
  URL path; the form-field change is verified by a new
  test in `frontend/src/__tests__/analyze_v1_5.test.tsx`.
- **Status / progress UX.** After intake succeeds, the
  page renders a status panel that polls the new scan
  using the existing `usePolling` hook. The reviewer can
  open the scan detail page at any time. Polling stops
  on terminal status (`completed` / `partial` / `failed`
  / `cancelled`).
- **No new backend endpoints.** v1.5 reuses the existing
  intake and scan routes. No new providers, no new export
  standards, no new evidence contracts, no migration.

## v1.4 — In-app demo home + reviewer flow

- **In-app reviewer flow.** New `/demo` route and
  `DemoHomePage` page. The page surfaces five sections
  (demo dataset status, reviewer flow, what to look for,
  what not to claim, quick command reminder) and a
  bounded "this is a demo, not a hosted service" footer.
  The page repeats the synthetic-dataset disclosure and
  the bounded "not a verdict / not a certification / not
  a compliance pass-or-fail" wording so a reviewer can
  never mistake the demo for a real provider scan result.
- **AppShell nav entry.** New `Demo` entry in the primary
  navigation, between `Dashboard` and `Repositories`. The
  link points at `/demo` and uses the `PlayCircle` icon.
- **New focused test** at
  `frontend/src/__tests__/demo_home_v1_4.test.tsx` covers
  all five sections, the five reviewer links, the bounded
  wording, the new nav entry, and the synthetic-dataset
  disclosure.
- **No product change.** No new providers, no new export
  standards, no new evidence contracts, no new API
  endpoints. The page is read-only and self-contained; it
  does not call any backend endpoint.
- **Docs.** `README.md` 30-second overview updated to
  mention `/demo`. `docs/demo-pack.md` "How to run the
  demo" section updated to point at `/demo` first.
- **Version.** Bumped `__version__` to `1.4.0`. The
  frontend `version_about` test mock now expects `1.4.0`.
- **Status.** Private portfolio-ready baseline. Not a
  production SaaS, not a hosted service, not a CI vendor.
  See `RELEASE_NOTES.md` for the full status.

## v1.3 — Screenshot assets + private portfolio demo pack

- **Demo pack.** New `docs/demo-pack.md` with the 60-second
  reviewer walkthrough script, the public/private
  recommendation, the "what to say" + "what not to claim"
  framing, and a cross-reference list to the other
  reviewer-facing docs.
- **Screenshot checklist rewrite.** `docs/screenshots.md`
  is now a 9-capture reviewer checklist with browser-
  agnostic manual capture instructions (macOS, Windows,
  Linux shortcuts), a pre-capture sensitive-data checklist,
  a post-capture sensitive-data checklist, and an explicit
  rationale for **not** shipping tracked image files. No
  headless-browser dependency is added; the live demo is
  the canonical evidence.
- **README polish.** New `## Screenshots` section under
  the existing `### What not to claim` block explaining the
  manual-capture strategy and pointing at
  `docs/demo-pack.md`. New `docs/demo-pack.md` entry in the
  `## Quick links` block.
- **No product change.** No new providers, no new export
  standards, no new evidence contracts, no new API
  endpoints. All v0.5–v1.2 surfaces continue to work
  unchanged. The application source tree is the same as
  v1.2.1.
- **Version.** Bumped `__version__` to `1.3.0`. The
  frontend `version_about` test mock now expects `1.3.0`.
- **Status.** Private portfolio-ready baseline. Not a
  production SaaS, not a hosted service, not a CI vendor.
  See `RELEASE_NOTES.md` for the full status.

## v1.2.1 — GitHub portfolio final pass

- **Repository presentation.** Added `CHANGELOG.md` (this file)
  and `RELEASE_NOTES.md` for a reviewer reading the repo on
  GitHub. Added a `Quick links` block to the top of `README.md`
  pointing to the demo walkthrough, the screenshot checklist,
  the security boundaries, the provider-honesty policy, the
  changelog, and the release notes.
- **No product change.** No new providers, no new export
  standards, no new evidence contracts, no new API endpoints.
  All v0.5–v1.2 surfaces continue to work unchanged.
- **Version.** Bumped `__version__` to `1.2.1`. The frontend
  `version_about` test mock now expects `1.2.1`.
- **Status.** Private portfolio-ready baseline. Not a
  production SaaS, not a hosted service, not a CI vendor. See
  `RELEASE_NOTES.md` for the full status.

## v1.2 — Demo UX polish

- **`backend/scripts/load_demo.py`**: rewrote the success-path
  console output to surface the dataset nature (synthetic
  persisted evidence, no provider calls), the four scan ids
  and their states, cross-platform startup commands, and the
  five reviewer-facing demo URLs in one place. New focused
  test pins the new output shape.
- **`docs/screenshots.md`**: new 10-capture reviewer
  checklist with exact URLs, expected visible states, what
  not to claim, and a reviewer-facing checkbox list.
- **`README.md`**: added a clean `## Run the demo` section
  with six numbered steps (generate DB → start backend →
  start frontend → open URLs → capture screenshots → stop
  demo) and a `### What not to claim` block. Updated
  `Current milestone` to v1.2.
- **`docs/demo-walkthrough.md`**: bumped v1.0/v1.1 references
  to v1.2; explicit "demo dataset is synthetic persisted
  evidence" wording.
- **`frontend/src/pages/ScansIndexPage.tsx`**: small neutral
  in-product demo-dataset notice gated on every listed scan
  belonging to the synthetic fixture repository. The notice
  does not appear for normal repositories.

## v1.1 — Demo loader + synthetic screenshot-ready dataset

- **`backend/scripts/load_demo.py`** (new, 462 lines): a
  deterministic, safe-to-commit seed script that creates a
  Lockverity demo SQLite database from hard-coded synthetic
  data. Refuses to overwrite an existing file unless
  `--reset-demo-db` is passed; refuses to write outside
  `backend/var/`. Runs Alembic migrations
  (`7efc41b356da` → `d4e5f6a7b8c9`); never calls
  `Base.metadata.create_all` as the primary schema creator.
- **Seed data:** one synthetic repository
  (`example-org/lockverity-fixture`), four scans
  (completed / partial / failed / cancelled) with
  hardcoded ids 1–4, synthetic package names only
  (`alpha`, `beta`, `gamma`, `left-pad`, `right-pad`, `stay`),
  the `deadbeef` repeated fill for the resolved commit SHA,
  and obviously-fake `a*64` / `b*64` content hashes.
- **Six new focused tests** in
  `backend/tests/test_load_demo.py` covering schema via
  Alembic, refusal to overwrite, refusal to write outside
  `backend/var/`, deterministic dataset shape, no-embedded-
  secrets invariant, and the failed-scan failure reason.

## v1.0.1 — Public/portfolio readiness docs

- **README rewrite** from a v0.1 baseline copy to the full
  v1.0 product description (10 "What Lockverity does"
  sections, 6 bounded "What Lockverity explicitly does not
  claim" sections, intended users, key v1.0 demo flow,
  architecture overview, local setup, database migration,
  testing commands, known ports, current milestone, what
  v1.0 does not include, provider-honesty policy,
  non-execution security boundary, license).
- **New `docs/demo-walkthrough.md`** (reviewer walkthrough)
  and **`docs/security-boundaries.md`** (public-facing
  statement of what Lockverity will and will not do).
- **Documentation correctness fix:** the v0.1-era `Cargo.toml`
  and `go.mod` parser claims were removed from
  `docs/security-boundaries.md` because the v1.0
  application only ships npm / pnpm / yarn / poetry /
  pyproject / requirements parsers; verified by listing
  `backend/app/parsers/`.
- **Version bump** to `1.0.1`; the `version_about` frontend
  test mock and three hardcoded `"1.0.0"` backend assertions
  were updated to read `__version__` dynamically.

## v1.0 — Human-readable evidence report

- **`backend/app/reports/`** (new module, 913 lines):
  `build_evidence_report` (pure projection) +
  `render_evidence_report_markdown` (deterministic renderer) +
  `EvidenceReportService(session_factory)` (session wrapper);
  reuses the v0.6 / v0.7 / v0.8 / v0.9 helpers verbatim.
- **API:** `GET /api/v1/scans/{id}/reports/evidence-summary/preview`
  (JSON) + `GET /api/v1/scans/{id}/reports/evidence-summary.md`
  (Markdown download with per-scan `Content-Disposition`).
- **Bounded 7-section Markdown:** metadata, scan identity,
  scan summary, evidence coverage, evidence gaps, component
  table (capped at 100 rows), export relationship, plus the
  10 evidence-honesty omission markers.
- **Frontend:** `EvidenceReportPanel` + `EvidenceReportSummary`
  on the Export Center page; the preview fetches lazily on
  the first expand.

## v0.9 — Evidence search and filtering

- **New endpoint**
  `GET /api/v1/scans/{scan_id}/components/evidence-summary`:
  text search, ecosystem, direct, version present / missing,
  licence evidence, provider evidence, PURL persisted /
  constructible / omitted, dependency edges, CycloneDX 1.7
  appears / version omitted / relationships emitted, plus
  6 sort options. Per-row evidence flags. Facet counts.
- **Lazy rewrite** of `DependencyExplorerPage` for the
  evidence-honest vocabulary ("not persisted" / "no
  persisted edges", never "no dependencies").

## v0.8 — Component evidence drilldown

- **New endpoint**
  `GET /api/v1/scans/{scan_id}/components/{component_id}/evidence`:
  per-component read-only summary with 6 sections
  (identity, manifest evidence, licence evidence, provider
  evidence, dependency evidence, export implications) plus
  an evidence-honesty markers list. Reuses the v0.6 CycloneDX
  exporter helpers for PURL, bom-ref, licence
  classification, and graph coverage.
- **Frontend:** `DetailsDrawer` side-panel on the Dependency
  Explorer.

## v0.7 — SBOM evidence preview and export explanation

- **New endpoint**
  `GET /api/v1/scans/{scan_id}/exports/cyclonedx_1_7/preview`:
  read-only JSON summary of the upcoming download (eligibility
  verdict, inventory summary, evidence coverage, SBOM
  output facts, omissions, legacy-export relationship note).
- **Frontend:** `CycloneDxPreviewPanel` on the Export Center;
  lazy fetch on first expand.

## v0.6 — CycloneDX 1.7 SBOM export

- **New endpoint**
  `GET /api/v1/scans/{scan_id}/exports/cyclonedx_1_7`:
  schema-validated CycloneDX 1.7 SBOM with deterministic
  serial numbers, evidence-coverage properties, and a
  per-scan `Content-Disposition`. Validated against the
  official `JsonStrictValidator(SchemaVersion.V1_7)`.
- **Eligibility helper** (`evaluate_export_eligibility`)
  with three states (`eligible`, `partial_evidence`,
  `ineligible`) and a bounded `not_applicable` coverage
  verdict for failed / cancelled / queued / running scans.

## v0.5 — Evidence-aware scan comparison

- **New endpoint**
  `GET /api/v1/scans/{head_scan_id}/compare/{base_scan_id}`:
  diffs two terminal scans of the same repository without
  inventing missing evidence. State vocabulary
  (`newly_observed`, `still_observed`, `no_longer_observed`,
  `changed_observation`, `coverage_changed`,
  `comparison_indeterminate`) is evidence-honest: missing
  provider data is never rendered as a clean bill of
  health.
- **Frontend:** `ScanComparisonPage` and
  `ScanCompareSelectPage`.

## Notes

- Pre-v0.5 history is not summarised here. The release-line
  tags are `checkpoint-v0.2`, `checkpoint-v0.2-polished`,
  `checkpoint-v0.2-vite8`, `checkpoint-v0.3`,
  `checkpoint-v0.4`, and the v0.5 onward tags listed above.
- Every release line has a corresponding annotated tag
  `checkpoint-vX.Y` in the repository.
