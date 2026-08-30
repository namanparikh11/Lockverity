# Lockverity v2.1.2 — Install guide

This is the central install guide for **Lockverity v2.1.2**. It
covers every supported install path: the Windows installer, the
Windows portable, the macOS source-based setup, and the Linux
source-based setup.

The published release assets are hosted on the GitHub Release
[`checkpoint-v2.1.2-public-release`](https://github.com/namanparikh11/lockverity/releases/tag/checkpoint-v2.1.2-public-release).
The release page is the canonical download index. The
`/releases/latest` URL always points to the current latest
release.

| Asset | Direct link |
| --- | --- |
| Release index | https://github.com/namanparikh11/lockverity/releases/tag/checkpoint-v2.1.2-public-release |
| Latest redirect | https://github.com/namanparikh11/lockverity/releases/latest |
| Windows installer | [Lockverity-2.1.2-windows-x64-setup.exe](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-setup.exe) |
| Windows portable | [Lockverity-2.1.2-windows-x64-portable.zip](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-portable.zip) |
| Release checksum file | [Lockverity-2.1.2-SHA256SUMS.txt](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-SHA256SUMS.txt) |
| Portable checksum file | [Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt) |
| Installer manifest | [INSTALLER-MANIFEST.json](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/INSTALLER-MANIFEST.json) |
| Build manifest | [BUILD-MANIFEST.json](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/BUILD-MANIFEST.json) |

## Code signing policy

- [Code signing policy](code-signing-policy.md)
- [Privacy policy](privacy.md)

Lockverity v2.1.2 is unsigned. SignPath Foundation approval was not
obtained; Lockverity has no code-signing integration, and no
SignPath-signed release exists.

> **Code → Download ZIP** downloads the Lockverity **source code**.
> It is not the Windows installer and is not the Windows portable.
> The source ZIP is intended for development and auditing only.


## What changed in v2.1.2

v2.1.2 is a narrow Windows-only hotfix on top of v2.1.1.
All documented install paths and runtime contracts are
unchanged. The two Windows-specific fixes are:

1. **Settings → Installed apps shows the Lockverity
   icon.** The v2.1.0 / v2.1.1 installer declared
   ``UninstallDisplayIcon={app}\Lockverity.exe`` --
   a path that does not exist on disk (the launcher
   is installed under ``{app}\app``) and that
   omitted the explicit ``,0`` icon index. v2.1.2
   fixes the path to
   ``{app}\app\Lockverity.exe,0`` and rebuilds the
   canonical Windows ICO with the full size set
   the Windows shell queries
   (16/24/32/48/64/128/256).
2. **Authenticode signing-readiness hooks.** A
   disabled-by-default helper
   (``backend/scripts/_authenticode_sign.py``)
   exposes the documented env-var contract for a
   future trusted Authenticode provider. The
   release is **not signed**; ``Unknown publisher``
   and SmartScreen warnings may still appear.
   Verify the SHA-256 hash before installing.

## Choosing a distribution

| Pick this | If you are a… |
| --- | --- |
| **Windows installer** (`.exe`) | Normal Windows user who wants the simplest install |
| **Windows portable** (`.zip`) | Operator who cannot install (locked-down laptop, USB-only workflow) |
| **Source code** (the `Code → Download ZIP` button on the GitHub repository) | Developer or auditor who wants to read or modify the code |
| macOS source setup | Developer or technical operator on macOS |
| Linux source setup | Developer or technical operator on Linux |

The v2.1.2 release does **not** publish a DMG, PKG, AppImage, DEB,
or RPM asset. macOS and Linux are source-based developer
workflows; they are not at the same packaged-acceptance level as
the Windows installer or portable.

## Windows installer

The Windows installer is the recommended option for most Windows
users. It is per-user (no admin, no UAC), per-architecture (x64
only), and self-contained.

### Download and verify

1. Download
   [`Lockverity-2.1.2-windows-x64-setup.exe`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-setup.exe).
2. Verify the SHA-256:

   ```powershell
   Get-FileHash .\Lockverity-2.1.2-windows-x64-setup.exe -Algorithm SHA256
   ```

   The output hash must equal
   `6ec50d612c8edb49ab9a7e8cdf20b071a0094070998eee8555ef09bbbdc5af5b`.
   The same value is in
   [`Lockverity-2.1.2-SHA256SUMS.txt`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-SHA256SUMS.txt)
   and in the bundled `INSTALLER-MANIFEST.json` next to the
   installer EXE.
3. Double-click the installer.
4. Read the licence, accept it, and click **Install**. No UAC
   prompt appears because the installer is per-user.
5. Optionally check **Launch Lockverity** on the completion page
   to open the trusted loopback URL in your default browser.

After install, the Start Menu contains a **Lockverity** folder
with the application shortcut, the documentation link, and the
**Uninstall Lockverity** entry. Apps & Features shows the
per-user **Lockverity** entry under the current user.

### Silent install

```powershell
Lockverity-2.1.2-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- ^
    /DIR="C:\Apps\Lockverity" ^
    /LOG="C:\Temp\lockverity-install.log"
```

| Switch | Meaning |
| --- | --- |
| `/VERYSILENT` | No wizard pages or progress UI. |
| `/SUPPRESSMSGBOXES` | Suppresses any blocking message box. |
| `/NORESTART` | Do not request a Windows reboot. |
| `/SP-` | Skip the **Welcome** page. |
| `/DIR=<path>` | Override the install path. Supports spaces and Unicode. |
| `/LOG=<file>` | Write the install log to the given file. |

### SmartScreen and antivirus

The installer is **not code-signed**. Windows may display
**Unknown publisher** and/or a Microsoft Defender SmartScreen
warning when an unsigned build is launched.

Unsigned or newly distributed binaries may trigger heuristic
antivirus detections. Verify the published SHA-256 and provenance;
suspected false positives can be submitted to the relevant vendor
for review. The documented public installer build command is
`python backend\scripts\build_windows_installer.py`.

### Where things live after install

| What | Path |
| --- | --- |
| Application files | `%LOCALAPPDATA%\Programs\Lockverity\app\` |
| Database | `%LOCALAPPDATA%\Lockverity\data\` |
| Logs | `%LOCALAPPDATA%\Lockverity\logs\` |
| Runtime state | `%LOCALAPPDATA%\Lockverity\run\` |
| Operator overrides | `%LOCALAPPDATA%\Lockverity\config\` |

The runtime home is preserved by uninstall; see below.

### Launch and CLI

After install, click the Start Menu shortcut, or invoke the
installed binary directly:

```powershell
& "$env:LOCALAPPDATA\Programs\Lockverity\app\Lockverity.exe"
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" --version
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" doctor
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" status
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" stop
```

The launcher starts the runtime in the background, waits for
`/api/v1/health`, then opens the trusted loopback URL in your
default browser. A second invocation of the launcher reuses the
existing instance (same PID, same port, same instance id).

### Reinstall / repair

Running the installer again performs a safe reinstall of the
accepted payload. The installer:

- detects a live installed instance via the documented Part B2
  identity check (state file + PID + creation time + instance
  UUID);
- requests a graceful stop via the installed
  `lockverity-cli.exe stop`;
- replaces the application files in
  `%LOCALAPPDATA%\Programs\Lockverity\app\`;
- preserves your runtime data, databases, and logs in
  `%LOCALAPPDATA%\Lockverity\`;
- does not duplicate Start Menu or desktop shortcuts;
- does not rewrite the uninstaller registration.

The reinstall never terminates a process based on a PID alone,
and it never kills unrelated processes. If a safe shutdown
cannot be verified, the installer aborts with an actionable
message pointing at `lockverity-cli.exe doctor` and the runtime
log path.

### Uninstall

Start the uninstaller from
**Start Menu → Lockverity → Uninstall** or from
**Settings → Apps → Lockverity → Uninstall**. The uninstaller:

- detects a live installed instance and requests a graceful stop;
- removes `%LOCALAPPDATA%\Programs\Lockverity\app\`;
- removes the Start Menu folder and the optional desktop
  shortcut;
- removes the per-user uninstaller registration;
- **preserves** `%LOCALAPPDATA%\Lockverity\` (databases, logs,
  configuration);
- shows a final dialog pointing at the retained-data path.

To remove runtime data manually after uninstalling:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Lockverity"
```

The full per-user, no-admin, no-service installer contract is
documented in
[`docs/windows-installer.md`](windows-installer.md).

## Windows portable

The Windows portable is a self-contained ZIP that bundles the
FastAPI backend, the cross-platform `lockverity-cli` command, the
React frontend, the Alembic migrations, and the approved Part A
brand assets. No separately installed Python or Node.js is
required; no administrator rights; no Windows service, scheduled
task, or registry autorun.

### Download and verify

1. Download
   [`Lockverity-2.1.2-windows-x64-portable.zip`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-portable.zip).
2. Verify the SHA-256:

   ```powershell
   Get-FileHash .\Lockverity-2.1.2-windows-x64-portable.zip -Algorithm SHA256
   ```

   The output hash must equal
   `7b8824e1fa780f46caafef25e84f3249e4f7f5cb11d5c5e22cfcf4491bfe6996`.
   The same value is in
   [`Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt)
   and in the bundled `SHA256SUMS.txt` inside the ZIP.
3. **Extract the entire archive** to any directory the operator
   controls (for example `C:\Tools\Lockverity` or
   `C:\Users\<you>\Lockverity`). Do not run from inside the ZIP
   viewer; extract first.
4. Double-click `Lockverity.exe` to start the runtime and open
   the trusted local URL in the default browser.
5. From a second terminal, use `lockverity-cli.exe` for the
   documented `start`, `stop`, `status`, `open`, `doctor`, and
   `logs` subcommands. `lockverity-cli.exe doctor --json` prints
   a structured diagnostic report.

### Runtime home

The portable does **not** mean runtime data is stored beside the
EXE. The runtime home still defaults to
`%LOCALAPPDATA%\Lockverity` and is created on first launch. To
override the path, set the `LOCKVERITY_HOME` environment
variable.

### Removal

1. Stop the running instance with
   `lockverity-cli.exe stop` (or close the launcher if no
   instance is running).
2. Delete the extracted folder.
3. Optionally delete the runtime home at
   `%LOCALAPPDATA%\Lockverity` to remove all databases, logs,
   and state.

Step 3 is independent of step 2: deleting the extracted folder
does not affect the runtime home, and deleting the runtime home
does not affect the extracted folder.

The full portable operator reference is in
[`docs/windows-portable.md`](windows-portable.md).

## macOS source setup

Lockverity v2.1.2 does **not** publish a packaged macOS binary
(no `.dmg`, no `.pkg`, no signed `.app`). The macOS workflow is
**source-based only** and is intended for developers or technical
operators.

### Prerequisites

- **macOS 12 (Monterey)** or newer.
- **Python 3.12** (`brew install python@3.12` or your preferred
  manager). The lockfile pins 3.12.x.
- **Node.js >= 22.22.0** (use `nvm use`, `fnm use`, or
  `volta pin`). The repo ships an `.nvmrc` pinning the floor.
- **Xcode Command Line Tools** (`xcode-select --install`).

### Source-based installation

```bash
# Clone the repository
git clone https://github.com/namanparikh11/lockverity.git
cd lockverity

# Backend: create the venv and install
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend: install and build the production bundle
cd ../frontend
npm ci
npm run build
cd ..

# Bring the database schema up to head
cd backend
alembic upgrade head
cd ..
```

### Start, status, doctor, logs, stop

Lockverity ships a cross-platform `lockverity` CLI. The CLI wraps
the single-port production runtime.

```bash
cd backend
source .venv/bin/activate

export LOCKVERITY_ENVIRONMENT=production
export LOCKVERITY_SERVE_FRONTEND=true
export LOCKVERITY_FRONTEND_DIST="../frontend/dist"
export LOCKVERITY_DATABASE_URL="sqlite:///$HOME/Library/Application Support/Lockverity/data/lockverity.sqlite"

lockverity doctor
lockverity start
lockverity status --json
lockverity open
# ...work...
lockverity stop
```

The runtime home on macOS defaults to
`~/Library/Application Support/Lockverity` and is created on
first start. The state file, the database, the rotating log,
and the start lock all live under that path. Override the path
with `--home <path>` or `LOCKVERITY_HOME=<path>` for one
invocation or one shell respectively.

The full single-port runtime contract is in
[`docs/release-checklist.md` § 4a](release-checklist.md). The CLI
contract is in [`docs/release-checklist.md` § 4b](release-checklist.md).

## Linux source setup

Lockverity v2.1.2 does **not** publish a packaged Linux binary
(no `AppImage`, no `.deb`, no `.rpm`, no Flatpak, no Snap). The
Linux workflow is **source-based only** and is intended for
developers or technical operators.

### Prerequisites

- **Linux x86_64** (the build host for the Windows portable is
  Windows; the application itself is cross-platform Python).
  glibc 2.31+ (RHEL 8 / Ubuntu 20.04 equivalent or newer).
- **Python 3.12** (your distribution's package manager, or
  `pyenv install 3.12`).
- **Node.js >= 22.22.0** (use `nvm use`, `fnm use`, or
  `volta pin`). The repo ships an `.nvmrc` pinning the floor.
- **Build tools** for the Python wheels you may need
  (`build-essential`, `libffi-dev`, `libssl-dev`).

### Source-based installation

```bash
# Clone the repository
git clone https://github.com/namanparikh11/lockverity.git
cd lockverity

# Backend: create the venv and install
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend: install and build the production bundle
cd ../frontend
npm ci
npm run build
cd ..

# Bring the database schema up to head
cd backend
alembic upgrade head
cd ..
```

### Start, status, doctor, logs, stop

```bash
cd backend
source .venv/bin/activate

export LOCKVERITY_ENVIRONMENT=production
export LOCKVERITY_SERVE_FRONTEND=true
export LOCKVERITY_FRONTEND_DIST="../frontend/dist"
export LOCKVERITY_DATABASE_URL="sqlite:///${XDG_DATA_HOME:-$HOME/.local/share}/lockverity/data/lockverity.sqlite"

lockverity doctor
lockverity start
lockverity status --json
lockverity open
# ...work...
lockverity stop
```

The runtime home on Linux defaults to
`${XDG_DATA_HOME:-$HOME/.local/share}/lockverity` and is created
on first start. The state file, the database, the rotating log,
and the start lock all live under that path. Override the path
with `--home <path>` or `LOCKVERITY_HOME=<path>` for one
invocation or one shell respectively.

The full single-port runtime contract is in
[`docs/release-checklist.md` § 4a](release-checklist.md). The CLI
contract is in [`docs/release-checklist.md` § 4b](release-checklist.md).

## Checksum verification

The authoritative v2.1.2 release tag is
`checkpoint-v2.1.2-public-release`, and its release source commit is
`2aa8729832ac94093966a67d2f3f76c0e2110d15`. The authoritative
published asset values are:

| Asset | SHA-256 | Size |
| --- | --- | --- |
| `Lockverity-2.1.2-windows-x64-setup.exe` | `6ec50d612c8edb49ab9a7e8cdf20b071a0094070998eee8555ef09bbbdc5af5b` | 31,227,275 bytes |
| `Lockverity-2.1.2-windows-x64-portable.zip` | `7b8824e1fa780f46caafef25e84f3249e4f7f5cb11d5c5e22cfcf4491bfe6996` | 56,198,519 bytes |
| `INSTALLER-MANIFEST.json` | `7e4c0328d6f3357429d8141490e69680357a7df06dfcc88fa8a89a35bf590025` | — |
| `BUILD-MANIFEST.json` | `e01692940e645e16639a84faf45334c77f22bf8a13a4c099302a24d21b47460b` | — |
| `Lockverity-2.1.2-SHA256SUMS.txt` | `ec324e6cad86a7a535567f5e0898559d560717df58e8a4a842d7630058306e9d` | — |
| `Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt` | `d297239fe49876d444563d7c116acc7e7d447002fcb26bec419ad2f27a649843` | — |

The release checksum file is
[`Lockverity-2.1.2-SHA256SUMS.txt`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.2-public-release/Lockverity-2.1.2-SHA256SUMS.txt).

The internal `Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt`
is a separate asset that lists the SHA-256 of every file inside
the portable ZIP (the launcher EXE, the CLI EXE, the build
manifest, the licence, the portable README, and the third-party
notices file).

**Windows (PowerShell):**

```powershell
Get-FileHash .\Lockverity-2.1.2-windows-x64-setup.exe -Algorithm SHA256
Get-FileHash .\Lockverity-2.1.2-windows-x64-portable.zip -Algorithm SHA256
```

**macOS / Linux:**

```bash
shasum -a 256 Lockverity-2.1.2-windows-x64-setup.exe
shasum -a 256 Lockverity-2.1.2-windows-x64-portable.zip
```

If the hash does not match, do not run the asset. Re-download
from the same URL; if the hash still differs, do not run the
asset and report the discrepancy to the maintainers.

## Previous v2.1.0 release (historical)

The v2.1.0 and v2.1.1 release assets are preserved on the
`checkpoint-v2.1.0-public-release` tag and remain
downloadable from the original release URL. v2.1.1 is a
code-only hotfix on top of v2.1.0; the v2.1.0 binaries
are not republished.

| Asset | SHA-256 | Size |
| --- | --- | --- |
| `Lockverity-2.1.0-windows-x64-setup.exe` | `db90854369d2bc0ca09fc935abe3f5213260f12229b979ab3bb55dfb5d73bec6` | 31,128,774 bytes |
| `Lockverity-2.1.0-windows-x64-portable.zip` | `a4414372c964f0f50d6e6a864d5a8b8c288acd8a5008e8d45a0ed67e8e58f302` | 56,088,933 bytes |

## Unsigned-build warning

The Lockverity v2.1.0 release is **not code-signed**:

- The Windows installer and the PE executables distributed inside
  the portable package are unsigned. The portable ZIP itself does
  not receive Authenticode signing. Windows may display
  **Unknown publisher** and/or a Microsoft Defender SmartScreen
  warning when an unsigned build is launched.
- Unsigned or newly distributed binaries may trigger heuristic
  antivirus detections. Verify the published SHA-256 and provenance;
  suspected false positives can be submitted to the relevant vendor
  for review. The source and build commands are public.

The build scripts never claim Microsoft certification, code
signing, or SmartScreen reputation. The
[`INSTALLER-MANIFEST.json`](https://github.com/namanparikh11/lockverity/releases/download/checkpoint-v2.1.0-public-release/INSTALLER-MANIFEST.json)
records the unsigned status honestly.

## First launch

After the first start, the runtime:

1. Creates the runtime home
   (`%LOCALAPPDATA%\Lockverity` on Windows,
   `~/Library/Application Support/Lockverity` on macOS,
   `${XDG_DATA_HOME:-$HOME/.local/share}/lockverity` on Linux).
2. Runs Alembic migrations against a fresh SQLite database.
   Existing databases are preserved verbatim; only missing
   migrations are applied.
3. Starts the server on `127.0.0.1:8000` unless overridden.
4. Opens the trusted local URL in the default browser.

The first-launch migration can take a few seconds on older
hardware. The launcher shows the log path in a native message
box if the migration or the health probe does not complete in
time.

## CLI basics

The CLI is the supported way to start, stop, and inspect the
local instance on every platform. The full command reference is
in [`docs/release-checklist.md` § 4b](release-checklist.md).

| Command | Purpose |
| --- | --- |
| `lockverity start` | Run Alembic migrations, launch the runtime (detached by default; foreground with `--foreground`), wait for `/api/v1/health`, write the state file. |
| `lockverity stop` | Verify the recorded identity, send `SIGTERM` (POSIX) or `CTRL_BREAK_EVENT` (Windows), wait for the process to exit, clear the state file and release the start lock. |
| `lockverity status` | Show the current instance state in human-readable text or in the documented `--json` schema. |
| `lockverity open` | Open the local URL in the default browser via the platform `webbrowser` facility. |
| `lockverity doctor` | Run a read-only diagnostic checklist and report each check as PASS / WARN / FAIL. |
| `lockverity logs` | Show the rotating runtime log (bounded tail, optional `--follow`). |

The `status` subcommand follows a separate, documented contract:
`0` for running-and-healthy, `1` for stopped, and `2` for
unhealthy, stale, or misconfigured.

## Runtime-data locations

| Platform | Runtime home |
| --- | --- |
| Windows (installer) | `%LOCALAPPDATA%\Lockverity` |
| Windows (portable) | `%LOCALAPPDATA%\Lockverity` (NOT beside the EXE) |
| macOS | `~/Library/Application Support/Lockverity` |
| Linux | `${XDG_DATA_HOME:-$HOME/.local/share}/lockverity` |

The runtime home contains four sub-directories (`data/`,
`logs/`, `run/`, `config/`) and is created on first start.
Override the path with `--home <path>` (one invocation) or
`LOCKVERITY_HOME=<path>` (one shell).

## Uninstall / removal

**Windows installer:**

- Start the uninstaller from
  **Start Menu → Lockverity → Uninstall** or from
  **Settings → Apps → Lockverity → Uninstall**.
- The uninstaller preserves `%LOCALAPPDATA%\Lockverity\`.
- To wipe runtime data manually:
  `Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Lockverity"`.

**Windows portable:**

- Stop the running instance with `lockverity-cli.exe stop`.
- Delete the extracted folder.
- Optionally delete `%LOCALAPPDATA%\Lockverity` to wipe runtime
  data.

**macOS / Linux:**

- Stop the running instance with `lockverity stop`.
- Delete the cloned source directory (or just remove
  `.venv`, `frontend/node_modules`, and `frontend/dist` if you
  want to keep the source for later).
- Optionally delete the runtime home
  (`~/Library/Application Support/Lockverity` on macOS or
  `${XDG_DATA_HOME:-$HOME/.local/share}/lockverity` on Linux) to
  wipe the database, logs, and state.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Installer does not start | Verify the SHA-256 of the installer EXE; run with `/LOG=<file>` and inspect the log. |
| Browser does not open after install | The installer never launches a browser. Invoke `Lockverity.exe` directly to see any error output. |
| Runtime is not reachable at `http://127.0.0.1:8000/` | Run `lockverity-cli.exe doctor` (or `lockverity doctor` on macOS / Linux). Read `%LOCALAPPDATA%\Lockverity\logs\lockverity.log` (or the equivalent runtime log path). |
| Reinstall fails with "still running" | Run `lockverity-cli.exe status`; if the recorded PID is dead but the state file remains, run `lockverity-cli.exe stop` and retry. |
| SmartScreen or Unknown publisher warning | Windows may display either warning for an unsigned build. Verify the published SHA-256 and provenance before deciding whether to proceed. |
| Antivirus flags the binary | Verify the published SHA-256 and provenance. Suspected false positives can be submitted to the relevant vendor for review. |
| Port `8000` is in use by an unrelated process | The runtime refuses to terminate the unrelated process. Stop the conflicting process or launch with `--port <N>`. |

The full installer-specific troubleshooting guide is in
[`docs/windows-installer.md`](windows-installer.md). The full
portable-specific troubleshooting guide is in
[`docs/windows-portable.md`](windows-portable.md).

## Distinction between release assets and source archives

| What you downloaded | What it is |
| --- | --- |
| `Lockverity-2.1.2-windows-x64-setup.exe` | The Windows installer. Run it to install. |
| `Lockverity-2.1.2-windows-x64-portable.zip` | The Windows portable. Extract it and run `Lockverity.exe`. |
| `Lockverity-2.1.2-SHA256SUMS.txt` | SHA-256 checksums of the primary release assets. |
| `Lockverity-2.1.2-windows-x64-portable-SHA256SUMS.txt` | SHA-256 checksums of the files inside the portable ZIP. |
| `INSTALLER-MANIFEST.json` | The installer's source identity, embedded payload identity, code-signing status, and Inno Setup compiler fingerprint. |
| `BUILD-MANIFEST.json` | The portable's source identity, build timestamp, Python / PyInstaller / Node / npm versions, and approved brand-asset hashes. |
| `Source code (zip)` / `Source code (tar.gz)` (the `Code → Download ZIP` button) | The Lockverity repository source. Not a packaged application. Use it to develop, audit, or build your own installer / portable. |
| `Lockverity-<version>-windows-x64-portable.zip` (in `dist/windows/`) | The maintainer-side build artefact. Same as the released portable, but generated by `python backend\scripts\build_windows_portable.py`. |

The release assets and the source archive are **not interchangeable**:

- The release assets run on a clean Windows x64 box with no
  Python or Node.js installed.
- The source archive requires Python 3.12, Node.js >= 22.22.0,
  the build tooling, and the v2.1 brand assets to produce a
  working build.
- The release assets can be checked against the per-asset SHA-256
  values and the `INSTALLER-MANIFEST.json` /
  `BUILD-MANIFEST.json`. There is no
  Authenticode signature, no GPG signature, and no Apple
  notarisation in v2.1.2.

## Cross-references

- [`README.md`](../README.md) — current milestone, product
  boundaries, source setup, and security links.
- [`docs/windows-installer.md`](windows-installer.md) — full
  Windows installer reference.
- [`docs/windows-portable.md`](windows-portable.md) — full Windows
  portable reference.
- [`docs/release-checklist.md`](release-checklist.md) — single-port
  runtime and CLI contract; verification script.
- [`docs/security-boundaries.md`](security-boundaries.md) —
  non-execution guarantee and security boundaries.
- [`docs/provider-honesty.md`](provider-honesty.md) — provider
  availability policy.
- [`CHANGELOG.md`](../CHANGELOG.md) — version history.
- [`RELEASE_NOTES.md`](../RELEASE_NOTES.md) — reviewer-facing
  status.
- [`SECURITY.md`](../SECURITY.md) — supported versions and
  responsible disclosure.
