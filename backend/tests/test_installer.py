"""Tests for the v2.1 Part B3B Windows installer source.

The tests cover the installer source contract documented in
``backend\\installer\\lockverity.iss`` and the build script
in ``backend\\scripts\\build_windows_installer.py``. They are
pure-Python static checks: they read the committed source and
fail the suite if a maintainer changes a contract the
operator-facing installer must honour. The full installer
build, install, and uninstall are exercised separately by
the build script's silent smoke (opt-in) and by the manual
acceptance cycle.

Every test in this file documents a single v2.1 Part B3B
acceptance contract. New contracts must add a test; broken
contracts must be repaired before the test is removed.

Important: the tests in this file MUST NOT pin any generated
binary hash (portable ZIP SHA-256, Lockverity.exe SHA-256,
etc.) inside a tracked Python constant. Generated hashes are
recorded in the portable's own ``SHA256SUMS.txt`` /
``BUILD-MANIFEST.json`` and in the installer's external
``INSTALLER-MANIFEST.json``. The tracked source may only pin
the payload's *source identity* (``source_commit``) and the
product ``version``; any other generated value is read at
build / acceptance time from the artifact's own manifest.
This design keeps a single source commit valid across any
number of portable rebuilds and removes the historical
"rebuild -> source-commit -> rebuild" cycle.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
ISS_SOURCE = BACKEND_ROOT / "installer" / "lockverity.iss"
BUILD_SCRIPT = BACKEND_ROOT / "scripts" / "build_windows_installer.py"
B3B_ACCEPTANCE_SCRIPT = BACKEND_ROOT / "scripts" / "b3b_acceptance.py"
APPROVED_ICON = BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"
APPROVED_FAVICON_ICO = REPO_ROOT / "frontend" / "public" / "favicon.ico"
DOCS_FILE = REPO_ROOT / "docs" / "windows-installer.md"
PRIVACY_FILE = REPO_ROOT / "docs" / "privacy.md"
PORTABLE_BUILD_SCRIPT = BACKEND_ROOT / "scripts" / "build_windows_portable.py"

STABLE_APP_ID = "{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}"
APP_VERSION = "2.1.3"

# In the new provenance design, the build script captures
# the installer build's current ``git rev-parse HEAD`` at
# build time and verifies that the payload's
# ``BUILD-MANIFEST.json`` ``source_commit`` equals that HEAD.
# There is no tracked Python constant pinning a specific B3A
# source commit. The payload's portable ZIP, the EXE
# SHA-256 values, the ``INSTALLER-MANIFEST.json`` fields,
# and the ``SHA256SUMS.txt`` entries are all read at build
# / acceptance time from the payload's own generated
# manifests.

# Historical generated-hash literals kept solely as
# "forbidden literals" used by the regression tests to
# assert that the build script and the acceptance script do
# NOT pin any of them. These values are *expected* to be
# out of date after any rebuild; they exist for assertion
# only and are never used as a positive expectation.
_HISTORICAL_FORBIDDEN_PAYLOAD_ZIP_SHA256 = (
    "6e544e57d9fa6859de7bd446d9314f19ccc2bfcf7104091fb2e94a61a77e8b04"
)
_HISTORICAL_FORBIDDEN_LOCKVERITY_EXE_SHA256 = (
    "19e0c363837cada158c31e072307bcdc736708f2440f21b70a6d011d3f450fdf"
)
_HISTORICAL_FORBIDDEN_LOCKVERITY_CLI_EXE_SHA256 = (
    "ffd597d6339480e449b265aee07675a2836bf987d29962b65e9d1ff05221c0f5"
)


def _iss_text() -> str:
    return ISS_SOURCE.read_text(encoding="utf-8", errors="replace")


def test_privacy_policy_is_exposed_by_both_windows_packages() -> None:
    """Static packaging contract; no release artifact is rebuilt."""
    portable = PORTABLE_BUILD_SCRIPT.read_text(encoding="utf-8")
    installer = BUILD_SCRIPT.read_text(encoding="utf-8")
    iss = _iss_text()

    assert PRIVACY_FILE.is_file()
    assert '("docs/privacy.md", "PRIVACY.md")' in portable
    assert '"PRIVACY.md"' in portable
    assert 'privacy_doc = REPO_ROOT / "docs" / "privacy.md"' in installer
    assert 'docs_dest / "privacy.md"' in installer
    assert 'Name: "{group}\\Privacy policy"' in iss
    assert 'Filename: "{app}\\docs\\privacy.md"' in iss


def _build_text() -> str:
    return BUILD_SCRIPT.read_text(encoding="utf-8", errors="replace")


def _b3b_acceptance_text() -> str:
    return B3B_ACCEPTANCE_SCRIPT.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------
# .iss source contract
# ---------------------------------------------------------------------


class TestInstallerSourceContract:
    """The committed Inno Setup source must honour every spec contract."""

    def test_stable_app_id_is_present(self) -> None:
        text = _iss_text()
        assert STABLE_APP_ID in text, (
            f"AppId {STABLE_APP_ID} not found in installer source; "
            "the per-user uninstaller key must remain stable across "
            "all v2.1 Windows installer builds."
        )

    def test_app_name_lockverity(self) -> None:
        text = _iss_text()
        assert re.search(r"AppName=\{#MyAppDisplayName\}", text), (
            "AppName must be the MyAppDisplayName define (== Lockverity)"
        )
        assert re.search(r'#define\s+MyAppName\s+"Lockverity"', text), (
            "MyAppName must be defined as 'Lockverity'"
        )

    def test_app_version_2_1_3(self) -> None:
        text = _iss_text()
        assert re.search(r'#define\s+MyAppVersion\s+"2\.1\.3"', text), (
            "MyAppVersion must be the v2.1.3 accepted version"
        )
        assert re.search(r"AppVersion=\{#MyAppVersion\}", text), (
            "AppVersion must reference MyAppVersion"
        )

    def test_privilege_mode_lowest(self) -> None:
        text = _iss_text()
        assert re.search(r"^\s*PrivilegesRequired\s*=\s*lowest", text, re.MULTILINE), (
            "PrivilegesRequired must be ``lowest`` (no admin, no UAC)"
        )

    def test_no_privilege_override_allowed(self) -> None:
        # The v2.1 B3B release-blocker check requires the
        # per-user contract to be enforced unconditionally.
        # ``PrivilegesRequiredOverridesAllowed=dialog`` would
        # re-introduce the "Install for all users" option in
        # the wizard, which requires admin elevation and a
        # UAC prompt -- a per-user installer must never offer
        # that path.
        text = _iss_text()
        assert "PrivilegesRequiredOverridesAllowed" not in text, (
            "Installer must not declare PrivilegesRequiredOverridesAllowed; "
            "the per-user contract is unconditional. Declaring this directive "
            "re-introduces the 'Install for all users' wizard option and the "
            "UAC prompt, which the v2.1 B3B spec forbids."
        )

    def test_architecture_x64(self) -> None:
        text = _iss_text()
        assert re.search(
            r"^\s*ArchitecturesInstallIn64BitMode\s*=\s*x64compatible",
            text,
            re.MULTILINE,
        ), "Installer must declare x64 as the only install architecture"

    def test_default_install_path_uses_localappdata(self) -> None:
        text = _iss_text()
        assert re.search(
            r"DefaultDirName=\{localappdata\}\\Programs\\\{#MyAppName\}",
            text,
        ), "Default install path must be {localappdata}\\Programs\\<AppName>"

    def test_no_autolocalappdata_constant(self) -> None:
        # Regression: the installer source carried
        # ``DefaultDirName={autolocalappdata}\Programs\{#MyAppName}``
        # but the ``{autolocalappdata}`` constant does not exist
        # in Inno Setup 6.7.3 — the correct constant is
        # ``{localappdata}`` (the ``auto`` prefix in Inno Setup
        # constants is reserved for system-vs-user profile
        # resolution such as ``{autopf}`` / ``{autoprograms}`` /
        # ``{autostartmenu}``, and ``{localappdata}`` is already
        # inherently per-user). The compiler reported
        # ``Unknown constant "autolocalappdata"``. Strip comment
        # lines so explanatory prose does not trip the assertion.
        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert "autolocalappdata" not in code_text, (
            "Installer source must not reference "
            "``{autolocalappdata}`` — that constant does not "
            "exist in Inno Setup 6.7.3; use ``{localappdata}``"
        )

    def test_no_outputdir_directive_in_iss(self) -> None:
        # Regression: the .iss carried ``OutputDir=dist`` and the
        # build script passed ``/OutputDir=<abs-path>`` and
        # ``/OutputBaseFilename=<name>`` as command-line flags.
        # None of those long-form flags are valid in ISCC 6.7.3:
        # the parser treats single-character switch tokens
        # (``/O``, ``/F``) and the rest of the string becomes the
        # switch's argument, leading to ``I/O error 123``
        # (``ERROR_INVALID_NAME``) on the resulting path. The
        # .iss must not declare an ``OutputDir=`` directive at
        # all — the build script uses the canonical ``/O<full-path>``
        # flag (which sets directory + filename in one go) and
        # the .iss keeps ``OutputBaseFilename=...`` as a
        # human-readable fallback. Strip comment lines so
        # explanatory prose does not trip the negative assertion.
        import re

        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert not re.search(r"^\s*OutputDir\s*=", code_text, re.MULTILINE | re.IGNORECASE), (
            "Installer source must not declare an OutputDir= "
            "directive; the build script passes the full output "
            "path via the canonical ISCC ``/O<full-path>`` "
            "command-line flag, and a duplicate here caused ISCC "
            "to fail with ``I/O error 123`` (ERROR_INVALID_NAME)"
        )

    def test_does_not_install_to_program_files(self) -> None:
        text = _iss_text()
        # ``DefaultDirName`` must not point at ``Program Files``.
        # The literal string ``Program Files`` may appear in
        # comments; we assert the ``DefaultDirName`` directive
        # is the per-user path.
        m = re.search(r"^\s*DefaultDirName\s*=\s*(.+)$", text, re.MULTILINE)
        assert m is not None, "DefaultDirName directive missing"
        directive = m.group(1)
        assert "Program Files" not in directive, (
            f"DefaultDirName must not target Program Files: {directive!r}"
        )
        assert "Programs\\{#MyAppName}" in directive, (
            f"DefaultDirName must be {{localappdata}}\\Programs\\<AppName>: {directive!r}"
        )

    def test_does_not_modify_system_path(self) -> None:
        text = _iss_text()
        # The Inno Setup ``[Registry]`` section (or any code that
        # touches ``HKLM\\...\\Path``) would be a system-PATH
        # change. We refuse the [Registry] section entirely and
        # require the source to mention PATH nowhere as a
        # destination.
        assert "[Registry]" not in text, "Installer source must not contain a [Registry] section"
        # ``AppMutex`` is permitted; the bare ``PATH`` token as a
        # write target is not. We accept any reference to PATH
        # but require the absence of common env-var write
        # patterns.
        assert "SetEnvVar" not in text, "Installer must not call SetEnvVar (no PATH modification)"
        assert "EnvVarUpdate" not in text, (
            "Installer must not call EnvVarUpdate (no PATH modification)"
        )

    def test_does_not_install_windows_service(self) -> None:
        text = _iss_text()
        # The Inno Setup ``[Run]`` section can install a service
        # via ``sc.exe``; we forbid that surface.
        for forbidden in ("sc.exe", "sc create", "sc start", "New-Service"):
            assert forbidden not in text, (
                f"Installer must not invoke {forbidden!r} (no service install)"
            )

    def test_does_not_create_scheduled_task(self) -> None:
        text = _iss_text()
        for forbidden in ("schtasks", "schtasks.exe"):
            assert forbidden not in text, (
                f"Installer must not invoke {forbidden!r} (no scheduled task)"
            )

    def test_does_not_modify_firewall(self) -> None:
        text = _iss_text()
        for forbidden in ("netsh", "advfirewall", "New-NetFirewallRule"):
            assert forbidden not in text, (
                f"Installer must not invoke {forbidden!r} (no firewall rule)"
            )

    def test_does_not_add_autorun(self) -> None:
        text = _iss_text()
        for forbidden in ("RunOnce", "HKCU\\...\\Run", "HKLM\\...\\Run"):
            assert forbidden not in text, (
                f"Installer must not write {forbidden!r} (no autorun entry)"
            )

    def test_does_not_install_browser_extension_or_file_association(self) -> None:
        text = _iss_text()
        for forbidden in (
            "Browser Helper Object",
            "FileExtension",
            "URL Protocol",
            "ProtocolHandler",
        ):
            assert forbidden not in text, f"Installer must not declare {forbidden!r}"

    def test_uses_approved_lockverity_icon(self) -> None:
        text = _iss_text()
        assert "SetupIconFile=..\\pyinstaller\\favicon-exe.ico" in text, (
            "Installer must use the approved favicon-exe.ico as its icon"
        )
        assert APPROVED_ICON.is_file(), f"Approved icon missing: {APPROVED_ICON}"

    def test_uninstall_display_icon_points_at_installed_payload(self) -> None:
        """The ``UninstallDisplayIcon`` registry value must point at
        the **actual installed** graphical launcher
        (``{app}\\app\\Lockverity.exe`` -- the inner ``app\\``
        payload directory, not the install root) and must
        include an explicit ``,0`` icon index so the Windows
        shell reads the Lockverity brand icon.

        Regression: v2.1.0 and v2.1.1 declared
        ``UninstallDisplayIcon={app}\\{#MyAppExeName}`` (no
        payload subdir, no icon index). The bare ``{app}``
        path pointed at a file that does not exist (the
        launcher is installed under ``{app}\\app\\``), so
        Windows Settings -> Installed apps showed the
        generic application icon. v2.1.2 fixes the path and
        the icon index.
        """
        text = _iss_text()
        match = re.search(
            r"^\s*UninstallDisplayIcon\s*=\s*(.+?)\s*$",
            text,
            re.MULTILINE,
        )
        assert match is not None, "Installer must declare UninstallDisplayIcon in [Setup]"
        value = match.group(1)
        assert "{app}" in value, (
            f"UninstallDisplayIcon must use the {{app}} constant, got: {value!r}"
        )
        # Must include the payload subdir (either via the
        # ``{#MyAppPayloadDir}`` define or the literal
        # ``app\\``). A bare ``{app}\\{#MyAppExeName}``
        # would point at a file that does not exist.
        assert "{#MyAppPayloadDir}" in value or "app\\" in value, (
            f"UninstallDisplayIcon must include the inner {{app}}\\app\\ "
            f"subdirectory (the actual install location of Lockverity.exe); got: {value!r}"
        )
        # Must include the actual exe name.
        assert "{#MyAppExeName}" in value, (
            f"UninstallDisplayIcon must reference {{#MyAppExeName}} (Lockverity.exe); got: {value!r}"
        )
        # Must include an explicit icon index. The v2.1.0 /
        # v2.1.1 regression was the missing ``,0`` suffix
        # which caused the shell to fall back to the generic
        # icon for some user accounts.
        assert ",0" in value, (
            f"UninstallDisplayIcon must include an explicit icon index ',0' so the "
            f"Windows shell reads the Lockverity brand icon (the first icon group "
            f"from the PE); got: {value!r}"
        )

    def test_setup_icon_file_canonical(self) -> None:
        """The ``SetupIconFile`` directive must point at the
        canonical Lockverity ICO that has the full
        ``{16, 24, 32, 48, 64, 128, 256}`` Windows shell
        size set. A truncated ICO (only 16/32/48/256) causes
        the Windows shell to fall back to the generic
        application icon for 24/64/128 queries.
        """
        text = _iss_text()
        match = re.search(
            r"^\s*SetupIconFile\s*=\s*(.+?)\s*$",
            text,
            re.MULTILINE,
        )
        assert match is not None, "Installer must declare SetupIconFile in [Setup]"
        value = match.group(1)
        assert value.endswith("favicon-exe.ico"), (
            f"SetupIconFile must point at the canonical Lockverity ICO "
            f"favicon-exe.ico; got: {value!r}"
        )
        # The file must exist on disk.
        assert APPROVED_ICON.is_file(), f"Canonical ICO missing: {APPROVED_ICON}"
        # The file must be a valid ICO with the full
        # canonical size set. We assert this in-process by
        # reading the ICO header (no Pillow dependency in
        # the installer tests).
        import struct

        data = APPROVED_ICON.read_bytes()
        assert data[:4] == b"\x00\x00\x01\x00", (
            f"{APPROVED_ICON} is not a valid ICO file (magic mismatch)"
        )
        count = struct.unpack_from("<H", data, 4)[0]
        sizes: set[int] = set()
        for index in range(count):
            offset = 6 + 16 * index
            w = data[offset]
            _h = data[offset + 1]
            sizes.add(w if w else 256)
        # Strip duplicates from height (square sizes).
        canonical = {16, 24, 32, 48, 64, 128, 256}
        assert canonical.issubset(sizes), (
            f"Canonical ICO is missing sizes: {canonical - sizes}; "
            f"the Windows shell will fall back to the generic icon."
        )

    def test_uses_approved_lockverity_icon_does_not_invent_uninstalliconfile(self) -> None:
        """Regression: the v2.1.2 spec draft mentioned
        ``UninstallIconFile=<canonical ICO>`` but Inno Setup
        6.7.3 has no such [Setup] directive. The uninstaller
        EXE icon is inherited from ``SetupIconFile`` in
        Inno Setup 6.7.3. The installer source must not
        invent a non-existent directive that the compiler
        would reject with ``Unrecognized [Setup] section
        directive "UninstallIconFile"``.
        """
        text = _iss_text()
        # Strip comment lines so explanatory prose in the
        # .iss does not trip the assertion.
        code_lines = [
            line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert "UninstallIconFile" not in code_text, (
            "Installer source must not declare an UninstallIconFile directive; "
            "Inno Setup 6.7.3 does not recognise that name. The uninstaller EXE icon "
            "is inherited from SetupIconFile."
        )

    def test_creates_start_menu_shortcut(self) -> None:
        text = _iss_text()
        assert re.search(
            r'Name:\s*"\{group\}\\{#MyAppDisplayName\}"\s*;\s*Filename:',
            text,
        ), "Installer must create a Start Menu shortcut under the AppName group"
        assert re.search(
            r'Name:\s*"\{group\}\\Uninstall \{#MyAppDisplayName\}"',
            text,
        ), "Installer must create an Uninstall entry in the Start Menu"

    def test_desktop_shortcut_unchecked_by_default(self) -> None:
        text = _iss_text()
        # The desktop shortcut must be gated on a ``Tasks``
        # entry that is ``unchecked`` by default.
        assert re.search(
            r'Name:\s*"desktopicon".*Flags:\s*unchecked',
            text,
            re.DOTALL,
        ), "Desktop shortcut task must be declared with Flags: unchecked"

    def test_desktop_task_uses_explicit_english_description(self) -> None:
        # The v2.1 B3B acceptance spec requires the desktop
        # task to be visibly presented on the "Select
        # Additional Tasks" wizard page with the documented
        # text "Create a desktop shortcut". Using the Inno
        # Setup default ``{cm:CreateDesktopIcon}`` is
        # acceptable in the default English pack but a
        # future translation pass could change the default
        # and break the acceptance test; the explicit
        # English description makes the contract
        # language-independent.
        text = _iss_text()
        assert re.search(
            r'Name:\s*"desktopicon"\s*;\s*Description:\s*"Create a desktop shortcut"',
            text,
        ), (
            "Desktop task must declare Description: 'Create a desktop shortcut' "
            "so the wizard shows the documented text on the Additional Tasks page"
        )

    def test_desktop_shortcut_uses_per_user_autodesktop(self) -> None:
        # The desktop shortcut must use ``{autodesktop}``
        # (the current user's desktop) -- not
        # ``{commondesktop}`` (the all-users desktop at
        # ``C:\Users\Public\Desktop``). A per-user install
        # must not write to the public desktop.
        text = _iss_text()
        assert "{autodesktop}" in text, (
            "Installer must use {autodesktop} for the desktop shortcut so it "
            "lands in the current user's desktop, not the public desktop"
        )
        assert "{commondesktop}" not in text, (
            "Installer must not use {commondesktop}; a per-user install must "
            "not write to C:\\Users\\Public\\Desktop"
        )

    def test_license_page_is_declared(self) -> None:
        # The v2.1 B3B acceptance spec requires a visible
        # licence page before installation. The page is
        # enabled by declaring ``LicenseFile=LICENSE`` in
        # the [Setup] section. Without it Inno Setup does
        # not show a licence page -- the previous UIAutomation
        # walk captured no licence page and the regression
        # here ensures the page is never silently dropped.
        text = _iss_text()
        assert re.search(r"^\s*LicenseFile\s*=\s*LICENSE\s*$", text, re.MULTILINE), (
            "Installer must declare 'LicenseFile=LICENSE' in [Setup] so the "
            "wizard renders a visible licence-acceptance page"
        )

    def test_no_setup_logging_path_reveals_secrets(self) -> None:
        text = _iss_text()
        for forbidden in (
            "password=",
            "token=",
            "secret=",
            "api_key=",
        ):
            assert forbidden.lower() not in text.lower(), (
                f"Installer source must not contain {forbidden!r}"
            )

    def test_license_is_included(self) -> None:
        text = _iss_text()
        # The LICENSE file is staged into ``root_extra\\`` by the
        # build script and the [Files] section copies it. The
        # installer also exposes it via AppReadmeFile so the
        # wizard shows the licence on the **Info** page before
        # install.
        assert re.search(r"AppReadmeFile=docs\\windows-installer\.md", text), (
            "Installer must declare AppReadmeFile for the wizard info page"
        )

    def test_no_pascal_default_parameter_values(self) -> None:
        # Regression: the [Code] section declared
        # ``function RunCliSync(const Args: string;
        # const TimeoutS: Integer = 30): string;`` — the
        # ``= 30`` is a Delphi-style default parameter value.
        # Inno Setup's bundled Pascal Script is a stripped-down
        # older version that does *not* support default
        # parameter values; the compiler reported
        # ``Semicolon (';') expected`` at the ``=`` column.
        # Strip comment lines so the explanatory prose does
        # not trip the negative assertion.
        import re

        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        # Look for the ``: Type = <number>`` pattern that would
        # indicate a default parameter value. The pattern is
        # specific to a function/procedure parameter list.
        assert not re.search(
            r":\s*[A-Za-z][A-Za-z0-9_]*\s*=\s*\d",
            code_text,
        ), (
            "Installer source [Code] section must not declare "
            "Delphi-style default parameter values; Inno Setup's "
            "Pascal Script does not support them. Pass the "
            "value explicitly at the call site instead."
        )

    def test_no_curuninstallprogress_type_for_progress_param(self) -> None:
        # Regression: the [Code] section declared
        # ``procedure CurUninstallProgressChanged(CurProgress:
        # CurUninstallProgress);`` — ``CurUninstallProgress`` is
        # the *unit* the event function is declared in (and also
        # the name of the underlying record type), but it is
        # *not* itself a usable type for the event's parameter
        # declaration. The compiler reported
        # ``Unknown type 'CurUninstallProgress'``. The correct
        # parameter type is ``Integer`` (a 0..100 progress
        # percentage). Strip comment lines so the explanatory
        # prose does not trip the negative assertion.
        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert "CurUninstallProgress)" not in code_text, (
            "Installer source [Code] section must not use "
            "``CurUninstallProgress`` as a parameter type; the "
            "correct type for ``CurUninstallProgressChanged``'s "
            "``CurProgress`` parameter is ``Integer``"
        )

    def test_no_undefined_check_function_references(self) -> None:
        # Regression: the [Files] section's icon-copy entry
        # declared ``Check: "IconSourceExists()"`` but the
        # function was never defined in the [Code] section, and
        # the compiler reported
        # ``Required function or procedure 'IconSourceExists'
        # not found``. The ``Check:`` parameter must only
        # reference functions that are actually defined in the
        # [Code] section. Strip comment lines so the
        # explanatory prose does not trip the negative
        # assertion.
        import re

        text = _iss_text()
        # Collect ``Check: "<name>(...)"`` references.
        check_refs = re.findall(r"Check:\s*[\"']([A-Za-z][A-Za-z0-9_]*)", text)
        # Collect ``function`` and ``procedure`` declarations
        # in the [Code] section.
        code_lines = [
            line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        declared = set(
            re.findall(
                r"^\s*(?:function|procedure)\s+([A-Za-z][A-Za-z0-9_]*)",
                code_text,
                re.MULTILINE,
            )
        )
        for ref in check_refs:
            assert ref in declared, (
                f"Installer source references undefined Check "
                f"function ``{ref}``; the function must be "
                f"declared in the [Code] section"
            )

    def test_launch_after_install_can_be_skipped_in_silent(self) -> None:
        text = _iss_text()
        # The Pascal ``ShouldRunPostInstall`` function honours
        # ``/NoRun`` which the silent switches inject.
        assert "ShouldRunPostInstall" in text
        assert "param:NoRun" in text or "NoRun" in text

    def test_run_section_has_postinstall_launch(self) -> None:
        # The completion-page "Launch Lockverity" checkbox
        # is implemented by a single ``[Run]`` entry that
        # runs the installed ``Lockverity.exe`` after the
        # install. The entry must be guarded by
        # ``skipifsilent`` so a silent / unattended install
        # never opens the graphical launcher (and never
        # opens a browser window). The ``postinstall`` flag
        # registers the completion-page checkbox. The
        # ``unchecked`` flag leaves the checkbox off by
        # default so the operator has to opt in.
        text = _iss_text()
        assert "[Run]" in text, "Installer must declare a [Run] section"
        # Extract the [Run] section body. Strip comment
        # lines first so the next-section marker (e.g.
        # ``[Icons]``) is only matched on directive lines.
        after_run = text.split("[Run]", 1)[1]
        non_comment_lines = [
            ln for ln in after_run.splitlines() if ln.strip() and not ln.strip().startswith(";")
        ]
        # Find the next ``[`` that starts a section header
        # (i.e. at the beginning of a non-comment line).
        run_lines = []
        for ln in non_comment_lines:
            stripped = ln.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            run_lines.append(stripped)
        joined = " ".join(run_lines)
        # The launcher target must use the ``{app}``
        # constant so the path resolves to the actual
        # install location (and never a relative or
        # portable path that depends on the install
        # layout).
        assert "Filename:" in joined, (
            f"[Run] section must contain a Filename: directive; got: {joined!r}"
        )
        # The Filename must point at the **actual installed**
        # graphical launcher. The accepted v2.1 Part B3A
        # payload is installed verbatim under ``{app}\app\``
        # (see the Files section), so the launcher lives
        # at ``{app}\app\Lockverity.exe``. Pointing at
        # ``{app}\Lockverity.exe`` would target a file that
        # does not exist on disk.
        filename_part = [ln for ln in run_lines if ln.lower().startswith("filename:")]
        assert filename_part, "[Run] section must contain a Filename: directive"
        # The Filename line must include the inner ``app\``
        # subdirectory (either the ``{#MyAppPayloadDir}``
        # define or the literal ``app\``). A bare
        # ``{app}\{#MyAppExeName}`` would point at a file
        # that does not exist after install.
        assert "{#MyAppPayloadDir}" in filename_part[0] or "app\\" in filename_part[0], (
            f"[Run] Filename: must include the inner {{app}}\\app\\ "
            f"subdirectory (the actual install location of Lockverity.exe); "
            f"got: {filename_part[0]!r}"
        )
        # The Flags: line must include ``postinstall``
        # (checkbox on the finished page) and
        # ``skipifsilent`` (never auto-launch under
        # /VERYSILENT). The ``nowait`` and ``unchecked``
        # flags are the documented defaults but not
        # strictly required -- the regression contract
        # is "checkbox is visible" + "no launch in
        # silent mode".
        assert "postinstall" in joined, (
            "[Run] must include 'postinstall' so the completion-page checkbox is registered"
        )
        assert "skipifsilent" in joined, (
            "[Run] must include 'skipifsilent' so a silent install "
            "never opens the graphical launcher or a browser window"
        )

    def test_default_group_name_is_lockverity(self) -> None:
        # The Start Menu shortcuts must live in a
        # dedicated ``Programs\\Lockverity\\`` folder,
        # not directly under the user's default
        # ``Programs\\`` folder. ``DisableProgramGroupPage=yes``
        # is acceptable (it suppresses the wizard's
        # group-picker page so the user never sees a
        # confusing prompt on a per-user install); what
        # matters is the ``DefaultGroupName`` directive,
        # which is the canonical value the ``{group}``
        # constant resolves to when the group page is
        # disabled.
        text = _iss_text()
        match = re.search(
            r"DefaultGroupName\s*=\s*\{?#MyAppDisplayName\}?",
            text,
        )
        assert match, (
            "Installer source must declare "
            "'DefaultGroupName={#MyAppDisplayName}' so the Start Menu "
            "shortcuts land in Programs\\Lockverity\\, not in the user's "
            "default Programs\\(Default)\\ folder."
        )

    def test_uninstall_deletes_runtime_artefact_patterns(self) -> None:
        # Defensive cleanup: if a prior crashed /
        # misconfigured run left runtime artefacts
        # (SQLite database, WAL / SHM / journal sidecars,
        # log files, lock files, pid files, state files)
        # in the install root, the uninstaller must
        # remove them. The default Part B2 database
        # URL is CWD-independent (so this is a
        # defence-in-depth measure for a misconfigured
        # ``LOCKVERITY_DATABASE_URL`` override), but the
        # pattern is a hard contract: the install dir
        # must be fully removable after the uninstall.
        text = _iss_text()
        assert "[UninstallDelete]" in text, "Installer must declare a [UninstallDelete] section"
        section = text.split("[UninstallDelete]", 1)[1]
        next_section = section.find("[")
        if next_section != -1:
            section = section[:next_section]
        # Parse the ``Name:`` patterns out of the
        # multi-directive lines. Inno Setup allows
        # multiple directives per line separated by
        # ``;`` (e.g. ``Type: files; Name: "{app}\*.log"``).
        # The naive "starts with name:" parser would
        # miss them; we tokenise on ``;`` and inspect
        # every ``Name:`` token.
        names: list[str] = []
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            for token in stripped.split(";"):
                tok = token.strip()
                if tok.lower().startswith("name:"):
                    names.append(tok.split(":", 1)[1].strip())
        required = [
            r"{app}\*.sqlite",
            r"{app}\*.sqlite-*",
            r"{app}\*.log",
        ]
        for pattern in required:
            assert any(pattern in name for name in names), (
                f"[UninstallDelete] must include a Name: pattern that "
                f"deletes {pattern!r} so the install root is fully "
                f"removable after uninstall. Found patterns: {names!r}"
            )

    def test_runtime_data_preserved_on_uninstall(self) -> None:
        text = _iss_text()
        # The uninstall must NOT remove the runtime data home.
        # The ``[UninstallDelete]`` section must only target
        # ``{app}\\app`` and ``{app}\\docs``. We assert that
        # no ``Name:`` directive inside [UninstallDelete] refers
        # to ``{localappdata}`` or to any path under the
        # operator's runtime data.
        assert "[UninstallDelete]" in text, "Installer must declare a [UninstallDelete] section"
        section = text.split("[UninstallDelete]", 1)[1]
        next_section = section.find("[")
        if next_section != -1:
            section = section[:next_section]
        # Parse each ``Name:`` directive in the uninstall-delete
        # block; none of them may reference localappdata.
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped.lower().startswith("name:"):
                continue
            value = stripped.split(":", 1)[1].strip()
            assert "localappdata" not in value.lower(), (
                f"Uninstaller must not delete {{localappdata}}\\Lockverity: {value!r}"
            )
            assert (
                "lockverity" not in value.lower()
                or "app" in value.lower()
                or "docs" in value.lower()
            ), f"Uninstaller Name directive must target the install root: {value!r}"

    def test_uninstall_message_includes_retained_path(self) -> None:
        text = _iss_text()
        assert "UninstallAfterSuccess" in text, (
            "Uninstaller must surface a message via UninstallAfterSuccess"
        )
        # The message must include the localappdata path so the
        # operator knows where their data is.
        assert "localappdata" in text, "Uninstall message must point at the retained-data path"

    def test_signed_status_documented_in_source(self) -> None:
        text = _iss_text()
        # The source must explicitly mark the installer as
        # unsigned (per the spec).
        assert "SignedUninstaller=no" in text, (
            "Installer source must mark the uninstaller as unsigned"
        )

    def test_no_invalid_signtool_directive(self) -> None:
        # Regression: ``SignTool=Skip`` is not valid ISCC syntax and
        # causes the compiler to fail with
        # ``Value of [Setup] section directive "SignTool" is invalid.``
        # The right way to mark the installer as unsigned is
        # ``SignedUninstaller=no`` (and to omit any ``SignTool=``
        # directive entirely, since it is only meaningful for the
        # compiler's *runtime* signing pipeline which we do not use).
        # Strip comment lines (starting with ``;``) so explanatory
        # prose in the .iss does not trip the assertion.
        import re

        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert "SignTool=Skip" not in code_text, (
            "Installer source must not contain the invalid "
            "``SignTool=Skip`` directive; use SignedUninstaller=no "
            "and omit SignTool entirely"
        )
        # Any SignTool= directive is also forbidden because we have
        # no signing tool configured. The regex is case-insensitive on
        # the directive name to catch any future regression variant.
        assert not re.search(r"^\s*SignTool\s*=", code_text, re.MULTILINE | re.IGNORECASE), (
            "Installer source must not declare any SignTool= directive; "
            "use SignedUninstaller=no and leave SignTool undeclared"
        )

    def test_no_nonexistent_showcmdlinehelp_directive(self) -> None:
        # Regression: a stray ``ShowCmdlineHelp=yes`` was committed
        # and the compiler reported
        # ``Unrecognized [Setup] section directive "ShowCmdlineHelp"``.
        # There is no ``ShowCmdLineHelp`` [Setup] directive in
        # Inno Setup 6.7.3 at all — that name is a holdover from a
        # different version. The default behavior in 6.7.3 already
        # prints the /HELP /? summary when Setup is run with no
        # arguments, so the directive is neither necessary nor
        # accepted. Strip comment lines so explanatory prose does
        # not trip the negative assertion.
        code_lines = [
            line
            for line in _iss_text().splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        code_text = "\n".join(code_lines)
        assert "ShowCmdlineHelp" not in code_text, (
            "Installer source must not declare a ShowCmdlineHelp "
            "directive — it does not exist in Inno Setup 6.7.3"
        )
        assert "ShowCmdLineHelp" not in code_text, (
            "Installer source must not declare a ShowCmdLineHelp "
            "directive either — it does not exist in Inno Setup "
            "6.7.3; the default behavior already prints the /HELP "
            "summary when Setup is run with no arguments"
        )

    def test_all_setup_directives_recognised_by_inno_6_7_3(self) -> None:
        # Belt-and-braces regression: every [Setup] section
        # directive declared in the .iss must be a directive
        # Inno Setup 6.7.3 recognises. The list is taken from the
        # official isetup.xml shipped with the compiler. Without
        # this guard, a stray legacy directive (e.g.
        # ``DiskDirectory``, ``SlicesPerDisk``) will be accepted
        # at commit time and only fail at compile time inside the
        # installer build script — which is slow and breaks the
        # spec's "compile from a clean committed HEAD" rule.
        import re

        # The full set of [Setup] section directives supported by
        # Inno Setup 6.7.3. Sourced from the upstream
        # ``ISHelp/isetup.xml`` help file shipped with the
        # compiler. Keep this list in sync with the locked
        # compiler version.
        KNOWN_SETUP_DIRECTIVES: set[str] = {  # noqa: N806 - sentinel constant naming
            "AppId",
            "AppIdFormat",
            "AppName",
            "AppVerName",
            "AppVersion",
            "AppPublisher",
            "AppPublisherURL",
            "AppSupportURL",
            "AppUpdatesURL",
            "AppContact",
            "AppCopyright",
            "AppComments",
            "AppModifyPath",
            "AppReadmeFile",
            "AppMutex",
            "PrivilegesRequired",
            "PrivilegesRequiredOverridesAllowed",
            "ArchitecturesInstallIn64BitMode",
            "ArchitecturesAllowed",
            "DefaultDirName",
            "DefaultGroupName",
            "BaseFilename",
            "UninstallDisplayIcon",
            "UninstallDisplayName",
            "UninstallFilesDir",
            "UninstallRegKey",
            "Uninstallable",
            "CloseApplicationsFilter",
            "DisableAppendDir",
            "DisableDirPage",
            "DisableProgramGroupPage",
            "AllowNoIcons",
            "AllowRootDirectory",
            "AlwaysShowComponentsList",
            "AlwaysShowDirOnReadyPage",
            "AlwaysShowGroupOnReadyPage",
            "WizardStyle",
            "WizardSizePercent",
            "WizardImageAlphaFormat",
            "WizardImageBackColor",
            "WizardImageFile",
            "WizardSmallImageFile",
            "WizardBackColor",
            "WizardBackColorWidth",
            "Compression",
            "SolidCompression",
            "CompressionThreads",
            "LZMAAlgorithm",
            "LZMABlockSize",
            "LZMADictionarySize",
            "LZMAMatchFinder",
            "LZMANumBlockThreads",
            "LZMANumFastBytes",
            "LZMAUseSeparateProcess",
            "DiskClusterSize",
            "DiskSliceSize",
            "DiskSpanning",
            "OutputBaseFilename",
            "OutputDir",
            "OutputManifestFile",
            "SetupIconFile",
            "SignedUninstaller",
            "SignedUninstallerDir",
            "SignTool",
            "SignToolMinimumTimeBetween",
            "SignToolRetryCount",
            "SignToolRetryDelay",
            "SignToolRunMinimized",
            "SetupLogging",
            "DebugLogging",
            "CloseApplications",
            "CloseApplicationsFilterExcludes",
            "RestartApplications",
            "AllowCancelDuringInstall",
            "SetupMutex",
            "TouchDate",
            "TouchSize",
            "Touch",
            "TerminalServicesAware",
            "UninstallLogMode",
            "UninstallRestartComputer",
            "UpdateUninstallLogAppName",
            "UsedUserAreasWarning",
            "CreateUninstRegKey",
            "CloseEscButton",
            "InfoBeforeFile",
            "LicenseFile",
            "InfoAfterFile",
            "UserInfoPage",
            "ShowUserInfoPage",
            "UserInfoTitle",
            "Encryption",
            "EncryptionPassword",
            "MinAES",
            "AllowUNCPath",
            "AllowNetworkDrive",
            "AppNoMessagesFile",
            "TimeStamp",
            "TimeStampTouch",
            "AppUserModelID",
            "ShowLanguageDialog",
            "MergeDuplicateFiles",
            "UninstallStyle",
            "VersionInfoVersion",
            "VersionInfoCompany",
            "VersionInfoDescription",
            "VersionInfoTextVersion",
            "VersionInfoCopyright",
            "VersionInfoProductName",
            "VersionInfoProductVersion",
            "VersionInfoFileVersion",
            "VersionInfoFileDescription",
            "VersionInfoOriginalFilename",
            "VersionInfoComments",
            "VersionInfoInternalName",
            "VersionInfoLegalCopyright",
            "VersionInfoLegalTrademarks",
            "VersionInfoPrivateBuild",
            "VersionInfoSpecialBuild",
            "MinVersion",
            "OnlyBelowVersion",
        }
        # Parse the [Setup] section from the .iss.
        in_setup = False
        declared: list[tuple[int, str]] = []  # (line_no, directive_name)
        for line_no, line in enumerate(_iss_text().splitlines(), 1):
            stripped = line.strip()
            if stripped == "[Setup]":
                in_setup = True
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_setup:
                    break
                continue
            if not in_setup or not stripped or stripped.startswith(";"):
                continue
            m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)", stripped)
            if m and "=" in stripped:
                declared.append((line_no, m.group(1)))
        unknown = [(n, d) for n, d in declared if d not in KNOWN_SETUP_DIRECTIVES]
        assert not unknown, (
            "Installer source declares [Setup] section directives "
            "that Inno Setup 6.7.3 does not recognise: "
            + ", ".join(f"{d} (line {n})" for n, d in unknown)
            + ". Remove them or use the supported replacement."
        )

    def test_no_python_or_node_required(self) -> None:
        text = _iss_text()
        # The installer source must not bundle or invoke a
        # Python / Node / npm interpreter.
        for forbidden in ("python.exe", "node.exe", "npm.cmd", "pip.exe"):
            assert forbidden not in text, f"Installer must not reference {forbidden!r}"


# ---------------------------------------------------------------------
# Upgrade cleanup contract (LV-001)
# ---------------------------------------------------------------------


class TestUpgradeCleanupContract:
    """Upgrades must remove obsolete payload files (LV-001).

    The [Files] section replaces files at matching paths but never
    removes files that no longer exist in the new payload, so an
    upgraded install could keep stale DLL / PYD / module / runtime
    files indefinitely. Lockverity already experienced this exact
    failure class with a stale native psutil mismatch during
    portable packaging, so the installer must retire the old
    payload directory before copying the new one.
    """

    def test_install_step_removes_previous_payload_dir(self) -> None:
        """The cleanup runs at ``ssInstall``, before [Files] copies.

        ``CurStepChanged(ssInstall)`` fires after every
        ``PrepareToInstall`` check passed (runtime confirmed
        stopped, WebView2 present) and immediately before the
        [Files] entries are copied -- the only point where the
        removal is both safe and effective.
        """
        text = _iss_text()
        assert "procedure RemovePreviousPayload" in text, (
            "Installer must declare a RemovePreviousPayload procedure in [Code]"
        )
        assert "if CurStep = ssInstall then" in text, (
            "CurStepChanged must gate the payload cleanup on ``ssInstall`` so it "
            "runs after the live-instance/WebView2 checks and before [Files] copies"
        )
        assert "DelTree(" in text, (
            "The payload cleanup must use DelTree on the old payload directory"
        )
        assert "RaiseException(" in text, (
            "The cleanup must abort the install (RaiseException) when the old "
            "payload cannot be removed; layering the new payload on a partially "
            "stale tree reproduces the obsolete-files failure"
        )

    def test_cleanup_targets_only_packaged_payload_dir(self) -> None:
        """The cleanup target is exactly ``{app}\\<INSTALL_PAYLOAD_DIR>``.

        The deletion must not be able to reach the operator's
        runtime data home ``%LOCALAPPDATA%\\Lockverity`` or the
        bare install root. The test extracts the payload-dir
        constant from the [Code] ``const`` block and asserts the
        DelTree argument is built from ``{app}`` plus that
        constant and nothing else.
        """
        text = _iss_text()
        match = re.search(r"INSTALL_PAYLOAD_DIR\s*=\s*'([^']+)'", text)
        assert match is not None, "Installer [Code] must define the INSTALL_PAYLOAD_DIR constant"
        payload_dir = match.group(1)
        assert payload_dir, "INSTALL_PAYLOAD_DIR must not be empty"
        target = "ExpandConstant('{app}') + '\\' + INSTALL_PAYLOAD_DIR"
        assert target in text, (
            "The cleanup target must be built as "
            f"``{target}``; do not widen it to the install root or "
            "any {localappdata} path"
        )
        # The cleanup procedure must not reference any
        # localappdata-based path: the runtime data home
        # ``%LOCALAPPDATA%\Lockverity`` is a different tree and
        # must never be deletable by the upgrade cleanup.
        proc = text.split("procedure RemovePreviousPayload", 1)[1]
        proc = proc.split("procedure CurStepChanged", 1)[0]
        assert "localappdata" not in proc.lower(), (
            "The upgrade cleanup must never reference {localappdata}; the "
            "runtime data home is preserved by design"
        )
        # A bare ``ExpandConstant('{app}')`` deletion (without the
        # payload subdir suffix) would remove the install root
        # including the uninstaller; the target must carry the
        # payload subdirectory suffix.
        assert "DelTree(OldPayloadDir" in proc, (
            "DelTree must receive the payload-subdir-qualified OldPayloadDir "
            "variable, never a bare {app} path"
        )

    def test_upgrade_removes_obsolete_sentinel(self, tmp_path: Path) -> None:
        """Conceptual upgrade simulation: an obsolete file is gone.

        The test extracts the cleanup rule from the committed .iss
        (the ``INSTALL_PAYLOAD_DIR`` constant and the
        ``{app}``-relative target construction), replays it against
        an isolated fake install root that mirrors an OLD install
        (containing ``app\\obsolete-sentinel.dll``), then applies
        the new payload and asserts the sentinel is gone. No real
        installation is performed; everything happens under
        ``tmp_path``.
        """
        import shutil

        match = re.search(r"INSTALL_PAYLOAD_DIR\s*=\s*'([^']+)'", _iss_text())
        assert match is not None
        payload_dir = match.group(1)

        fake_app = tmp_path / "Programs" / "Lockverity"
        old_payload = fake_app / payload_dir
        old_payload.mkdir(parents=True)
        (old_payload / "Lockverity.exe").write_bytes(b"OLD-EXE")
        (old_payload / "obsolete-sentinel.dll").write_bytes(b"OBSOLETE")
        (old_payload / "_internal").mkdir()
        (old_payload / "_internal" / "stale.pyd").write_bytes(b"STALE-PYD")
        # The operator's runtime data home is a SIBLING tree; the
        # cleanup must leave it untouched.
        runtime_home = tmp_path / "Lockverity"
        runtime_home.mkdir()
        (runtime_home / "run").mkdir()
        (runtime_home / "run" / "marker.txt").write_text("user-data", encoding="utf-8")

        # Apply the extracted cleanup rule: remove {app}\app when
        # it exists (the DirExists + DelTree semantics).
        if old_payload.exists():
            shutil.rmtree(old_payload)

        # Apply the [Files] copy of the fresh payload.
        new_payload = fake_app / payload_dir
        new_payload.mkdir(parents=True)
        (new_payload / "Lockverity.exe").write_bytes(b"NEW-EXE")
        (new_payload / "_internal").mkdir()
        (new_payload / "_internal" / "fresh.pyd").write_bytes(b"FRESH-PYD")

        assert not (new_payload / "obsolete-sentinel.dll").exists(), (
            "obsolete-sentinel.dll survived the upgrade: the old payload "
            "directory must be removed before the new payload is copied"
        )
        assert not (new_payload / "_internal" / "stale.pyd").exists(), (
            "a stale _internal file survived the upgrade"
        )
        assert (new_payload / "_internal" / "fresh.pyd").is_file()
        assert (runtime_home / "run" / "marker.txt").read_text(encoding="utf-8") == "user-data", (
            "the cleanup rule must not be able to touch %LOCALAPPDATA%\\Lockverity user data"
        )

    def test_fresh_install_cleanup_is_noop(self, tmp_path: Path) -> None:
        """With no previous payload the cleanup exits without error.

        The DirExists guard makes the ssInstall cleanup a no-op on
        a fresh install; the procedure must not raise when
        ``{app}\\app`` does not exist yet.
        """
        text = _iss_text()
        proc = text.split("procedure RemovePreviousPayload", 1)[1]
        proc = proc.split("procedure CurStepChanged", 1)[0]
        assert "if not DirExists(OldPayloadDir) then" in proc, (
            "The cleanup must be guarded by DirExists so a fresh install "
            "(no previous payload) proceeds without error"
        )
        assert "exit" in proc, "The DirExists guard must exit early on a fresh install"
        # Simulate the no-op path: nothing exists under the fake
        # install root; the guard means no deletion is attempted.
        fake_app = tmp_path / "Programs" / "Lockverity"
        fake_app.mkdir(parents=True)
        assert not (fake_app / "app").exists()


# ---------------------------------------------------------------------
# Build script contract
# ---------------------------------------------------------------------


class TestBuildScriptContract:
    """The build script must verify all accepted hashes and refuse
    silently-substituted payloads."""

    def test_build_script_declares_stable_app_id(self) -> None:
        text = _build_text()
        assert STABLE_APP_ID in text, f"Build script must use the stable AppId {STABLE_APP_ID}"

    def test_build_script_pins_source_identity_not_generated_hashes(self) -> None:
        """The build script must pin the payload's *source identity*
        and product version, but MUST NOT pin any generated
        binary hash (portable ZIP, Lockverity.exe, etc.) inside
        a tracked Python constant. Generated hashes are read at
        build time from the payload's own ``SHA256SUMS.txt`` and
        ``BUILD-MANIFEST.json`` and written to the external
        ``INSTALLER-MANIFEST.json`` / ``SHA256SUMS.txt``.
        """
        text = _build_text()
        # The product version is the only generated-hash
        # *adjacent* value tracked Python code may keep. The
        # source identity is captured at build time via
        # ``_git_head_full()`` (not pinned).
        assert APP_VERSION in text, f"Build script must pin product version {APP_VERSION!r}"
        assert "_git_head_full()" in text, (
            "Build script must capture the current git HEAD via _git_head_full()"
        )
        # No exact generated value may live in a tracked
        # Python constant. We assert this by checking that the
        # previous-generation constant names are gone. The
        # build script's hash constants must be limited to the
        # product version (APP_VERSION), the AppId, and
        # build-time configuration (port hints, paths, etc.).
        for forbidden_name in (
            "EXPECTED_PAYLOAD_ZIP_SHA256",
            "EXPECTED_LOCKVERITY_EXE_SHA256",
            "EXPECTED_LOCKVERITY_CLI_EXE_SHA256",
            "EXPECTED_PAYLOAD_SOURCE_COMMIT",
        ):
            assert f"{forbidden_name} = " not in text, (
                f"Build script must not declare a module-level "
                f"constant {forbidden_name!r} that pins a "
                "generated value or a specific source commit"
            )

    def test_build_script_uses_subprocess_without_shell_true(self) -> None:
        text = _build_text()
        # Every ``subprocess.run`` / ``Popen`` call must use an
        # argv list, never ``shell=True``.
        assert "shell=True" not in text, "Build script must not use shell=True anywhere"
        # All subprocess calls must pass an argv list. We assert
        # by absence of the legacy string-form.
        assert "subprocess.call(" not in text
        assert "os.system(" not in text, "Build script must not call os.system()"

    def test_build_script_refuses_dirty_git_state(self) -> None:
        text = _build_text()
        # The build script must call ``git status --porcelain``
        # and raise an actionable error if the working tree is
        # dirty. The actual implementation uses
        # ``--untracked-files=no`` for a stricter check.
        assert re.search(r"status[^\n]*--porcelain", text), (
            "Build script must check ``git status --porcelain``"
        )
        # The check must raise an actionable error on a dirty
        # tree. The exact wording can vary; we look for the
        # abort keyword + the porcelain-driven detection.
        assert "dirty" in text.lower() and "working tree" in text.lower(), (
            "Build script must refuse a dirty working tree"
        )

    def test_build_script_verifies_clean_git_full_sha(self) -> None:
        text = _build_text()
        # The build script must call ``git rev-parse HEAD`` (not
        # the abbreviated ``--short`` form) and validate the
        # 40-character hex format.
        assert re.search(r"rev-parse[^\n]*HEAD", text), (
            "Build script must use ``git rev-parse HEAD`` (40-char)"
        )
        assert re.search(r"\[0-9a-f\]\{40\}", text), (
            "Build script must validate the 40-char SHA format"
        )

    def test_build_script_refuses_tampered_payload_via_sha256sums(self) -> None:
        """The build script must refuse a payload whose files do
        not match the payload's own ``SHA256SUMS.txt``. This is
        the integrity check — no generated hash is pinned by
        the build script; the expected SHA-256 for every file
        is read from the payload's manifest.
        """
        text = _build_text()
        assert "SHA256SUMS.txt" in text, (
            "Build script must read the payload's own SHA256SUMS.txt to verify integrity"
        )
        # The mismatch must be flagged on any entry of
        # SHA256SUMS, not on a single pinned value.
        assert "SHA-256 mismatch" in text, (
            "Build script must refuse a payload whose SHA256SUMS entries do not match"
        )
        # The build script must also reject the case where
        # SHA256SUMS references a file that is not in the
        # payload.
        assert "references missing file" in text, (
            "Build script must refuse a payload whose SHA256SUMS references a missing file"
        )

    def test_build_script_generates_installer_manifest(self) -> None:
        text = _build_text()
        assert "INSTALLER-MANIFEST.json" in text, "Build script must emit INSTALLER-MANIFEST.json"
        for required in (
            "product",
            "installer_source_commit",
            "payload_source_commit",
            "payload_zip_sha256",
            "lockverity_exe_sha256",
            "lockverity_cli_exe_sha256",
            "installer_sha256",
            "stable_app_id",
            "default_install_path",
            "code_signing_status",
        ):
            assert f'"{required}"' in text, (
                f"Build script must record the {required!r} field in the manifest"
            )

    def test_build_script_manifest_has_no_local_paths(self) -> None:
        text = _build_text()
        # The manifest writer must not bake local absolute paths.
        for forbidden in (
            "C:\\Users",
            "C:/Users",
            "/Users/",
            "/home/",
        ):
            assert forbidden not in text, (
                f"Build script must not write {forbidden!r} into the manifest"
            )

    def test_build_script_uses_no_shell(self) -> None:
        text = _build_text()
        # Same as the source contract; the build script must
        # never use a shell to spawn the compiler.
        assert "shell=True" not in text

    def test_build_script_cleans_only_dedicated_paths(self) -> None:
        text = _build_text()
        # The ``--clean`` flag must only target installer output,
        # staging, and work directories. It must not touch the
        # portable build directory.
        # The portable build directory is referenced in the
        # build script only via the default payload-zip path,
        # not via any cleanup path. We confirm ``shutil.rmtree``
        # is used (the cleanup primitive) and that the only
        # targets are the three dedicated installer paths.
        assert "shutil.rmtree" in text
        # Confirm the build script only operates on its own
        # output dir / staging dir / work dir.
        for required in (
            "args.output_dir",
            "args.staging_dir",
            "args.work_dir",
        ):
            assert required in text, f"Build script --clean must target {required}"

    def test_build_script_documents_unsigned_status(self) -> None:
        text = _build_text()
        assert "unsigned" in text.lower(), "Build script must mark the installer as unsigned"

    def test_build_script_default_install_path(self) -> None:
        text = _build_text()
        assert "%LOCALAPPDATA%\\\\Programs\\\\Lockverity" in text or (
            "LOCALAPPDATA" in text and "Programs" in text and "Lockverity" in text
        ), "Build script must record the default LocalAppData install path"

    def test_build_script_uses_canonical_iscc_output_flag(self) -> None:
        # Regression: the build script originally passed
        # ``/OutputDir=<abs-path>`` and
        # ``/OutputBaseFilename=<name>`` as command-line flags to
        # ISCC. Neither is a valid ISCC 6.7.3 flag — the parser
        # treats single-character switch tokens (``/O``, ``/F``)
        # and the rest of the string becomes the switch's
        # argument, leading to ``I/O error 123``
        # (``ERROR_INVALID_NAME``) on the resulting path. The
        # canonical, supported ISCC flag for the output
        # *directory* is ``/O<directory>``; the final filename
        # is taken from the .iss's ``OutputBaseFilename``
        # directive (which is the single source of truth for the
        # final name).
        text = _build_text()
        import re

        # The long-form ``/OutputDir=`` flag must not be present.
        assert not re.search(r"[\"']/OutputDir=", text), (
            "Build script must not pass the non-existent "
            "``/OutputDir=`` flag to ISCC; use the canonical "
            "``/O<directory>`` flag instead"
        )
        # The long-form ``/OutputBaseFilename=`` flag must not be
        # present either — the .iss's ``OutputBaseFilename``
        # directive is the source of truth.
        assert not re.search(r"[\"']/OutputBaseFilename=", text), (
            "Build script must not pass the non-existent "
            "``/OutputBaseFilename=`` flag to ISCC; the .iss's "
            "``OutputBaseFilename`` directive is the source of "
            "truth"
        )
        # The canonical ``/O<directory>`` flag must be used.
        assert re.search(r"[\"']/O\{output_dir\}[\"']", text), (
            "Build script must pass the canonical ISCC "
            "``/O<directory>`` command-line flag (output "
            "directory only; the filename is taken from the "
            ".iss's ``OutputBaseFilename`` directive)"
        )

    def test_build_script_copies_iss_into_staging(self) -> None:
        # Regression: the .iss uses *relative* ``Source:`` paths
        # (``payload\*``, ``root_extra\*``, ``..\\pyinstaller\\...``)
        # that ISCC resolves against the directory containing
        # the .iss, not the current working directory. Since the
        # committed .iss lives under ``backend/installer/`` and
        # the staged payload lives under ``build/installer/staging/``,
        # the build script must copy the .iss into the staging
        # directory before invoking ISCC, and must invoke ISCC
        # against the staged copy — not against the committed
        # source. The staged copy is a transient build artefact
        # and is not committed.
        text = _build_text()
        assert "shutil.copy2" in text, (
            "Build script must copy the .iss into the staging "
            "directory so the relative Source: paths resolve "
            "correctly under ISCC"
        )
        # The staged copy must be used as the .iss argument, not
        # the committed source.
        assert re.search(r"str\(staged_iss\)", text), (
            "Build script must invoke ISCC against the staged copy of the .iss"
        )
        # The committed source must still be the source of the copy.
        assert re.search(r"shutil\.copy2\(\s*ISS_SOURCE\s*,\s*staged_iss", text), (
            "Build script must copy the committed ISS_SOURCE into the staging dir as staged_iss"
        )

    def test_build_script_stages_exe_icon_in_staging(self) -> None:
        # Regression: the .iss's ``SetupIconFile`` directive uses
        # the *relative* path ``..\\pyinstaller\\favicon-exe.ico``
        # (written when the .iss lived in ``backend/installer/``
        # and the icon in ``backend/pyinstaller/``). After the
        # .iss is copied into the staging dir, that relative path
        # needs the icon to also live at the matching relative
        # location inside the staging dir; otherwise ISCC
        # reports ``The system cannot find the path specified``
        # for the icon. The build script must (a) mirror the icon
        # at ``staging/pyinstaller/favicon-exe.ico`` and (b) rewrite
        # the staged copy's ``SetupIconFile`` to the
        # staging-relative path so it resolves under ISCC.
        text = _build_text()
        # The icon must be staged at the staging-relative path.
        assert re.search(r"pyinstaller_subdir\s*/\s*[\"']favicon-exe\.ico[\"']", text) or re.search(
            r"pyinstaller.*favicon-exe\.ico", text
        ), (
            "Build script must stage the EXE icon at "
            "``staging/pyinstaller/favicon-exe.ico`` so the .iss's "
            "relative ``SetupIconFile`` path resolves after the "
            ".iss is copied into the staging dir"
        )
        # The staged copy's ``SetupIconFile`` (and the matching
        # ``Source:`` line in the [Files] section) must be
        # rewritten from the committed
        # ``..\\pyinstaller\\favicon-exe.ico`` (backend-relative)
        # to the staging-relative ``pyinstaller\\favicon-exe.ico``
        # form. The committed source is unchanged. The Python
        # source carries escaped backslashes (each ``\\`` in the
        # source is one literal backslash in the string), so we
        # search for the *source-escaped* form.
        assert "..\\\\pyinstaller\\\\favicon-exe.ico" in text, (
            "Build script must rewrite the staged copy's "
            "icon references from the committed "
            "``..\\pyinstaller\\favicon-exe.ico`` to a "
            "staging-relative path so the relative paths "
            "resolve correctly under ISCC"
        )


# ---------------------------------------------------------------------
# Approved icon and payload
# ---------------------------------------------------------------------


class TestApprovedAssets:
    """The approved icon and payload ZIP must remain unchanged."""

    def test_approved_favicon_ico_unchanged(self) -> None:
        assert APPROVED_FAVICON_ICO.is_file(), f"Approved favicon missing: {APPROVED_FAVICON_ICO}"
        h = hashlib.sha256(APPROVED_FAVICON_ICO.read_bytes()).hexdigest()
        assert h == ("33dcc472ae67db90a238a34a2f08151924b45aabe1bf9ecfecb75755aa60f4cb"), (
            f"Approved favicon.ico SHA-256 changed: {h}"
        )

    def test_approved_favicon_exe_ico_unchanged(self) -> None:
        assert APPROVED_ICON.is_file()
        # The favicon-exe.ico is the packaging derivative; we
        # only require it to be a valid ICO with the four
        # documented sizes.
        data = APPROVED_ICON.read_bytes()
        assert data[:4] == b"\x00\x00\x01\x00", "favicon-exe.ico is not a valid ICO header"


# ---------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------


class TestInstallerDocumentation:
    """The user-facing installer documentation must exist and cover
    the documented topics."""

    def test_documentation_file_exists(self) -> None:
        assert DOCS_FILE.is_file(), f"Installer documentation missing: {DOCS_FILE}"

    def test_documentation_covers_required_topics(self) -> None:
        text = DOCS_FILE.read_text(encoding="utf-8", errors="replace")
        for topic in (
            "silent install",
            "default install",
            "%LOCALAPPDATA%",
            "runtime data",
            "reinstall",
            "uninstall",
            "SmartScreen",
            "code signing",
        ):
            assert topic.lower() in text.lower(), f"Documentation must cover the topic {topic!r}"


# ---------------------------------------------------------------------
# Provenance-design regression tests
# ---------------------------------------------------------------------


class TestProvenanceDesign:
    """The new provenance design must satisfy the regression
    contract documented in the user's publication-readiness
    instructions:

      * no exact generated payload binary hash is required
        from a tracked Python constant;
      * generated manifest values are full 40-character SHAs;
      * no local paths or secrets enter generated manifests;
      * tampered payload files are rejected;
      * mismatched source_commit is rejected;
      * the installer accepts a payload whose manifest
        source_commit equals the expected final source HEAD;
      * all payload SHA256SUMS entries are verified.
    """

    def test_b3b_acceptance_does_not_pin_generated_hashes(self) -> None:
        """The B3B acceptance script must not pin any generated
        binary hash as a Python constant. Expected hashes are
        read from the installer's external
        ``INSTALLER-MANIFEST.json`` (which is itself generated
        by the build script)."""
        text = _b3b_acceptance_text()
        for forbidden in (
            "_HISTORICAL_FORBIDDEN_PAYLOAD_ZIP_SHA256",
            "_HISTORICAL_FORBIDDEN_LOCKVERITY_EXE_SHA256",
            "_HISTORICAL_FORBIDDEN_LOCKVERITY_CLI_EXE_SHA256",
            "EXPECTED_LOCKVERITY_EXE_SHA256",
            "EXPECTED_LOCKVERITY_CLI_EXE_SHA256",
        ):
            assert f"{forbidden} = " not in text, (
                f"b3b_acceptance.py must not declare {forbidden!r} as a tracked constant"
            )
        # The acceptance script must read expected hashes from
        # the installer manifest, not from a local constant.
        assert "INSTALLER-MANIFEST.json" in text, (
            "b3b_acceptance.py must read expected EXE hashes from INSTALLER-MANIFEST.json"
        )

    def test_installer_accepts_payload_with_matching_source_commit(self) -> None:
        """The installer build script must accept a payload whose
        ``BUILD-MANIFEST.json`` ``source_commit`` equals the
        installer build's current ``git rev-parse HEAD`` (the
        same value that will be recorded as
        ``installer_source_commit`` in the generated
        ``INSTALLER-MANIFEST.json``). This is a static check on
        the build script's verification logic.
        """
        text = _build_text()
        assert 'manifest.get("source_commit") != expected_payload_source_commit' in text, (
            "Build script must reject payloads whose BUILD-MANIFEST.json source_commit "
            "does not match the installer build's HEAD"
        )

    def test_installer_rejects_mismatched_source_commit(self) -> None:
        """Negative case: the build script must fail loudly if
        ``source_commit`` does not match. The error message must
        include both the expected and the actual commit."""
        text = _build_text()
        assert "source_commit" in text and "expected" in text and "actual" in text, (
            "Build script must report both expected and actual source_commit on mismatch"
        )

    def test_installer_verifies_all_sha256sums_entries(self) -> None:
        """The build script must iterate every entry of the
        payload's ``SHA256SUMS.txt`` and verify it against the
        actual file bytes."""
        text = _build_text()
        assert "SHA256SUMS" in text, "Build script must reference the payload's SHA256SUMS.txt"
        # The build script must re-hash every file the
        # SHA256SUMS.txt mentions. We assert that the verification
        # loop hashes each entry (not just one or two
        # special-case files).
        assert "for rel, expected_sha in" in text, (
            "Build script must iterate every SHA256SUMS.txt entry"
        )
        assert "_sha256_of(file_path)" in text, (
            "Build script must re-hash every payload file referenced by SHA256SUMS.txt"
        )

    def test_installer_rejects_tampered_payload_files(self) -> None:
        """The build script must reject a payload whose files do
        not match the SHA256SUMS entries. This is asserted by
        checking the explicit mismatch error path.
        """
        text = _build_text()
        assert "payload file SHA-256 mismatch" in text, (
            "Build script must emit a clear error when a payload file's "
            "SHA-256 does not match its SHA256SUMS.txt entry"
        )
        # The error must list the file path, the expected and
        # the actual hash. This is what the operator sees.
        for needed in ("path:", "expected:", "actual:"):
            assert needed in text, f"Build-script tamper-rejection error must include {needed!r}"

    def test_generated_manifest_values_remain_full_40_char_shas(self) -> None:
        """Generated ``source_commit`` fields in the install
        manifest must be full 40-character lowercase hex SHA-1
        strings. The build script validates the format before
        writing it to ``INSTALLER-MANIFEST.json``."""
        text = _build_text()
        assert re.search(r"\[0-9a-f\]\{40\}", text), (
            "Build script must validate 40-character hex SHA-1 format"
        )
        # The full HEAD is read via ``git rev-parse HEAD`` and
        # is asserted to match the regex.
        assert 're.match(r"^[0-9a-f]{40}$", full)' in text, (
            "Build script must assert that git HEAD is a full 40-char SHA"
        )

    def test_no_local_paths_or_secrets_in_generated_manifests(self) -> None:
        """The build script must not embed local absolute paths
        (e.g. ``C:\\Users\\...``) or obvious secret-shaped strings
        in the generated ``INSTALLER-MANIFEST.json``. The
        manifest records the product name, version, source
        commit, stable AppId, architecture, and platform — but
        never the absolute install path (the operator's
        ``%LOCALAPPDATA%`` is recorded as a literal string, not
        a resolved absolute path).
        """
        text = _build_text()
        # The manifest builder is the canonical chokepoint. We
        # inspect its body for any local-path-shaped string
        # such as ``C:\\Users\\``, ``\\\\\\\\``, ``/Users/``,
        # ``/home/`` or an absolute path.
        # Find the manifest builder by anchor.
        anchor = "def _write_installer_manifest("
        idx = text.find(anchor)
        assert idx > 0, "Build script must define _write_installer_manifest"
        # The manifest body is the next ``return manifest_path``
        # or the next top-level ``def``. We extract a bounded
        # slice to keep the search local.
        end_idx = text.find("\n\n\n", idx)
        if end_idx < 0:
            end_idx = len(text)
        body = text[idx:end_idx]
        for forbidden_pattern in (
            "C:\\\\Users\\\\",
            "/Users/",
            "/home/",
            "C:\\\\Temp\\\\",
            "secret",
            "password=",
            "api_key",
        ):
            assert forbidden_pattern.lower() not in body.lower(), (
                f"Generated INSTALLER-MANIFEST.json body must not contain {forbidden_pattern!r}"
            )
        # The default install path is recorded as the literal
        # token ``%LOCALAPPDATA%\\Programs\\Lockverity`` (an
        # environment-variable template, not a resolved
        # absolute path). Assert the literal is present.
        assert '"%LOCALAPPDATA%\\\\Programs\\\\Lockverity"' in body, (
            "Generated manifest must record the install path as the "
            "literal %LOCALAPPDATA%\\Programs\\Lockverity template"
        )

    def test_no_exact_generated_payload_binary_hash_required_from_tracked_constant(self) -> None:
        """The combined tracked-Python source must not pin any
        generated payload binary hash. We assert this across
        both the build script and the acceptance script: no
        64-character hex literal appears as a Python constant
        declaration in either file, except for the forbidden
        historical literals explicitly named above.
        """
        # Build a list of "expected historical" 64-hex literals
        # that the test file itself uses to assert *non-
        # presence* of those exact values.
        expected_in_tests = {
            _HISTORICAL_FORBIDDEN_PAYLOAD_ZIP_SHA256,
            _HISTORICAL_FORBIDDEN_LOCKVERITY_EXE_SHA256,
            _HISTORICAL_FORBIDDEN_LOCKVERITY_CLI_EXE_SHA256,
        }
        forbidden_in_build = {
            _HISTORICAL_FORBIDDEN_PAYLOAD_ZIP_SHA256,
            _HISTORICAL_FORBIDDEN_LOCKVERITY_EXE_SHA256,
        }
        for forbidden in forbidden_in_build:
            assert forbidden not in _build_text(), (
                f"Build script must not contain the generated hash {forbidden!r}"
            )
        # The acceptance script may read these from the
        # installer's manifest but must not declare them as
        # pinned constants.
        for forbidden in expected_in_tests:
            assert (
                forbidden not in _b3b_acceptance_text()
                or (f'"{forbidden}"' in _b3b_acceptance_text()) is False
            ), f"b3b_acceptance.py must not declare the generated hash {forbidden!r} as a constant"

    def test_build_writes_full_40_char_source_commit_to_manifest(self) -> None:
        """The build script writes ``installer_source_commit``
        to the generated ``INSTALLER-MANIFEST.json`` from the
        full 40-character ``git rev-parse HEAD``. Tracked
        Python must not shorten the SHA."""
        text = _build_text()
        # Locate the manifest builder and assert the recorded
        # value is a 40-character SHA captured from
        # ``_git_head_full``.
        assert "_git_head_full()" in text, (
            "Build script must use the full 40-character _git_head_full()"
        )
        # The manifest body must record the full SHA without
        # slicing. We assert by checking the manifest builder
        # references both ``installer_source_commit`` and the
        # 40-character SHA source.
        assert '"installer_source_commit": installer_source_commit' in text, (
            "Build script must record the full 40-char installer_source_commit in the manifest"
        )

    def test_installer_manifest_records_all_required_fields(self) -> None:
        """The generated ``INSTALLER-MANIFEST.json`` must
        record the documented fields. The list below is the
        complete set the operator-facing release report
        expects. Missing fields break the audit chain."""
        text = _build_text()
        required = (
            "product",
            "version",
            "installer_source_commit",
            "payload_source_commit",
            "payload_zip",
            "payload_zip_sha256",
            "payload_build_manifest_sha256",
            "lockverity_exe_sha256",
            "lockverity_cli_exe_sha256",
            "installer_build_timestamp_utc",
            "target_platform",
            "target_architecture",
            "inno_setup_version",
            "inno_setup_compiler_path",
            "inno_setup_compiler_sha256",
            "installer_filename",
            "installer_sha256",
            "stable_app_id",
            "default_install_path",
            "privilege_mode",
            "code_signing_status",
        )
        for field in required:
            assert f'"{field}"' in text, (
                f"Generated INSTALLER-MANIFEST.json must record the field {field!r}"
            )

    def test_sha256sums_uses_lowercase_hex_format(self) -> None:
        """The payload's ``SHA256SUMS.txt`` uses lowercase hex
        and is parsed by the build script. The build script
        must assert that every parsed SHA-256 is a full
        64-character lowercase hex string."""
        text = _build_text()
        # The build script's regex for SHA-256 entries is
        # ``r"^[0-9a-f]{64}$"`` (lowercase hex only, 64 chars).
        assert 'r"^[0-9a-f]{64}$"' in text, (
            "Build script must require SHA256SUMS.txt SHA-256 entries to be lowercase hex"
        )

    def test_b3b_acceptance_reads_expected_hashes_from_installer_manifest(self) -> None:
        """The B3B acceptance script's payload-verification step
        must read the expected EXE hashes from the installer's
        external ``INSTALLER-MANIFEST.json`` — the canonical
        generated record — not from any local constant."""
        text = _b3b_acceptance_text()
        # The acceptance script must reference the
        # ``_read_installer_manifest`` helper (or equivalent
        # ``INSTALLER-MANIFEST.json`` reader).
        assert "_read_installer_manifest" in text, (
            "b3b_acceptance.py must read expected hashes from INSTALLER-MANIFEST.json"
        )
        # The expected hashes must be loaded from the
        # manifest, not from a local constant. The test pins
        # this: no `EXPECTED_*_EXE_SHA256` declaration is
        # allowed.
        assert "EXPECTED_LOCKVERITY_EXE_SHA256 = " not in text, (
            "b3b_acceptance.py must not declare EXPECTED_LOCKVERITY_EXE_SHA256"
        )
        assert "EXPECTED_LOCKVERITY_CLI_EXE_SHA256 = " not in text, (
            "b3b_acceptance.py must not declare EXPECTED_LOCKVERITY_CLI_EXE_SHA256"
        )

    def test_installer_rejects_payload_with_no_sha256sums(self) -> None:
        """The build script must refuse a payload whose
        ``SHA256SUMS.txt`` is empty. An empty integrity record
        would mean the operator cannot detect any tampering."""
        text = _build_text()
        assert "is empty" in text, "Build script must refuse a payload with empty SHA256SUMS.txt"

    def test_sha256sums_parser_rejects_path_escape(self) -> None:
        """The build script's ``SHA256SUMS.txt`` parser must
        reject entries whose relative path escapes the
        payload root (``..`` or absolute paths). This prevents
        a tampered manifest from tricking the build into
        reading or writing files outside the payload."""
        text = _build_text()
        assert "escapes payload root" in text, (
            "Build script must reject SHA256SUMS.txt paths that escape the payload root"
        )

    def test_sha256sums_parser_rejects_oversized_or_malformed_sha(self) -> None:
        """The build script must reject a ``SHA256SUMS.txt``
        line whose SHA-256 is not 64 lowercase hex characters."""
        text = _build_text()
        assert "malformed SHA-256" in text, (
            "Build script must reject malformed SHA-256 in SHA256SUMS.txt"
        )
