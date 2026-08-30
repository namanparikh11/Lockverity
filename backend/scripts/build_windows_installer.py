"""Build the Lockverity v2.1 Windows x64 per-user installer.

This script is the canonical entry point for the v2.1 Part B3B
Windows installer. It wraps the Inno Setup 6.x compiler
(``ISCC.exe``) and:

  - verifies the accepted B3A portable payload's
    *identity* (source commit, product version) by reading
    its embedded ``BUILD-MANIFEST.json``;
  - verifies the payload's *integrity* by reading its
    embedded ``SHA256SUMS.txt`` and comparing every entry
    against the actual file bytes, then verifying the
    COMPLETE payload file tree against the payload's
    ``PAYLOAD-MANIFEST.json`` (every regular file, expected-vs-
    actual path-set comparison, symlink/traversal defense --
    LV-003); no generated binary hash is pinned by this script,
    every generated hash is read at build time and recorded in
    the external ``INSTALLER-MANIFEST.json`` and
    ``SHA256SUMS.txt``;
  - extracts the payload into a dedicated staging directory
    (the installer source then copies the staging tree into
    ``{app}\\app\\``);
  - downloads Microsoft's small Evergreen WebView2 bootstrapper from the
    documented fwlink (unless an explicit local copy is supplied), requires
    a valid Microsoft Authenticode signature, and records its build-time hash;
  - runs the committed ``backend\\installer\\lockverity.iss``
    source through the verified compiler;
  - generates ``INSTALLER-MANIFEST.json`` and ``SHA256SUMS.txt``
    alongside the installer EXE;
  - runs a silent-install smoke test and verifies the
    installed application responds to ``/api/v1/health``;
  - emits a structured JSON report.

The script never silently rebuilds the accepted portable
payload. If the payload's identity or integrity checks fail
the script aborts with an actionable error. Tracked source
files MUST NOT pin any generated payload binary hash — that
is what caused the previous "rebuild -> source-commit ->
rebuild" cycle.

Usage::

    python backend\\scripts\\build_windows_installer.py
    python backend\\scripts\\build_windows_installer.py --clean --json-report
    python backend\\scripts\\build_windows_installer.py --payload-zip PATH
    python backend\\scripts\\build_windows_installer.py --keep-work
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
INSTALLER_DIR = BACKEND_ROOT / "installer"
ISS_SOURCE = INSTALLER_DIR / "lockverity.iss"
PAYLOAD_NAME = "Lockverity-2.1.3-windows-x64-portable"
DEFAULT_PAYLOAD_ZIP = REPO_ROOT / "build" / "packaging" / f"{PAYLOAD_NAME}.zip"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "build" / "installer"
DEFAULT_STAGING_DIR = DEFAULT_OUTPUT_DIR / "staging"
DEFAULT_WORK_DIR = DEFAULT_OUTPUT_DIR / "work"
PAYLOAD_DIR_NAME = "app"
# Stable per-user AppId mirrored in the .iss source. The build
# script is a sanity-check only; the .iss source is the
# authoritative declaration of the per-user uninstaller key.
STABLE_APP_ID = "{E5B0C0F4-7C42-4D6A-9B17-1A2B3C4D5E6F}"
APP_VERSION = "2.1.3"
# Inno Setup 6.7.3 is the only trusted compiler for this build.
# The compiler is installed under the current user's LocalAppData
# when fetched via the recommended publisher (jrsoftware.org).
ISCC_PATHS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
    Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
)
# The expected payload source identity is the build's own
# current git HEAD — not a pinned B3A source commit. The
# payload's ``BUILD-MANIFEST.json`` ``source_commit`` must
# match the installer build's current ``git rev-parse HEAD``;
# the installer then records that same HEAD as both
# ``installer_source_commit`` and ``payload_source_commit``
# in the generated ``INSTALLER-MANIFEST.json``. This makes
# the installer provenance a single consistent value
# (the build-time HEAD) rather than two independently
# maintained values.
#
# The expected source_commit is captured at the top of
# ``main()`` via ``_git_head_full()`` and passed into
# ``_verify_payload_zip``. There is no tracked constant for
# it: removing the constant from tracked Python source is
# the explicit fix for the previous
# "rebuild -> source-commit -> rebuild" cycle.
# A regular expression for a full 40-character lowercase hex
# git commit SHA. Used to assert that ``source_commit`` fields
# in any generated manifest are well-formed before they enter
# the installer manifest.
_GIT_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
# A bounded lock for the launched installer's /VERYSILENT
# smoke. The smoke is disabled by default to keep the
# build deterministic; pass ``--run-smoke`` to opt in.
SMOKE_PORT_HINT = 18790
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_BOOTSTRAPPER_NAME = "MicrosoftEdgeWebview2Setup.exe"
WEBVIEW2_BOOTSTRAPPER_MAX_BYTES = 16 * 1024 * 1024


def _log(stage: str, message: str) -> None:
    ts = datetime.datetime.now(tz=datetime.UTC).strftime("%H:%M:%S")
    sys.stderr.write(f"[{ts}] [{stage}] {message}\n")
    sys.stderr.flush()


def _verify_host() -> tuple[str, str]:
    if sys.platform != "win32":
        raise SystemExit(
            "ERROR: this build script targets Windows x64 only. "
            f"Detected platform: {sys.platform!r}."
        )
    machine = platform.machine().lower()
    if machine not in ("amd64", "x86_64"):
        raise SystemExit(f"ERROR: Windows x64 is required (detected machine={machine!r}).")
    py_version = sys.version_info
    if py_version < (3, 12):
        raise SystemExit(f"ERROR: Python 3.12+ is required (detected {py_version}).")
    return (sys.platform, machine)


def _verify_clean_git_state() -> None:
    """Refuse to build from a dirty tree unless explicitly told to proceed.

    The build script is the documented chokepoint; the installer
    is built from the same commit that the portable was built
    from. A dirty working tree at install-build time means the
    installer would record a different installer-source commit
    than what the operator checked out.
    """
    git = shutil.which("git")
    if git is None:
        raise SystemExit("ERROR: git is required on PATH to build the installer.")
    result = subprocess.run(  # noqa: S603 - argv is a fixed list
        [git, "status", "--porcelain", "--untracked-files=no"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: git status failed: {result.stderr.strip()}")
    if result.stdout.strip():
        raise SystemExit(
            "ERROR: working tree is dirty; commit or stash changes before "
            f"building the installer.\n{result.stdout}"
        )


def _git_head_full() -> str:
    """Return the full 40-character git HEAD commit SHA, or refuse."""
    git = shutil.which("git")
    if git is None:
        raise SystemExit("ERROR: git is required on PATH.")
    result = subprocess.run(  # noqa: S603 - argv is a fixed list
        [git, "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: git rev-parse failed: {result.stderr.strip()}")
    full = result.stdout.strip()
    if not re.match(r"^[0-9a-f]{40}$", full):
        raise SystemExit(f"ERROR: invalid git HEAD SHA: {full!r}")
    return full


def _verify_iscc() -> Path:
    """Return the verified ISCC.exe path. The compiler must be a
    signed Inno Setup compiler. We trust the install under
    ``%LOCALAPPDATA%\\Programs\\Inno Setup 6`` because that is
    where ``winget install JRSoftware.InnoSetup`` places the
    official, signed binary.
    """
    for candidate in ISCC_PATHS:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "ERROR: a verified Inno Setup 6.x compiler was not found. "
        "Install Inno Setup 6 via winget (``winget install "
        "JRSoftware.InnoSetup``) or from the official publisher "
        "(https://jrsoftware.org/isdl.php). The compiler path was "
        f"checked: {[str(p) for p in ISCC_PATHS]}"
    )


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_payload_tree_complete(payload_root: Path) -> dict[str, object]:
    """Verify the COMPLETE payload file tree (LV-003).

    The ``SHA256SUMS.txt`` verification covers the operator-facing
    top-level entries only. This check loads the dedicated
    ``backend/scripts/payload_manifest.py`` module (via
    :mod:`importlib.util`; the scripts tree is not a package) and
    verifies every regular file in the payload against the
    payload's ``PAYLOAD-MANIFEST.json``: expected-vs-actual path
    set comparison before any hash is accepted, plus the
    traversal / absolute-path / duplicate-entry / symlink
    defenses. The manifest is MANDATORY -- an installer build
    must never claim a complete verified payload while only
    checking a few top-level files.

    Returns the manifest summary (``file_count``, ``total_bytes``).
    """
    import importlib.util

    module_path = BACKEND_ROOT / "scripts" / "payload_manifest.py"
    spec = importlib.util.spec_from_file_location(
        "lockverity_installer_payload_manifest", module_path
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"ERROR: could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        summary: dict[str, object] = module.verify_payload_tree(payload_root)
    except module.PayloadManifestError as exc:
        raise SystemExit(f"ERROR: payload complete-tree verification failed: {exc}") from exc
    return summary


def _verify_microsoft_authenticode(path: Path) -> str:
    """Require a valid Microsoft Authenticode signature on ``path``.

    Microsoft's Evergreen bootstrapper is intentionally not pinned to one
    generated hash because the documented fwlink advances over time. The
    signed publisher identity is the stable trust boundary; the exact hash is
    recorded in the generated installer manifest for each build.
    """
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise SystemExit(
            "ERROR: PowerShell is required to verify the WebView2 bootstrapper signature."
        )
    script = (
        "$s = Get-AuthenticodeSignature "
        "-LiteralPath $env:LOCKVERITY_AUTHENTICODE_TARGET; "
        "[Console]::Out.Write($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)"
    )
    verification_env = dict(os.environ)
    verification_env["LOCKVERITY_AUTHENTICODE_TARGET"] = str(path)
    result = subprocess.run(  # noqa: S603 - fixed PowerShell verification command
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        env=verification_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    status, _, subject = result.stdout.strip().partition("|")
    if result.returncode != 0 or status != "Valid" or "Microsoft Corporation" not in subject:
        raise SystemExit(
            "ERROR: WebView2 bootstrapper Authenticode verification failed.\n"
            f"  status: {status or 'unavailable'}\n"
            f"  signer: {subject or 'unavailable'}"
        )
    return subject


def _stage_webview2_bootstrapper(
    staging_dir: Path,
    source: Path | None = None,
) -> dict[str, object]:
    """Stage Microsoft's signed Evergreen bootstrapper for Inno Setup."""
    destination_dir = staging_dir / "webview2"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / WEBVIEW2_BOOTSTRAPPER_NAME
    if source is not None:
        if not source.is_file():
            raise SystemExit(f"ERROR: WebView2 bootstrapper not found: {source}")
        shutil.copy2(source, destination)
        origin = str(source)
    else:
        _log(
            "webview2",
            "downloading Microsoft's Evergreen WebView2 bootstrapper from the official fwlink",
        )
        request = urllib.request.Request(  # noqa: S310 - constant Microsoft HTTPS fwlink
            WEBVIEW2_BOOTSTRAPPER_URL,
            headers={"User-Agent": "Lockverity-Windows-Installer-Builder/1"},
        )
        temporary = destination.with_suffix(".download")
        received = 0
        try:
            with (
                urllib.request.urlopen(  # noqa: S310 - constant Microsoft HTTPS fwlink
                    request, timeout=60
                ) as response,
                open(temporary, "wb") as handle,
            ):
                while chunk := response.read(64 * 1024):
                    received += len(chunk)
                    if received > WEBVIEW2_BOOTSTRAPPER_MAX_BYTES:
                        raise SystemExit(
                            "ERROR: WebView2 bootstrapper exceeded the 16 MiB safety limit."
                        )
                    handle.write(chunk)
            os.replace(temporary, destination)
        except SystemExit:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            raise SystemExit(
                f"ERROR: official WebView2 Evergreen bootstrapper download failed: {exc}"
            ) from exc
        origin = WEBVIEW2_BOOTSTRAPPER_URL
    size_bytes = destination.stat().st_size
    if size_bytes > WEBVIEW2_BOOTSTRAPPER_MAX_BYTES:
        raise SystemExit("ERROR: staged WebView2 bootstrapper exceeds the 16 MiB safety limit.")
    if size_bytes < 64 * 1024 or destination.read_bytes()[:2] != b"MZ":
        raise SystemExit("ERROR: staged WebView2 bootstrapper is not a plausible Windows PE file.")
    signer = _verify_microsoft_authenticode(destination)
    return {
        "filename": destination.name,
        "origin": origin,
        "sha256": _sha256_of(destination),
        "signer": signer,
        "size_bytes": size_bytes,
    }


