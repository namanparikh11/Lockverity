# Lockverity v2.1.2 — Windows x64 per-user installer

The Lockverity v2.1.2 Windows installer is a per-user, x64,
self-contained EXE that installs the accepted Windows portable
payload (`Lockverity-2.1.2-windows-x64-portable.zip`) into
`%LOCALAPPDATA%\Programs\Lockverity`. The installer does **not**
modify the operator's `PATH`, does **not** install a Windows
service, does **not** register an autorun entry, does **not** add
a firewall rule, and does **not** require administrator privilege.

> **Publication integrity note:** the published v2.1.2 installer,
> portable payload, tag, hashes, manifests, and provenance remain
> immutable and retain their historical default-browser launcher. The
> native-window and WebView2 bootstrapper behaviour below applies to
> development/smoke installers built from current `main`; it does not
> describe a republished v2.1.2 release asset.

### Accepted v2.1 B3A portable payload (supersession ledger)

The v2.1 B3B installer embeds the accepted Part B3A portable payload
that was built from a commit which already contains the
**CWD-independent runtime database** fix. The fix is the v2.1
Part B3B release-blocker correction: the historical default
`sqlite:///./lockverity.sqlite` (CWD-relative) was replaced with a
runtime-home-relative absolute path so an installed Lockverity never
writes its database beside the installed executables.

> **This ledger is now an informational record of every portable
> payload that has been published for v2.1.** The build script
> (`backend/scripts/build_windows_installer.py`) and the
> acceptance script (`backend/scripts/b3b_acceptance.py`) do not
> read or assert against any of the generated hashes recorded
> here. Every generated binary hash is read at build /
> acceptance time from the payload's own
> `SHA256SUMS.txt` and `BUILD-MANIFEST.json` and is written
> to the external `INSTALLER-MANIFEST.json` and
> `SHA256SUMS.txt`. The build pins the payload's *source
> identity* (the B3A source commit) and the product version,
> and nothing else. This design keeps a single source commit
> valid across any number of portable rebuilds (PyInstaller
> timestamps, bootloader versions, etc.) and removes the
> historical "rebuild -> source-commit -> rebuild" cycle.

| Ledger entry | Source commit | Portable ZIP SHA-256 | Lockverity.exe SHA-256 | lockverity-cli.exe SHA-256 | Status |
| --- | --- | --- | --- | --- | --- |
| Original v2.1 B3A | `81b400bc40ae6ada2787470fca8b31c5ea8b1c30` | `ec9a4d3fdf160e5364a62acba25fc2bcbaaf5e067ba116cd3f355d2c61cca588` | `beecc5cd4d9d336f5adf450c947bf1db62a6493876a8250bfdba9889997ff059` | `f74f3e5b8631bf3ec5f018064367fd26a2b5b8b1cf19518a94a0deb40c2e4796` | **Superseded for v2.1** (CWD-relative default DB URL) |
| Intermediate v2.1 B3A | `c9b4bb5bcfb14f3143d72e3ba11d21e4490d8f09` | `348b8c555b05f41b0b5ae762c5edc125c509785470903abbd6e74626b5bb2f7e` | `11f4fef6a79f60d215b4ccb3158a5cdc93dca56cac3e086dde287bbc58aaa0a8` | `73b8a3ce2a971095512af735f2dcd13e82aef008c4aa949baf4d9861b48d5313` | **Superseded for v2.1 publication** (pre-frontend-audit-fix B3A build; built before the `fix: resolve frontend dependency audit finding` commit) |
| Intermediate v2.1 B3A | `c9b4bb5bcfb14f3143d72e3ba11d21e4490d8f09` | `cfca46d8c9381fdbb21543a80704139dd9affb2327f099de4953b3b053030cc0` | `3ff7f81eac4b1709986dec8396801b2798fd7813440280ed03cb0897a27ac4fe` | `8c27a93cb9f2cd9669afb103e465e90148a7931dc3fe8bc9e0bdaf3187c7cb52` | **Superseded for v2.1 publication** (rebuilt from HEAD `e3d803d` which contains the lockfile change; PyInstaller timestamps differ from a clean `c9b4bb5` build even though the B3A source commit and the embedded frontend dist are unchanged) |
| Current v2.1 B3A | `c9b4bb5bcfb14f3143d72e3ba11d21e4490d8f09` | `6e544e57d9fa6859de7bd446d9314f19ccc2bfcf7104091fb2e94a61a77e8b04` | `19e0c363837cada158c31e072307bcdc736708f2440f21b70a6d011d3f450fdf` | `ffd597d6339480e449b265aee07675a2836bf987d29962b65e9d1ff05221c0f5` | **Canonical for v2.1 publication** (rebuilt from a clean `c9b4bb5` worktree after the `fix: resolve frontend dependency audit finding` and `fix: close Windows installer publication - record portable payload supersession` commits) |

