"""Tests for :mod:`app.utils.paths`."""

from __future__ import annotations

import pytest
from app.utils.paths import (
    PathNormalizationError,
    join_relative,
    normalize_relative_path,
    windows_collision_key,
)


def test_basic_normalization() -> None:
    assert normalize_relative_path("src/lib/index.js") == "src/lib/index.js"


def test_collapses_separators() -> None:
    assert normalize_relative_path("src//lib///index.js") == "src/lib/index.js"


def test_rejects_absolute_unix() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("/etc/passwd")


def test_rejects_drive_letter() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("C:\\evil")
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("C:evil")


def test_rejects_drive_letter_with_forward_slash() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("D:/malicious")


def test_rejects_unc_path() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("\\\\server\\share")
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("//server/share")


def test_rejects_parent_traversal() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("../etc/passwd")
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("src/../../etc/passwd")


def test_rejects_null_bytes() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("src/\x00evil")


def test_rejects_empty() -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("")
    with pytest.raises(PathNormalizationError):
        normalize_relative_path("///")


def test_normalizes_unicode() -> None:
    # No-op for ASCII, but the call should not raise.
    assert normalize_relative_path("docs/notice.txt") == "docs/notice.txt"


def test_join_relative_combines_fragments() -> None:
    assert join_relative("src", "lib", "index.js") == "src/lib/index.js"


def test_join_relative_rejects_traversal() -> None:
    with pytest.raises(PathNormalizationError):
        join_relative("src", "..", "etc")


# ---------------------------------------------------------------------------
# LV-010: Win32 path semantics
# ---------------------------------------------------------------------------
# Lockverity extracts untrusted archives onto a Windows host, where a
# handful of component spellings that are ordinary on POSIX are not
# ordinary filenames at all. Each of these used to normalize cleanly
# and reach the extraction layer.


@pytest.mark.parametrize(
    "raw",
    [
        "payload.txt:stream",
        "dir/payload.txt:stream",
        "NUL",
        "nul.txt",
        "CON.txt",
        "CON",
        "COM1",
        "LPT9.log",
        "prn",
        "aux.json",
        "COM1.lock",
        "name.",
        "name ",
        "dir/name./file.txt",
        "trailing /file.txt",
    ],
)
def test_rejects_unsafe_windows_component(raw: str) -> None:
    with pytest.raises(PathNormalizationError):
        normalize_relative_path(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "console.txt",
        "nullability.json",
        "com10.txt",
        "lpt10.log",
        "auxiliary.py",
        "prn_helper.py",
        "connection/config.yml",
        "src/components/Nullable.tsx",
        ".gitignore",
        "COMPOSER.json",
    ],
)
def test_allows_names_that_only_look_reserved(raw: str) -> None:
    """A reserved *device* name is the whole stem, not a prefix."""
    assert normalize_relative_path(raw) == raw


def test_windows_collision_key_folds_case() -> None:
    assert windows_collision_key("README.md") == windows_collision_key("readme.md")
    assert windows_collision_key("Src/Lib/Index.JS") == "src/lib/index.js"


def test_windows_collision_key_keeps_distinct_paths_distinct() -> None:
    assert windows_collision_key("a/b.txt") != windows_collision_key("a/c.txt")
    assert windows_collision_key("a/b.txt") != windows_collision_key("b/a.txt")


def test_windows_collision_key_folds_trailing_dot_and_space() -> None:
    """Win32 strips these while canonicalising, so the key must too.

    ``normalize_relative_path`` rejects both spellings outright, so
    this is the defence-in-depth half of the rule: any caller that
    reaches the key function with such a component still gets the
    same key as the trimmed spelling.
    """
    assert windows_collision_key("name.") == windows_collision_key("name")
    assert windows_collision_key("name ") == windows_collision_key("name")


def test_traversal_protection_is_unchanged_by_windows_rules() -> None:
    """The new component rules do not displace the older checks."""
    for raw in ("../etc/passwd", "a/../../b", "/etc/passwd", "C:\\evil", "//server/share"):
        with pytest.raises(PathNormalizationError):
            normalize_relative_path(raw)
