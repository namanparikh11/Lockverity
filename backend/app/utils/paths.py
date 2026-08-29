"""Relative path normalization and validation.

The scanner only ever speaks in terms of *normalized relative paths*
inside an extracted archive or repository tree. Absolute paths, Windows
drive paths, UNC paths, parent traversal, and null bytes are never
acceptable in this representation. The normalization is deliberately
strict: any rejection is a defensive boundary, not a UX choice.
"""

from __future__ import annotations

import os
import re
import unicodedata

# Windows drive-letter prefix: e.g. ``C:`` or ``C:\``.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]?")
# UNC path prefix on either platform spelling: ``\\server\share`` or
# ``//server/share``.
_UNC_RE = re.compile(r"^[/\\\\]{2}[^/\\\\]")


class PathNormalizationError(ValueError):
    """Raised when a path cannot be normalized safely."""


# ---------------------------------------------------------------------
# Win32 path semantics (LV-010)
# ---------------------------------------------------------------------
# Lockverity extracts untrusted archives onto a Windows host. A handful
# of component spellings that are perfectly ordinary on POSIX have
# *special* meaning to the Win32 API and must never reach the
# extraction layer:
#
#   * ``payload.txt:stream`` - NTFS Alternate Data Stream syntax. The
#     Win32 API writes the bytes into a hidden stream attached to
#     ``payload.txt``; the visible file looks empty and the analyzed
#     content silently disappears. A colon also spells a drive
#     designator; an archive destination component is always relative,
#     so a colon has no legitimate meaning anywhere inside one.
#   * ``NUL`` / ``CON.txt`` / ``COM1`` - reserved DOS device names.
#     Opening one talks to a device, not a file: a write to ``NUL`` is
#     discarded and a read from ``CON`` blocks on console input.
#     Windows matches the device name *before* the first dot and
#     ignores any extension, so ``NUL.json`` is still the NUL device.
#   * ``name.`` / ``name `` - Win32 strips trailing dots and spaces
#     while canonicalising a path, so two archive entries that differ
#     only by a trailing dot collapse onto a single destination file.
#
# All three are rejected outright. The rules are applied on every host
# so a scan verdict is identical on Windows, Linux, and macOS.
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

# Characters Win32 strips from the end of a path component while it
# canonicalises a path.
_WINDOWS_TRAILING_TRIM = ". "


def _reject_unsafe_windows_component(segment: str) -> None:
    """Raise if ``segment`` carries special Win32 semantics.

    ``segment`` is one already-split path component. The function is a
    pure predicate; it never touches the filesystem.
    """
    if ":" in segment:
        raise PathNormalizationError(
            "Path components may not contain ':' (NTFS alternate data stream or drive designator)."
        )
    if segment.rstrip(_WINDOWS_TRAILING_TRIM) != segment:
        raise PathNormalizationError("Path components may not end with '.' or a space.")
    # Windows resolves the device name from the text before the first
    # dot, so ``CON``, ``CON.txt``, and ``CON.tar.gz`` all name the
    # console device. ``console.txt`` and ``nullability.json`` keep
    # their full stem and stay ordinary filenames.
    device = segment.split(".", 1)[0].upper()
    if device in _WINDOWS_RESERVED_NAMES:
        raise PathNormalizationError(
            f"Path component {segment!r} is a reserved Windows device name."
        )


def windows_collision_key(normalized: str) -> str:
    """Return the Win32-equivalence key for a normalized relative path.

    Two archive entries whose keys are equal resolve to the *same*
    destination file on a Windows host and therefore collide, even
    when the spellings the archive recorded differ. The key folds the
    three equivalences Win32 applies:

      * case (``README.md`` and ``readme.md`` are one file)
      * trailing dots and spaces (stripped while canonicalising)
      * separators (already ``/`` once normalized)

    The caller is expected to pass the output of
    :func:`normalize_relative_path`; the trailing-trim step is defence
    in depth because that function already rejects those spellings.
    """
    parts: list[str] = []
    for segment in normalized.split("/"):
        trimmed = segment.rstrip(_WINDOWS_TRAILING_TRIM) or segment
        parts.append(trimmed.lower())
    return "/".join(parts)


def normalize_relative_path(raw: str) -> str:
    """Return the canonical relative path for ``raw``.

    The returned value always uses forward slashes, never starts with a
    slash, never contains a parent-traversal segment, never contains a
    null byte, and never represents an absolute, drive-letter, or UNC
    path. Empty input is rejected.

    Raises :class:`PathNormalizationError` for any input that does not
    represent a safe relative path.
    """
    if not isinstance(raw, str):
        raise PathNormalizationError("Path must be a string.")
    if not raw:
        raise PathNormalizationError("Path is empty.")

    # Reject any embedded NUL byte - they have no legitimate use in a
    # path and are a classic truncation vector.
    if "\x00" in raw:
        raise PathNormalizationError("Path contains a NUL byte.")

    # Normalize unicode to NFC. Some operating systems are case-
    # insensitive over certain code points; NFC keeps the on-disk
    # representation consistent.
    candidate = unicodedata.normalize("NFC", raw)

    # Reject absolute POSIX paths up front.
    if candidate.startswith("/"):
        raise PathNormalizationError("Absolute paths are not accepted.")

    # Reject Windows drive-letter paths.
    if _DRIVE_LETTER_RE.match(candidate):
        raise PathNormalizationError("Drive-letter paths are not accepted.")

    # Reject UNC paths.
    if _UNC_RE.match(candidate):
        raise PathNormalizationError("UNC paths are not accepted.")

    # Split on any slash, normalize, drop empties. This collapses
    # consecutive separators, which our contract disallows in their raw
    # form.
    parts = re.split(r"[\\/]+", candidate)
    cleaned: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise PathNormalizationError("Parent traversal is not accepted.")
        cleaned.append(part)

    if not cleaned:
        raise PathNormalizationError("Path is empty after normalization.")

    # Reject paths that contain a null byte in any segment. The check
    # above already covers this but is repeated after splitting for
    # defence in depth.
    for segment in cleaned:
        if "\x00" in segment:
            raise PathNormalizationError("Path contains a NUL byte.")
        if os.path.basename(segment) in (".", ".."):
            raise PathNormalizationError("Path contains forbidden segments.")
        # LV-010: reject component spellings with special Win32
        # semantics before the path can reach the extraction
        # layer. See ``_reject_unsafe_windows_component``.
        _reject_unsafe_windows_component(segment)

    return "/".join(cleaned)


