"""Release-artifact resolution policy tests (LV-002).

The audit found packaged-artifact tests whose candidate lists
prioritised ``backend/build/dev`` over the canonical packaging
output ``<repo>/build/packaging``. A full backend suite run then
selected a stale dev payload as release evidence: validation
could fail against irrelevant old files or, worse, pass while
never validating the actual release candidate.

The policy under test lives in
:mod:`tests.packaging_artifacts`:

  * canonical release validation resolves artifacts from
    ``<repo>/build/packaging`` ONLY;
  * a missing canonical artifact is ``None`` (the caller skips
    or fails, naming the expected canonical path) -- never a
    silent fallback to a developer output;
  * skip / failure messages identify the resolved or expected
    artifact path.

The behavioural tests build a sandbox repository layout under
``tmp_path`` containing BOTH a canonical portable and a stale
developer portable, then prove the resolver can only ever return
the canonical one.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.packaging_artifacts import (
    CANONICAL_PACKAGING_DIR,
    CANONICAL_PORTABLE_ROOT,
    PORTABLE_NAME,
    find_release_lockverity_exe,
    find_release_portable_root,
    release_packaging_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# The test modules that consume the canonical resolver plus the
# resolver module itself. These are the release-validation
# surfaces the LV-002 static guard pins.
RELEASE_RESOLUTION_SURFACES: tuple[Path, ...] = (
    REPO_ROOT / "backend" / "tests" / "packaging_artifacts.py",
    REPO_ROOT / "backend" / "tests" / "test_pyinstaller_payload.py",
    REPO_ROOT / "backend" / "tests" / "test_exe_icon_pe.py",
)


def _make_sandbox_repo(tmp_path: Path, *, with_canonical: bool) -> Path:
    """Build a sandbox repository layout with a stale dev portable.

    The sandbox always contains a stale developer portable under
    ``backend/build/dev/packaging`` (carrying an
    ``obsolete-sentinel.dll``); when ``with_canonical`` is set it
    also contains the canonical portable under
    ``build/packaging``. This mirrors the audited machine state
    where both trees exist side by side.
    """
    repo = tmp_path / "repo"
    stale_dev = repo / "backend" / "build" / "dev" / "packaging" / PORTABLE_NAME
    (stale_dev / "_internal").mkdir(parents=True)
    (stale_dev / "Lockverity.exe").write_bytes(b"STALE-DEV-EXE")
    (stale_dev / "_internal" / "obsolete-sentinel.dll").write_bytes(b"OBSOLETE")
    if with_canonical:
        canonical = repo / "build" / "packaging" / PORTABLE_NAME
        canonical.mkdir(parents=True)
        (canonical / "Lockverity.exe").write_bytes(b"CANONICAL-EXE")
    return repo


class TestCanonicalResolution:
    """The resolver serves the canonical packaging output only."""

    def test_canonical_root_wins_when_dev_tree_exists(self, tmp_path: Path) -> None:
        repo = _make_sandbox_repo(tmp_path, with_canonical=True)
        resolved = find_release_portable_root(repo)
        assert resolved is not None
        assert resolved == repo / "build" / "packaging" / PORTABLE_NAME, (
            "With both a canonical portable and a stale developer portable on "
            "disk, release resolution must return the canonical "
            "build/packaging tree"
        )
        assert not (resolved / "_internal" / "obsolete-sentinel.dll").exists(), (
            "the resolved release tree must not be the stale developer "
            "payload carrying obsolete-sentinel.dll"
        )

    def test_missing_canonical_never_falls_back_to_dev(self, tmp_path: Path) -> None:
        repo = _make_sandbox_repo(tmp_path, with_canonical=False)
        resolved = find_release_portable_root(repo)
        assert resolved is None, (
            "A missing canonical portable must resolve to None so the caller "
            "skips or fails; a stale developer payload under "
            "backend/build/dev must never silently satisfy release "
            "validation"
        )
        assert find_release_lockverity_exe(repo) is None

    def test_canonical_exe_resolved_from_inside_portable_root(self, tmp_path: Path) -> None:
        repo = _make_sandbox_repo(tmp_path, with_canonical=True)
        exe = find_release_lockverity_exe(repo)
        assert exe is not None
        assert exe == repo / "build" / "packaging" / PORTABLE_NAME / "Lockverity.exe"
        assert exe.read_bytes() == b"CANONICAL-EXE"

    def test_packaging_dir_is_repo_build_packaging(self) -> None:
        assert release_packaging_dir() == CANONICAL_PACKAGING_DIR
        assert CANONICAL_PACKAGING_DIR == REPO_ROOT / "build" / "packaging", (
            "the canonical packaging directory is <repo>/build/packaging; "
            "redefining canonical packaging is out of scope for LV-002"
        )
        assert CANONICAL_PORTABLE_ROOT == CANONICAL_PACKAGING_DIR / PORTABLE_NAME

    def test_on_disk_resolution_matches_canonical_path(self) -> None:
        """Against this repository the resolver names the canonical root.

        The audited machine carries a stale developer portable at
        ``backend/build/dev/packaging``; this test proves the live
        resolution still returns the canonical tree (or None when
        no canonical build exists), never the stale dev one.
        """
        resolved = find_release_portable_root()
        stale_dev = REPO_ROOT / "backend" / "build" / "dev" / "packaging" / PORTABLE_NAME
        if resolved is None:
            assert not CANONICAL_PORTABLE_ROOT.is_dir()
        else:
            assert resolved == CANONICAL_PORTABLE_ROOT
        assert resolved != stale_dev


class TestReleaseSurfaceGuards:
    """Static guards: the release surfaces must not reference developer roots.

    These follow the repository's static-contract test style (see
    ``test_installer.py``): they pin the LV-002 policy at the
    source level so a future edit cannot quietly reintroduce a
    ``build/dev`` candidate list ahead of the canonical output.
    """

    def test_release_surfaces_construct_no_dev_artifact_paths(self) -> None:
        # A Path-construction of a developer output root in a
        # release-validation module: ``"..." / "dev" / "..."``.
        dev_segment = re.compile(r'/\s*"dev"\s*/')
        for surface in RELEASE_RESOLUTION_SURFACES:
            text = surface.read_text(encoding="utf-8")
            found = dev_segment.findall(text)
            assert not found, (
                f"{surface.name} constructs a developer-output path "
                f"({found[:2]}); release validation must resolve artifacts "
                "from <repo>/build/packaging only (LV-002). Developer-only "
                "validation, if ever needed, must live in a module that "
                "opts into developer outputs explicitly."
            )

    def test_skip_messages_name_the_expected_canonical_path(self) -> None:
        for surface in RELEASE_RESOLUTION_SURFACES[1:]:
            text = surface.read_text(encoding="utf-8")
            assert "CANONICAL_PORTABLE_ROOT" in text, (
                f"{surface.name} must name the expected canonical artifact "
                "path (via CANONICAL_PORTABLE_ROOT) in its skip/failure "
                "output so the operator sees what was resolved or expected"
            )
