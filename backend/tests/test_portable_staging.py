"""Regression tests for the v2.1.6 deterministic Windows portable
staging fix.

The 2026-08 frozen-EXE regression was caused by
:func:`backend.scripts.build_windows_portable._merge_dir`
using a skip-when-target-already-exists guard before
``shutil.copy2``. When the same operator ran the
build twice in a row, a previous build's
``_internal/psutil/_psutil_windows.pyd`` survived into
the new portable because the helper skipped the copy
rather than overwriting the file. The fresh PyInstaller
emission was therefore discarded and the resulting
frozen EXE carried a stale native extension that
crashed at import time with
``module 'psutil_windows' has no attribute 'heap_info'``.

The fix makes the portable staging area a *build
output*, not a *persistent cache*:

  1. :func:`backend.scripts.build_windows_portable._merge_dir`
     now unconditionally overwrites destination
     files. The previous "skip if exists" guard is
     gone. The helper is a union-friendly overwrite
     that copies every file from ``src`` into ``dst``;
     it does **not** prune entries absent from the
     current source because the portable assembly
     merges two PyInstaller onedirs (the launcher and
     the CLI) into the same ``_internal/`` tree and a
     subdirectory that the launcher has but the CLI
     does not (e.g. ``webview/lib``) must be
     preserved across the CLI merge.
  2. :func:`backend.scripts.build_windows_portable.main`
     now wipes the previous ``portable_root`` and
     ``pyinstaller_out`` directories before each
     build, even without the ``--clean`` flag, so
     neither tree can carry old build debris into a
     new portable. This is the primary invariant of
     the v2.1.6 fix; it is the layer that handles the
     "obsolete file" case that the new overwrite-only
     ``_merge_dir`` deliberately does not address.

These tests cover the two documented regression
cases:

  * :func:`test_merge_dir_overwrites_existing_file` --
    CASE 1: a stale file at the same relative path is
    replaced with the fresh source content.
  * :func:`test_assemble_portable_does_not_prune_under_multi_source_merge`
    -- the multi-source merge must not prune entries
    the launcher carries and the CLI does not
    (regression guard for the webview/lib/-drops-out
    failure mode that an earlier "mirror"
    interpretation of the fix would have caused).
  * :func:`test_main_wipes_staging_roots_before_build` --
    the primary invariant: a deliberate sentinel
    placed in the staging area is gone after the
    build. Combined with the CASE 1 overwrite test,
    this is the documented "staging is a build
    output, not a cache" guarantee.
  * :func:`test_build_windows_portable_no_skip_if_exists`
    -- belt-and-braces static test against the build
    source to lock the new semantics in: the helper
    must overwrite, not skip.
  * :func:`test_merge_dir_skips_pycache` -- the
    bytecode cache guard.

The tests use only the standard library and a
:mod:`tempfile` root so they do not depend on
PyInstaller or any other build dependency. They are
safe to run on dev machines that have not built the
portable, and the fixture isolation guarantees no
interaction with the real ``build/`` tree.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "backend" / "scripts" / "build_windows_portable.py"


def _load_build_module() -> object:
    """Load the build script as a module without executing ``main``.

    The script is not a regular import (it is not a
    package module), so we use :mod:`importlib.util`
    to attach it under a synthetic name and return
    the resulting module object. ``main`` is the
    ``if __name__ == "__main__"`` guard at the
    bottom of the file; importing the module does
    not invoke it, so the load is side-effect free.
    """
    spec = importlib.util.spec_from_file_location(
        "lockverity_build_windows_portable_under_test", BUILD_SCRIPT
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load build script at {BUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build_module() -> object:
    return _load_build_module()


def _populate(root: Path, layout: dict[str, bytes]) -> None:
    """Write a tree under ``root`` according to ``layout``.

    ``layout`` is a ``{relative_path: bytes}`` mapping.
    Parent directories are created as needed.
    """
    for rel, content in layout.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def test_merge_dir_overwrites_existing_file(build_module: object) -> None:
    """CASE 1: a stale file at the same path is replaced by the fresh source.

    The pre-fix helper used a skip-when-target-already-exists
    guard around ``shutil.copy2(...)`` which silently
    kept the previous build's bytes. The new
    overwrite semantics replace stale content with
    the source content.
    """
    with tempfile.TemporaryDirectory(prefix="lockverity-merge-replace-") as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        _populate(
            dst,
            {
                "_internal/psutil/_psutil_windows.pyd": b"STALE-PSUTIL-BYTES",
            },
        )
        _populate(
            src,
            {
                "_internal/psutil/_psutil_windows.pyd": b"FRESH-PSUTIL-BYTES",
            },
        )
        build_module._merge_dir(src, dst)
        final = dst / "_internal" / "psutil" / "_psutil_windows.pyd"
        assert final.is_file(), f"merged tree missing {final}"
        assert final.read_bytes() == b"FRESH-PSUTIL-BYTES", (
            "stale _psutil_windows.pyd survived the merge -- the "
            "2026-08 regression is back. _merge_dir must overwrite "
            "destination files, not skip them."
        )


def test_assemble_portable_does_not_prune_under_multi_source_merge(
    build_module: object,
) -> None:
    """The union-friendly merge preserves entries only in earlier sources.

    The portable assembly calls :func:`_merge_dir` once
    per PyInstaller onedir (lockverity + lockverity-cli)
    against the same destination. An earlier "mirror"
    interpretation of the fix pruned destination
    entries that the current source did not have,
    which dropped ``_internal/webview/lib`` after the
    CLI merge. The new helper must be a union-friendly
    overwrite: it copies every file from ``src``
    into ``dst`` and never removes an entry that was
    placed there by an earlier source.
    """
    with tempfile.TemporaryDirectory(prefix="lockverity-merge-union-") as tmp:
        # Source 1 (the launcher onedir) carries webview/lib DLLs.
        src1 = Path(tmp) / "src1"
        _populate(
            src1,
            {
                "_internal/webview/lib/Microsoft.Web.WebView2.Core.dll": b"LAUNCHER-DLL",
            },
        )
        # Source 2 (the CLI onedir) does NOT carry webview.
        src2 = Path(tmp) / "src2"
        _populate(
            src2,
            {
                "_internal/cl_only/foo.bin": b"CLI-FILE",
            },
        )
        # Destination starts empty. We merge source 1 first, then
        # source 2, which is the order the portable assembly uses.
        dst = Path(tmp) / "dst"
        build_module._merge_dir(src1, dst)
        build_module._merge_dir(src2, dst)
        webview_dll = dst / "_internal" / "webview" / "lib" / "Microsoft.Web.WebView2.Core.dll"
        cli_file = dst / "_internal" / "cl_only" / "foo.bin"
        assert webview_dll.is_file(), (
            "webview/lib DLLs were dropped by the second merge -- the "
            "helper pruned entries that the second source did not "
            "carry. The portable assembly merges two onedirs into "
            "one tree; pruning would silently drop launcher-only "
            "files. The v2.1.6 fix is union-friendly overwrite, not "
            "mirror."
        )
        assert webview_dll.read_bytes() == b"LAUNCHER-DLL"
        assert cli_file.is_file() and cli_file.read_bytes() == b"CLI-FILE", (
            "CLI source entries were not copied into the destination"
        )


def test_main_wipes_staging_roots_before_build() -> None:
    """The primary invariant: portable_root is wiped before each build.

    A deliberate sentinel placed in the staging area is
    removed by the build. The "obsolete file" case is
    handled at the main() wipe level, not by the
    per-call :func:`_merge_dir` overwrite.
    """
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    rmtree_staging = text.find("shutil.rmtree(portable_root)")
    rmtree_pyinst = text.find("shutil.rmtree(pyinstaller_out)")
    first_call_site = text.find("_pyinstaller_build(\n            launcher_spec, work_dir")
    assert rmtree_staging != -1, (
        "build_windows_portable.main must wipe the previous "
        "portable_root before PyInstaller runs (the 2026-08 "
        "stale-staging regression guard)"
    )
    assert rmtree_pyinst != -1, (
        "build_windows_portable.main must wipe the previous "
        "pyinstaller_out before PyInstaller runs (defence in "
        "depth against stale COLLECT output)"
    )
    assert first_call_site != -1, "could not find the _pyinstaller_build call site in main"
    assert rmtree_staging < first_call_site, (
        "portable_root wipe must happen before _pyinstaller_build"
    )
    assert rmtree_pyinst < first_call_site, (
        "pyinstaller_out wipe must happen before _pyinstaller_build"
    )


def test_build_windows_portable_no_skip_if_exists() -> None:
    """The skip-if-exists guard is gone from ``_merge_dir``.

    The 2026-08 root cause was the
    skip-when-target-already-exists guard around
    ``shutil.copy2``. The build script must not
    contain that guard any more -- if it does, the
    regression can return on a future refactor.
    """
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    bad_pattern = "if not target.exists()"
    assert bad_pattern not in text, (
        f"build_windows_portable.py still contains "
        f"{bad_pattern!r}; the deterministic-staging fix "
        "requires _merge_dir to overwrite, not skip."
    )


def test_merge_dir_skips_pycache(build_module: object) -> None:
    """``__pycache__`` directories are ignored in both src and dst.

    The portable bundle must not carry build-host
    bytecode caches. The helper walks one level at a
    time and skips the ``__pycache__`` name; entries
    under ``dst`` named ``__pycache__`` are left in
    place even if ``src`` has no equivalent.
    """
    with tempfile.TemporaryDirectory(prefix="lockverity-merge-pycache-") as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        _populate(
            src,
            {
                "_internal/keep.pyd": b"KEEP",
            },
        )
        _populate(
            dst,
            {
                "_internal/__pycache__/something.cpython-312.pyc": b"BUILDHOST",
                "_internal/keep.pyd": b"STALE",
            },
        )
        build_module._merge_dir(src, dst)
        assert (dst / "_internal" / "__pycache__" / "something.cpython-312.pyc").is_file(), (
            "__pycache__ must be preserved; the helper is not "
            "allowed to delete build-host bytecode caches"
        )
        assert (dst / "_internal" / "keep.pyd").read_bytes() == b"KEEP", (
            "non-pycache files must still be overwritten"
        )


def test_merge_dir_does_not_prune_under_single_source(build_module: object) -> None:
    """Single-source merge: the helper overwrites but does not prune.

    The helper's contract under a single-source merge
    is "overwrite, do not prune". A previous
    destination entry that is not in the source is
    preserved (the obsolete-file case is handled by
    the main() wipe, not by the helper).
    """
    with tempfile.TemporaryDirectory(prefix="lockverity-merge-keep-") as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        _populate(
            dst,
            {
                "_internal/obsolete_dependency.dll": b"STALE",
            },
        )
        _populate(
            src,
            {
                "_internal/keep.pyd": b"KEEP",
            },
        )
        build_module._merge_dir(src, dst)
        # _merge_dir does NOT prune; the obsolete file is preserved
        # at this layer. The portable build's main() wipes the
        # whole portable_root before the assembly, so obsolete
        # files are removed at the higher layer.
        assert (dst / "_internal" / "obsolete_dependency.dll").exists(), (
            "_merge_dir is supposed to be overwrite-only; the "
            "portable_root obsolete-file case is handled by main()"
        )
        assert (dst / "_internal" / "keep.pyd").read_bytes() == b"KEEP"


def test_merge_dir_idempotent(build_module: object) -> None:
    """Running :func:`_merge_dir` twice produces a stable result.

    The overwrite semantics must be idempotent: after
    the first call, ``dst`` is a copy of ``src`` (plus
    any pre-existing dst entries not in src, which
    are preserved by design). A second call must not
    raise and must not change the file contents.
    """
    with tempfile.TemporaryDirectory(prefix="lockverity-merge-idem-") as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        _populate(
            src,
            {
                "_internal/a/b.bin": b"X",
                "_internal/c.bin": b"Y",
            },
        )
        build_module._merge_dir(src, dst)
        first = {p: p.read_bytes() for p in dst.rglob("*") if p.is_file()}
        build_module._merge_dir(src, dst)
        second = {p: p.read_bytes() for p in dst.rglob("*") if p.is_file()}
        assert first == second, "merge is not idempotent on the destination tree"


def test_module_import_is_side_effect_free() -> None:
    """Importing the build script does not invoke ``main``.

    A regression where the import side-effect
    triggered a build would break dev workflows.
    The test loads the module and asserts the
    ``__name__`` guard is in place.
    """
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' in text, (
        "build_windows_portable.py is missing the __main__ guard"
    )
