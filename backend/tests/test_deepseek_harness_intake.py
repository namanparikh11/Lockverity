"""End-to-end intake regression test for the DeepSeek-Harness
``archive_symlink_forbidden`` failure.

The manual-QA pass surfaced a production scan failure:
scanning the ``deepseek-ai/deepseek-harness`` repository
rejected the entire archive because it contains a single
relative symbolic-link entry under
``.agents/notes/implemented/CLAUDE.md``. The
``build_deepseek_harness_zip`` fixture reproduces the
exact pattern locally so the test does not depend on
a live GitHub mirror.

The fixture mirrors the real DeepSeek Harness
symlink structure exactly:

  - ordinary file: ``deepseek-harness/README.md``
  - relative symlink: ``deepseek-harness/.agents/notes/implemented/CLAUDE.md``
    with target ``../../README.md`` (relative to the
    symlink's parent directory)

Symlink resolution calculation
==============================

The classifier in
:func:`app.utils.archive_validation.ArchiveValidationCollector._classify_symlink_target`
performs the following deterministic, host-filesystem-free
calculation:

  1. **Extract parent dir.** The normalised entry
     path is ``deepseek-harness/.agents/notes/implemented/CLAUDE.md``.
     :func:`posixpath.dirname` returns
     ``deepseek-harness/.agents/notes/implemented``.

  2. **Join parent with literal target.** The
     literal link target string from the ZIP
     entry body is ``../../README.md``.
     :func:`posixpath.join` returns
     ``deepseek-harness/.agents/notes/implemented/../../README.md``.

  3. **Normalise ``..`` segments.**
     :func:`posixpath.normpath` collapses the
     ``..`` segments without touching the host
     filesystem and returns
     ``deepseek-harness/.agents/README.md``.

  4. **Classify.** The resolved path is inside
     the archive namespace (does not start with
     ``..`` or ``/``). The classifier returns
     ``action="skip"`` with
     ``code="archive_symlink_skipped"``. The
     recorded :class:`SkippedSymlink` carries the
     entry path and the normalised target string.

The classifier does **not** check whether the
resolved target actually exists as an archive
entry. The real DeepSeek Harness symlink resolves
to ``deepseek-harness/.agents/README.md`` even
though the project's ``README.md`` lives at the
repo root (``deepseek-harness/README.md``), so the
real symlink is also a "broken" symlink from the
filesystem's point of view. The classifier's
contract is purely about the *literal target
string* and the *archive-namespace safety*; it
does not dereference, does not ``stat``, and does
not require the target to exist.

The ``archive_symlink_target_missing`` code is
raised only for **empty or None** literal target
strings. It is not raised for "target does not
exist as an archive entry" — the classifier never
inspects the archive's other entries.

The test exercises the full ``inspect -> validate ->
extract`` pipeline against the fixture and asserts
the v2.1.3 policy:

  1. The intake succeeds (the archive is not
     rejected).
  2. The ``skipped_symlinks`` collection contains a
     single :class:`SkippedSymlink` with the
     recorded target equal to
     ``deepseek-harness/.agents/README.md`` (the
     deterministic result of the
     ``posixpath.normpath`` calculation above).
  3. The ordinary file (``README.md``) is present
     in the extracted workspace.
  4. The symlink is *not* materialised as a
     filesystem symlink (the validator never
     dereferences it).
  5. The file count and uncompressed size reflect
     only the ordinary file.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.fixtures import build_deepseek_harness_zip


def _read_zip_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_deepseek_harness_intake_continues_with_skipped_symlink(
    tmp_path: Path,
) -> None:
    from app.utils.zip_intake import (
        ArchiveLimits,
        WorkspacePaths,
        intake_zip,
    )

    fixture = build_deepseek_harness_zip()
    archive_bytes = _read_zip_bytes(fixture)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace_key = "deepseek-harness-key"
    paths = WorkspacePaths(
        workspace_dir=workspace_root / workspace_key,
        quarantine_dir=workspace_root / workspace_key / "quarantine",
        contents_dir=workspace_root / workspace_key / "contents",
    )
    paths.ensure()
    limits = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=10_000_000,
        max_file_count=128,
        max_file_bytes=10_000_000,
        max_depth=16,
        suspicious_ratio=100,
    )
    result = intake_zip(paths, source=[archive_bytes], limits=limits)
    # The intake succeeded. The single symbolic link
    # is recorded in ``skipped_symlinks`` with the
    # safe normalised target. The ordinary file
    # contributed to the file count.
    assert len(result.skipped_symlinks) == 1
    skipped = result.skipped_symlinks[0]
    assert skipped.path == ("deepseek-harness/.agents/notes/implemented/CLAUDE.md")
    # Resolution calculation (documented in the
    # module docstring above):
    #   posixpath.dirname("deepseek-harness/.agents/notes/implemented/CLAUDE.md")
    #     = "deepseek-harness/.agents/notes/implemented"
    #   posixpath.join(..., "../../README.md")
    #     = "deepseek-harness/.agents/notes/implemented/../../README.md"
    #   posixpath.normpath(...)
    #     = "deepseek-harness/.agents/README.md"
    # The classifier records this as the safe
    # archive-relative target. The literal
    # ``../../README.md`` is never persisted.
    assert skipped.target == "deepseek-harness/.agents/README.md", (
        "the recorded target must be the posixpath.normpath "
        "of the parent + literal target; the literal target "
        "string is never persisted as the recorded value"
    )
    # The recorded target must stay inside the
    # archive namespace (no leading ``..``, no
    # absolute prefix, no drive letter, no UNC).
    assert not skipped.target.startswith("/"), (
        f"the classifier must reject POSIX-absolute symlink targets; got {skipped.target!r}"
    )
    assert not skipped.target.startswith(".."), (
        "the classifier must reject symlink targets that escape "
        f"the archive root; got {skipped.target!r}"
    )
    assert os.sep not in skipped.target and "\\" not in skipped.target, (
        "the recorded target is archive-relative POSIX; it must "
        "not contain any host-filesystem separators"
    )
    # The file count is one (only the README
    # contributed); the symlink is not counted as a
    # file.
    assert result.file_count == 1
    # The README was extracted; the symlink was not.
    contents = paths.contents_dir
    assert (contents / "deepseek-harness" / "README.md").is_file()
    # The symlink was never materialised. We
    # explicitly check that no path on disk is a
    # symlink in the extracted workspace.
    for candidate in contents.rglob("*"):
        assert not candidate.is_symlink(), (
            f"the intake layer must not materialise filesystem symlinks; found one at {candidate}"
        )


def test_archive_symlink_target_missing_means_empty_target_string() -> None:
    """The ``archive_symlink_target_missing`` code is for empty target
    strings, not for "target does not exist as an archive entry".

    The contract is the literal target string
    contract: the classifier inspects only the
    archive entry's stored target value. An empty
    or None target is rejected with
    ``archive_symlink_target_missing``; a non-empty
    target whose normalised resolution is inside
    the archive namespace is recorded as
    ``archive_symlink_skipped`` even when no entry
    in the archive has that path.
    """
    from app.utils.archive_validation import (
        ArchiveEntry,
        ArchiveLimits,
        ArchiveValidationCollector,
    )

    limits = ArchiveLimits(
        max_compressed_bytes=10_000,
        max_uncompressed_bytes=10_000,
        max_file_count=5,
        max_file_bytes=2_000,
        max_depth=3,
        suspicious_ratio=100,
    )
    # Case 1: empty target string.
    collector = ArchiveValidationCollector(limits)
    collector.check_entry(
        ArchiveEntry(
            name="broken",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="",
        )
    )
    codes = [error.code for error in collector.errors]
    assert codes == ["archive_symlink_target_missing"]

    # Case 2: a safe relative target whose
    # resolved path does NOT exist as an archive
    # entry. The classifier still records-and-
    # skips because the resolved path is inside
    # the archive namespace; the code is
    # ``archive_symlink_skipped`` and never
    # ``archive_symlink_target_missing``.
    collector = ArchiveValidationCollector(limits)
    collector.check_entry(
        ArchiveEntry(
            name="orphan",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="does-not-exist.md",
        )
    )
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    assert collector.skipped_symlinks[0].path == "orphan"
    # The recorded target is the normalised
    # resolution. The literal "does-not-exist.md"
    # joined to the parent "" normalises to
    # "does-not-exist.md" (the parent is "" because
    # the entry is at the archive root).
    assert collector.skipped_symlinks[0].target == "does-not-exist.md"