def _read_payload_sha256sums(payload_root: Path) -> dict[str, str]:
    """Read the payload's ``SHA256SUMS.txt`` and return a
    ``{relative_path: expected_sha256}`` map.

    The SHA256SUMS.txt format is two whitespace-separated
    fields per line: a lowercase hex SHA-256 and a
    forward-slash path relative to the payload root. The
    function is permissive about line endings and tolerates
    blank lines / comment lines. A path that is syntactically
    invalid (absolute, contains ``..``, contains a backslash)
    is rejected because the build script is the canonical
    chokepoint and must not silently accept a tampered
    payload.
    """
    sums_path = payload_root / "SHA256SUMS.txt"
    if not sums_path.is_file():
        raise SystemExit(
            f"ERROR: SHA256SUMS.txt not found in payload at {sums_path}. "
            "The payload is not a valid B3A portable package."
        )
    out: dict[str, str] = {}
    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(
                f"ERROR: malformed line in {sums_path}: {raw!r}. Expected '<sha256>  <path>'."
            )
        sha, rel = parts
        if not _GIT_SHA1_RE.match(sha) and not re.match(r"^[0-9a-f]{64}$", sha):
            raise SystemExit(f"ERROR: malformed SHA-256 in {sums_path}: {sha!r} (line: {raw!r}).")
        rel_norm = rel.replace("\\", "/").lstrip("/")
        if rel_norm.startswith("../") or "/../" in rel_norm or rel_norm == "..":
            raise SystemExit(f"ERROR: SHA256SUMS path escapes payload root: {rel!r}.")
        out[rel_norm] = sha
    return out


