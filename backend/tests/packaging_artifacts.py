"""Canonical packaged-artifact resolution for release validation.

LV-002 policy: release-mode verification must resolve packaged
artifacts from the canonical packaging output
``<repo>/build/packaging`` ONLY. Historical developer outputs
(``<repo>/backend/build/dev``, ``<repo>/build/dev``,
``<repo>/backend/pyinstaller_out``) must NEVER satisfy a release
check, not even as a fallback.

The audit found packaged-artifact tests whose candidate lists
prioritised ``backend/build/dev`` over the canonical output, so a
stale development payload could silently stand in for the real
release candidate: release validation would either fail against
irrelevant old files or, worse, pass while never validating the
actual candidate.

Every release test that needs a packaged artifact resolves it
through this module so the policy lives in exactly one place:

  * :func:`release_packaging_dir` -- the canonical packaging
    directory (``<repo>/build/packaging``).
  * :func:`find_release_portable_root` -- the canonical portable
    root, or ``None`` when no canonical build exists (the caller
    skips or fails, naming :data:`CANONICAL_PORTABLE_ROOT`).
  * :func:`find_release_lockverity_exe` -- the canonical frozen
    GUI launcher inside the portable root, or ``None``.

A missing canonical artifact is reported as ``None`` -- never
silently substituted. Tests that want genuinely developer-only
validation must opt into developer outputs explicitly and
separately; nothing in this module may return a developer path.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The canonical packaging output, written by
# ``backend/scripts/build_windows_portable.py`` (default
# ``--output-dir build/packaging``). This is the only root a
# release check may resolve artifacts from.
CANONICAL_PACKAGING_DIR = REPO_ROOT / "build" / "packaging"

# The canonical portable name produced by the v2.1.2 portable
# build (``DEFAULT_PORTABLE_NAME`` in the build script).
PORTABLE_NAME = "Lockverity-2.1.2-windows-x64-portable"

# The full canonical portable root. Use this constant in skip /
# failure messages so the operator always sees the exact path
# that was expected or resolved.
CANONICAL_PORTABLE_ROOT = CANONICAL_PACKAGING_DIR / PORTABLE_NAME


def release_packaging_dir(repo_root: Path | None = None) -> Path:
    """Return the canonical packaging directory for a repository.

    ``repo_root`` is injectable so tests can exercise the policy
    against a sandbox layout; production callers omit it and get
    this repository's canonical ``build/packaging``.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    return root / "build" / "packaging"


def find_release_portable_root(repo_root: Path | None = None) -> Path | None:
    """Return the canonical portable root, or ``None`` if absent.

    There is deliberately no fallback: a missing canonical
    portable is the caller's signal to skip (naming
    :data:`CANONICAL_PORTABLE_ROOT`) or fail. Stale developer
    outputs that happen to exist must never satisfy the caller.
    """
    candidate = release_packaging_dir(repo_root) / PORTABLE_NAME
    return candidate if candidate.is_dir() else None


def find_release_lockverity_exe(repo_root: Path | None = None) -> Path | None:
    """Return the canonical frozen ``Lockverity.exe``, or ``None``.

    The launcher is resolved from inside the canonical portable
    root (``<portable>/Lockverity.exe``); a stray development
    ``Lockverity.exe`` anywhere else never satisfies this lookup.
    """
    root = find_release_portable_root(repo_root)
    if root is None:
        return None
    exe = root / "Lockverity.exe"
    return exe if exe.is_file() else None