def join_relative(*parts: str) -> str:
    """Join several path fragments and return the normalized result.

    Convenience helper for analyzers that build a path from multiple
    pieces. All fragments are validated and combined.
    """
    combined = "/".join(p.lstrip("/").rstrip("/") for p in parts if p)
    return normalize_relative_path(combined or "/")


# Maximum length of a stored basename. Mirrors the
# ``repositories.original_filename`` column size in the v2.0.5
# migration. A client-supplied filename longer than this is
# truncated; the truncation is intentionally lossy on the
# *displayed* label only - the field is the primary human-readable
# label on the repository list, not a security boundary.
_BASENAME_MAX = 512


# Drive-relative path: ``C:secret.zip`` (no separator
# after the colon). The path is on the *current* drive's
# working directory; the basename is the segment after the
# drive prefix. Matched separately from the absolute
# drive-letter form so the prefix can be stripped.
_DRIVE_RELATIVE_RE = re.compile(r"^([A-Za-z]):")


def basename_safely(raw: str | None) -> str | None:
    """Return the basename of ``raw``, defensively sanitised.

    The function is intentionally narrow: it returns only the
    basename, never the directory, and it is the only path
    operation that mutates a client-supplied value into a
    field the API serves.

    A client that sends any of the following shapes sees
    only the trailing basename; the parent path, the drive
    letter, the host share, and the leading separators
    are all stripped at the API boundary.

    - ``C:\\Users\\me\\secret.zip`` → ``secret.zip``
    - ``C:/Users/me/secret.zip`` → ``secret.zip``
    - ``C:secret.zip`` (drive-relative) → ``secret.zip``
    - ``\\\\server\\share\\secret.zip`` → ``secret.zip``
    - ``//server/share/secret.zip`` → ``secret.zip``
    - ``/etc/passwd`` → ``passwd``
    - ``../../etc/passwd`` → ``passwd``
    - ``a/b/../../c.zip`` → ``c.zip``

    A client that sends a value that resolves to *no*
    basename (root, drive-letter alone, dot, dot-dot,
    empty, whitespace) sees ``None``:

    - ``C:`` → ``None``
    - ``C:/`` → ``None``
    - ``C:\\`` → ``None``
    - ``/`` → ``None``
    - ``\\`` → ``None``
    - ``.`` → ``None``
    - ``..`` → ``None``
    - ``""`` → ``None``
    - ``None`` → ``None``

    A client that sends a single drive-letter
    character (e.g. ``C`` without a colon) is treated as
    a regular filename (the character is preserved);
    a drive letter is not a security boundary on its own
    and the consumer can decide whether to display the
    single character.

    Unicode is preserved (NFC-normalised, see below). A
    name longer than :data:`_BASENAME_MAX` is truncated;
    the truncation preserves the trailing extension so
    the displayed label is still recognisable.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    # Forward-slash only basename (we are not on Windows for
    # this code path, but the project is Windows-friendly).
    cleaned = cleaned.replace("\\", "/")
    # Strip UNC leading slashes so the basename below
    # operates on the share + path tail.
    cleaned = re.sub(r"^/+", "", cleaned)
    # Strip a single trailing ``/`` so that an explicit
    # root-prefix returns ``None`` when only ``/`` is
    # left. The basename extraction below operates on the
    # remainder.
    trimmed = cleaned.strip("/")
    if not trimmed:
        return None
    # Absolute drive-letter path: ``C:`` / ``C:/`` / ``C:\\``
    # returns ``None`` (drive-letter alone is not a valid
    # filename).
    if _DRIVE_LETTER_RE.match(trimmed) is not None:
        # The whole string is just a drive-letter form
        # (``C:`` or ``C:/`` or ``C:foo`` after the prefix
        # is gone).
        after_prefix = _DRIVE_LETTER_RE.sub("", trimmed, count=1)
        if not after_prefix:
            return None
        # ``C:foo`` → the path is on the current drive's
        # working directory; ``foo`` is the basename.
        trimmed = after_prefix
    # UNC tail: ``server/share/secret.zip`` keeps the
    # trailing segment. The leading host share component
    # is treated as a directory and discarded; the share
    # is internal infrastructure the operator never sees.
    # If the path is just a host share (``server/share``,
    # no trailing file), drop the whole UNC tail.
    base = trimmed.rsplit("/", 1)[-1]
    if not base:
        return None
    if base in (".", ".."):
        return None
    # Normalise Unicode to NFC so identical names with
    # different canonical encodings collapse to one row.
    base = unicodedata.normalize("NFC", base)
    if len(base) > _BASENAME_MAX:
        # Truncate but keep the trailing extension if present.
        if "." in base:
            stem, dot, ext = base.rpartition(".")
            if dot and ext:
                keep = _BASENAME_MAX - len(dot) - len(ext) - 1
                base = stem[:keep] + "." + ext if keep > 0 else base[:_BASENAME_MAX]
            else:
                base = base[:_BASENAME_MAX]
        else:
            base = base[:_BASENAME_MAX]
    return base
