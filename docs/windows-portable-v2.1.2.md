# Lockverity Windows portable package

The Lockverity Windows portable package is a
self-contained, single-folder, no-installer distribution
of the Lockverity local runtime for Windows x64. It
bundles the FastAPI backend, the cross-platform
``lockverity-cli`` command, the React frontend, the
Alembic migrations, the approved brand assets, and the
``LICENSE`` into a single ZIP that an operator can
extract to any user-controlled directory and run without
administrator rights and without a separately installed
Python or Node.js runtime.

This document is the operator reference for the Windows
portable and the maintainer reference for the build
pipeline. The portable is the v2.1.2 Windows x64
portable; for the v2.1.2 Windows installer see
[`docs/windows-installer.md`](windows-installer.md). For
the central download and install guide see
[`docs/install.md`](install.md).

> **Publication integrity note:** the already-published v2.1.2 ZIP,
> tag, hashes, manifests, and provenance are immutable and retain their
> documented browser-launch behaviour. The native-window behaviour in
> this document applies to development/smoke artifacts built from the
> current `main` source. No updated v2.1.2 asset is implied or published.

## TL;DR

1. Download ``Lockverity-2.1.2-windows-x64-portable.zip``.
2. Extract it to any directory the user controls
   (for example ``C:\Tools\Lockverity`` or
   ``C:\Users\<you>\Lockverity``).
3. Double-click ``Lockverity.exe`` to start the
   runtime. In a current-source development build this opens the
   dedicated Lockverity desktop window; the published v2.1.2 ZIP opens
   the trusted local URL in the default browser.
4. From a second terminal, use ``lockverity-cli.exe``
   for the documented ``start``, ``stop``, ``status``,
   ``open``, ``doctor`` and ``logs`` subcommands.

No administrator rights are required at any point.
No system service, scheduled task, registry autorun, or
firewall rule is installed by the portable package.

## Directory layout

The portable root contains the user-facing entry points
plus a single ``_internal`` support directory that
PyInstaller generates. The exact internal layout of
``_internal`` is opaque; the user-facing root is
stable.

```
Lockverity-2.1.2-windows-x64-portable\
  Lockverity.exe                  (native Windows desktop launcher in current builds)
  lockverity-cli.exe               (console CLI; same commands as source-based lockverity)
  _internal\                       (PyInstaller support files; do not edit)
  frontend\dist\                   (bundled React build; read-only)
    index.html
    assets\
    favicon.ico, favicon-*.png
    brand\
      lockverity-symbol.png
      lockverity-horizontal-logo.png
  alembic\                         (frozen Alembic scripts; read-only)
    versions\
  alembic.ini                      (frozen config; read-only)
  favicon.ico                      (approved Part A icon)
  brand\                           (approved Part A PNGs)
  LICENSE                          (MIT)
  README-PORTABLE.txt              (this file, bundled at the frozen root)
  THIRD_PARTY_NOTICES.txt          (Python + frontend dependency licences)
  BUILD-MANIFEST.json              (build provenance)
  SHA256SUMS.txt                   (SHA-256 of every user-facing file)
  PAYLOAD-MANIFEST.json            (complete file-tree integrity manifest)
```

The frozen directory is treated as read-only application
content. The portable never writes databases, logs,
state files, or user data beside the executable.
Deleting the extracted folder does not affect any
runtime state.

## Default runtime home

The runtime stores databases, logs, the state file,
the start lock, and any operator overrides in the
**runtime home** under the user's local AppData:

```
%LOCALAPPDATA%\Lockverity\
  data\        (SQLite databases)
  logs\        (rotating lockverity.log)
  run\         (state file and start lock)
  config\      (operator overrides, if any)
```

The runtime home is created on first start. An
operator can override the path with the
``LOCKVERITY_HOME`` environment variable; the
override is honoured by both the graphical launcher
and the CLI.

## Graphical launcher (``Lockverity.exe``)

Current-source development artifacts use a normal, resizable Windows
desktop window with the approved Lockverity icon and title. The window
uses Microsoft Edge WebView2 to render the existing React application;
FastAPI remains on loopback and remains the only frontend/backend
integration surface. No console window or default browser is opened
during normal GUI launch.

On invocation, the launcher:

1. Resolves the runtime home (default
   ``%LOCALAPPDATA%\Lockverity``).
2. Acquires the single-GUI-instance Windows mutex.
3. Starts the existing Part B2 foreground runtime on
   ``127.0.0.1`` and waits for readiness before showing the window.
4. Renders only the exact loopback origin in the embedded WebView.
   Approved HTTPS documentation/resource links open in the system
   browser; unexpected external navigation is blocked.
5. On a stale, conflicting, or unhealthy instance, runs bounded
   recovery and refuses to terminate unrelated
   processes. If automatic safe recovery is
   impossible, shows a native message box with the
   log path and a recommendation to run
   ``lockverity-cli.exe doctor``.
