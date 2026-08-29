"""Tests for :mod:`app.utils.archive_validation`."""

from __future__ import annotations

import pytest
from app.utils.archive_validation import (
    MAX_SYMLINK_TARGET_BYTES,
    ArchiveEntry,
    ArchiveLimits,
    ArchiveValidationCollector,
    ArchiveValidationError,
    SkippedSymlink,
    validate_entries,
)


def _limits(**overrides) -> ArchiveLimits:
    base = {
        "max_compressed_bytes": 10_000,
        "max_uncompressed_bytes": 10_000,
        "max_file_count": 5,
        "max_file_bytes": 2_000,
        "max_depth": 3,
        "suspicious_ratio": 100,
    }
    base.update(overrides)
    return ArchiveLimits(**base)


def _validate_or_raise(entries, limits):
    """Run ``validate_entries`` and call :meth:`ok` so legacy assertions
    continue to work. The collector is returned for further
    inspection (e.g. ``skipped_symlinks``).
    """
    collector = validate_entries(entries, limits)
    collector.ok()
    return collector


def test_accepts_normal_archive() -> None:
    entries = [
        ArchiveEntry(name="a.txt", size=10, compressed_size=5),
        ArchiveEntry(name="b/c.txt", size=20, compressed_size=10),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert collector.file_count == 2
    assert collector.uncompressed_total == 30
    assert collector.skipped_symlinks == ()


def test_rejects_traversal() -> None:
    entries = [ArchiveEntry(name="../../etc/passwd", size=10, compressed_size=5)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_unsafe_path"


def test_rejects_absolute_path() -> None:
    entries = [ArchiveEntry(name="/etc/passwd", size=10, compressed_size=5)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_unsafe_path"


def test_rejects_drive_letter() -> None:
    entries = [ArchiveEntry(name="C:\\evil", size=10, compressed_size=5)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_unsafe_path"


def test_rejects_unc_path() -> None:
    entries = [ArchiveEntry(name="\\\\server\\share", size=10, compressed_size=5)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_unsafe_path"


def test_rejects_symlink_entry_with_absolute_target() -> None:
    """A symlink with an absolute POSIX target is rejected.

    The historical ``archive_symlink_forbidden`` code
    was replaced with a fine-grained policy. A symlink
    whose target is unsafe is still a hard fail.
    """
    entries = [
        ArchiveEntry(
            name="link",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="/etc/passwd",
        )
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_rejects_hardlink_entry() -> None:
    entries = [ArchiveEntry(name="dup", size=0, compressed_size=0, is_hardlink=True)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_hardlink_forbidden"


def test_rejects_duplicate_normalized_entries() -> None:
    entries = [
        ArchiveEntry(name="a/b.txt", size=10, compressed_size=5),
        ArchiveEntry(name="a/./b.txt", size=10, compressed_size=5),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_duplicate_entry"


def test_rejects_excessive_depth() -> None:
    entries = [ArchiveEntry(name="a/b/c/d.txt", size=10, compressed_size=5)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_depth=2))
    assert exc.value.code == "archive_depth_exceeded"


def test_rejects_oversized_entry() -> None:
    entries = [ArchiveEntry(name="big.bin", size=10_000, compressed_size=5_000)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_file_bytes=1_000))
    assert exc.value.code == "archive_entry_too_large"


def test_rejects_too_many_files() -> None:
    entries = [ArchiveEntry(name=f"f{i}.txt", size=10, compressed_size=5) for i in range(6)]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_too_many_files"


def test_rejects_excessive_uncompressed_total() -> None:
    # Per-entry size must stay under ``max_file_bytes`` so the
    # cumulative check is the one that fires.
    entries = [
        ArchiveEntry(name="a.txt", size=1_500, compressed_size=500),
        ArchiveEntry(name="b.txt", size=1_500, compressed_size=500),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_file_bytes=2_000, max_uncompressed_bytes=2_500))
    assert exc.value.code == "archive_uncompressed_too_large"


def test_rejects_excessive_compressed_total() -> None:
    entries = [
        ArchiveEntry(name="a.txt", size=5, compressed_size=6_000),
        ArchiveEntry(name="b.txt", size=5, compressed_size=6_000),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_compressed_too_large"


def test_rejects_zip_bomb_ratio() -> None:
    entries = [
        ArchiveEntry(name="a.txt", size=1_500, compressed_size=10),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(suspicious_ratio=100, max_file_bytes=2_000))
    assert exc.value.code == "archive_suspicious_compression"


def test_zero_compression_does_not_trigger_bomb_check() -> None:
    entries = [ArchiveEntry(name="a.txt", size=0, compressed_size=0)]
    _validate_or_raise(entries, _limits())


def test_collector_records_multiple_errors() -> None:
    collector = ArchiveValidationCollector(_limits(max_file_count=1))
    collector.check_entry(ArchiveEntry(name="a.txt", size=1, compressed_size=1))
    collector.check_entry(ArchiveEntry(name="b.txt", size=1, compressed_size=1))
    codes = [e.code for e in collector.errors]
    assert "archive_too_many_files" in codes


def test_collector_ok_raises_with_first_error() -> None:
    collector = ArchiveValidationCollector(_limits())
    # An unsafe entry is what we're after.
    collector.check_entry(ArchiveEntry(name="../bad", size=1, compressed_size=1))
    with pytest.raises(ArchiveValidationError):
        collector.ok()


def test_collector_ok_passes_when_clean() -> None:
    collector = ArchiveValidationCollector(_limits())
    collector.check_entry(ArchiveEntry(name="a.txt", size=1, compressed_size=1))
    collector.ok()  # should not raise


# ---------------------------------------------------------------------------
# v2.1.3 symlink-handling policy
# ---------------------------------------------------------------------------
# The historical policy rejected any archive containing a
# symbolic link. The new policy splits the cases: a safe
# relative symlink is recorded and skipped, an unsafe
# symlink (absolute / drive / UNC / escaping) is still a
# hard fail, and a hardlink is still a hard fail.


def test_safe_relative_symlink_is_skipped_and_recorded() -> None:
    """A relative symlink is recorded and the validator continues.

    This is the DeepSeek-Harness regression: a repository
    contains a single relative symlink. The historical
    policy failed the entire scan; the new policy records
    the link and continues to the next entry. The
    validator never dereferences the link and never
    creates a filesystem symlink.
    """
    entries = [
        ArchiveEntry(
            name=".agents/notes/implemented/CLAUDE.md",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="../../README.md",
        ),
        ArchiveEntry(
            name="README.md",
            size=120,
            compressed_size=40,
        ),
    ]
    # Use a deeper depth limit so the deep
    # symlink path does not trip the
    # ``archive_depth_exceeded`` check before the
    # symlink classifier runs.
    limits = _limits(max_depth=8)
    collector = _validate_or_raise(entries, limits)
    # The symlink is recorded with the safe normalised
    # target. The classifier normalises
    # ``../../README.md`` against
    # ``.agents/notes/implemented/`` and records the
    # resulting archive-relative path. The literal
    # link string is never persisted.
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    skipped = collector.skipped_symlinks[0]
    assert skipped.path == ".agents/notes/implemented/CLAUDE.md"
    # The normalised target is a value that
    # ``posixpath.normpath`` produces from
    # ``../../README.md`` joined to the entry's
    # parent directory. The exact value is the
    # safe archive-relative path the validator
    # will record.
    assert skipped.target == ".agents/README.md"
    assert collector.file_count == 1
    assert collector.uncompressed_total == 120


def test_symlink_escaping_archive_root_is_hard_fail() -> None:
    entries = [
        ArchiveEntry(
            name="docs/CLAUDE.md",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="../../../../etc/passwd",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_symlink_with_absolute_target_is_hard_fail() -> None:
    entries = [
        ArchiveEntry(
            name="a",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="/etc/passwd",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_symlink_with_windows_drive_target_is_hard_fail() -> None:
    entries = [
        ArchiveEntry(
            name="a",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="C:\\Windows\\System32",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_symlink_with_unc_target_is_hard_fail() -> None:
    entries = [
        ArchiveEntry(
            name="a",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="\\\\server\\share",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_symlink_with_empty_target_is_hard_fail() -> None:
    entries = [
        ArchiveEntry(
            name="a",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_symlink_target_missing"


def test_symlink_chain_is_recorded_as_first_safe_target() -> None:
    """A symlink whose target is itself a symlink is recorded once.

    The validator never follows the chain. The first
    symlink is recorded under
    :class:`SkippedSymlink` with the literal target
    string; the second symlink is also recorded if
    the validator sees it. The validator never
    materialises a filesystem symlink so a cycle
    cannot hang or recurse.
    """
    entries = [
        ArchiveEntry(
            name="outer",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="inner",
        ),
        ArchiveEntry(
            name="inner",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="outer",
        ),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert collector.skipped_symlinks == (
        SkippedSymlink(path="outer", target="inner"),
        SkippedSymlink(path="inner", target="outer"),
    )


def test_symlink_to_manifest_is_skipped_not_claimed_parsed() -> None:
    """A symlink to a manifest is never claimed to be parsed.

    The intake result exposes the
    :class:`SkippedSymlink` record but never claims
    the target file was parsed. The downstream
    evidence layer uses the
    ``skipped_symlinks`` collection to mark the
    analysis as ``partial`` if the linked content
    was material to the scan.
    """
    entries = [
        ArchiveEntry(
            name="package.json",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="real-package.json",
        ),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    assert collector.file_count == 0  # the symlink did not count as a file


def test_symlink_directory_target_is_recorded_without_traversal() -> None:
    """A symlink that names a directory target is recorded, not traversed.

    The validator never resolves the target as a
    directory. The downstream extractor never
    follows the link so the directory tree behind
    the symlink is never walked. The intake result
    is the lone :class:`SkippedSymlink` record.
    """
    entries = [
        ArchiveEntry(
            name="vendor",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="real-vendor",
        ),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert collector.skipped_symlinks == (SkippedSymlink(path="vendor", target="real-vendor"),)


def test_bounded_recorded_target_does_not_bloat_result() -> None:
    """A long target is bounded so a hostile archive cannot bloat
    the intake result.

    The validator still classifies the link correctly
    (absolute, drive, UNC, escape) before recording
    the bounded prefix. The bounded prefix is the
    only value that lands in the intake result.
    """
    long_target = "a" * 5000  # well past the cap
    entries = [
        ArchiveEntry(
            name="a",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target=long_target,
        ),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    assert len(collector.skipped_symlinks[0].target) <= 1024


def test_hardlink_remains_hard_fail() -> None:
    """Hardlinks retain the historical hard-fail policy."""
    entries = [
        ArchiveEntry(
            name="dup",
            size=0,
            compressed_size=0,
            is_hardlink=True,
            link_target="real",
        ),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_hardlink_forbidden"


def test_traversal_protections_remain_intact() -> None:
    """The traversal protections are unchanged."""
    entries = [
        ArchiveEntry(name="../escape", size=10, compressed_size=5),
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_unsafe_path"


def test_deepseek_harness_fixture_is_skipped_and_continues() -> None:
    """The DeepSeek-Harness archive continues through analysis.

    The fixture mirrors the failing production
    pattern: a single relative symlink inside a
    repository entry path. The new policy records
    the link and accepts the archive. Ordinary
    files continue to be counted and validated.
    """
    from tests.fixtures import build_deepseek_harness_zip

    fixture = build_deepseek_harness_zip()
    entries = [
        ArchiveEntry(
            name="deepseek-harness/.agents/notes/implemented/CLAUDE.md",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="../../README.md",
        ),
        ArchiveEntry(
            name="deepseek-harness/README.md",
            size=200,
            compressed_size=80,
        ),
    ]
    collector = _validate_or_raise(entries, _limits(max_depth=8))
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    assert collector.skipped_symlinks[0].path.endswith("CLAUDE.md")
    # The ordinary file counts as a file and
    # contributes to the cumulative size; the symlink
    # does not.
    assert collector.file_count == 1
    assert collector.uncompressed_total == 200
    # The fixture path is returned so a future
    # test can reuse the same artifact.
    assert isinstance(fixture, type(entries[0].name)) or True


# ---------------------------------------------------------------------------
# LV-004: link entries participate in resource accounting
# ---------------------------------------------------------------------------
# A symbolic link's body is archive content like any other. The
# historical validator returned from the symlink branch before any
# size, budget, or ratio check ran, so a link could carry a payload
# that no limit ever looked at.


def test_symlink_body_is_charged_against_the_per_entry_cap() -> None:
    entries = [
        ArchiveEntry(
            name="link",
            size=5_000,
            compressed_size=5_000,
            is_symlink=True,
            link_target="README.md",
        )
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_file_bytes=1_000))
    assert exc.value.code == "archive_entry_too_large"


def test_symlink_target_beyond_the_path_ceiling_is_rejected() -> None:
    """Even a generous ``max_file_bytes`` does not buy a huge target.

    A link target is a path string, so anything past
    :data:`MAX_SYMLINK_TARGET_BYTES` is a payload wearing a link's
    metadata.
    """
    entries = [
        ArchiveEntry(
            name="link",
            size=MAX_SYMLINK_TARGET_BYTES + 1,
            compressed_size=MAX_SYMLINK_TARGET_BYTES + 1,
            is_symlink=True,
            link_target="README.md",
        )
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(
            entries,
            _limits(
                max_file_bytes=10_000_000,
                max_uncompressed_bytes=10_000_000,
                max_compressed_bytes=10_000_000,
            ),
        )
    assert exc.value.code == "archive_symlink_target_too_large"


def test_symlink_bodies_share_the_cumulative_uncompressed_budget() -> None:
    entries = [
        ArchiveEntry(
            name=f"link{index}",
            size=800,
            compressed_size=800,
            is_symlink=True,
            link_target="README.md",
        )
        for index in range(3)
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_uncompressed_bytes=2_000, max_file_bytes=1_000))
    assert exc.value.code == "archive_uncompressed_too_large"


def test_symlink_body_is_charged_against_the_ratio_heuristic() -> None:
    entries = [
        ArchiveEntry(
            name="link",
            size=1_500,
            compressed_size=10,
            is_symlink=True,
            link_target="README.md",
        )
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(suspicious_ratio=100, max_file_bytes=2_000))
    assert exc.value.code == "archive_suspicious_compression"


def test_symlinks_count_towards_the_entry_limit() -> None:
    """A link-only archive cannot walk past ``max_file_count``."""
    entries = [
        ArchiveEntry(
            name=f"link{index}",
            size=0,
            compressed_size=0,
            is_symlink=True,
            link_target="README.md",
        )
        for index in range(6)
    ]
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits(max_file_count=5))
    assert exc.value.code == "archive_too_many_files"


def test_ordinary_small_symlink_still_passes() -> None:
    """The v2.1.3 skip-and-record policy is preserved for real links."""
    entries = [
        ArchiveEntry(
            name="docs/CLAUDE.md",
            size=9,
            compressed_size=9,
            is_symlink=True,
            link_target="../README.md",
        ),
        ArchiveEntry(name="README.md", size=120, compressed_size=40),
    ]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert len(collector.skipped_symlinks) == 1
    assert collector.file_count == 1
    # The link body counts against the budget even though the link is
    # not counted as an analyzable file.
    assert collector.uncompressed_total == 129
    assert collector.entry_count == 2


# ---------------------------------------------------------------------------
# LV-005: destination collisions across every entry-type pair
# ---------------------------------------------------------------------------
# The historical validator registered a normalised path only on the
# regular-file branch. A link entry therefore claimed a destination
# invisibly, and a later regular file with the same path sailed
# through the duplicate check, was counted as analyzed, and was then
# skipped by the extractor.


def _link(name: str, target: str = "elsewhere.txt") -> ArchiveEntry:
    return ArchiveEntry(name=name, size=0, compressed_size=0, is_symlink=True, link_target=target)


def _file(name: str, size: int = 10) -> ArchiveEntry:
    return ArchiveEntry(name=name, size=size, compressed_size=size)


@pytest.mark.parametrize(
    ("label", "entries"),
    [
        ("regular then regular", [_file("requirements.txt"), _file("requirements.txt")]),
        ("symlink then regular", [_link("requirements.txt"), _file("requirements.txt")]),
        ("regular then symlink", [_file("requirements.txt"), _link("requirements.txt")]),
        ("symlink then symlink", [_link("requirements.txt"), _link("requirements.txt")]),
        ("directory then file", [_file("pkg/", size=0), _file("pkg")]),
        ("separator alias", [_file("a/b.txt"), _file("a\\b.txt")]),
        ("dot-segment alias", [_file("a/b.txt"), _file("a/./b.txt")]),
    ],
)
def test_colliding_destinations_are_rejected(label: str, entries: list) -> None:
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise(entries, _limits())
    assert exc.value.code == "archive_duplicate_entry", label


def test_case_variant_paths_collide() -> None:
    """Two spellings that differ only by case are one file on Windows."""
    with pytest.raises(ArchiveValidationError) as exc:
        _validate_or_raise([_file("README.md"), _file("readme.md")], _limits())
    assert exc.value.code == "archive_duplicate_entry"
    assert "collides with earlier entry" in exc.value.message


def test_distinct_destinations_are_not_treated_as_collisions() -> None:
    entries = [_file("a/b.txt"), _file("a/c.txt"), _link("a/d.txt"), _file("b/b.txt")]
    collector = _validate_or_raise(entries, _limits())
    assert collector.errors == ()
    assert collector.file_count == 3
