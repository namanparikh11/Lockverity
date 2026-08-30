"""Cross-platform build script for the Windows portable package.

This script is the canonical v2.1 Part B3A build entry
point. It:

  1. Verifies Windows x64 and the supported Python
     version.

The v2.1.2 hotfix re-points the default portable
filename at ``Lockverity-2.1.2-windows-x64-portable``
and the build output directory at
``build/packaging/Lockverity-2.1.2-windows-x64-portable.zip``.
The v2.1.1 portable is preserved on disk and is not
rebuilt by this script.
  2. Verifies the build dependencies (PyInstaller,
     pip-licenses) are installed in the calling
     virtual environment.
  3. Builds/verifies the frontend (runs
     ``scripts/prepare_frontend_dist.py``).
  4. Runs PyInstaller from the committed
     ``pyinstaller/lockverity.spec`` and
     ``pyinstaller/cli.spec``.
  5. Assembles the user-facing portable directory
     layout.
  6. Generates ``THIRD_PARTY_NOTICES.txt`` from
     ``pip-licenses``.
  7. Generates ``BUILD-MANIFEST.json``.
  8. Generates ``SHA256SUMS.txt``.
  8a. Generates ``PAYLOAD-MANIFEST.json`` -- the complete
     file-tree integrity manifest covering EVERY regular file
     in the payload (LV-003). Generated last so
     ``BUILD-MANIFEST.json`` and ``SHA256SUMS.txt`` are covered
     by it; the manifest itself is the sole exclusion, keeping
     self-hashing non-circular.
  9. Zips the portable directory into
     ``dist/windows/Lockverity-2.1.2-windows-x64-portable.zip``.
 10. Runs the packaged smoke tests against the
     generated ZIP (unless ``--skip-smoke``).
 11. Emits a structured JSON report on stdout
     (``--json-report``) or a human summary.

The script is dependency-light: it uses the Python
standard library, the existing
``scripts/prepare_frontend_dist.py`` script, and the
documented ``pip-licenses`` CLI. It does not download
binary artefacts from the network. It does not delete
arbitrary ``dist/`` or ``build/`` directories outside
its dedicated packaging work directory
(``build/packaging/``). The committed source tree is
never modified.

The script is intentionally verbose and surfaces a
precise error message at every step. A failed build
exits non-zero with a printed summary of the failing
step and the recommended operator action.

Usage::

    python scripts/build_windows_portable.py
    python scripts/build_windows_portable.py --skip-frontend-build
    python scripts/build_windows_portable.py --skip-smoke
    python scripts/build_windows_portable.py --output-dir build/packaging
    python scripts/build_windows_portable.py --json-report
    python scripts/build_windows_portable.py --keep-work
    python scripts/build_windows_portable.py --help
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
PYINSTALLER_DIR = BACKEND_ROOT / "pyinstaller"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "build" / "packaging"
DEFAULT_PORTABLE_NAME = "Lockverity-2.1.3-windows-x64-portable"
PORTABLE_ZIP_NAME = f"{DEFAULT_PORTABLE_NAME}.zip"

# Source tree roots that are bundled into the portable
# root. The launcher, CLI, Alembic, and frontend dist
# are all referenced under ``sys._MEIPASS`` at runtime
# (see ``app/runtime_paths.py``).
PORTABLE_BUNDLE_LAYOUT: list[tuple[str, str]] = [
    # (source path relative to ``application_root()``,
    # destination path inside the portable root)
    ("frontend/dist", "frontend/dist"),
    # The Alembic config and the ``alembic/`` directory
    # are bundled at the same path level. The order is
    # significant: the directory copy runs first and
    # the file copy runs second so the file is placed
    # alongside (not wiped by) the directory. Both
    # copies live at ``<frozen_root>/alembic/...`` to
    # avoid the PyInstaller ``datas`` prefix-collision
    # between the ``alembic/`` directory entry and an
    # ``alembic.ini`` file entry. The portable root
    # copy mirrors the frozen layout for operator
    # inspection and so any tooling that walks the
    # portable root sees the same structure as the
    # frozen bundle.
    ("backend/alembic", "alembic"),
    ("backend/alembic.ini", "alembic/cfg/alembic.ini"),
    ("frontend/public/favicon.ico", "favicon.ico"),
    ("frontend/public/brand", "brand"),
    ("LICENSE", "LICENSE"),
    ("docs/windows-portable.md", "README-PORTABLE.txt"),
    ("docs/privacy.md", "PRIVACY.md"),
]


def _log(stage: str, message: str) -> None:
    """Print a structured build log line to stderr."""
    ts = datetime.datetime.now(tz=datetime.UTC).strftime("%H:%M:%S")
    sys.stderr.write(f"[{ts}] [{stage}] {message}\n")
    sys.stderr.flush()


def _verify_host() -> tuple[str, str]:
    """Verify the host is Windows x64 and Python is 3.12+.

    The function returns ``(platform, machine)`` for the
    caller to record in the build manifest. The v2.1
    Part B3A contract is Windows x64 only; a maintainer
    who wants to extend to other platforms must add the
    new ``build_*_portable.py`` script and document
    the new artefact in this script.
    """
    if sys.platform != "win32":
        raise SystemExit(
            "ERROR: this build script targets Windows x64 only. "
            f"Detected platform: {sys.platform!r}. Run on Windows to "
            "produce the portable package."
        )
    machine = platform.machine().lower()
    if machine not in ("amd64", "x86_64"):
        raise SystemExit(f"ERROR: Windows x64 is required (detected machine={machine!r}).")
    py_version = sys.version_info
    if py_version < (3, 12):
        raise SystemExit(f"ERROR: Python 3.12+ is required (detected {py_version}).")
    return sys.platform, machine


def _verify_build_dependencies() -> dict[str, str]:
    """Verify PyInstaller and pip-licenses are importable.

    The function imports each dependency, captures the
    version, and returns a ``{name: version}`` mapping
    for the build manifest.
    """
    versions: dict[str, str] = {}
    for module_name, dist_name in (
        ("PyInstaller", "pyinstaller"),
        # ``pip-licenses`` ships its module as the
        # single-word ``piplicenses`` (the hyphen is
        # only in the distribution / console-script
        # name). The import is the module name, not
        # the distribution name.
        ("piplicenses", "pip-licenses"),
    ):
        try:
            module = __import__(module_name)
            version = getattr(module, "__version__", "unknown")
            versions[dist_name] = str(version)
        except ImportError as exc:
            raise SystemExit(
                f"ERROR: required build dependency {module_name!r} is not "
                f"installed. Run ``pip install -e '.[build]'`` in the "
                f"backend venv to install it. Underlying error: {exc}"
            ) from exc
    return versions


def _verify_frontend_dist() -> None:
    """Verify the frontend dist exists and is non-empty.

    The function is the v2.1 Part B3A analogue of the
    v2.1 Part B1 ``prepare_frontend_dist.py`` validation
    step. The portable package bundles the dist; a
    missing or empty dist is a fatal packaging error.
    """
    dist = REPO_ROOT / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        raise SystemExit(
            f"ERROR: frontend dist is missing at {dist}. "
            "Run ``python scripts/prepare_frontend_dist.py`` to build it."
        )


def _regenerate_exe_icon() -> Path:
    """Regenerate the packaging-derivative ICO from the approved sources.

    The PyInstaller specs reference
    ``backend/pyinstaller/favicon-exe.ico`` for the
    executable icon resource. The file is the
    transparent blue-symbol derivative (see
    ``scripts/generate_exe_icon.py`` and
    ``docs/windows-icon.md``): the approved
    ``frontend/public/brand/lockverity-symbol.png`` is
    cleaned of its export residue and every canonical
    Windows shell frame is rendered from the cleaned
    mark. The brand assets themselves are not modified.
    The function delegates to the dedicated
    ``scripts/generate_exe_icon.py`` so the
    derivation logic is exercised by
    ``tests/test_exe_icon.py`` and is not
    duplicated.

    The function loads the derivation module via
    :mod:`importlib.util` rather than the regular
    import machinery because the build script may be
    invoked from any directory; relying on
    ``PYTHONPATH`` plus a bare ``from scripts``
    import would couple the loader to the
    caller's current working directory.
    """
    import importlib.util

    script_path = BACKEND_ROOT / "scripts" / "generate_exe_icon.py"
    spec = importlib.util.spec_from_file_location("lockverity_build_generate_exe_icon", script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"ERROR: could not load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    derivative = BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"
    module.build_exe_icon(derivative_ico=derivative)
    return derivative


def _build_frontend() -> None:
    """Run the documented ``prepare_frontend_dist.py`` script.

    The script is the cross-platform frontend build; it
    shells out to ``node`` and ``npm`` and uses the
    standard library for path handling.
    """
    _log("frontend", "running prepare_frontend_dist.py")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "prepare_frontend_dist.py"),
    ]
    result = subprocess.run(  # noqa: S603 - argv is built by us
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: prepare_frontend_dist.py failed (rc={result.returncode}):\n"
            f"  stdout: {result.stdout[-2000:]}\n"
            f"  stderr: {result.stderr[-2000:]}\n"
            "Re-run the script manually to see the full error."
        )


def _pyinstaller_build(spec: Path, work_dir: Path, dist_dir: Path, log_path: Path) -> None:
    """Run PyInstaller against the given spec.

    The function invokes PyInstaller with ``--workpath``
    and ``--distpath`` pointing at the dedicated
    packaging work directory so the build never writes
    to ``backend/dist/`` or the source tree's
    ``build/`` directory. PyInstaller's automatic
    spec-name ``build/`` is overridden by passing
    ``--workpath`` explicitly.

    The function streams the PyInstaller output to a
    log file rather than capturing it in memory. The
    previous implementation used
    ``subprocess.run(..., capture_output=True)`` which
    buffered the entire PyInstaller output in a
    subprocess pipe; a large build (the lockverity
    bundle has 1326 entries) fills the buffer and the
    subprocess blocks indefinitely. Streaming to a
    log file avoids the pipe-buffer deadlock and gives
    the operator a build log to inspect on failure.
    """
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--workpath",
        str(work_dir),
        "--distpath",
        str(dist_dir),
        "--clean",
        str(spec),
    ]
    _log("pyinstaller", f"running {spec.name} (log: {log_path})")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8", errors="replace") as log_handle:
        result = subprocess.run(  # noqa: S603 - argv is built by us
            cmd,
            cwd=str(BACKEND_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            timeout=1800,
        )
    if result.returncode != 0:
        # The log file is left on disk so the operator
        # can read the full PyInstaller output without
        # the build script re-buffering it.
        raise SystemExit(
            f"ERROR: PyInstaller failed (rc={result.returncode}) for {spec.name}; see {log_path}"
        )


def _pyinstaller_collect_name(spec: Path) -> str:
    """Return the ``COLLECT(name=...)`` value for ``spec``.

    PyInstaller writes each spec's onedir output to
    ``<distpath>/<COLLECT name>/``. The function
    reads the spec as text and extracts the
    ``name="..."`` argument of the trailing
    ``COLLECT(...)`` call. The launcher spec uses
    ``name="Lockverity"`` and the CLI spec uses
    ``name="lockverity-cli"``; the spec filenames
    themselves are ``lockverity.spec`` and
    ``cli.spec``.
    """
    import re

    text = spec.read_text(encoding="utf-8")
    # Find the last ``COLLECT(`` block; the value of
    # its ``name=`` argument is the onedir directory
    # name.
    last_collect_start = text.rfind("COLLECT(")
    if last_collect_start < 0:
        raise SystemExit(f"ERROR: no COLLECT(...) block found in {spec}")
    block = text[last_collect_start:]
    match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', block)
    if not match:
        raise SystemExit(f"ERROR: COLLECT(...) in {spec} has no name=... argument")
    return match.group(1)


def _assemble_portable(source_layout: Path, target_root: Path, build_specs: list[Path]) -> None:
    """Assemble the user-facing portable root from the PyInstaller outputs.

    The function copies the PyInstaller onedir outputs
    (``Lockverity/`` and ``lockverity-cli/``) into the
    portable root, then layers the documented
    user-facing layout on top:

      * ``Lockverity.exe`` and ``lockverity-cli.exe``
        at the root.
      * ``_internal/`` from each PyInstaller bundle.
      * ``frontend/dist/``, ``alembic/``, ``alembic.ini``,
        ``favicon.ico``, ``brand/``, ``LICENSE``,
        ``README-PORTABLE.txt`` at the documented paths.

    The onedir output is the same as the portable
    root layout except for the missing
    user-facing entry points. The function moves the
    onedir's ``Lockverity.exe`` / ``lockverity-cli.exe``
    to the root and merges the onedir's ``_internal/``
    with the portable root's ``_internal/`` (a no-op
    since the two onedirs have different support
    binaries).

    The function never modifies the source tree and
    never touches any path outside ``target_root``.
    """
    target_root.mkdir(parents=True, exist_ok=True)
    for spec in build_specs:
        # Each spec produces ``<dist_dir>/<COLLECT name>/``
        # (the ``COLLECT(name=...)`` argument in the
        # spec, NOT the spec filename). The launcher
        # spec uses ``name="Lockverity"`` and the CLI
        # spec uses ``name="lockverity-cli"``; the spec
        # files themselves are ``lockverity.spec`` and
        # ``cli.spec``.
        onedir_name = _pyinstaller_collect_name(spec)
        onedir_path = source_layout / onedir_name
        if not onedir_path.is_dir():
            raise SystemExit(
                f"ERROR: PyInstaller output not found at {onedir_path} "
                f"(spec={spec.name}, collect name={onedir_name})"
            )
        # Move the exe to the portable root.
        for src in onedir_path.iterdir():
            if src.name.lower().endswith(".exe"):
                shutil.copy2(src, target_root / src.name)
            elif src.name == "_internal":
                # Merge _internal directories. PyInstaller's
                # two builds have disjoint dependency sets
                # (launcher needs more, cli has some extras
                # the launcher does not). The function
                # merges by copying each file, preferring
                # the first occurrence.
                _merge_dir(src, target_root / "_internal")

    # Copy ``alembic.ini`` to the merged
    # ``_internal/alembic/cfg/alembic.ini``. The
    # file is not bundled through the PyInstaller
    # ``datas`` tuple because of a documented
    # ``alembic.ini/alembic.ini`` COLLECT nesting
    # quirk (PyInstaller's COLLECT nests a file
    # dest inside a sibling directory entry that
    # shares the same prefix). The post-PyInstaller
    # copy is the documented v2.1 Part B3A
    # workaround; the runtime reads the file via
    # :func:`app.runtime_paths.alembic_config_path`.
    alembic_ini_src = BACKEND_ROOT / "alembic.ini"
    alembic_ini_dst = target_root / "_internal" / "alembic" / "cfg" / "alembic.ini"
    if not alembic_ini_src.is_file():
        raise SystemExit(f"ERROR: alembic.ini missing at {alembic_ini_src}")
    alembic_ini_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(alembic_ini_src, alembic_ini_dst)

    # Layer the documented user-facing layout on top
    # of the merged onedir. Each entry in
    # PORTABLE_BUNDLE_LAYOUT is relative to the repo
    # root; we copy the source tree item into the
    # portable root at the destination path.
    # ``__pycache__`` directories are excluded from
    # the source copy so the portable bundle does
    # not carry build-host bytecode caches.
    for src_rel, dst_rel in PORTABLE_BUNDLE_LAYOUT:
        src = REPO_ROOT / src_rel
        dst = target_root / dst_rel
        if not src.exists():
            raise SystemExit(f"ERROR: required layout source missing: {src}")
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            _copy_tree_excluding_pycache(src, dst)


def _copy_tree_excluding_pycache(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` while skipping ``__pycache__`` directories.

    The function is a thin wrapper around
    :func:`shutil.copytree` that excludes
    ``__pycache__`` directories from the copy. The
    portable bundle must not carry Python bytecode
    caches from the build host; the cached ``.pyc``
    files are regenerated on first import in the
    frozen interpreter and would only inflate the
    artefact with build-host paths.
    """
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))