6. Gracefully stops and reaps the owned backend when the main window
   closes. If the backend exits unexpectedly, the window also closes.

A second double-click focuses/restores the existing window and does not
start another server. The fixed default port remains ``8000`` because it
is part of the proven CLI/state identity contract; ``--port <N>`` is
available for an explicit conflict workaround. The launcher never
requires administrator rights.

The published v2.1.2 binaries predate this source change: their
``Lockverity.exe`` is the historical short-lived launcher that starts or
reuses a background runtime and opens the loopback URL in the default
browser. Those immutable assets are not replaced by this work.

The launcher exit codes are documented in
``backend/app/launcher/__init__.py`` so a future
installer can match them:

| Code | Meaning                                                   |
| ---- | --------------------------------------------------------- |
| 0    | Success (window closed normally or existing window focused). |
| 20   | Generic launcher error.                                   |
| 21   | Port already in use by an unrelated process.              |
| 22   | Database migration failure.                               |
| 23   | Health endpoint did not report ready in time.             |
| 24   | Bundled frontend dist is missing or invalid.              |
| 25   | Microsoft Edge WebView2 Runtime is missing.                |

## Console CLI (``lockverity-cli.exe``)

The console CLI exposes the documented v2.1 Part B2
subcommands. The contract is identical to the
source-based ``lockverity`` command; an operator who
switches between the two should not see a difference
beyond the file extension.

The CLI exit codes are:

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| 0    | Success.                                                     |
| 1    | Generic error.                                               |
| 2    | Health or allow-remote guard violation.                      |
| 64   | Command-line usage error.                                    |
| 130  | SIGINT / ``CTRL_BREAK_EVENT`` (foreground mode interrupted). |

The full command reference is in
``docs/release-checklist.md``.

## First-launch behaviour

The first time a portable extraction is launched:

1. The runtime home is created under
   ``%LOCALAPPDATA%\Lockverity``.
2. The bundled Alembic migrations run against a fresh
   SQLite database. Existing databases are preserved
   verbatim; only missing migrations are applied.
3. The server starts on ``127.0.0.1:8000`` unless
   overridden.
4. A current-source build shows the native desktop window after the
   health check succeeds. A published v2.1.2 binary opens the trusted
   URL in the default browser.

The first-launch migration can take a few seconds on
older hardware. The launcher shows the log path in a
native message box if the migration or the health
probe does not complete in time.

## Troubleshooting

If the launcher shows a native message box, the box
contains the runtime log path. Run the diagnostic:

```
lockverity-cli.exe doctor --json
```

The ``doctor`` command inspects the runtime
environment (Python interpreter is frozen so the
check is informational only; SQLite, Alembic, the
frontend dist, the start lock, the port, the brand
assets, the licence, and the live process identity)
and prints a structured JSON report.

For background output, inspect the rotating log at
``%LOCALAPPDATA%\Lockverity\logs\lockverity.log`` or
use the documented ``lockverity-cli.exe logs``
subcommand.

If the port is in use by an unrelated process, the
launcher refuses to terminate the process and shows a
message box that names the conflicting port. The
operator can either stop the conflicting process or
launch with a different port.

The portable does not install WebView2. If the native launcher reports
that the Evergreen Runtime is missing, install it from Microsoft's
official WebView2 download page and relaunch. The installer distribution
handles this prerequisite automatically; the portable remains a
no-installer archive.

## Unsigned-build / SmartScreen warning

The portable package is not code-signed. Windows may
display **Unknown publisher** and/or a Microsoft Defender
SmartScreen warning when an unsigned build is launched.
Verify the package integrity using the ``SHA256SUMS.txt``
file and the documented ``BUILD-MANIFEST.json`` before
deciding whether to proceed.

See the [Code signing policy](code-signing-policy.md) and
[Privacy policy](privacy.md).

## Antivirus heuristic detections

Unsigned or newly distributed binaries may trigger heuristic
antivirus detections. The portable package uses
the standard PyInstaller onedir layout without UPX or
any other compression. Verify the published SHA-256 and
provenance; suspected false positives can be submitted to
the relevant vendor for review with the ``SHA256SUMS.txt``
and ``BUILD-MANIFEST.json``. The source commit SHA is
verifiable on the public GitHub repository.

## Not a Windows installer

The portable is a "drop anywhere" artefact. The v2.1.2
release also ships a per-user Windows installer for
operators who prefer a Start Menu entry and a registered
uninstall path; see
[`docs/windows-installer.md`](windows-installer.md) for
the installer reference. Installing the portable into
``Program Files`` is supported but not recommended
because the runtime home continues to live under
``%LOCALAPPDATA%\Lockverity`` and the user
preference is to keep the portable directory
self-contained.

## No service, no auto-update, no telemetry