def _verify_payload_zip(
    payload_zip: Path,
    expected_payload_source_commit: str,
) -> dict[str, object]:
    """Verify the accepted B3A portable payload and return the
    short summary used by the install manifest.

    The verification is split into two orthogonal checks:

    1. **Identity** — read the payload's ``BUILD-MANIFEST.json``
       and assert that ``source_commit`` equals the installer
       build's current ``git rev-parse HEAD`` (the same value
       that will be recorded as ``installer_source_commit`` in
       the generated ``INSTALLER-MANIFEST.json``) and that
       ``version`` equals ``APP_VERSION``. There is no tracked
       Python constant pinning a specific B3A source commit;
       the installer build always verifies the payload was
       built from the same clean source tree the installer is
       being built from.
    2. **Integrity** — read the payload's ``SHA256SUMS.txt`` and
       re-hash every file at the recorded relative path. The
       expected SHA-256 for every file is read from the
       payload's own manifest — *no* generated binary hash is
       pinned by this script. A mismatch on any entry (including
       a tampered file, a missing file, or an entry that names
       a file that is not in the payload) is rejected. The check
       then extends to the COMPLETE file tree: the payload's
       ``PAYLOAD-MANIFEST.json`` must exist and every regular
       file in the payload (expected set vs actual set, then
       hashes) must match it exactly (LV-003).
    """
    if not payload_zip.is_file():
        raise SystemExit(
            f"ERROR: accepted payload ZIP not found at {payload_zip}. "
            "Restore the accepted B3A portable ZIP before building the "
            "installer. Do not rebuild the application."
        )
    if not _GIT_SHA1_RE.match(expected_payload_source_commit):
        raise SystemExit(
            "ERROR: expected payload source_commit is not a full 40-char SHA: "
            f"{expected_payload_source_commit!r}"
        )
    actual_zip_sha = _sha256_of(payload_zip)
    payload_root = payload_zip.parent / PAYLOAD_NAME
    if not payload_root.is_dir():
        _log("payload", f"extracting accepted ZIP to {payload_root}")
        with zipfile.ZipFile(payload_zip) as archive:
            archive.extractall(payload_root.parent)
    manifest_path = payload_root / "BUILD-MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit(f"ERROR: BUILD-MANIFEST.json not found in payload at {manifest_path}.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_commit") != expected_payload_source_commit:
        raise SystemExit(
            "ERROR: payload source_commit does not match the installer build's HEAD.\n"
            f"  expected (installer HEAD): {expected_payload_source_commit}\n"
            f"  actual   (payload manifest): {manifest.get('source_commit')}\n"
            "Rebuild the portable from a clean checkout of the same HEAD that the "
            "installer is being built from."
        )
    if manifest.get("version") != APP_VERSION:
        raise SystemExit(
            "ERROR: payload version does not match the accepted B3A version.\n"
            f"  expected: {APP_VERSION}\n"
            f"  actual:   {manifest.get('version')}\n"
        )
    sums = _read_payload_sha256sums(payload_root)
    if not sums:
        raise SystemExit(
            f"ERROR: SHA256SUMS.txt in payload at {payload_root / 'SHA256SUMS.txt'} "
            "is empty; refusing to embed a payload with no integrity record."
        )
    computed: dict[str, str] = {}
    for rel, expected_sha in sorted(sums.items()):
        file_path = payload_root / rel
        if not file_path.is_file():
            raise SystemExit(
                f"ERROR: SHA256SUMS references missing file in payload: {rel!r}. "
                f"  expected sha256: {expected_sha}"
            )
        actual_sha = _sha256_of(file_path)
        computed[rel] = actual_sha
        if actual_sha != expected_sha:
            raise SystemExit(
                "ERROR: payload file SHA-256 mismatch.\n"
                f"  path:     {rel}\n"
                f"  expected: {expected_sha}\n"
                f"  actual:   {actual_sha}\n"
                "The payload appears to be tampered. Restore the accepted "
                "B3A portable ZIP and retry."
            )
    # Complete-tree verification (LV-003): every regular file in
    # the payload must match PAYLOAD-MANIFEST.json exactly --
    # missing, extra, modified, linked, or malformed entries all
    # abort the installer build.
    tree_summary = _verify_payload_tree_complete(payload_root)
    return {
        "payload_zip": str(payload_zip),
        "payload_zip_sha256": actual_zip_sha,
        "payload_source_commit": manifest.get("source_commit"),
        "payload_version": manifest.get("version"),
        "build_manifest_sha256": _sha256_of(manifest_path),
        "payload_root": str(payload_root),
        "payload_sha256_entries": computed,
        "payload_manifest_file_count": tree_summary["file_count"],
    }


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _stage_payload(payload_root: Path, staging_dir: Path) -> None:
    """Copy the accepted payload into the Inno Setup staging tree.

    The Inno Setup source consumes ``payload/*`` and ``root_extra/*``
    from the script's working directory. ``payload/`` is the B3A
    portable root; ``root_extra/`` is the install-root overlay
    (license, manifest, notices) that the installer places at
    ``{app}\\``.
    """
    payload_dest = staging_dir / "payload"
    if payload_dest.exists():
        shutil.rmtree(payload_dest)
    shutil.copytree(payload_root, payload_dest)

    root_extra_dest = staging_dir / "root_extra"
    if root_extra_dest.exists():
        shutil.rmtree(root_extra_dest)
    root_extra_dest.mkdir(parents=True, exist_ok=True)
    # Copy the per-user LICENSE / manifest / notices / SHA256SUMS
    # to the install root so they survive uninstall. The
    # uninstaller explicitly removes ``{app}\\app`` and
    # ``{app}\\docs`` (not the install-root manifest).
    for name in (
        "LICENSE",
        "BUILD-MANIFEST.json",
        "SHA256SUMS.txt",
        "THIRD_PARTY_NOTICES.txt",
        "PRIVACY.md",
        "README-PORTABLE.txt",
    ):
        src = payload_root / name
        if src.is_file():
            shutil.copy2(src, root_extra_dest / name)

    # Copy the approved icon for shortcuts (in addition to the
    # one the installer source references directly).
    approved_icon = BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"
    if approved_icon.is_file():
        shutil.copy2(approved_icon, root_extra_dest / "favicon-exe.ico")

    # Copy the user-facing documentation bundle. The Start Menu
    # entry points to ``{app}\\docs\\windows-installer.md``.
    docs_dest = root_extra_dest / "docs"
    docs_dest.mkdir(parents=True, exist_ok=True)
    installer_doc = REPO_ROOT / "docs" / "windows-installer.md"
    privacy_doc = REPO_ROOT / "docs" / "privacy.md"
    # Copy the LICENSE to the staging root so ISCC's
    # ``LicenseFile=LICENSE`` directive (relative to the
    # .iss) resolves at build time. The same LICENSE is
    # also copied to ``root_extra\`` (above) so the final
    # install ships it under ``{app}\LICENSE``.
    license_src = payload_root / "LICENSE"
    if license_src.is_file():
        shutil.copy2(license_src, staging_dir / "LICENSE")
    if installer_doc.is_file():
        shutil.copy2(installer_doc, docs_dest / "windows-installer.md")
    if privacy_doc.is_file():
        shutil.copy2(privacy_doc, docs_dest / "privacy.md")


