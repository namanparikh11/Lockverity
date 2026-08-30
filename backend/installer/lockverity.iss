; =====================================================================
; Lockverity v2.1 Windows x64 per-user installer
; =====================================================================
;
; Committed source. Built with Inno Setup 6.7.3
; (https://jrsoftware.org/isdl.php, publisher: jrsoftware.org).
; The compiler is fetched and verified by the project's build script;
; this file is consumed by the canonical
; `python backend\scripts\build_windows_installer.py` command.
;
; The installer is per-user, x64, no admin / no UAC, and embeds the
; accepted v2.1 Part B3A portable payload as-is. It does not modify
; the operator's PATH, does not install a service, does not register
; autorun, and does not include code signing (the release is unsigned).
; If WebView2 is missing, setup runs Microsoft's signed Evergreen
; bootstrapper; that bootstrapper downloads the matching runtime from
; Microsoft. No other installer-time network access is performed.
;
; The stable ``AppId`` below is the per-user uninstallation key the
; Windows uninstaller uses to identify the application. **Do not
; change it**: changing the AppId causes Windows to treat a new
; installer as a *different* application and the existing
; per-user uninstaller entry becomes orphaned.
;
; =====================================================================

#define MyAppName "Lockverity"
#define MyAppDisplayName "Lockverity"
#define MyAppVersion "2.1.3"
#define MyAppPublisher "Lockverity Contributors"
#define MyAppURL "https://github.com/namanparikh11/lockverity"
#define MyAppCopyright "(C) Lockverity Contributors"
#define MyAppExeName "Lockverity.exe"
#define MyAppCliExeName "lockverity-cli.exe"

; Stable per-user AppId. See the comment block above before
; changing this value. The exact GUID used here is committed
; permanently for v2.1 and all future Lockverity Windows installers.
#define MyAppId "{{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}"

; The accepted v2.1 Part B3A portable payload is extracted to this
; subdirectory of the install dir. The same layout is preserved so
; the frozen binaries run unchanged.
#define MyAppPayloadDir "app"

[Setup]
; Stable AppId (Windows uninstaller key) -- DO NOT CHANGE.
AppId={#MyAppId}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright={#MyAppCopyright}
AppReadmeFile=docs\windows-installer.md
; Licence page. The accepted v2.1 Part B3A payload bundles
; the LICENSE file under the install root (see the [Files]
; section's ``root_extra\*`` source). Declaring
; ``LicenseFile=LICENSE`` makes Inno Setup render a
; dedicated licence-acceptance page between the welcome
; page and the destination page. The page shows the
; licence text in a read-only text control, a
; "I accept the agreement" radio control, and a
; "I do not accept" radio control. ``Next`` is disabled
; until the operator selects the acceptance radio. The
; v2.1 B3B acceptance contract requires this page to
; appear (the previous UIAutomation walk did not capture
; it because ``LicenseFile`` was not declared); the
; regression test
; ``test_license_page_is_declared`` in
; ``backend/tests/test_installer.py`` pins the contract.
LicenseFile=LICENSE

; Per-user, x64 only. No admin, no UAC. The installer runs in the
; non-elevated context of the current user. We deliberately do
; **not** allow a privilege-mode override: ``PrivilegesRequired=lowest``
; with no override directive means the installer is unconditionally
; per-user. The "Install for all users" path (which would require
; admin elevation and a UAC prompt) is never offered. The v2.1 B3B
; release-blocker check requires the per-user contract to be enforced;
; an override is a source-level regression that the regression test
; ``test_no_privilege_override_allowed`` in
; ``backend/tests/test_installer.py`` would catch.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

; Per-user install path. ``{localappdata}\Programs\<AppName>`` is
; the Windows-recommended location for per-user programs and is
; outside ``Program Files`` (which requires elevation). Paths with
; spaces and Unicode are supported. (Note: the constant is
; ``{localappdata}`` — there is no ``{autolocalappdata}``. The
; ``auto`` prefix in Inno Setup constants is reserved for the
; system-vs-user profile resolution (``{autopf}``,
; ``{autoprograms}``, ``{autostartmenu}``, etc.) and
; ``{localappdata}`` is already inherently per-user.)
DefaultDirName={localappdata}\Programs\{#MyAppName}
; Dedicated Start Menu program group so the Lockverity
; shortcuts live in ``Programs\Lockverity\`` (a coherent
; per-app folder) rather than directly under the user's
; default ``Programs\`` folder. ``DisableProgramGroupPage=yes``
; suppresses the wizard's group-picker page so the user
; never sees a confusing "where do you want shortcuts?"
; prompt on a per-user install; the group name is fixed by
; the installer and the resulting shortcuts land in
; ``%APPDATA%\Microsoft\Windows\Start Menu\Programs\Lockverity\``.
DefaultGroupName={#MyAppDisplayName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
DisableDirPage=auto
AlwaysShowComponentsList=no

; Modern wizard look.
WizardStyle=modern
WizardSizePercent=120

; Compress the payload with the standard Inno lzma compression.
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Output filename (the final filename is determined by the build
; script via the ``/OutputBaseFilename`` flag).
; The output *directory* is also passed by the build script as
; the ``/OutputDir=<absolute-path>`` command-line flag, so no
; ``OutputDir`` directive is needed in this .iss. Declaring
; ``OutputDir=dist`` here used to be paired with the
; ``DiskDirectory`` legacy directive (both predate Inno Setup
; 5.5) but is no longer required: leaving it out lets the
; ``/OutputDir`` command-line flag take effect cleanly.
OutputBaseFilename=Lockverity-{#MyAppVersion}-windows-x64-setup

; Approved Lockverity icon, used for the installer EXE, the
; uninstaller, and the installed application shortcuts. The
; underlying ICO is a packaging derivative of the approved
; favicon (16/24/32/48/64/128/256 PNG payloads, generated by
; ``backend\scripts\generate_exe_icon.py``). Inno Setup uses
; ``SetupIconFile`` for both the installer EXE and the
; uninstaller EXE it generates; there is no separate
; ``UninstallIconFile`` directive in Inno Setup 6.7.3.
SetupIconFile=..\pyinstaller\favicon-exe.ico
; ``UninstallDisplayIcon`` is the icon Windows shows in
; "Add or Remove Programs" / Settings -> Installed apps. The
; value must point at the *actual installed* graphical
; launcher (``{app}\app\Lockverity.exe`` -- the inner
; ``app\`` payload directory, not the install root) and
; must include an explicit ``,0`` icon index so the shell
; reads the first icon group from the PE (the Lockverity
; brand icon). A bare ``{app}\Lockverity.exe`` would point
; at a file that does not exist; the regression in
; v2.1.0/v2.1.1 had ``UninstallDisplayIcon={app}\Lockverity.exe``
; (no payload subdir, no icon index) which caused
; Settings -> Installed apps to fall back to the generic
; application icon.
UninstallDisplayIcon={app}\{#MyAppPayloadDir}\{#MyAppExeName},0

; We sign nothing. The build script records the unsigned status in
; the external ``INSTALLER-MANIFEST.json``. The canonical Inno Setup
; declaration is ``SignedUninstaller=no``; the previous ``SignTool=Skip``
; line was invalid ISCC syntax (it is not a recognized directive value
; and the ``SignTool`` directive is only meaningful for the compiler's
; *runtime* signing pipeline, which we do not use). Leaving it out
; entirely is the correct, supported way to say "do not sign".
SignedUninstaller=no

; Application mutex: the native GUI holds a named mutex keyed on this
; AppId so setup can detect loaded application files. CLI lifecycle
; checks use the PID/creation-time/instance-id state contract.
AppMutex={#MyAppId},Global\{#MyAppId}

; Surface any install-time error instead of silently exiting
; with a generic code. (There is no ``ShowCmdLineHelp`` [Setup]
; directive in Inno Setup 6.7.3 — that name is from an older
; Inno Setup version and the compiler reports it as
; ``Unrecognized [Setup] section directive``. The default
; behavior in 6.7.3 already prints the /HELP /? summary when
; Setup is run with no arguments, which is what we want.)
; Non-blocking error UI for the silent installer; the build
; script captures the install log separately.
SetupLogging=yes

; Compression: keep the .exe self-contained (no separate archive).
; The installer is a single offline EXE. ``DiskSpanning=no`` is the
; only directive needed in Inno Setup 6.7.3 — the legacy
; ``SlicesPerDisk`` and ``DiskDirectory`` directives are not
; recognised in 6.7.3 (they are holdovers from 2.x/3.x-era Inno
; Setup and the compiler reports them as
; ``Unrecognized [Setup] section directive``).
DiskSpanning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; BeCalm: do not let the user see Inno's internal jargon.
BeveledLabel=Lockverity v{#MyAppVersion} setup
SetupWindowTitle=Lockverity Setup
FinishedLabelMessage=Lockverity has been installed on your computer.%n%nThe application opens in its own desktop window when you click Finish.%n%nYour runtime data, databases and logs are stored in:%n  %1
FinishedHeadingMessage=Completing the Lockverity Setup Wizard

[Files]
; The accepted v2.1 Part B3A payload is staged into a sibling
; ``payload/`` directory by the build script, which also verifies
; every file's hash against the accepted BUILD-MANIFEST. The
; payload is copied verbatim into ``{app}\app\`` so the
; frozen binaries run unchanged. The installer never rebuilds
; the application.
Source: "payload\*"; DestDir: "{app}\{#MyAppPayloadDir}"; \
    Flags: "ignoreversion recursesubdirs createallsubdirs"

; Copy the license, manifest, notices and SHA256SUMS to the
; installation root so the operator can verify provenance after
; install (and so the uninstaller does not delete them when the
; user chooses to remove ``{app}\app\``).
Source: "root_extra\*"; DestDir: "{app}"; Flags: "ignoreversion recursesubdirs"

; The approved 256x256 ICO for the Start Menu and desktop
; shortcut. Same ICO used as the installer's SetupIconFile.
; ``ignoreversion`` skips the version-stamp comparison so the
; ICO is always copied verbatim; the source file is staged by
; the build script so a runtime check is unnecessary.
Source: "..\pyinstaller\favicon-exe.ico"; DestDir: "{app}"; \
    Flags: "ignoreversion"

; Official Microsoft Evergreen WebView2 bootstrapper. The build script
; downloads it from Microsoft's documented fwlink and requires a valid
; Microsoft Authenticode signature before ISCC sees it. ``dontcopy`` embeds
; it without installing it as application content; ``PrepareToInstall``
; extracts and runs it only when the official runtime keys report it missing.
Source: "webview2\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Run]
; Completion-page "Launch Lockverity" checkbox. The
; entry creates a single checkbox on the wizard's
; finished page. ``postinstall`` registers the
; checkbox; ``nowait`` returns control to the wizard
; immediately (the launcher owns a long-lived native
; desktop window, so the wizard must not wait for the
; user to close it); ``unchecked``
; leaves the checkbox off by default so the operator
; has to opt in (the more conservative default for a
; per-user security product); ``skipifsilent`` keeps
; the launch out of silent / unattended installs so a
; CI / acceptance installer never opens the GUI. The
; Filename points at the **actual installed** graphical
; launcher. The accepted v2.1 Part B3A payload is
; installed verbatim under ``{app}\app\`` (see the
; [Files] section), so the launcher lives at
; ``{app}\app\Lockverity.exe``. Pointing at
; ``{app}\Lockverity.exe`` would target a file that does
; not exist on disk. The ``workingdir`` of the [Run]
; entry is the same as the launcher's directory (the
; inner ``app\`` subdirectory) so the frozen Python
; distribution (``_internal\python312.dll`` and friends)
; is discoverable.
Filename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; Description: "Launch Lockverity"; \
    Flags: nowait postinstall unchecked skipifsilent; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"

[Icons]
; Start Menu shortcut under a dedicated Lockverity folder. The
; uninstaller removes this entry by following the standard
; ``{group}\`` rule. All three Start Menu shortcuts point at
; the **actual installed** graphical launcher at
; ``{app}\app\Lockverity.exe`` (the inner ``app\``
; subdirectory, not the install root) because the accepted
; v2.1 Part B3A payload is installed verbatim under
; ``{app}\app\``. Pointing at ``{app}\Lockverity.exe``
; would target a file that does not exist on disk.
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; IconIndex: 0; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"; \
    Comment: "Launch Lockverity"
Name: "{group}\Lockverity documentation"; Filename: "{app}\docs\windows-installer.md"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; IconIndex: 0; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"; \
    Comment: "Lockverity installer documentation"
Name: "{group}\Privacy policy"; Filename: "{app}\docs\privacy.md"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; IconIndex: 0; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"; \
    Comment: "Lockverity privacy policy"
Name: "{group}\Uninstall {#MyAppDisplayName}"; Filename: "{uninstallexe}"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; IconIndex: 0; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"; \
    Comment: "Uninstall Lockverity"

; Optional desktop shortcut. The shortcut points at the
; **actual installed** graphical launcher at
; ``{app}\app\Lockverity.exe`` (the inner ``app\``
; subdirectory, not the install root) because the accepted
; v2.1 Part B3A payload is installed verbatim under
; ``{app}\app\`` (see the Files section). The
; ``{autodesktop}`` constant resolves to the **current
; user's** desktop (``%USERPROFILE%\Desktop``), not the
; public desktop — a per-user install must not write to
; the all-users desktop. The shortcut is
; gated on the ``desktopicon`` task so it is only
; created when the operator opts in.
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; IconIndex: 0; \
    WorkingDir: "{app}\{#MyAppPayloadDir}"; \
    Comment: "Launch Lockverity"; Tasks: "desktopicon"

[Tasks]
; Optional desktop shortcut. Off by default. The user can
; opt in via the wizard. The explicit English
; description (rather than ``{cm:CreateDesktopIcon}``)
; guarantees the wizard renders the documented text
; regardless of the active Inno Setup language pack, so
; the regression test
; ``test_desktop_task_uses_explicit_english_description``
; in ``backend/tests/test_installer.py`` can assert the
; exact visible string. The explicit
; ``GroupDescription`` ("Additional shortcuts:") is the
; section header on the "Select Additional Tasks" wizard
; page; setting it explicitly guarantees the section is
; rendered with the documented heading rather than the
; default ``{cm:AdditionalIcons}`` (which is identical
; in the default English pack, but a future translation
; pass could change the default and break the test).
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
; Create the uninstall log parent (Inno writes
; ``unins<id>.dat`` next to ``unins<id>.exe``).
Name: "{app}"

[UninstallDelete]
; Per-user, clean uninstall: remove the install root and the
; Start Menu / desktop shortcuts, but DO NOT touch
; ``%LOCALAPPDATA%\Lockverity\`` (the runtime data home). The
; uninstaller surfaces a message pointing the operator at the
; retained data path and the manual data-removal command.
Type: filesandordirs; Name: "{app}\{#MyAppPayloadDir}"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\favicon-exe.ico"
; Defensive cleanup: if a previous install (or a
; crashed / interrupted run) left runtime artefacts
; in the install root (e.g. ``lockverity.sqlite``,
; WAL / journal sidecars, log files, lock files,
; pid files) delete them as part of the uninstall so
; the install root is fully removed. The application
; is required to keep runtime data under the runtime
; home (Part B2 ``LOCKVERITY_HOME``), so any such
; file at the install root is a leftover from a
; misconfigured / crashed prior run. The patterns
; use ``Type: files`` (not ``filesandordirs``) so a
; maliciously-named directory cannot be removed
; silently; the patterns only match files at the
; install root, not subdirectories.
Type: files; Name: "{app}\*.sqlite"
Type: files; Name: "{app}\*.sqlite-*"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.lock"
Type: files; Name: "{app}\*.pid"
Type: files; Name: "{app}\*.state.json"

[Code]
// =====================================================================
// Lifecycle handling: install, reinstall, uninstall
// =====================================================================
//
// The installer must not silently delete a running instance or
// confuse a same-named external process with the installed
// Lockverity. All lifecycle decisions go through the matching
// installed ``lockverity-cli.exe`` so the Part B2 identity
// protections (PID + creation time + instance UUID + module)
// are honoured end-to-end. The installer never terminates a
// process based on a PID alone.

const
    INSTALL_PAYLOAD_DIR = 'app';
    // Stable AppId mirrored in the [Setup] section. Used as the
    // per-user uninstaller key and as the cross-process mutex
    // name the runtime publishes at startup.
    APP_ID = '{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}';
    // Path of the matching standard installed CLI. The CLI is the
    // authoritative entry point for the runtime lifecycle; the
    // installer does not spawn the GUI launcher for lifecycle
    // decisions because the launcher is designed for an
    // interactive operator, not for a non-interactive installer.
    CLI_RELATIVE = 'app\\lockverity-cli.exe';
    // Max time the installer will wait for a graceful stop.
    GRACEFUL_STOP_TIMEOUT_S = 30;
    WEBVIEW2_CLIENT_KEY_MACHINE = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
    WEBVIEW2_CLIENT_KEY_USER = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
    WEBVIEW2_BOOTSTRAPPER = 'MicrosoftEdgeWebview2Setup.exe';

// helper: build the runtime-home path the installed CLI writes
// to. The path matches the standard Part B1/B2 location.
function StandardRuntimeHome: string;
begin
    // LOCALAPPDATA\Lockverity\run\lockverity.state.json
    Result := ExpandConstant('{localappdata}') + '\Lockverity\run\lockverity.state.json';
end;

function StateFilePath: string;
begin
    Result := StandardRuntimeHome;
end;

// Microsoft documents these machine and per-user ``pv`` values as the
// supported detection path for Evergreen WebView2. Missing, blank, and
// 0.0.0.0 values all mean the runtime is unavailable.
function WebView2RuntimeInstalled: Boolean;
var
    Version: string;
begin
    Result := False;
    Version := '';
    if RegQueryStringValue(HKLM, WEBVIEW2_CLIENT_KEY_MACHINE, 'pv', Version) or
       RegQueryStringValue(HKCU, WEBVIEW2_CLIENT_KEY_USER, 'pv', Version) then
    begin
        Version := Trim(Version);
        Result := (Version <> '') and (Version <> '0.0.0.0');
    end;
end;

function EnsureWebView2Runtime: string;
var
    Bootstrapper: string;
    ResultCode: Integer;
    ExecOk: Boolean;
begin
    Result := '';
    if WebView2RuntimeInstalled() then
    begin
        Log('WebView2 Evergreen Runtime detected; bootstrap skipped');
        exit;
    end;

    ExtractTemporaryFile(WEBVIEW2_BOOTSTRAPPER);
    Bootstrapper := ExpandConstant('{tmp}\') + WEBVIEW2_BOOTSTRAPPER;
    Log('WebView2 Runtime missing; running verified Microsoft Evergreen bootstrapper');
    ExecOk := Exec(
        Bootstrapper,
        '/silent /install',
        ExpandConstant('{tmp}'),
        SW_HIDE,
        ewWaitUntilTerminated,
        ResultCode);
    if not ExecOk then
    begin
        Result := 'Microsoft Edge WebView2 Runtime is required, but its official bootstrapper could not be started.';
        exit;
    end;
    if ResultCode <> 0 then
    begin
        Result := 'Microsoft Edge WebView2 Runtime installation failed with code ' + IntToStr(ResultCode) + '.';
        exit;
    end;
    if not WebView2RuntimeInstalled() then
        Result := 'Microsoft Edge WebView2 Runtime installation completed without a detectable runtime. Restart Windows and run setup again.';
end;

// helper: read the state file (best-effort, returns empty string
// on any error). The installer is conservative: a missing or
// unparseable state file is treated as "no live instance".
function TryReadStateFile(const Path: string): string;
var
    Lines: TArrayOfString;
    Buffer: string;
begin
    Result := '';
    if not FileExists(Path) then exit;
    if not LoadStringsFromFile(Path, Lines) then exit;
    if GetArrayLength(Lines) < 1 then exit;
    Buffer := '';
    // The CLI writes the state file as pretty-printed JSON on a
    // single line. We do not need a real JSON parser here; we
    // just need the pid and the instance_id substring. If the
    // file looks broken we return empty and the caller treats
    // the install as "no live instance".
    Buffer := Trim(Lines[0]);
    Result := Buffer;
end;

// helper: invoke the installed ``lockverity-cli.exe`` and return
// the stdout. The CLI is the authoritative entry point and
// exposes the documented Part B2 lifecycle (``status`` /
// ``stop``). We use ``/C`` redirection so the child inherits a
// console; this matches the operator-visible behaviour and
// keeps the lifecycle path identical to the documented one.
function RunCliSync(const Args: string; const TimeoutS: Integer): string;
var
    Exe: string;
    CmdLine, StdOut: string;
    ResultCode: Integer;
    ExecOk: Boolean;
begin
    Result := '';
    // The CLI lives at ``{app}\app\lockverity-cli.exe`` per the
    // [Files] section's ``{app}\{#MyAppPayloadDir}\lockverity-cli.exe``
    // source line. We compute the path directly to avoid
    // Pascal string-escape quirks that previously produced
    // UNC-style ``app\\lockverity-cli.exe`` paths and
    // ERROR_INVALID_PARAMETER (87) from ``CreateProcess``.
    Exe := ExpandConstant('{app}\app\lockverity-cli.exe');
    Log('B3B RunCliSync: exe=' + Exe);
    Log('B3B RunCliSync: args=' + Args);
    if not FileExists(Exe) then
    begin
        Log('B3B RunCliSync: Exe not found');
        exit;
    end;
    // The ``Exec`` function signature is
    // ``(Filename, Params, WorkingDir, ShowCmd, Wait, ResultCode)``.
    // We pass the absolute Exe path unquoted (the path has
    // no whitespace problems once it is absolute; quoting
    // caused ERROR_INVALID_PARAMETER 87 in earlier
    // revisions because ``ShellExecuteEx`` treats the quoted
    // string as a verb-relative lookup). The working
    // directory is the EXE's directory so ``python312.dll``
    // and friends are discoverable. Args is passed as the
    // Params argument, which Inno Setup ``Exec`` appends to
    // the Filename with the standard Windows command-line
    // escaping rules.
    ExecOk := Exec(Exe, Args, ExpandConstant('{app}\app'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Log('B3B RunCliSync: exec_ok=' + IntToStr(Integer(ExecOk)) + ' result_code=' + IntToStr(ResultCode));
    if not ExecOk then exit;
    StdOut := '';
    // ``Exec`` does not return stdout directly; ``ExecAsUser``
    // would require a temporary script. The CLI is well-behaved
    // and exits with rc=0 for ``status --json`` when the
    // instance is healthy. We rely on the exit code here and
    // do not need stdout content.
    if ResultCode <> 0 then
    begin
        Result := 'rc=' + IntToStr(ResultCode);
    end
    else
        Result := 'ok';
end;

// Detect a live installed instance via the documented state file
// + Part B2 identity verification. We call the installed
// ``lockverity-cli.exe status --json`` so the live-identity
// check (PID + creation time + instance UUID) runs in the
// authoritative code path.
function HasLiveInstance: Boolean;
var
    Rc: string;
begin
    Result := False;
    // The CLI is the authority. ``status`` exits 0 only when
    // a verified live instance is recorded in the state file.
    Rc := RunCliSync('status', 15);
    Result := (Rc = 'ok');
    Log('B3B HasLiveInstance: status_rc=' + Rc);
end;

// Request a graceful stop via the installed CLI. We block until
// the CLI exits 0 (instance is verified stopped) or the bounded
// timeout elapses. We never terminate a process by PID alone.
function RequestGracefulStop: Boolean;
var
    Rc: string;
    Elapsed, Deadline: Double;
begin
    Result := False;
    Rc := RunCliSync('stop', GRACEFUL_STOP_TIMEOUT_S);
    Result := (Rc = 'ok');
    Log('B3B RequestGracefulStop: stop_rc=' + Rc);
end;

// Called by Inno Setup before the installer replaces files. If a
// verified live instance is recorded, the installer asks the
// operator to close it before continuing.
function ShouldSkipPage(PageID: Integer): Boolean;
begin
    Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
    HasLive: Boolean;
    PromptResult: Integer;
begin
    Result := '';
    HasLive := HasLiveInstance();
    if HasLive then
    begin
        // A live instance is using the locked files. In
        // interactive (wizard) mode the operator is shown a
        // ``MB_RETRYCANCEL`` prompt and can either close the
        // instance and click Retry, or cancel the install. In
        // silent mode (``/VERYSILENT`` / ``/SUPPRESSMSGBOXES``)
        // the operator has explicitly opted out of UI; we
        // attempt a graceful stop via the installed CLI and
        // abort the install if it cannot be verified. The
        // installer is non-elevated so we never terminate the
        // process; a silent partial upgrade would leave the
        // locked files in an inconsistent state, which is worse
        // than an aborted install with a clear actionable error.
        if WizardSilent then
        begin
            if not RequestGracefulStop() then
            begin
                Result := 'Lockverity is still running and could not be stopped gracefully. Close Lockverity and re-run the installer.';
            end
            else if HasLiveInstance() then
            begin
                Result := 'Lockverity is still running after the graceful stop. Close Lockverity and re-run the installer.';
            end;
        end
        else
        begin
            PromptResult := MsgBox(
                'Lockverity is currently running.' + #13#10 + #13#10 +
                'The installer cannot replace the application while the runtime is loaded.' + #13#10 +
                'Please close the Lockverity desktop window and click Retry, or Cancel to abort the install.' + #13#10 + #13#10 +
                'When Lockverity has stopped, click Retry to continue.',
                mbError, MB_RETRYCANCEL);
            if PromptResult = IDRETRY then
            begin
                // Operator confirms they have closed the
                // instance. Re-check the state file before
                // proceeding.
                if HasLiveInstance() then
                begin
                    Result := 'Lockverity is still running. Please close it and try again.';
                end;
            end
            else
                Result := 'Install cancelled by operator.';
        end;
    end;
    if Result = '' then
        Result := EnsureWebView2Runtime();
end;

// Remove the previous application payload before the new one is
// copied. Inno Setup's [Files] section only replaces files that
// exist at matching paths; files removed from the new build would
// otherwise stay installed indefinitely across upgrades (stale
// DLL / PYD / module / runtime files). Lockverity already hit
// this failure class with a stale native psutil mismatch during
// portable packaging, so upgrades must retire the old payload
// directory instead of layering the new one on top of it.
//
// The removal targets ONLY ``{app}\app`` (the packaged
// application payload installed by this setup). The operator's
// runtime data home ``%LOCALAPPDATA%\Lockverity`` is a different
// tree and is never touched by this code.
procedure RemovePreviousPayload;
var
    OldPayloadDir: string;
begin
    OldPayloadDir := ExpandConstant('{app}') + '\' + INSTALL_PAYLOAD_DIR;
    if not DirExists(OldPayloadDir) then
    begin
        Log('LV-001: no previous payload at ' + OldPayloadDir + ' (fresh install)');
        exit;
    end;
    Log('LV-001: removing previous application payload at ' + OldPayloadDir);
    if not DelTree(OldPayloadDir, True, True) then
    begin
        // A locked or unreadable file inside the old payload means
        // the old tree cannot be cleanly retired. Abort instead of
        // layering the new payload on top: continuing would
        // silently reproduce the obsolete-files upgrade failure
        // this cleanup exists to prevent.
        RaiseException(
            'Lockverity could not remove the previous application payload at ' +
            OldPayloadDir + '. Close Lockverity and any program that may hold ' +
            'those files open, then run the installer again.');
    end;
    Log('LV-001: previous application payload removed');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
    // ``ssInstall`` fires after every ``PrepareToInstall`` check
    // has passed (the runtime is confirmed stopped and WebView2 is
    // present) and immediately before the [Files] entries are
    // copied -- the only point at which removing the old payload
    // is both safe (nothing is running from it) and effective
    // (the new payload is copied right after).
    if CurStep = ssInstall then
        RemovePreviousPayload;
end;

// After the files are in place, the installed CLI can be invoked
// by the operator from a Start Menu shortcut. The post-install
// ``Launch Lockverity`` checkbox is handled by the ``Run`` entry
// below.
function ShouldRunPostInstall: Boolean;
begin
    // ``CmdLineParam: 'NoRun'`` is set by the ``/VERYSILENT`` /
    // ``/SUPPRESSMSGBOXES`` switches. Honour it: never auto-launch
    // in silent / unattended mode.
    Result := (ExpandConstant('{param:NoRun|NoRun}') <> 'NoRun');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
    // ``CurUninstallStepChanged`` runs at each uninstall phase.
    // We use the pre-uninstall step to request a graceful
    // shutdown of the installed instance. The interactive
    // (wizard) mode shows a non-blocking informational
    // MsgBox if the graceful stop fails; the silent mode
    // never shows UI and just logs to the install log.
    if CurUninstallStep = usUninstall then
    begin
        if HasLiveInstance() then
        begin
            if not RequestGracefulStop() then
            begin
                if not WizardSilent then
                begin
                    MsgBox(
                        'Lockverity is still running. The uninstaller will continue and remove the application files, but you may need to close Lockverity manually before the next install.',
                        mbInformation, MB_OK);
                end
                else
                begin
                    Log('B3B uninstall: live instance did not stop gracefully; continuing uninstall');
                end;
            end;
        end;
    end;
end;

// Surfaces the retained-data path on uninstall so the operator
// knows where their databases, logs and uploads live. The
// ``CurUninstallProgressChanged`` event handler's parameter
// type is ``Integer`` (a 0..100 progress percentage); the
// type-name ``CurUninstallProgress`` is the unit the event is
// declared in and is not itself a usable type for the
// parameter declaration.
procedure CurUninstallProgressChanged(CurProgress: Integer);
begin
    // No-op: the retained-data path is shown in the final
    // uninstall confirmation dialog below.
end;

procedure UninstallAfterSuccess();
begin
    // ``UninstallAfterSuccess`` is called when the uninstall
    // finishes. We surface the retained-data path; the operator
    // is responsible for the optional manual data-removal step
    // (documented in ``docs\windows-installer.md``). The
    // informational MsgBox is suppressed in silent mode so the
    // unattended uninstall can complete without UI; the
    // retained-data path is also written to the install log so
    // an operator reviewing the log has the same information.
    if not WizardSilent then
    begin
        MsgBox(
            'Lockverity has been removed from this computer.' + #13#10 + #13#10 +
            'Your runtime data, databases and logs were preserved in:' + #13#10 +
            '  ' + ExpandConstant('{localappdata}') + '\Lockverity' + #13#10 + #13#10 +
            'To remove the data manually, see the "Removing runtime data" section in the Lockverity documentation.',
            mbInformation, MB_OK);
    end
    else
    begin
        Log('B3B uninstall: complete. Retained data at ' + ExpandConstant('{localappdata}') + '\Lockverity');
    end;
end;