The portable package does not install a Windows
service, a scheduled task, a registry autorun, or a
firewall rule. It does not include an automatic
update mechanism. It does not collect, transmit, or
phone home any telemetry. The web application binds to
``127.0.0.1``. GitHub repository intake necessarily
contacts GitHub to resolve and download the submitted
repository. At scan execution, OSV, deps.dev, and OpenSSF
Scorecard are enabled by default when applicable and can
be selected independently; disabled providers receive no
client, cache, or network call. See the
[privacy policy](privacy.md). The portable package also
includes this policy as ``PRIVACY.md``.

## Clean uninstall

To uninstall the portable:

1. Stop the running instance with
   ``lockverity-cli.exe stop`` or close the native desktop window.
2. Delete the extracted folder.
3. Optionally delete the runtime home at
   ``%LOCALAPPDATA%\Lockverity`` to remove all
   databases, logs, and state.

Step 3 is independent of step 2: deleting the
extracted folder does not affect the runtime home, and
deleting the runtime home does not affect the extracted
folder. The two are intentionally separate so an
operator can wipe state without losing the application
or vice versa.

## Build instructions for maintainers

The portable package is built by the committed
``backend/scripts/build_windows_portable.py``
script. The script is the single source of truth for
how the artefact is produced.

Prerequisites on the build host:

* Windows x64
* Python 3.12.x in the project venv
* Node.js 22.22.0 or newer on PATH
* The ``build`` optional-dependency group installed:
  ``pip install -e '.[build]'``
* Microsoft Edge WebView2 Evergreen Runtime installed for native GUI
  smoke testing (it is not copied into the portable ZIP)
* The approved Part A brand assets under
  ``frontend/public/favicon.ico`` and
  ``frontend/public/brand\`` (committed; do not
  regenerate)

Build command:

```
python backend\scripts\build_windows_portable.py
```

Useful options:

* ``--clean`` removes the dedicated packaging work
  directory before starting.
* ``--skip-frontend-build`` skips the
  ``prepare_frontend_dist.py`` step (assumes a fresh
  ``frontend/dist`` is already in place).
* ``--skip-smoke`` skips the packaged smoke checks
  (faster; do not use for a release).
* ``--output-dir <path>`` writes the artefact under
  ``<path>\windows\`` instead of the default
  ``dist\windows\``.
* ``--keep-work`` retains the dedicated packaging
  work directory for inspection.
* ``--json-report <path>`` writes a structured JSON
  report of the build to ``<path>``.

The build script writes the SHA-256 of every
user-facing file to ``SHA256SUMS.txt`` and a structured
``BUILD-MANIFEST.json`` containing the source commit
SHA, the build timestamp in UTC, the Python and
PyInstaller versions, the Node and npm versions, the
Alembic head, and the SHA-256 of every approved brand
asset. It also writes ``PAYLOAD-MANIFEST.json``, the
complete file-tree integrity manifest: the SHA-256 and
size of EVERY regular file in the payload (the frozen
runtime under ``_internal\``, the frontend assets, the
Alembic configuration and migrations, the notices and
licences, and everything else actually distributed).
The manifest covers every file except itself; it is
generated last so ``SHA256SUMS.txt`` and
``BUILD-MANIFEST.json`` are covered by it. Lockverity
is unsigned: the manifest establishes payload
completeness and integrity relative to the build
output, not code signing or publisher authentication.
The distributed ZIP hash is the outer verification
point.

The committed ``backend/pyinstaller/lockverity.spec``
and ``backend/pyinstaller/cli.spec`` files are the
canonical PyInstaller inputs. Do not commit
auto-generated mutable spec output; the committed
specs are reviewed and pinned.

## What the portable does not include

The following items are explicit future work and are
**not** included in the v2.1.2 portable:

* Code signing and SmartScreen reputation building.
* Automatic update mechanism.
* Linux or macOS packaging.
* Docker packaging.
* A system service, scheduled task, or
  ``launchd``/``systemd`` integration.
* Backup / restore tooling.
* Cloud sync, multi-user, or authentication.
* Telemetry, crash reporting, or outbound analytics.
* A bundled fixed-version browser engine. Current builds use the
  supported Evergreen WebView2 Runtime installed on Windows.

The v2.1.2 Windows installer is a separate distribution
that uses Inno Setup; see
[`docs/windows-installer.md`](windows-installer.md). Code
signing, automatic updates, and additional packaging
targets are separate deliverables that may be addressed
on later release tracks.

## Verifying the artefact

The SHA-256 hashes of every user-facing file are in
``SHA256SUMS.txt``. The source commit, the build
timestamp, the Python and PyInstaller versions, the
Node and npm versions, the Alembic head, and the
approved brand-asset hashes are in
``BUILD-MANIFEST.json``. The dependency inventory
(including declared licences) is in
``THIRD_PARTY_NOTICES.txt``. The documented public build
command builds the artefact from the recorded source and inputs;
deterministic, bit-for-bit reproducibility is not claimed.