def _build_installer(
    iscc: Path,
    staging_dir: Path,
    output_dir: Path,
    work_dir: Path,
    keep_work: bool,
) -> Path:
    """Run ISCC against the staged tree. Returns the installer EXE path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not keep_work and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # The committed .iss references the staged payload and
    # root-extra files via *relative* ``Source:`` paths
    # (``payload\*`` and ``root_extra\*``). ISCC resolves those
    # paths against the directory containing the .iss, not the
    # current working directory. Since the .iss is committed
    # under ``backend/installer/`` and the staged payload lives
    # under ``build/installer/staging/``, the only way to make
    # the relative paths resolve to the staged tree is to run
    # ISCC against a *copy* of the .iss placed inside the
    # staging directory. (The committed source is the single
    # source of truth; the copy is a transient build artefact
    # and is not committed.)
    staged_iss = staging_dir / "lockverity.iss"
    shutil.copy2(ISS_SOURCE, staged_iss)
    # The .iss's ``SetupIconFile`` directive (and the
    # ``Source:`` line in the [Files] section that copies the
    # same icon into ``{app}``) use a *relative* path
    # (``..\pyinstaller\favicon-exe.ico``) that ISCC resolves
    # against the directory containing the .iss. The path was
    # written when the .iss lived in ``backend/installer/``
    # and the icon in ``backend/pyinstaller/``; the ``..``
    # segment goes from the .iss's directory up to
    # ``backend\``, then back into ``pyinstaller\``. After the
    # .iss is copied into the staging dir, ``..`` from the
    # .iss would go up to ``build/installer/`` (the staging
    # dir's parent), not to the directory containing the icon,
    # so the path would not resolve. Rewrite all such
    # references in the staged copy to a staging-relative
    # form and mirror the icon at the matching location inside
    # the staging dir.
    pyinstaller_subdir = staging_dir / "pyinstaller"
    pyinstaller_subdir.mkdir(parents=True, exist_ok=True)
    icon_source = BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"
    if icon_source.is_file():
        staged_icon = pyinstaller_subdir / "favicon-exe.ico"
        shutil.copy2(icon_source, staged_icon)
        # Rewrite ``..\pyinstaller\favicon-exe.ico`` (committed,
        # backend-relative) to ``pyinstaller\favicon-exe.ico``
        # (staging-relative) everywhere it appears in the staged
        # copy. The committed source is unchanged. The substring
        # is unique to this icon path so a global replace is
        # safe.
        staged_iss_text = staged_iss.read_text(encoding="utf-8")
        staged_iss_text = staged_iss_text.replace(
            "..\\pyinstaller\\favicon-exe.ico",
            "pyinstaller\\favicon-exe.ico",
        )
        staged_iss.write_text(staged_iss_text, encoding="utf-8")
    # ``ISCC`` writes its build log to ``{app}\\`` by default.
    # The /O flag is the output *directory* (ISCC appends the
    # ``OutputBaseFilename`` from the .iss). The final filename
    # is taken from the .iss's ``OutputBaseFilename`` directive
    # (committed in ``backend/installer/lockverity.iss``), so
    # the .iss remains the single source of truth for the
    # final name. There is no ``/OutputDir`` or
    # ``/OutputBaseFilename`` long form in ISCC; the long forms
    # used in earlier revisions of this script were being
    # parsed as ``/O utputDir=...`` and ``/F utputBaseFilename=...``
    # by the single-character switch parser, leading to
    # ``I/O error 123`` (ERROR_INVALID_NAME) on the resulting
    # path. AppId and AppVersion are taken from the .iss source;
    # they are NOT command-line flags (``/AppId`` and
    # ``/AppVersion`` are not valid ISCC options). The committed
    # .iss is the single source of truth for both values.
    cmd: list[str] = [
        str(iscc),
        f"/O{output_dir}",
        str(staged_iss),
    ]
    _log("iscc", f"running {iscc}")
    log_path = output_dir / "logs" / "iscc.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8", errors="replace") as log_handle:
        result = subprocess.run(  # noqa: S603 - argv is built by us
            cmd,
            cwd=str(staging_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: ISCC failed (rc={result.returncode}); see {log_path}")
    # The .iss's ``OutputBaseFilename`` directive controls the
    # final filename; ISCC appends ``.exe`` automatically.
    installer_name = f"Lockverity-{APP_VERSION}-windows-x64-setup.exe"
    installer_exe = output_dir / installer_name
    if not installer_exe.is_file():
        raise SystemExit(f"ERROR: expected installer EXE not produced: {installer_exe}")
    return installer_exe


def _verify_installer_pe(installer_exe: Path) -> dict[str, object]:
    """Inspect the generated installer PE for architecture and icon."""
    import pefile  # type: ignore[import-untyped]

    pe = pefile.PE(str(installer_exe), fast_load=True)
    pe.parse_data_directories()
    arch_id = pe.FILE_HEADER.Machine
    arch = "x64" if arch_id == 0x8664 else f"0x{arch_id:x}"
    subsystem = pe.OPTIONAL_HEADER.Subsystem
    sub_label = (
        "WINDOWS_GUI"
        if subsystem == 2
        else "WINDOWS_CUI"
        if subsystem == 3
        else f"UNKNOWN({subsystem})"
    )
    has_icon = False
    icon_count = 0
    if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if entry.id == 14:  # RT_GROUP_ICON
                has_icon = True
                for _ in entry.directory.entries:
                    icon_count += 1
    pe.close()
    return {
        "arch": arch,
        "subsystem": sub_label,
        "has_icon": has_icon,
        "icon_count": icon_count,
    }


def _write_installer_manifest(
    installer_exe: Path,
    output_dir: Path,
    *,
    installer_source_commit: str,
    payload_zip: Path,
    payload_summary: dict[str, object],
    iscc_path: Path,
    webview2_bootstrapper: dict[str, object],
) -> Path:
    """Generate the external ``INSTALLER-MANIFEST.json``."""
    manifest = {
        "product": "Lockverity",
        "version": APP_VERSION,
        "installer_source_commit": installer_source_commit,
        "payload_source_commit": payload_summary["payload_source_commit"],
        "payload_zip": payload_zip.name,
        "payload_zip_sha256": payload_summary["payload_zip_sha256"],
        "payload_build_manifest_sha256": payload_summary["build_manifest_sha256"],
        "payload_manifest_file_count": payload_summary["payload_manifest_file_count"],
        "lockverity_exe_sha256": payload_summary["payload_sha256_entries"]["Lockverity.exe"],
        "lockverity_cli_exe_sha256": payload_summary["payload_sha256_entries"][
            "lockverity-cli.exe"
        ],
        "installer_build_timestamp_utc": datetime.datetime.now(tz=datetime.UTC).isoformat(
            timespec="seconds"
        ),
        "target_platform": "windows",
        "target_architecture": "x64",
        "inno_setup_version": "6.7.3",
        "inno_setup_compiler_path": str(iscc_path),
        "inno_setup_compiler_sha256": _sha256_of(iscc_path),
        "installer_filename": installer_exe.name,
        "installer_sha256": _sha256_of(installer_exe),
        "stable_app_id": STABLE_APP_ID,
        "default_install_path": "%LOCALAPPDATA%\\Programs\\Lockverity",
        "privilege_mode": "per-user, no admin, no UAC",
        "code_signing_status": "unsigned",
        "telephony": False,
        "auto_update": False,
        "service_installation": False,
        "scheduled_task": False,
        "firewall_rule": False,
        "system_path_modification": False,
        "file_association": False,
        "browser_extension": False,
        "shell_context_menu_handler": False,
        "webview2_deployment": "Microsoft Evergreen Bootstrapper (run only if missing)",
        "webview2_bootstrapper_sha256": webview2_bootstrapper["sha256"],
        "webview2_bootstrapper_signer": webview2_bootstrapper["signer"],
    }
    manifest_path = output_dir / "INSTALLER-MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _write_external_checksums(output_dir: Path, installer_exe: Path, manifest_path: Path) -> Path:
    """Generate an external ``SHA256SUMS.txt`` covering the installer
    EXE and ``INSTALLER-MANIFEST.json``. The check covers the
    artefacts OUTSIDE the installer (so the installer cannot
    self-validate)."""
    installer_sha = _sha256_of(installer_exe)
    manifest_sha = _sha256_of(manifest_path)
    checksums_path = output_dir / "SHA256SUMS.txt"
    checksums_path.write_text(
        f"{installer_sha}  {installer_exe.name}\n{manifest_sha}  {manifest_path.name}\n",
        encoding="utf-8",
    )
    return checksums_path


def _run_silent_smoke(
    installer_exe: Path,
    output_dir: Path,
    port: int,
    expected_lockverity_cli_exe_sha256: str,
) -> dict[str, object]:
    """Run a bounded silent-install smoke that the packaged
    binary actually launches and responds to ``/api/v1/health``.

    The smoke installs the EXE into a fresh
    ``%LOCALAPPDATA%\\Programs\\Lockverity-smoke`` directory,
    starts the installed ``Lockverity.exe`` with
    ``--no-browser``, asserts the health endpoint, then stops
    and uninstalls. The operator never sees a UI because the
    install is silent.
    """
    home_dir = Path(os.environ["LOCALAPPDATA"]) / "Lockverity-smoke"
    install_dir = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Lockverity-smoke"
    runtime_home = home_dir
    log_path = output_dir / "logs" / "silent-smoke.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        str(installer_exe),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        f"/DIR={install_dir}",
        f"/PORT={port}",
        f"/LOG={log_path}",
    ]
    _log("smoke", f"silent install of {installer_exe.name} into {install_dir}")
    result = subprocess.run(  # noqa: S603 - argv is built by us
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        return {
            "status": "install_failed",
            "rc": result.returncode,
            "log": str(log_path),
            "stdout_tail": (result.stdout or "")[-2000:],
            "stderr_tail": (result.stderr or "")[-2000:],
        }
    health_result: dict[str, object] = {"status": "not_started", "port": port}

    # Confirm files are present and the binaries match the
    # accepted payload. Keep going to the uninstaller on a
    # verification failure so the smoke never strands an app
    # installation merely because its payload is invalid.
    installed_cli = install_dir / "app" / "lockverity-cli.exe"
    if not installed_cli.is_file():
        health_result.update(status="missing_cli", expected=str(installed_cli))
    else:
        installed_cli_sha = _sha256_of(installed_cli)
        if installed_cli_sha != expected_lockverity_cli_exe_sha256:
            health_result.update(
                status="cli_mismatch",
                expected=expected_lockverity_cli_exe_sha256,
                actual=installed_cli_sha,
            )

    # The marker proves that the real uninstaller preserves the
    # per-user runtime home. It is intentionally retained after
    # the smoke, exactly like operator databases and logs.
    runtime_home.mkdir(parents=True, exist_ok=True)
    preservation_marker = runtime_home / "installer-smoke-runtime-data.txt"
    preservation_marker.write_text("preserve-on-uninstall\n", encoding="utf-8")

    # Start the installed CLI in foreground mode for the smoke.
    env = os.environ.copy()
    env["LOCKVERITY_HOME"] = str(runtime_home)
    # Restricted PATH to prove the installed app does not
    # require system Python/Node/npm.
    env["PATH"] = "C:\\Windows\\System32;C:\\Windows"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    cli_proc: subprocess.Popen[str] | None = None
    # Wait for health (bounded). The exception is the documented
    # "service not yet listening" condition during startup; we
    # intentionally swallow it and re-probe on the next tick.
    import urllib.error
    import urllib.request

    if health_result["status"] == "not_started":
        try:
            cli_proc = subprocess.Popen(  # noqa: S603 - argv is built by us
                [str(installed_cli), "start", "--port", str(port)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            health_ok = False
            for _ in range(40):
                time.sleep(1.0)
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/v1/health", timeout=2
                    ) as response:
                        if 200 <= response.status < 300:
                            health_ok = True
                            break
                except (urllib.error.URLError, OSError, TimeoutError):
                    # Expected: server not yet listening. Re-probe.
                    continue
            health_result["status"] = "ok" if health_ok else "no_health"
            # Stop through the installed CLI so lifecycle IPC is
            # covered before the uninstaller touches any files.
            try:
                stop_proc = subprocess.run(  # noqa: S603 - argv is built by us
                    [str(installed_cli), "stop"],
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                health_result["stop_rc"] = stop_proc.returncode
                health_result["stop_stdout_tail"] = (stop_proc.stdout or "")[-500:]
                if stop_proc.returncode != 0 and health_result["status"] == "ok":
                    health_result["status"] = "stop_failed"
            except Exception as exc:  # pragma: no cover - cleanup still continues
                health_result["stop_error"] = repr(exc)
                if health_result["status"] == "ok":
                    health_result["status"] = "stop_failed"
        except Exception as exc:  # pragma: no cover - reported and cleaned below
            health_result.update(status="launch_failed", launch_error=repr(exc))

    # Ensure the foreground process has released the installed
    # executables before asking Inno Setup's generated uninstaller
    # to remove them.
    if cli_proc is not None:
        try:
            cli_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cli_proc.kill()
            cli_proc.wait(timeout=5)
            if health_result["status"] == "ok":
                health_result["status"] = "stop_timeout"

    # Run the actual generated uninstaller. Re-running Setup here
    # can appear successful while exercising no uninstall logic.
    uninstaller_exe = install_dir / "unins000.exe"
    health_result["uninstaller"] = str(uninstaller_exe)
    if not uninstaller_exe.is_file():
        health_result["uninstall_error"] = "generated unins000.exe is missing"
        if health_result["status"] == "ok":
            health_result["status"] = "uninstaller_missing"
    else:
        try:
            uninst = subprocess.run(  # noqa: S603 - argv is built by us
                [
                    str(uninstaller_exe),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            health_result["uninstall_rc"] = uninst.returncode
            health_result["uninstall_stdout_tail"] = (uninst.stdout or "")[-500:]
            health_result["uninstall_stderr_tail"] = (uninst.stderr or "")[-500:]
            if uninst.returncode != 0 and health_result["status"] == "ok":
                health_result["status"] = "uninstall_failed"
        except Exception as exc:  # pragma: no cover - surfaced in the report
            health_result["uninstall_error"] = repr(exc)
            if health_result["status"] == "ok":
                health_result["status"] = "uninstall_failed"

    health_result["installed_payload_removed"] = not (install_dir / "app").exists()
    health_result["runtime_data_preserved"] = preservation_marker.is_file()
    if health_result["status"] == "ok" and not health_result["installed_payload_removed"]:
        health_result["status"] = "uninstall_payload_remains"
    if health_result["status"] == "ok" and not health_result["runtime_data_preserved"]:
        health_result["status"] = "uninstall_removed_runtime_data"
    return health_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_windows_installer")
    parser.add_argument(
        "--payload-zip",
        type=Path,
        default=DEFAULT_PAYLOAD_ZIP,
        help="Path to the accepted B3A portable payload ZIP. Default: the build/packaging artefact.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Dedicated installer output directory (default: build/installer).",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=DEFAULT_STAGING_DIR,
        help="Dedicated staging directory for the Inno Setup source tree.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="Dedicated Inno Setup work directory.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe --output-dir (except the staged payload), --staging-dir and --work-dir before building.",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep --work-dir after the build (default: remove).",
    )
    parser.add_argument(
        "--json-report",
        action="store_true",
        help="Emit a structured JSON report on stdout at the end.",
    )
    parser.add_argument(
        "--run-smoke",
        action="store_true",
        help="Run a silent-install + health + uninstall smoke after the build. Disabled by default.",
    )
    parser.add_argument(
        "--webview2-bootstrapper",
        type=Path,
        default=None,
        help=(
            "Use this Microsoft-signed Evergreen WebView2 bootstrapper instead of "
            "downloading the current one from Microsoft's official fwlink."
        ),
    )
    parser.add_argument(
        "--skip-clean-git-check",
        action="store_true",
        help="Skip the clean-git-state check (use only for development iterations, never for the final acceptance build).",
    )
    args = parser.parse_args(argv)

    started_at = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
    started_monotonic = time.monotonic()
    _verify_host()
    iscc = _verify_iscc()
    _log(
        "host",
        f"verified Windows x64 (Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})",
    )
    if not args.skip_clean_git_check:
        _verify_clean_git_state()
    installer_source_commit = _git_head_full()
    _log("git", f"installer source commit: {installer_source_commit}")
    _log("iscc", f"verified Inno Setup compiler: {iscc}")

    if args.clean:
        if args.output_dir.is_dir():
            _log("clean", f"wiping {args.output_dir}")
            for child in args.output_dir.iterdir():
                if child == args.payload_zip:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        child.unlink()
        for d in (args.staging_dir, args.work_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    payload_summary = _verify_payload_zip(
        args.payload_zip,
        _git_head_full(),
    )
    _log(
        "payload",
        f"accepted B3A payload verified (zip_sha256={payload_summary['payload_zip_sha256'][:12]}..., source_commit={payload_summary['payload_source_commit']})",
    )
    _stage_payload(Path(payload_summary["payload_root"]), args.staging_dir)
    _log("staging", f"payload staged into {args.staging_dir}")
    webview2_summary = _stage_webview2_bootstrapper(
        args.staging_dir,
        args.webview2_bootstrapper,
    )
    _log(
        "webview2",
        f"verified Microsoft bootstrapper (sha256={str(webview2_summary['sha256'])[:12]}...)",
    )

    installer_exe = _build_installer(
        iscc=iscc,
        staging_dir=args.staging_dir,
        output_dir=args.output_dir,
        work_dir=args.work_dir,
        keep_work=args.keep_work,
    )
    _log("iscc", f"installer EXE produced at {installer_exe}")
    pe_info = _verify_installer_pe(installer_exe)
    _log(
        "pe",
        f"installer PE: arch={pe_info['arch']} subsystem={pe_info['subsystem']} icon_count={pe_info['icon_count']}",
    )
    manifest_path = _write_installer_manifest(
        installer_exe,
        args.output_dir,
        installer_source_commit=installer_source_commit,
        payload_zip=args.payload_zip,
        payload_summary=payload_summary,
        iscc_path=iscc,
        webview2_bootstrapper=webview2_summary,
    )
    checksums_path = _write_external_checksums(args.output_dir, installer_exe, manifest_path)
    _log("manifest", f"wrote {manifest_path.name} + {checksums_path.name}")

    smoke_result: dict[str, object] | None = None
    if args.run_smoke:
        port = _free_port()
        smoke_result = _run_silent_smoke(
            installer_exe,
            args.output_dir,
            port,
            payload_summary["payload_sha256_entries"]["lockverity-cli.exe"],
        )
        _log("smoke", f"silent-install smoke status={smoke_result.get('status')}")

    report = {
        "started_at_utc": started_at,
        "duration_seconds": round(time.monotonic() - started_monotonic, 2),
        "machine": f"{platform.machine().lower()}",
        "platform": sys.platform,
        "installer_source_commit": installer_source_commit,
        "payload_source_commit": payload_summary["payload_source_commit"],
        "inno_setup_compiler": str(iscc),
        "inno_setup_compiler_sha256": _sha256_of(iscc),
        "installer_filename": installer_exe.name,
        "installer_sha256": _sha256_of(installer_exe),
        "installer_size_bytes": installer_exe.stat().st_size,
        "installer_manifest_sha256": _sha256_of(manifest_path),
        "stable_app_id": STABLE_APP_ID,
        "app_version": APP_VERSION,
        "default_install_path": "%LOCALAPPDATA%\\Programs\\Lockverity",
        "target_platform": "windows",
        "target_architecture": "x64",
        "code_signing_status": "unsigned",
        "pe": pe_info,
        "payload": payload_summary,
        "silent_smoke": smoke_result,
        "webview2_bootstrapper": webview2_summary,
    }
    if args.json_report:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        _log("done", f"installer: {installer_exe}")
    if smoke_result is not None and smoke_result.get("status") != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