The superseded payload is retained in the ledger so the correction
trail is auditable. The final v2.1 installer (`03e1924a7c49f1c5e1e35efa7ca5ac2c7e3c4a003515d939c8708cdb9f5b4852`)
embeds only the current payload.

This page documents installation, silent install, runtime data
separation, reinstall / repair, and uninstall. It also explains the
unsigned / SmartScreen / antivirus behaviour you should expect.

## At a glance

| Property | Value |
|---|---|
| Filename | `Lockverity-2.1.2-windows-x64-setup.exe` |
| Architecture | x64 only |
| Privilege mode | per-user, no admin, no UAC |
| Default install path | `%LOCALAPPDATA%\Programs\Lockverity` |
| Stable AppId | `{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}` |
| Runtime data | `%LOCALAPPDATA%\Lockverity` (preserved on uninstall) |
| Code signing | **Unsigned** (see [SmartScreen / unsigned / antivirus](#smartscreen--unsigned--antivirus)) |

## Interactive install

1. Double-click `Lockverity-2.1.2-windows-x64-setup.exe`.
2. The wizard shows the Lockverity licence; click **I accept** to
   continue.
3. The default install path is
   `%LOCALAPPDATA%\Programs\Lockverity`. Change the path only if
   you have a specific reason.
4. The wizard offers an **optional** desktop shortcut. It is
   **unchecked by default** so a fresh install does not add a
   desktop icon unless you ask for one.
5. Click **Install**. No UAC prompt appears.
6. A current-source installer verifies Microsoft Edge WebView2 and, only
   when it is missing, runs Microsoft's signed Evergreen bootstrapper.
7. On the completion page, optionally check **Launch Lockverity**. A
   current-source build opens the dedicated desktop window; the immutable
   published v2.1.2 installer opens the trusted URL in your browser.

After install:

- Start Menu: a **Lockverity** folder with the application shortcut,
  the documentation link, and the **Uninstall Lockverity** entry.
- Desktop: the optional shortcut if you opted in.
- Apps & Features (Settings → Apps): the per-user **Lockverity**
  entry under the current user.

## Silent install

Silent install is supported via standard Inno Setup switches. The
operator never sees a UI. No browser is launched in silent mode.

```powershell
Lockverity-2.1.2-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- ^
    /DIR="C:\Apps\Lockverity" ^
    /LOG="C:\Temp\lockverity-install.log"
```

| Switch | Meaning |
|---|---|
| `/VERYSILENT` | No wizard pages or progress UI. |
| `/SUPPRESSMSGBOXES` | Suppresses any blocking message box. |
| `/NORESTART` | Do not request a Windows reboot. |
| `/SP-` | Skip the **Welcome** page. |
| `/DIR=<path>` | Override the install path. Supports spaces and Unicode. |
| `/LOG=<file>` | Write the install log to the given file. |

The installer returns an Inno Setup exit code:

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Initialisation failed (see `/LOG`) |
| 2 | The user clicked **Cancel** (only relevant in interactive mode) |
| Other non-zero | An error occurred (see `/LOG`) |

Silent install never launches the application and never shows blocking
dialogs. If WebView2 is absent, a current-source installer runs the
embedded Microsoft-signed Evergreen bootstrapper silently; that
bootstrapper obtains the runtime from Microsoft. The full accepted
payload is installed into `/DIR`.

## Runtime data

Lockverity's runtime data (databases, logs, configuration and
uploads) lives in a separate directory from the application
binaries:

```
%LOCALAPPDATA%\Lockverity\
  data\        # SQLite databases
  logs\        # rotating log files
  run\         # runtime state files (lockverity.state.json)
  config\      # operator overrides
```

The default location is the standard per-user path resolved by the
runtime's `app.cli.home.default_home()`. You can override the
location with the `LOCKVERITY_HOME` environment variable for one
shell, or with the `--home <path>` CLI option for one invocation.

## Launch

After install, click the Start Menu shortcut, or invoke the
installed binary directly:

```powershell
& "$env:LOCALAPPDATA\Programs\Lockverity\app\Lockverity.exe"
```

In a current-source build, the launcher starts the existing foreground
runtime on `127.0.0.1`, waits for `/api/v1/health`, and then shows a
normal resizable desktop window backed by WebView2. Closing the window
gracefully stops and reaps that backend. A second invocation restores and
focuses the existing window without creating another backend. Approved
HTTPS documentation/resource links open in the system browser; the exact
loopback origin stays embedded and unexpected navigation is blocked.

The immutable published v2.1.2 installer instead contains the historical
short-lived launcher that opens the trusted loopback URL in the default
browser. Its assets and provenance are unchanged.

CLI access:

```powershell
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" --version
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" doctor
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" status
& "$env:LOCALAPPDATA\Programs\Lockverity\app\lockverity-cli.exe" stop
```

## Reinstall / repair

Running the installer again performs a safe reinstall of the accepted
payload. The installer:

- detects a live installed instance via the documented Part B2
  identity check (state file + PID + creation time + instance UUID);
- requests a graceful stop via the installed
  `lockverity-cli.exe stop`;
- replaces the application files in
  `%LOCALAPPDATA%\Programs\Lockverity\app\` — the previous
  payload directory is removed before the new payload is copied,
  so files that no longer exist in the new build cannot survive
  an upgrade as stale DLL / PYD / runtime files;
- preserves your runtime data, databases, and logs in
  `%LOCALAPPDATA%\Lockverity\`;
- does not duplicate Start Menu or desktop shortcuts;
- does not rewrite the uninstaller registration.

The reinstall never terminates a process based on a PID alone, and
it never kills unrelated processes. If a safe shutdown cannot be
verified, the installer aborts with an actionable message pointing
at `lockverity-cli.exe doctor` and the runtime log path.

## Uninstall

Uninstall removes the application files and the shortcuts, and
preserves your runtime data. Start the uninstaller from
**Start Menu → Lockverity → Uninstall** or from
**Settings → Apps → Lockverity → Uninstall**.

The uninstaller:

- detects a live installed instance and requests a graceful stop;
- removes `%LOCALAPPDATA%\Programs\Lockverity\app\`;
- removes the Start Menu folder and the optional desktop shortcut;
- removes the per-user uninstaller registration;
- **preserves** `%LOCALAPPDATA%\Lockverity\` (databases, logs,
  configuration);
- shows a final dialog pointing at the retained-data path.

If a live instance is still running when the uninstaller runs, it
prompts you to close Lockverity first. No reboot is required.

### Removing runtime data (manual)

The installer never deletes your runtime data automatically. To
remove it after uninstalling the application, run from PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Lockverity"
```

This deletes the `data`, `logs`, `run`, and `config` sub-directories.
The runtime will recreate them on the next start if you reinstall
the application.

## What the installer does NOT do

The Lockverity v2.1 Windows installer intentionally omits a
number of behaviours that are common in commercial installers. None
of these are part of the v2.1 contract; each is a deliberate
omission to keep the install per-user, offline, and operator
controllable.

- **No system PATH modification.** The installer does not write
  `%PATH%` or `HKLM\...\\Path`.
- **No Program Files default.** The default install path is
  `%LOCALAPPDATA%\Programs\Lockverity`, which is outside
  `Program Files` and requires no elevation.
- **No service installation.** The installer never invokes
  `sc.exe` or `New-Service`. Lockverity runs as a regular
  process owned by the operator.
- **No scheduled task.** The installer never invokes
  `schtasks.exe`. Lockverity is started by the operator.
- **No firewall rule.** The installer never invokes `netsh advfirewall`
  or `New-NetFirewallRule`. The runtime binds loopback only.
- **No autorun entry.** The installer never writes
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` or
  `RunOnce`.
- **No automatic updates.** The installer never downloads a newer
  Lockverity version. Operators update Lockverity manually. A
  current-source installer may contact Microsoft only through the signed
  Evergreen bootstrapper when the WebView2 prerequisite is missing.
- **No telemetry / no phone-home.** Lockverity never reports
  usage data to a Lockverity-operated service. GitHub repository
  intake contacts GitHub, and applicable scan execution can
  contact the OSV, deps.dev, and OpenSSF Scorecard providers the
  operator selects. See the [privacy policy](privacy.md).
- **No file association / no URL protocol / no shell extension.**
  The installer does not register `.lockverity` or any
  `lockverity://` protocol.

## SmartScreen / unsigned / antivirus

Lockverity v2.1.2 is **not code-signed**. The Windows installer
and the PE executables distributed inside the portable package
are currently unsigned. This means:

- Windows may display **Unknown publisher** and/or a Microsoft
  Defender SmartScreen warning when an unsigned build is launched.
- Unsigned or newly distributed binaries may trigger heuristic
  antivirus detections. Verify the published SHA-256 and provenance;
  suspected false positives can be submitted to the relevant vendor
  for review. The documented public installer build command is
  `python backend\scripts\build_windows_installer.py`.
- The `INSTALLER-MANIFEST.json` next to the installer records
  the unsigned status honestly. The build script never claims
  Microsoft certification, code signing, or SmartScreen
  reputation.

See the [Code signing policy](code-signing-policy.md) and
[Privacy policy](privacy.md).

## Troubleshooting

### The installer does not start

1. Verify the SHA-256 of `Lockverity-2.1.2-windows-x64-setup.exe`
   matches the value in `INSTALLER-MANIFEST.json`.
2. Run the installer with `/LOG=<file>` and inspect the log.

### The runtime is not reachable at `http://127.0.0.1:<port>/`

1. Open `lockverity-cli.exe doctor` from
   `%LOCALAPPDATA%\Programs\Lockverity\app\` and read the
   diagnostic output.
2. Read the latest log under
   `%LOCALAPPDATA%\Lockverity\logs\lockverity.log`.
3. Check that no other process is bound to the default port
   (`8000`). Override with `lockverity-cli.exe start --port <N>`.

### Reinstall fails because the runtime is "still running"

The installer uses the Part B2 identity check (PID + creation time
+ instance UUID) to verify the running instance. If you see
"Lockverity is still running" after closing the GUI, the runtime
may be holding the start lock:

1. Open a terminal and run
   `lockverity-cli.exe status` to inspect the recorded
   state.
2. If the recorded PID is dead but the state file is still
   present, run `lockverity-cli.exe stop` to clear the state,
   then retry the install.

### The desktop window does not open after install

The installer never launches Lockverity during silent install. Start
`Lockverity.exe` manually. If the native launcher reports a missing
WebView2 Runtime, install the Evergreen Runtime from Microsoft's official
WebView2 page and retry. For other failures, run
`lockverity-cli.exe doctor --json` and inspect
`%LOCALAPPDATA%\Lockverity\logs\lockverity.log`.

## Build source

The installer is built from the committed `backend\installer\lockverity.iss`
source. The canonical build command is:

```powershell
python backend\scripts\build_windows_installer.py --clean --json-report
```

The build script:

1. verifies Windows x64 and a clean Git working tree;
2. verifies the accepted B3A portable payload's hashes (it will
   **refuse** to build if any hash differs) and verifies the
   COMPLETE payload file tree against the payload's
   `PAYLOAD-MANIFEST.json` — every regular file must match, with
   no missing, extra, modified, or linked entries;
3. extracts the payload into a dedicated staging directory;
4. obtains the official WebView2 Evergreen bootstrapper and rejects it
   unless Microsoft Authenticode verification succeeds;
5. invokes Inno Setup 6.x with the committed `.iss` source;
6. emits `INSTALLER-MANIFEST.json` and an external
   `SHA256SUMS.txt` next to the installer EXE;
7. runs a bounded silent-install + health + uninstall smoke if
   `--run-smoke` is passed.

Inno Setup 6.7.3 is the only trusted compiler for this build.
The compiler is fetched and verified by the project's build
script; the source is signed by jrsoftware.org.

## Acceptance contract

The installer's behaviour is tested by:

- `backend\tests\test_installer.py` — static contract tests
  covering AppId, install path, privilege mode, architecture,
  icon, shortcuts, no-service / no-firewall / no-PATH /
  no-telemetry / no-update / no-autorun / no-scheduled-task
  guarantees, and unsigned-status representation.
- `backend\scripts\build_windows_installer.py --run-smoke` —
  end-to-end silent install + health + uninstall smoke.

The v2.1 Part B3B acceptance cycle verifies:

- interactive install under the current non-elevated user;
- silent install into a path containing spaces and Unicode;
- installed `BUILD-MANIFEST.json` reports
  `source_commit = 81b400bc40ae6ada2787470fca8b31c5ea8b1c30`;
- reinstall while the runtime is running performs a verified
  graceful stop;
- uninstall while the runtime is running performs a verified
  graceful stop;
- runtime data is preserved on every variant of uninstall;
- no registry key outside the per-user uninstaller registration
  is created.

Current-source native-shell acceptance additionally checks dedicated
window creation, resize/minimize/maximize/restore behavior, single-instance
focus, loopback API health, no normal browser launch, packaged GUI/CLI
startup, and graceful backend teardown on close.