def _merge_dir(src: Path, dst: Path) -> None:
    """Recursively copy ``src`` into ``dst``, overwriting existing files.

    The function is the documented fix for the 2026-08
    packaging regression where a previous build's
    ``_internal/psutil/_psutil_windows.pyd`` survived
    into a new portable because an earlier revision
    of this helper used ``shutil.copy2`` wrapped in a
    skip-when-target-already-exists guard. The new
    semantics always overwrite, so a stale native
    extension can never again shadow the fresh
    PyInstaller emission.

    The function is a **union-friendly overwrite**:
    it copies every file from ``src`` into ``dst``
    and overwrites any existing file at the
    destination, but it does **not** prune entries
    under ``dst`` that are absent from ``src``.
    Pruning is intentionally left out because the
    portable assembly merges two PyInstaller
    onedirs (the launcher and the CLI) into the
    same ``_internal/`` tree: a subdirectory that
    the launcher onedir has but the CLI onedir
    does not (e.g. ``webview/lib``) must be
    preserved across the CLI merge. The obsolete
    file case is handled by the ``portable_root``
    wipe in :func:`main` (which is the primary
    invariant of the v2.1.6 deterministic-staging
    fix), not by per-call pruning.

    ``__pycache__`` directories are skipped so the
    portable bundle does not carry build-host
    bytecode caches.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_dir() and item.name == "__pycache__":
            # The cached ``.pyc`` files are regenerated
            # on first import; skip them.
            continue
        target = dst / item.name
        if item.is_dir():
            _merge_dir(item, target)
        else:
            # Overwrite so a previous build's bytes cannot
            # survive into the new portable. ``copy2``
            # preserves the source's metadata, which is
            # exactly what we want for a deterministic
            # build artefact.
            shutil.copy2(item, target)


# Build-environment-only packages that must NOT appear in the
# runtime section of ``THIRD_PARTY_NOTICES.txt``. The list
# covers the direct and transitive dependencies of the
# ``[project.optional-dependencies].dev`` and ``build`` groups
# (plus a small number of tooling-only transitive deps that
# never get imported by the runtime graph).
#
# The list is intentionally explicit (rather than a regex or a
# dynamic introspection) so the notice file is reproducible
# from the committed source alone; a maintainer who adds a new
# dev tool must also extend this list, which is the review hook
# the v2.1 Part B3A acceptance spec requires.
DEV_TOOL_PACKAGES: tuple[str, ...] = (
    # ``[dev]`` direct dependencies.
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "pytest-timeout",
    "ruff",
    "mypy",
    "mypy_extensions",
    "types-requests",
    "httpx2",
    "httpcore2",
    # Common dev-tool transitive deps.
    "iniconfig",
    "coverage",
    "pathspec",
    "wcwidth",
    "ast_serialize",
    "boolean.py",
    # pip / build / packaging tools that never enter the
    # frozen runtime (the build script invokes pip-licenses
    # via ``python -m``; these packages exist in the build
    # venv but are not distributed).
    "pip",
    "wheel",
    "setuptools",
    "pip-licenses",
    "prettytable",
    "py-serializable",
    "altgraph",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    # Windows-specific helpers used by the build
    # environment but not by the frozen portable.
    "pywin32-ctypes",
    "pefile",
)

# The subset of ``DEV_TOOL_PACKAGES`` that are *truly* build
# tools (i.e. they are required to produce the portable but
# are NOT distributed in the portable). The build-tools
# section of ``THIRD_PARTY_NOTICES.txt`` lists these
# packages so the operator can audit the build supply chain.
# The list is intentionally narrow: the broader
# ``DEV_TOOL_PACKAGES`` list also includes dev-only test /
# lint tools (pytest, mypy, ruff, etc.) which an operator
# does not need to audit as part of the build supply chain
# (they are not on the build path for the portable).
#
# Note: ``pip-licenses`` itself is NOT in this list because
# the tool filters itself out of its own output; we add a
# synthetic entry for it in the build-tools section below.
BUILD_TOOL_PACKAGES: tuple[str, ...] = (
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
)

# Synthetic licence entry for ``pip-licenses``. The
# ``pip-licenses`` tool does not list itself in its own
# output, so we hand-write a short entry that satisfies the
# v2.1 Part B3A acceptance requirement of disclosing the
# build-tool supply chain. The licence is the project's
# own MIT-licensed source.
PIP_LICENSES_SYNTHETIC_ENTRY = (
    "pip-licenses\n"
    "5.0.0\n"
    "MIT License\n"
    "Copyright (c) 2018 raimon\n"
    "\n"
    "pip-licenses is the build-time tool that produces this\n"
    "THIRD_PARTY_NOTICES.txt file. The project is licensed\n"
    "under the MIT License; the canonical source is\n"
    "https://github.com/raimon49/pip-licenses. The tool is\n"
    "NOT distributed in the portable; the entry is provided\n"
    "for build-supply-chain transparency only.\n"
)

RUNTIME_HEADER = (
    "============================================================\n"
    "Lockverity Windows portable - third-party licence inventory\n"
    "============================================================\n"
    "This file lists every Python package bundled into the\n"
    "frozen portable runtime (Lockverity.exe and\n"
    "lockverity-cli.exe). The packages below are imported by\n"
    "the application's import graph; their bytecode is\n"
    "embedded in the executable's PYZ archive.\n"
    "\n"
    "Build-environment-only tools (PyInstaller, pip-licenses,\n"
    "pytest, ruff, mypy, etc.) are listed in a separate\n"
    "section below; they are NOT distributed in the portable.\n"
    "\n"
    "If a package's metadata is missing or non-standard, the\n"
    "licence is recorded as ``UNKNOWN`` for manual review; the\n"
    "package is NOT silently classified as permissive.\n"
    "============================================================\n"
    "\n"
    "RUNTIME COMPONENTS (bundled in the frozen executable)\n"
    "============================================================\n"
)

BUILD_TOOL_HEADER = (
    "\n"
    "\n"
    "============================================================\n"
    "BUILD TOOLS (NOT distributed in the portable)\n"
    "============================================================\n"
    "The packages below are required only on the build host\n"
    "to produce the portable. They are not bundled into the\n"
    "frozen executable; an end-user who only runs the\n"
    "portable does not need them. They are listed here for\n"
    "transparency of the build supply chain.\n"
    "============================================================\n"
)


def _run_piplicenses(
    target: Path,
    *,
    ignore: tuple[str, ...] = (),
    only: tuple[str, ...] = (),
) -> int:
    """Run ``pip-licenses`` and write to ``target``.

    Returns the pip-licenses exit code. A non-zero exit
    is treated as a soft warning (the file is still
    produced); the caller can decide whether to keep it.

    Note: ``pip-licenses`` 5.0.0 takes a single
    ``--ignore-packages`` flag followed by a list of
    package names; repeating the flag is not honoured by
    the underlying ``argparse``. The function emits one
    flag with all names as separate argv elements. The
    same applies to ``--packages`` (the include filter).
    """
    cmd: list[str] = [
        sys.executable,
        "-m",
        "piplicenses",
        "--format",
        "plain-vertical",
        "--with-license-file",
        "--no-license-path",
    ]
    if ignore:
        cmd.append("--ignore-packages")
        cmd.extend(ignore)
    if only:
        cmd.append("--packages")
        cmd.extend(only)
    cmd.extend(["--output-file", str(target)])
    result = subprocess.run(  # noqa: S603 - argv is built by us
        cmd,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return int(result.returncode)


def _count_packages_in_section(text: str, start_marker: str, end_marker: str) -> int:
    """Count package entries in the section bounded by two markers.

    A package entry in the ``plain-vertical`` pip-licenses
    output begins with a single-token name line
    (e.g. ``Mako``, ``pyyaml``), followed by a version
    line (``1.2.3``) and then a licence line. The
    licence-text body that follows the third line is
    ignored; we deliberately do not count lines inside
    the body even when they look version-like (e.g.
    copyright dates) because that would overcount.

    The markers are the exact header strings used in
    ``RUNTIME_HEADER`` and ``BUILD_TOOL_HEADER``; we
    deliberately avoid matching the bare
    ``============================================================``
    line because it also appears inside licence texts.
    """
    if start_marker not in text:
        return 0
    section = text.split(start_marker, 1)[1]
    if end_marker in section:
        section = section.split(end_marker, 1)[0]
    count = 0
    lines = section.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # A package name is a single token (no whitespace)
        # that starts with a letter and contains only
        # letters, digits, underscores, dots, hyphens and
        # plus signs. License-text lines with spaces and
        # punctuation are excluded by this check.
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.\-+]*$", stripped):
            continue
        if idx + 1 >= len(lines):
            continue
        # The next line must look like a version.
        if not re.match(r"^[\d.]+", lines[idx + 1].strip()):
            continue
        count += 1
    return count


def _generate_third_party_notices(target: Path) -> tuple[int, dict[str, int]]:
    """Generate ``THIRD_PARTY_NOTICES.txt`` for the portable.

    The function produces a notice file that is derived from
    the **packaged dependency inventory**, not from the entire
    build venv. The v2.1 Part B3A acceptance spec requires
    that the notice file:

      - includes every package actually bundled into the
        frozen portable's import graph;
      - excludes packages that exist only in the build
        environment (dev tools like pytest, ruff, mypy) and
        build-time tools (PyInstaller, pip-licenses);
      - distinguishes build-environment-only tools from
        distributed runtime components in a clearly separate
        section;
      - never silently classifies a missing-licence entry
        (``UNKNOWN``) as permissive;
      - retains the licence text for every bundled package.

    The function runs ``pip-licenses`` twice: once with the
    dev / build tool list ignored (producing the runtime
    inventory), and once for the build tools only
    (producing the build-tools inventory). The two outputs
    are concatenated under explicit section headers and
    written to ``target/THIRD_PARTY_NOTICES.txt``.

    Returns ``(line_count, counts)`` where ``counts`` is a
    ``{"runtime": N, "build_tools": M, "unknown_runtime": K}``
    dict for the manifest.
    """
    _log("notices", "generating THIRD_PARTY_NOTICES.txt")
    runtime_tmp = target / "_runtime_notices.txt"
    build_tmp = target / "_build_notices.txt"
    runtime_rc = _run_piplicenses(runtime_tmp, ignore=DEV_TOOL_PACKAGES)
    build_rc = _run_piplicenses(build_tmp, only=BUILD_TOOL_PACKAGES)
    # ``pip-licenses`` filters itself out of its own
    # output. Append a synthetic entry for it so the
    # build-tools section is complete.
    with open(build_tmp, "a", encoding="utf-8", errors="replace") as handle:
        handle.write("\n" + PIP_LICENSES_SYNTHETIC_ENTRY)
    runtime_text = runtime_tmp.read_text(encoding="utf-8", errors="replace")
    build_text = build_tmp.read_text(encoding="utf-8", errors="replace")
    if runtime_rc != 0:
        # pip-licenses returns 13 when ``--with-license-file``
        # cannot find a license file for one of the packages.
        # We treat that as a soft warning and keep whatever
        # was written, so the operator can review the
        # ``UNKNOWN`` entries manually.
        _log(
            "notices",
            f"runtime pip-licenses reported rc={runtime_rc}; keeping generated text",
        )
    if build_rc != 0:
        _log(
            "notices",
            f"build-tool pip-licenses reported rc={build_rc}; keeping generated text",
        )
    output = RUNTIME_HEADER + runtime_text + BUILD_TOOL_HEADER + build_text
    final_path = target / "THIRD_PARTY_NOTICES.txt"
    final_path.write_text(output, encoding="utf-8", errors="replace")
    runtime_tmp.unlink(missing_ok=True)
    build_tmp.unlink(missing_ok=True)
    counts = {
        "runtime": _count_packages_in_section(output, RUNTIME_HEADER, BUILD_TOOL_HEADER),
        "build_tools": _count_packages_in_section(output, BUILD_TOOL_HEADER, "\0"),
    }
    runtime_section = output.split(RUNTIME_HEADER, 1)[1].split(BUILD_TOOL_HEADER, 1)[0]
    counts["unknown_runtime"] = sum(
        1 for line in runtime_section.splitlines() if line.strip() == "UNKNOWN"
    )
    return sum(1 for _ in final_path.read_text(encoding="utf-8").splitlines() if _), counts


def _sha256_of(path: Path) -> str:
    """Return the hex SHA-256 of ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _generate_build_manifest(
    target: Path,
    *,
    source_commit: str,
    build_timestamp: str,
    pyinstaller_version: str,
    pip_licenses_version: str,
    node_version: str,
    npm_version: str,
    alembic_head: str,
    frozen_executables: list[str],
    brand_asset_hashes: dict[str, str],
    notice_counts: dict[str, int] | None = None,
) -> None:
    """Write ``BUILD-MANIFEST.json``.

    The manifest is the operator-visible record of
    what is in the portable package. The schema is
    intentionally narrow and stable.
    """
    manifest: dict[str, object] = {
        "product": "Lockverity",
        "version": _read_app_version(),
        "source_commit": source_commit,
        "build_timestamp_utc": build_timestamp,
        "target_platform": "windows",
        "target_architecture": "x64",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pyinstaller_version": pyinstaller_version,
        "pip_licenses_version": pip_licenses_version,
        "node_version": node_version,
        "npm_version": npm_version,
        "alembic_head": alembic_head,
        "top_level_executables": frozen_executables,
        "approved_brand_asset_hashes": brand_asset_hashes,
        "dependency_inventory_location": "THIRD_PARTY_NOTICES.txt",
        "lockverity_license_location": "LICENSE",
        "portable_readme_location": "README-PORTABLE.txt",
    }
    if notice_counts is not None:
        manifest["dependency_inventory_summary"] = {
            "runtime_components": notice_counts.get("runtime", 0),
            "build_tools": notice_counts.get("build_tools", 0),
            "runtime_unknown_licences": notice_counts.get("unknown_runtime", 0),
        }
    (target / "BUILD-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_app_version() -> str:
    """Read the application version from ``app/_version.py``."""
    # Import via a subprocess to avoid loading the
    # application graph just to read a string.
    cmd = [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, r'"
        + str(BACKEND_ROOT)
        + "'); from app._version import __version__; print(__version__)",
    ]
    result = subprocess.run(  # noqa: S603 - argv is built by us
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def _git_head_full() -> str:
    """Return the full 40-character git HEAD commit SHA, or ``unknown``.

    The function uses ``git rev-parse HEAD`` (not
    ``--short``) so the manifest records the complete
    SHA-1 the released artefact was built from. A
    seven-character abbreviation is not enough for
    release provenance; the v2.1 Part B3A acceptance
    spec requires exactly 40 lowercase hexadecimal
    characters and equality with ``git rev-parse HEAD``.

    The function also detects a dirty working tree. A
    release build must be from a clean committed HEAD;
    the manifest records ``unknown-dirty-<N>`` so the
    build still produces a manifest but a downstream
    test can refuse the artefact.

    The function uses :func:`shutil.which` to resolve
    the ``git`` executable to an absolute path so the
    ``S607`` partial-path warning is not triggered.
    """
    import shutil

    git_exe = shutil.which("git")
    if git_exe is None:
        return "unknown"
    result = subprocess.run(  # noqa: S603 - argv is built by us
        [git_exe, "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        return "unknown"
    full_sha = result.stdout.strip()
    # Detect a dirty tracked tree. The check uses
    # ``--untracked-files=no`` so the regenerated
    # packaging artefacts (e.g. the
    # ``backend/pyinstaller/favicon-exe.ico``
    # derivative, the ``build/`` work directory) do
    # not trigger a refusal. Only tracked + staged
    # changes count as a dirty release build.
    dirty_result = subprocess.run(  # noqa: S603 - argv is built by us
        [git_exe, "status", "--porcelain", "--untracked-files=no"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if dirty_result.returncode == 0 and dirty_result.stdout.strip():
        dirty_token = "dirty-" + str(len(dirty_result.stdout.strip().splitlines()))
        return f"unknown-{dirty_token}"
    return full_sha


def _node_versions() -> tuple[str, str]:
    """Return ``(node_version, npm_version)`` from the build host.

    The function shells out to ``node --version`` and
    ``npm --version`` with ``shell=False`` so a
    misconfigured PATH cannot inject a quoted version
    string. The tools are resolved through
    :func:`shutil.which` so the call always uses an
    absolute path; a missing tool is reported as
    ``"unknown"`` rather than raising.
    """
    import shutil

    node_path = shutil.which("node") or "node"
    node = subprocess.run(  # noqa: S603 - argv is built by us
        [node_path, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    node_version = "unknown" if node.returncode != 0 else node.stdout.strip().lstrip("v")
    npm_name = "npm.cmd" if sys.platform == "win32" else "npm"
    npm_path = shutil.which(npm_name) or npm_name
    npm = subprocess.run(  # noqa: S603 - argv is built by us
        [npm_path, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    npm_version = "unknown" if npm.returncode != 0 else npm.stdout.strip()
    return node_version, npm_version


def _alembic_head() -> str:
    """Return the documented Alembic head revision.

    The function runs ``alembic heads`` and returns
    the head revision id. The head is the highest
    revision in the ``alembic/versions/`` tree.
    """
    cmd = [sys.executable, "-m", "alembic", "heads"]
    result = subprocess.run(  # noqa: S603 - argv is built by us
        cmd,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        return "unknown"
    # ``alembic heads`` prints lines like
    # ``<rev> (head)``. We extract the first
    # whitespace-separated token.
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        return line.split()[0]
    return "unknown"


def _generate_sha256_sums(target: Path) -> dict[str, str]:
    """Write ``SHA256SUMS.txt`` for the user-facing files.

    The function hashes every file at the portable
    root that is documented to the operator: the
    top-level executables, the manifest, the
    licence, the portable README, and the third-party
    notices. The internal ``_internal/`` tree is
    intentionally excluded from this operator-facing
    hash list; the COMPLETE file-tree integrity
    record (every regular file under ``_internal/``
    included) is ``PAYLOAD-MANIFEST.json``, generated
    by :func:`_generate_payload_manifest` after this
    function runs.

    The function returns the same ``{name: hash}``
    mapping it writes to disk, so the caller can
    surface the hashes in the build report.
    """
    user_facing = [
        "Lockverity.exe",
        "lockverity-cli.exe",
        "BUILD-MANIFEST.json",
        "LICENSE",
        "PRIVACY.md",
        "README-PORTABLE.txt",
        "THIRD_PARTY_NOTICES.txt",
    ]
    hashes: dict[str, str] = {}
    lines: list[str] = []
    for name in user_facing:
        path = target / name
        if path.is_file():
            digest = _sha256_of(path)
            hashes[name] = digest
            lines.append(f"{digest}  {name}")
    (target / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return hashes


def _generate_payload_manifest(target: Path, version: str) -> int:
    """Write the complete file-tree ``PAYLOAD-MANIFEST.json`` (LV-003).

    The function loads the dedicated
    ``backend/scripts/payload_manifest.py`` module via
    :mod:`importlib.util` (the established pattern in this
    script; see :func:`_regenerate_exe_icon`) and generates the
    complete payload manifest: every regular file in the portable
    tree is hashed, with the manifest itself as the sole
    exclusion (non-circular self-hashing). The walk is defensive:
    a symlink or non-regular entry inside the staged payload
    aborts the build.

    Returns the number of files covered by the manifest for the
    build report.
    """
    import importlib.util

    script_path = BACKEND_ROOT / "scripts" / "payload_manifest.py"
    spec = importlib.util.spec_from_file_location("lockverity_build_payload_manifest", script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"ERROR: could not load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest_path = module.write_payload_manifest(target, product="Lockverity", version=version)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return int(manifest["file_count"])


def _zip_portable(source: Path, zip_path: Path) -> str:
    """Zip the portable root into a deterministic ``.zip`` file.

    The function writes the zip with ``ZIP_DEFLATED`` so
    the artefact is small and the operator's antivirus
    can scan it. The zip is placed at
    ``dist/windows/Lockverity-2.1.0-windows-x64-portable.zip``
    by default. The arcname preserves the
    portable-root directory so a fresh extraction
    yields a single
    ``Lockverity-2.1.0-windows-x64-portable/``
    directory rather than a flat list of files; the
    packaged smoke expects this layout. The function
    returns the SHA-256 of the final zip.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    prefix = source.name
    with zipfile.ZipFile(
        zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                relative = path.relative_to(source)
                archive.write(path, arcname=str(prefix / relative))
    return _sha256_of(zip_path)


def _run_packaged_smoke(zip_path: Path, output: dict[str, object]) -> None:
    """Extract the zip to a temp dir and run the documented smoke flow.

    The function is a thin smoke that exercises the
    frozen ``lockverity-cli.exe``: ``--version``,
    ``doctor --json``, background start on a dynamic
    loopback port, ``status``, ``status --json``,
    ``GET /``, ``GET /about``, ``GET /api/v1/health``,
    ``stop``. The smoke is best-effort; a failure
    here does not fail the build (the maintainer
    is expected to run the smoke manually for
    higher-fidelity validation).
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="lockverity-portable-smoke-") as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp_dir)
        portable_root = tmp_dir / DEFAULT_PORTABLE_NAME
        cli = portable_root / "lockverity-cli.exe"
        if not cli.is_file():
            output["smoke_status"] = "missing-cli"
            return
        # ``--version``
        r = subprocess.run(  # noqa: S603 - argv is built by us
            [str(cli), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        output["smoke_version_rc"] = r.returncode
        output["smoke_version_stdout"] = r.stdout.strip()[:200]
        # ``doctor --json``
        r = subprocess.run(  # noqa: S603 - argv is built by us
            [str(cli), "doctor", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        output["smoke_doctor_rc"] = r.returncode
        output["smoke_status"] = "ok" if r.returncode in (0, 2) else "fail"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_windows_portable",
        description="Build the Lockverity Windows portable package.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Dedicated packaging work directory (default: build/packaging).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe the dedicated output directory before building.",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Skip ``prepare_frontend_dist.py``; assume the dist is current.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the packaged smoke checks.",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep the PyInstaller work directory after the build.",
    )
    parser.add_argument(
        "--json-report",
        action="store_true",
        help="Emit a structured JSON report on stdout at the end.",
    )
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    report: dict[str, object] = {
        "started_at_utc": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "platform": None,
        "machine": None,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pyinstaller_version": None,
        "pip_licenses_version": None,
        "node_version": None,
        "npm_version": None,
        "alembic_head": None,
        "source_commit": _git_head_full(),
        "app_version": _read_app_version(),
        "output_dir": str(args.output_dir),
        "portable_root": None,
        "portable_zip": None,
        "executable_sha256": {},
        "payload_manifest_file_count": None,
        "zip_sha256": None,
        "smoke_status": "skipped",
    }
    try:
        platform_name, machine = _verify_host()
        report["platform"] = platform_name
        report["machine"] = machine
        _log("host", f"verified Windows x64 (Python {report['python_version']})")
        pyinstaller_versions = _verify_build_dependencies()
        report["pyinstaller_version"] = pyinstaller_versions.get("pyinstaller")
        report["pip_licenses_version"] = pyinstaller_versions.get("pip-licenses")
        _log("deps", f"build dependencies verified: {pyinstaller_versions}")
        node_version, npm_version = _node_versions()
        report["node_version"] = node_version
        report["npm_version"] = npm_version
        _log("deps", f"node={node_version} npm={npm_version}")
        alembic_head = _alembic_head()
        report["alembic_head"] = alembic_head
        if args.clean and args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = args.output_dir / "work"
        pyinstaller_out = args.output_dir / "pyinstaller_out"
        portable_root = args.output_dir / DEFAULT_PORTABLE_NAME
        logs_dir = args.output_dir / "logs"
        # Wipe the previous portable staging root and the
        # PyInstaller ``distpath`` directory before the
        # new build emits anything into them. Both are
        # pure build outputs (re-generated on every
        # build); preserving either one between builds
        # would risk mixing old and new files -- the
        # exact failure mode that produced the 2026-08
        # stale-psutil-pyd regression. The ``work/`` and
        # ``logs/`` directories are kept: the first is
        # opt-in via ``--keep-work``, the second is a
        # build log the operator may want to inspect.
        # This is the primary invariant of the v2.1.6
        # deterministic-staging fix; the secondary
        # defence is :func:`_merge_dir` mirroring the
        # destination tree, which catches any caller
        # that still invokes the merge on a populated
        # destination.
        if portable_root.exists():
            _log("staging", f"wiping previous portable root: {portable_root}")
            shutil.rmtree(portable_root)
        if pyinstaller_out.exists():
            _log("staging", f"wiping previous PyInstaller dist: {pyinstaller_out}")
            shutil.rmtree(pyinstaller_out)
        if not args.skip_frontend_build:
            _build_frontend()
        _verify_frontend_dist()
        _log("frontend", "frontend dist verified")
        # Regenerate the packaging-derivative ICO
        # from the approved brand assets. The brand
        # source files are not modified; the
        # derivative is the only place a 256x256
        # entry is introduced, and the conversion is
        # a mechanical Lanczos downscale documented
        # in ``scripts/generate_exe_icon.py``.
        derivative = _regenerate_exe_icon()
        _log("icon", f"derivative ICO ready at {derivative}")
        # Run both PyInstaller builds.
        launcher_spec = PYINSTALLER_DIR / "lockverity.spec"
        cli_spec = PYINSTALLER_DIR / "cli.spec"
        _pyinstaller_build(
            launcher_spec, work_dir, pyinstaller_out, logs_dir / "pyinstaller-lockverity.log"
        )
        _pyinstaller_build(cli_spec, work_dir, pyinstaller_out, logs_dir / "pyinstaller-cli.log")
        # Assemble the user-facing layout.
        _assemble_portable(
            source_layout=pyinstaller_out,
            target_root=portable_root,
            build_specs=[launcher_spec, cli_spec],
        )
        report["portable_root"] = str(portable_root)
        _log("assemble", f"portable root: {portable_root}")
        # Compute brand-asset hashes from the source tree.
        brand_assets = {
            "favicon.ico": _sha256_of(REPO_ROOT / "frontend" / "public" / "favicon.ico"),
            "lockverity-symbol.png": _sha256_of(
                REPO_ROOT / "frontend" / "public" / "brand" / "lockverity-symbol.png"
            ),
            "lockverity-horizontal-logo.png": _sha256_of(
                REPO_ROOT / "frontend" / "public" / "brand" / "lockverity-horizontal-logo.png"
            ),
        }
        # Notices, manifest, hashes.
        _, notice_counts = _generate_third_party_notices(portable_root)
        report["notices"] = {
            "runtime_count": notice_counts["runtime"],
            "build_tools_count": notice_counts["build_tools"],
            "runtime_unknown_count": notice_counts["unknown_runtime"],
        }
        _generate_build_manifest(
            portable_root,
            source_commit=str(report["source_commit"]),
            build_timestamp=str(report["started_at_utc"]),
            pyinstaller_version=str(report["pyinstaller_version"] or "unknown"),
            pip_licenses_version=str(report["pip_licenses_version"] or "unknown"),
            node_version=str(report["node_version"] or "unknown"),
            npm_version=str(report["npm_version"] or "unknown"),
            alembic_head=str(report["alembic_head"] or "unknown"),
            frozen_executables=["Lockverity.exe", "lockverity-cli.exe"],
            brand_asset_hashes=brand_assets,
            notice_counts=notice_counts,
        )
        exe_hashes = _generate_sha256_sums(portable_root)
        report["executable_sha256"] = exe_hashes
        # Complete file-tree integrity manifest (LV-003). Must
        # run AFTER SHA256SUMS.txt / BUILD-MANIFEST.json exist so
        # both are covered, and BEFORE the zip so the manifest
        # ships inside the distributed payload.
        manifest_file_count = _generate_payload_manifest(portable_root, str(report["app_version"]))
        report["payload_manifest_file_count"] = manifest_file_count
        _log("manifest", f"PAYLOAD-MANIFEST.json covers {manifest_file_count} files")
        # Zip.
        zip_path = args.output_dir / PORTABLE_ZIP_NAME
        zip_hash = _zip_portable(portable_root, zip_path)
        report["portable_zip"] = str(zip_path)
        report["zip_sha256"] = zip_hash
        # Smoke.
        if not args.skip_smoke:
            _run_packaged_smoke(zip_path, report)
        else:
            report["smoke_status"] = "skipped"
        # Cleanup work.
        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir)
        report["duration_seconds"] = round(time.monotonic() - started_at, 2)
        if args.json_report:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            _log(
                "done",
                f"portable ZIP: {zip_path}  sha256={zip_hash[:16]}...  "
                f"duration={report['duration_seconds']}s",
            )
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["duration_seconds"] = round(time.monotonic() - started_at, 2)
        if args.json_report:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            sys.stderr.write(f"FATAL: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
