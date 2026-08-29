"""Safe ZIP intake and extraction.

The intake pipeline is:

1. Stream the request body to a quarantine file under the
   workspace's quarantine directory, computing the SHA-256 on the
   fly. If the stream exceeds the configured compressed-byte
   cap, the partial quarantine file is deleted and an
   :class:`ArchiveValidationError` is raised.
2. Preflight the ZIP end-of-central-directory metadata (classic
   and ZIP64) and reject an archive that declares more entries
   than the configured limit *before* any inventory is built.
3. Inspect the central directory with the standard library
   ``zipfile`` module. Build a list of :class:`ArchiveEntry`
   records. The inspection is bounded: the only body we ever
   read is a symbolic link's target, and that read is capped.
4. Feed the records to :func:`app.utils.archive_validation
   .validate_entries`. If any entry fails, delete the
   quarantine and return the first error.
5. Open the archive and extract every validated entry into the
   workspace's ``contents`` directory. Re-validate the
   destination path on each iteration. Refuse to overwrite an
   existing file.

The implementation deliberately uses only the standard library;
no extra dependencies are introduced. Nothing in this module
executes, imports, or interprets repository content: archives are
inspected as metadata and copied as bytes.

A :class:`ZipIntakeError` is raised for any failure that
prevents the archive from being accepted. The on-disk state is
always cleaned up on failure.
"""

from __future__ import annotations

import gzip
import io
import os
import secrets
import shutil
import struct
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.utils.archive_validation import (
    MAX_SYMLINK_TARGET_BYTES,
    ArchiveEntry,
    ArchiveLimits,
    ArchiveValidationCollector,
    ArchiveValidationError,
    SkippedSymlink,
    validate_entries,
)
from app.utils.hashing import DEFAULT_CHUNK_SIZE
from app.utils.paths import (
    PathNormalizationError,
    normalize_relative_path,
    windows_collision_key,
)

# ZipInfo constants that we treat as indicating a symlink or
# hardlink on POSIX systems. Windows-created zips do not encode
# symlinks in the same way; this is the only portable signal.
_S_IFLNK = 0o120000
_S_IFMT = 0o170000

# Windows ``MAX_PATH`` (260) is the legacy ceiling for the
# Win32 ANSI file API. The wide (UTF-16) API accepts paths
# longer than 260 characters when the caller opts in with
# the Windows long-path prefix (a literal ``\\?\`` at the
# start of the path). A long-named repository plus a deep
# workspace tree (e.g. ``<home>\\var\\workspace\\
# workspaces\\<key>\\contents\\<repo>-<sha>\\<deeper>\\<file>``)
# can exceed 260 characters; the ``Path.open("wb")`` call
# on such a path raises ``FileNotFoundError`` on Windows
# even when the parent directory exists and is writable.
# The v2.1.1 hotfix detects the over-limit path and writes
# through the long-path prefix so the extraction is
# invariant of the operator's home directory depth. The
# prefix is a no-op on POSIX.
_LONG_PATH_PREFIX = "\\\\?\\"
_WINDOWS_MAX_PATH = 260

# ---------------------------------------------------------------------
# ZIP end-of-central-directory structures (LV-006)
# ---------------------------------------------------------------------
# ``zipfile.ZipFile.__init__`` reads the whole central directory and
# builds one ``ZipInfo`` object per member before any caller-supplied
# limit can be consulted. A compact ZIP64 archive that declares
# millions of members therefore costs hundreds of megabytes of Python
# objects *before* the archive is rejected. The preflight below reads
# only the fixed-size trailer records, so the entry count is known
# before a single ``ZipInfo`` exists.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56
_CENTRAL_HEADER_SIGNATURE = b"PK\x01\x02"
# A central-directory file header is 46 fixed bytes plus the variable
# name, extra, and comment fields, so 46 bytes is the floor for one
# member. The floor turns the recorded directory size into a hard
# ceiling on how many members the archive can possibly contain.
_CENTRAL_HEADER_SIZE = 46
# A ZIP comment is a 16-bit length, so the EOCD record starts at most
# 22 + 65535 bytes from the end of the file.
_MAX_ZIP_COMMENT = 0xFFFF
# Sentinel values that say "the real value lives in the ZIP64 record".
_ZIP64_MARKER_16 = 0xFFFF
_ZIP64_MARKER_32 = 0xFFFFFFFF


def _open_for_write(path: Path):
    r"""Return a writable file handle for ``path``, supporting long Windows paths.

    On Windows the ``Path.open("wb")`` call uses the Win32
    ANSI file API which is bounded by ``MAX_PATH`` (260).
    When the resolved path is longer than that, the call
    fails with ``FileNotFoundError`` even when the parent
    directory exists. The Windows wide file API accepts
    paths longer than 260 characters when the caller uses
    the Windows long-path prefix (a literal backslash
    backslash question mark backslash at the start of the
    path); the same approach works in Python by passing the
    prefixed string to :func:`builtins.open`. The prefix is
    rejected on POSIX so this helper falls through to the
    default ``Path.open`` on every non-Windows host.
    """
    if sys.platform != "win32":
        return path.open("wb")
    text = str(path)
    if len(text) < _WINDOWS_MAX_PATH:
        return path.open("wb")
    return open(_LONG_PATH_PREFIX + text, "wb")


def _mkdir_parents(path: Path) -> None:
    """Create the parent directory of ``path`` (and all missing ancestors).

    On Windows the ``Path.mkdir(parents=True, exist_ok=True)``
    call fails for the same ``MAX_PATH`` reason as the
    ``Path.open`` call above. This helper retries the
    long-path-prefixed form on the same condition.
    """
    parent = path.parent
    if parent.exists():
        return
    if sys.platform != "win32":
        parent.mkdir(parents=True, exist_ok=True)
        return
    text = str(parent)
    if len(text) < _WINDOWS_MAX_PATH:
        parent.mkdir(parents=True, exist_ok=True)
        return
    # The long-path prefix is documented for ``CreateDirectoryW``
    # as well. We bypass the strict-prefix check by passing the
    # prefixed string through ``os.makedirs``; the function
    # supports arbitrary paths when the prefix is supplied.
    os.makedirs(_LONG_PATH_PREFIX + text, exist_ok=True)


class ZipIntakeError(Exception):
    """Raised when a ZIP archive cannot be safely ingested."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True, slots=True)
class ZipIntakeResult:
    """The result of a successful ZIP intake."""

    archive_path: Path
    archive_sha256: str
    archive_size: int
    file_count: int
    uncompressed_size: int
    contents_dir: Path
    # The list of symbolic links the validator
    # intentionally skipped for safety. The list is
    # empty for archives that contain no symbolic
    # links. The scan evidence layer uses the list
    # to mark the analysis ``partial`` when the
    # omitted content materially affects coverage.
    skipped_symlinks: tuple[SkippedSymlink, ...] = ()


@dataclass(frozen=True, slots=True)
class ZipDirectoryPreflight:
    """What the ZIP trailer records declare about the central directory."""

    entry_count: int
    directory_size: int
    directory_offset: int
    is_zip64: bool


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """The on-disk layout for one workspace."""

    workspace_dir: Path
    quarantine_dir: Path
    contents_dir: Path

    def ensure(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.contents_dir.mkdir(parents=True, exist_ok=True)


def create_workspace_paths(root: Path, workspace_key: str) -> WorkspacePaths:
    """Build the on-disk layout for a new workspace."""
    if not workspace_key or len(workspace_key) < 16:
        raise ValueError("workspace_key must be at least 16 characters long.")
    base = root / "workspaces" / workspace_key
    return WorkspacePaths(
        workspace_dir=base,
        quarantine_dir=base / "quarantine",
        contents_dir=base / "contents",
    )


def new_workspace_key() -> str:
    """Return a fresh, unguessable workspace key."""
    return secrets.token_urlsafe(24)


def quarantine_archive(
    paths: WorkspacePaths,
    *,
    source: Callable[[int], bytes] | Iterable[bytes],
    limits: ArchiveLimits,
) -> tuple[Path, str, int]:
    """Stream ``source`` into the quarantine directory.

    ``source`` is either an iterable of byte chunks (any object
    that yields ``bytes`` when iterated) or a callable that
    returns a single chunk (used to fetch a fixed size from a
    buffer or ``SpooledTemporaryFile.read``). The total bytes
    written are bounded by ``limits.max_compressed_bytes``; the
    function raises :class:`ZipIntakeError` if the cap is
    exceeded.

    Returns ``(archive_path, sha256_hex, size)`` on success.
    """
    paths.ensure()
    archive_path = paths.quarantine_dir / "archive.bin"
    sha256_path = paths.quarantine_dir / "archive.sha256"
    if archive_path.exists():
        archive_path.unlink()
    if sha256_path.exists():
        sha256_path.unlink()

    import hashlib

    digest = hashlib.sha256()
    size = 0
    try:
        with archive_path.open("wb") as fh:
            if callable(source):
                chunk = source(DEFAULT_CHUNK_SIZE)
                while chunk:
                    size += len(chunk)
                    if size > limits.max_compressed_bytes:
                        raise ZipIntakeError(
                            "archive_compressed_too_large",
                            f"Archive exceeds max_compressed_bytes={limits.max_compressed_bytes}.",
                        )
                    digest.update(chunk)
                    fh.write(chunk)
                    chunk = source(DEFAULT_CHUNK_SIZE)
            else:
                for chunk in source:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limits.max_compressed_bytes:
                        raise ZipIntakeError(
                            "archive_compressed_too_large",
                            f"Archive exceeds max_compressed_bytes={limits.max_compressed_bytes}.",
                        )
                    digest.update(chunk)
                    fh.write(chunk)
    except ZipIntakeError:
        archive_path.unlink(missing_ok=True)
        raise

    sha_hex = digest.hexdigest()
    sha256_path.write_text(sha_hex + "\n", encoding="ascii")
    return archive_path, sha_hex, size


# ---------------------------------------------------------------------
# ZIP structural preflight (LV-006)
# ---------------------------------------------------------------------


def _find_eocd(tail: bytes) -> int:
    """Return the offset of the EOCD record inside ``tail``, or ``-1``.

    The signature can also occur inside the archive comment or inside
    compressed data, so a candidate is accepted only when its own
    comment-length field accounts for exactly the bytes that follow
    it. The search runs backwards, which is what the format
    prescribes.
    """
    index = tail.rfind(_EOCD_SIGNATURE)
    while index != -1:
        if index + _EOCD_SIZE <= len(tail):
            comment_length = int.from_bytes(tail[index + 20 : index + 22], "little")
            if index + _EOCD_SIZE + comment_length == len(tail):
                return index
        index = tail.rfind(_EOCD_SIGNATURE, 0, index)
    return -1


def _read_zip64_directory(fh, eocd_offset: int, file_size: int) -> tuple[int, int, int] | None:
    """Return ``(entry_count, directory_size, directory_offset)`` from ZIP64.

    Returns ``None`` when no ZIP64 locator precedes the EOCD record,
    which is the legitimate case for a classic archive holding
    exactly ``0xFFFF`` members. A locator that *is* present but points
    outside the file, or a record that does not carry the ZIP64
    signature, is malformed and raises.
    """
    locator_offset = eocd_offset - _ZIP64_LOCATOR_SIZE
    if locator_offset < 0:
        return None
    fh.seek(locator_offset)
    locator = fh.read(_ZIP64_LOCATOR_SIZE)
    if len(locator) != _ZIP64_LOCATOR_SIZE or not locator.startswith(_ZIP64_LOCATOR_SIGNATURE):
        return None
    _disk, record_offset, _total_disks = struct.unpack("<IQI", locator[4:_ZIP64_LOCATOR_SIZE])
    if record_offset < 0 or record_offset + _ZIP64_EOCD_SIZE > file_size:
        raise ZipIntakeError(
            "archive_invalid",
            "ZIP64 end-of-central-directory record lies outside the archive.",
        )
    fh.seek(record_offset)
    record = fh.read(_ZIP64_EOCD_SIZE)
    if len(record) != _ZIP64_EOCD_SIZE or not record.startswith(_ZIP64_EOCD_SIGNATURE):
        raise ZipIntakeError(
            "archive_invalid",
            "ZIP64 end-of-central-directory record is malformed.",
        )
    (
        _record_size,
        _version_made_by,
        _version_needed,
        _disk_number,
        _directory_disk,
        _entries_this_disk,
        entry_count,
        directory_size,
        directory_offset,
    ) = struct.unpack("<QHHIIQQQQ", record[4:_ZIP64_EOCD_SIZE])
    return entry_count, directory_size, directory_offset


def _count_central_directory_records(fh, *, start: int, size: int, ceiling: int) -> int:
    """Walk the central directory, counting members, stopping past ``ceiling``.

    The walk reads one 46-byte fixed header at a time and seeks over
    the variable-length fields; it allocates nothing per member and
    performs at most ``ceiling + 1`` iterations. It exists for the
    archive whose recorded directory is small enough to pass the
    declared-count check yet large enough to hold far more members
    than the configured limit.
    """
    end = start + size
    offset = start
    count = 0
    while offset < end:
        fh.seek(offset)
        header = fh.read(_CENTRAL_HEADER_SIZE)
        if len(header) != _CENTRAL_HEADER_SIZE or not header.startswith(_CENTRAL_HEADER_SIGNATURE):
            raise ZipIntakeError(
                "archive_invalid",
                "ZIP central directory is malformed.",
            )
        name_length, extra_length, comment_length = struct.unpack("<HHH", header[28:34])
        offset += _CENTRAL_HEADER_SIZE + name_length + extra_length + comment_length
        count += 1
        if count > ceiling:
            return count
    return count


def preflight_zip_directory(archive_path: Path, *, limits: ArchiveLimits) -> ZipDirectoryPreflight:
    """Validate the ZIP trailer records before any inventory is built.

    The function reads the classic end-of-central-directory record
    and, when the record's sentinel fields say so, the ZIP64 locator
    and ZIP64 record behind it. It then checks, in order:

    1. the recorded directory lies inside the file;
    2. the recorded member count is within ``max_file_count``;
    3. the recorded member count fits in the recorded directory
       (46 bytes is the floor for one central-directory header),
       which catches metadata that overstates the count;
    4. for a directory large enough to hold more members than the
       limit, the real member count, obtained by an allocation-free
       walk that stops one past the limit, which catches metadata
       that understates it.

    Every check happens before ``zipfile.ZipFile`` is constructed, so
    an over-limit archive never materialises a single ``ZipInfo``.
    """
    file_size = archive_path.stat().st_size
    if file_size < _EOCD_SIZE:
        raise ZipIntakeError(
            "archive_invalid",
            "Archive is too small to contain a ZIP end-of-central-directory record.",
        )
    tail_length = min(file_size, _EOCD_SIZE + _MAX_ZIP_COMMENT)
    with archive_path.open("rb") as fh:
        fh.seek(file_size - tail_length)
        tail = fh.read(tail_length)
        index = _find_eocd(tail)
        if index == -1:
            raise ZipIntakeError(
                "archive_invalid",
                "ZIP end-of-central-directory record not found.",
            )
        eocd_offset = file_size - tail_length + index
        (
            disk_number,
            directory_disk,
            _entries_this_disk,
            entry_count,
            directory_size,
            directory_offset,
        ) = struct.unpack("<HHHHII", tail[index + 4 : index + 20])

        is_zip64 = False
        if _ZIP64_MARKER_16 in (disk_number, directory_disk, entry_count) or (
            _ZIP64_MARKER_32 in (directory_size, directory_offset)
        ):
            zip64 = _read_zip64_directory(fh, eocd_offset, file_size)
            if zip64 is not None:
                entry_count, directory_size, directory_offset = zip64
                is_zip64 = True

        # 1. Structural bounds. A directory that claims to start or
        # end outside the file cannot be walked safely.
        if directory_size > file_size or directory_offset > file_size:
            raise ZipIntakeError(
                "archive_invalid",
                "ZIP central directory lies outside the archive.",
            )
        if directory_offset + directory_size > file_size:
            raise ZipIntakeError(
                "archive_invalid",
                "ZIP central directory extends past the end of the archive.",
            )

        # 2. The declared count. This is the cheap rejection: no part
        # of the directory has been read yet, and it runs ahead of the
        # structural cross-check so an archive that declares millions
        # of members is reported as what it is rather than as generic
        # corruption.
        if entry_count > limits.max_file_count:
            raise ZipIntakeError(
                "archive_too_many_files",
                f"Archive declares {entry_count} entries; max_file_count={limits.max_file_count}.",
            )

        # 3. Cross-check the declared count against the space the
        # directory actually occupies, so metadata that overstates the
        # count within the limit is still caught.
        if entry_count * _CENTRAL_HEADER_SIZE > directory_size:
            raise ZipIntakeError(
                "archive_invalid",
                f"ZIP declares {entry_count} entries but its central directory "
                f"holds only {directory_size} bytes.",
            )

        # 4. The real count, but only when the recorded directory is
        # roomy enough for the declared count to be an understatement.
        # A normal archive skips the walk entirely.
        if directory_size // _CENTRAL_HEADER_SIZE > limits.max_file_count:
            # ``zipfile`` tolerates data prepended to the archive (a
            # self-extracting stub) by shifting every recorded offset;
            # mirror that so the walk starts at the real directory.
            concat = eocd_offset - directory_size - directory_offset
            walk_start = directory_offset + max(0, concat)
            real_count = _count_central_directory_records(
                fh,
                start=walk_start,
                size=directory_size,
                ceiling=limits.max_file_count,
            )
            if real_count > limits.max_file_count:
                raise ZipIntakeError(
                    "archive_too_many_files",
                    f"Archive holds more than {limits.max_file_count} entries.",
                )

    return ZipDirectoryPreflight(
        entry_count=entry_count,
        directory_size=directory_size,
        directory_offset=directory_offset,
        is_zip64=is_zip64,
    )


def _read_symlink_target(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, *, limits: ArchiveLimits
) -> str | None:
    """Return the link target recorded for ``info`` under a hard cap.

    A ZIP stores a symbolic link's target as the entry's data, so the
    target has to be read to be recorded. The historical code read it
    with an unbounded ``handle.read()``, which let a link entry carry
    an arbitrarily large body past every size and ratio limit
    (LV-004). The read is now gated twice:

      * the *declared* metadata is checked first, so a link that
        announces a large or highly compressed body is rejected
        without decompressing a byte;
      * the read itself is capped at one byte past the target
        ceiling, so metadata that lies about the body still cannot
        pull more than the cap into memory.
    """
    ceiling = min(MAX_SYMLINK_TARGET_BYTES, limits.max_file_bytes)
    if info.file_size > ceiling:
        raise ZipIntakeError(
            "archive_symlink_target_too_large",
            f"Symbolic link {info.filename!r} declares a {info.file_size}-byte "
            f"target; max is {ceiling}.",
        )
    if (
        info.compress_size > 0
        and info.file_size > 0
        and info.file_size >= limits.suspicious_ratio * info.compress_size
    ):
        raise ZipIntakeError(
            "archive_suspicious_compression",
            f"Symbolic link {info.filename!r} compresses "
            f"{info.file_size // info.compress_size}x; ratio above "
            f"{limits.suspicious_ratio}x is suspicious.",
        )
    try:
        with zf.open(info, "r") as handle:
            raw = handle.read(ceiling + 1)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    if len(raw) > ceiling:
        raise ZipIntakeError(
            "archive_symlink_target_too_large",
            f"Symbolic link {info.filename!r} has a target longer than {ceiling} bytes.",
        )
    return raw.decode("utf-8", errors="replace") or None


def inspect_zip_entries(archive_path: Path, *, limits: ArchiveLimits) -> list[ArchiveEntry]:
    """Open ``archive_path`` and return one :class:`ArchiveEntry` per member.

    The function does not extract the archive; it reads the central
    directory and, for symbolic links only, the bounded link target.
    A :class:`ZipIntakeError` is raised if the central directory is
    missing, unreadable, or declares more members than ``limits``
    allows.
    """
    preflight_zip_directory(archive_path, limits=limits)
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            entries: list[ArchiveEntry] = []
            # ``infolist()`` returns the inventory ``ZipFile`` already
            # holds; we derive exactly one neutral record per member
            # and never copy the inventory again. The preflight has
            # already bounded how many members can exist, and the
            # guard below re-asserts it against the inventory itself
            # in case the trailer records disagree with the directory.
            for info in zf.infolist():
                if len(entries) >= limits.max_file_count:
                    raise ZipIntakeError(
                        "archive_too_many_files",
                        f"Archive exceeds max_file_count={limits.max_file_count}.",
                    )
                # Detect symlinks: zipfile stores unix mode in
                # ``external_attr`` shifted 16 bits. We only need
                # to know the file type; the entry is rejected
                # by the validator either way.
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                is_symlink = (unix_mode & _S_IFMT) == _S_IFLNK
                is_dir = info.is_dir()
                # v2.1.3: capture the literal link
                # target for symlink entries. The ZIP
                # format stores the target as the
                # entry's data, so we read it from the
                # handle. The validator never
                # dereferences the link; the target is
                # only inspected as a string.
                link_target: str | None = None
                if is_symlink and not is_dir:
                    link_target = _read_symlink_target(zf, info, limits=limits)
                if is_dir:
                    # Directories are not counted as files. The
                    # path is still validated for safety.
                    entries.append(
                        ArchiveEntry(
                            name=info.filename,
                            size=0,
                            compressed_size=0,
                            is_symlink=is_symlink,
                        )
                    )
                    continue
                entries.append(
                    ArchiveEntry(
                        name=info.filename,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        is_symlink=is_symlink,
                        link_target=link_target,
                    )
                )
            return entries
    except zipfile.BadZipFile as exc:
        raise ZipIntakeError(
            "archive_invalid",
            f"Archive is not a valid ZIP file: {exc}",
        ) from exc


def validate_zip(entries: list[ArchiveEntry], limits: ArchiveLimits) -> ArchiveValidationCollector:
    """Run the validator and surface both errors and skipped symlinks.

    The function returns the populated
    :class:`ArchiveValidationCollector` so the
    caller can read the ``skipped_symlinks``
    collection and persist it through the intake
    result. The function raises a
    :class:`ZipIntakeError` with the first
    :class:`ArchiveValidationError`'s code so the
    historical hard-fail semantics are preserved.
    Skipped symlinks are *not* an error.
    """
    collector = validate_entries(entries, limits)
    try:
        collector.ok()
    except ArchiveValidationError as exc:
        # Surface the same code under a
        # :class:`ZipIntakeError` so callers can
        # handle intake failures uniformly.
        raise ZipIntakeError(exc.code, exc.message) from exc
    return collector


def extract_zip(
    archive_path: Path,
    paths: WorkspacePaths,
    entries: list[ArchiveEntry],
    *,
    limits: ArchiveLimits,
) -> tuple[int, int]:
    """Extract ``archive_path`` into ``paths.contents_dir``.

    The function re-validates every destination path while
    extracting and refuses to overwrite an existing file. The
    running totals (file count, uncompressed size) are kept in
    sync with ``limits`` so extraction stops as soon as the
    configured cap is hit.

    Returns ``(file_count, uncompressed_total)`` for the files that
    were actually written, so the intake result reports what analysis
    can really see rather than what the archive claimed.
    """
    paths.ensure()
    if not paths.contents_dir.exists():
        paths.contents_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    file_count = 0
    uncompressed_total = 0
    try:
        # The validator recorded-and-skipped these link entries; the
        # extractor never materialises them. Precomputing the set
        # keeps the loop linear (the historical code rescanned every
        # entry for every member).
        skipped_link_paths = {
            windows_collision_key(normalize_relative_path(entry.name))
            for entry in entries
            if entry.is_symlink
        }
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename
                try:
                    normalized = normalize_relative_path(name)
                except PathNormalizationError as exc:
                    raise ZipIntakeError("archive_unsafe_path", str(exc)) from exc
                key = windows_collision_key(normalized)
                if info.is_dir():
                    target = paths.contents_dir / normalized.replace("/", os.sep)
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                # v2.1.3: the validator may have
                # recorded-and-skipped a symbolic
                # link. The extractor never materialises
                # the link; it simply moves on. Hardlinks
                # are not present here because the
                # validator rejected them with
                # ``archive_hardlink_forbidden``.
                if key in skipped_link_paths:
                    continue
                if key in seen:
                    raise ZipIntakeError(
                        "archive_duplicate_entry",
                        f"Duplicate normalized path {normalized!r}.",
                    )
                seen.add(key)
                dest = paths.contents_dir / normalized.replace("/", os.sep)
                # Re-validate destination. The dest must stay
                # under ``contents_dir``.
                try:
                    dest_resolved = dest.resolve(strict=False)
                    contents_resolved = paths.contents_dir.resolve(strict=False)
                except OSError as exc:
                    raise ZipIntakeError(
                        "archive_path_resolve_failed",
                        f"Could not resolve destination path: {exc}",
                    ) from exc
                if not _is_within(dest_resolved, contents_resolved):
                    raise ZipIntakeError(
                        "archive_path_escape",
                        f"Destination {dest_resolved} is outside the workspace contents.",
                    )
                # v2.1.1: ``_mkdir_parents`` retries the call through
                # the long-path prefix on Windows when the
                # resolved path exceeds ``MAX_PATH`` (260). Same
                # rationale as in the tarball path above.
                _mkdir_parents(dest)
                if dest.exists() or dest.is_symlink():
                    raise ZipIntakeError(
                        "archive_overwrite_forbidden",
                        f"Destination {dest} already exists.",
                    )
                # Stream-extract with a hard cap on bytes read.
                file_count += 1
                if file_count > limits.max_file_count:
                    raise ZipIntakeError(
                        "archive_too_many_files",
                        f"Archive exceeds max_file_count={limits.max_file_count}.",
                    )
                if info.file_size > limits.max_file_bytes:
                    raise ZipIntakeError(
                        "archive_entry_too_large",
                        f"Entry {normalized!r} is {info.file_size} bytes; "
                        f"max is {limits.max_file_bytes}.",
                    )
                uncompressed_total += info.file_size
                if uncompressed_total > limits.max_uncompressed_bytes:
                    raise ZipIntakeError(
                        "archive_uncompressed_too_large",
                        "Cumulative uncompressed size exceeds limit.",
                    )
                with zf.open(info, "r") as src, _open_for_write(dest) as out:
                    _copy_capped(src, out, max_bytes=info.file_size + 1)
                # Update mtime / perms to match the archive entry
                # when reasonable.
                try:
                    mode = (info.external_attr >> 16) & 0o7777
                    if mode:
                        dest.chmod(mode)
                except OSError:  # pragma: no cover - non-fatal
                    pass
    except ZipIntakeError:
        # Clean up the partial contents directory.
        if paths.contents_dir.exists():
            shutil.rmtree(paths.contents_dir, ignore_errors=True)
            paths.contents_dir.mkdir(parents=True, exist_ok=True)
        raise
    return file_count, uncompressed_total


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _copy_capped(src: io.BufferedIOBase, dst: io.BufferedIOBase, *, max_bytes: int) -> int:
    written = 0
    while True:
        chunk = src.read(DEFAULT_CHUNK_SIZE)
        if not chunk:
            break
        written += len(chunk)
        if written > max_bytes:
            raise ZipIntakeError(
                "archive_entry_too_large",
                "Streamed entry exceeded the expected size.",
            )
        dst.write(chunk)
    return written


def intake_zip(
    paths: WorkspacePaths,
    *,
    source: Callable[[int], bytes] | Iterable[bytes],
    limits: ArchiveLimits,
) -> ZipIntakeResult:
    """Run the full intake pipeline (quarantine -> validate -> extract)."""
    archive_path, sha_hex, size = quarantine_archive(paths, source=source, limits=limits)
    entries = inspect_zip_entries(archive_path, limits=limits)
    # ``validate_zip`` returns the populated
    # collector; the skipped symlinks are surfaced
    # through the intake result so the scan evidence
    # layer can mark the analysis ``partial`` when
    # the omission matters.
    collector = validate_zip(entries, limits)
    # LV-005: the reported counts are the extractor's, not the
    # inventory's, so an entry the extractor skipped is never
    # advertised as analyzed content.
    file_count, uncompressed_size = extract_zip(archive_path, paths, entries, limits=limits)
    return ZipIntakeResult(
        archive_path=archive_path,
        archive_sha256=sha_hex,
        archive_size=size,
        file_count=file_count,
        uncompressed_size=uncompressed_size,
        contents_dir=paths.contents_dir,
        skipped_symlinks=tuple(collector.skipped_symlinks),
    )


def cleanup_workspace(paths: WorkspacePaths) -> None:
    """Remove a workspace from disk. No-op if it does not exist."""
    if paths.workspace_dir.exists():
        shutil.rmtree(paths.workspace_dir, ignore_errors=True)


# ---------------------------------------------------------------------
# tar.gz intake (LV-009)
# ---------------------------------------------------------------------


class BoundedDecompressedStream(io.RawIOBase):
    """Cap the number of decompressed bytes a tar walk may consume.

    ``tarfile`` reaches the next member header by reading or seeking
    over the previous member's body, so simply enumerating a hostile
    ``tar.gz`` decompresses the whole archive - the work the limits
    exist to prevent happens before any limit is consulted. This
    wrapper sits between the ``GzipFile`` and ``tarfile`` and turns
    the archive-wide uncompressed budget into a property of the
    stream itself:

      * a read is clamped to one byte past the remaining budget, so
        even ``read(-1)`` cannot inflate an unbounded amount;
      * a seek past the budget is refused before the underlying
        decompressor is asked to skip forward;
      * ``SEEK_END`` is refused outright because answering it means
        decompressing everything.

    The budget is measured with the inner stream's own uncompressed
    position, so bytes skipped by a seek are charged exactly like
    bytes returned by a read.
    """

    def __init__(self, inner, *, max_bytes: int) -> None:
        super().__init__()
        self._inner = inner
        self._max_bytes = max_bytes

    @property
    def bytes_consumed(self) -> int:
        return self._inner.tell()

    def _enforce(self) -> None:
        if self._inner.tell() > self._max_bytes:
            raise ZipIntakeError(
                "archive_uncompressed_too_large",
                f"Archive decompresses to more than {self._max_bytes} bytes.",
            )

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return self._inner.seekable()

    def read(self, size: int = -1) -> bytes:
        remaining = self._max_bytes - self._inner.tell()
        if remaining < 0:
            self._enforce()
        cap = remaining + 1
        want = cap if size is None or size < 0 else min(size, cap)
        data = self._inner.read(want)
        self._enforce()
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_END:
            raise ZipIntakeError(
                "archive_uncompressed_too_large",
                "Seeking to the end of a compressed archive would decompress all of it.",
            )
        target = offset if whence == io.SEEK_SET else self._inner.tell() + offset
        if target > self._max_bytes:
            raise ZipIntakeError(
                "archive_uncompressed_too_large",
                f"Archive decompresses to more than {self._max_bytes} bytes.",
            )
        position = self._inner.seek(offset, whence)
        self._enforce()
        return position

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        # The caller owns the inner stream's lifetime (it is held by a
        # ``with`` block); closing it here would break that contract.
        pass


def _open_gzip(archive_path: Path):
    """Open ``archive_path`` as a gzip stream.

    A named seam so tests can observe exactly how much of a hostile
    archive was decompressed before the intake gave up.
    """
    return gzip.open(archive_path, "rb")


def inspect_tar_gz_entries(archive_path: Path, *, limits: ArchiveLimits) -> list[ArchiveEntry]:
    """Walk a ``tar.gz`` member list under the configured limits.

    Every limit that can be decided from a member header is enforced
    the moment the header is read, and the archive-wide decompressed
    budget is enforced by the stream itself, so the walk stops at the
    first offending member instead of enumerating (and therefore
    decompressing) the rest of the archive.
    """
    entries: list[ArchiveEntry] = []
    seen_paths: dict[str, str] = {}
    declared_total = 0
    with _open_gzip(archive_path) as gz:
        stream = BoundedDecompressedStream(gz, max_bytes=limits.max_uncompressed_bytes)
        with tarfile.open(fileobj=stream, mode="r:") as tf:
            for member in tf:
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ZipIntakeError(
                        "archive_unsafe_entry",
                        f"Entry {member.name!r} is not a regular file, directory, or link.",
                    )
                name = member.name
                try:
                    normalized = normalize_relative_path(name)
                except PathNormalizationError as exc:
                    raise ZipIntakeError("archive_unsafe_path", str(exc)) from exc

                # LV-005: every member claims its destination before
                # any type-specific branch, so a link cannot shadow a
                # later regular file (or the reverse).
                key = windows_collision_key(normalized)
                previous = seen_paths.get(key)
                if previous is not None:
                    detail = (
                        f"Duplicate normalized path {normalized!r}."
                        if previous == normalized
                        else (
                            f"Entry {normalized!r} collides with earlier entry "
                            f"{previous!r} after Windows path normalization."
                        )
                    )
                    raise ZipIntakeError("archive_duplicate_entry", detail)
                seen_paths[key] = normalized

                # LV-009: the count and the size budgets are decided
                # from the header, before the member's body is read.
                if len(entries) >= limits.max_file_count:
                    raise ZipIntakeError(
                        "archive_too_many_files",
                        f"Archive exceeds max_file_count={limits.max_file_count}.",
                    )
                if member.size > limits.max_file_bytes:
                    raise ZipIntakeError(
                        "archive_entry_too_large",
                        f"Entry {name!r} is {member.size} bytes; max is {limits.max_file_bytes}.",
                    )
                declared_total += max(0, member.size)
                if declared_total > limits.max_uncompressed_bytes:
                    raise ZipIntakeError(
                        "archive_uncompressed_too_large",
                        "Cumulative uncompressed size exceeds limit.",
                    )

                # v2.1.3: defer the symlink / hardlink
                # classification to the shared
                # ``validate_entries`` helper. The
                # tarball layer only records the
                # ``is_symlink`` / ``is_hardlink`` flag
                # and the literal link target so the
                # validator can decide whether the link
                # is safe to record and skip or whether
                # the link is unsafe and must be
                # rejected.
                if member.isdir():
                    entries.append(
                        ArchiveEntry(
                            name=name,
                            size=0,
                            compressed_size=0,
                            is_symlink=False,
                        )
                    )
                    continue
                if member.issym() or member.islnk():
                    link_target = member.linkname or None
                    if link_target is not None and len(link_target) > MAX_SYMLINK_TARGET_BYTES:
                        raise ZipIntakeError(
                            "archive_symlink_target_too_large",
                            f"Link {name!r} has a target longer than "
                            f"{MAX_SYMLINK_TARGET_BYTES} bytes.",
                        )
                    entries.append(
                        ArchiveEntry(
                            name=name,
                            size=max(0, member.size),
                            compressed_size=max(0, member.size),
                            is_symlink=bool(member.issym()),
                            is_hardlink=bool(member.islnk()),
                            link_target=link_target,
                        )
                    )
                    continue
                entries.append(
                    ArchiveEntry(
                        name=name,
                        size=member.size,
                        compressed_size=member.size,
                        is_symlink=False,
                    )
                )
    return entries


def _extract_tar_gz(
    archive_path: Path,
    paths: WorkspacePaths,
    *,
    limits: ArchiveLimits,
    skipped_paths: set[str],
) -> tuple[int, int]:
    """Extract a validated ``tar.gz`` and return the real counts."""
    if not paths.contents_dir.exists():
        paths.contents_dir.mkdir(parents=True, exist_ok=True)
    file_count = 0
    uncompressed_total = 0
    with _open_gzip(archive_path) as gz:
        stream = BoundedDecompressedStream(gz, max_bytes=limits.max_uncompressed_bytes)
        with tarfile.open(fileobj=stream, mode="r:") as tf:
            for member in tf:
                name = member.name
                try:
                    normalized = normalize_relative_path(name)
                except PathNormalizationError as exc:
                    raise ZipIntakeError("archive_unsafe_path", str(exc)) from exc
                if member.isdir():
                    target = paths.contents_dir / normalized.replace("/", os.sep)
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    # Skipped symbolic links are
                    # intentionally not extracted.
                    # Hardlinks are not present here
                    # because the validator rejected
                    # them. The double-check below is a
                    # safety net.
                    if normalized in skipped_paths or member.issym():
                        continue
                    raise ZipIntakeError(
                        "archive_symlink_forbidden",
                        f"Entry {name!r} is a link; not accepted.",
                    )
                file_count += 1
                if file_count > limits.max_file_count:
                    raise ZipIntakeError(
                        "archive_too_many_files",
                        f"Archive exceeds max_file_count={limits.max_file_count}.",
                    )
                if member.size > limits.max_file_bytes:
                    raise ZipIntakeError(
                        "archive_entry_too_large",
                        f"Entry {name!r} is {member.size} bytes; max is {limits.max_file_bytes}.",
                    )
                uncompressed_total += member.size
                if uncompressed_total > limits.max_uncompressed_bytes:
                    raise ZipIntakeError(
                        "archive_uncompressed_too_large",
                        "Cumulative uncompressed size exceeds limit.",
                    )
                dest = paths.contents_dir / normalized.replace("/", os.sep)
                try:
                    dest_resolved = dest.resolve(strict=False)
                    contents_resolved = paths.contents_dir.resolve(strict=False)
                except OSError as exc:
                    raise ZipIntakeError(
                        "archive_path_resolve_failed",
                        f"Could not resolve destination path: {exc}",
                    ) from exc
                if not _is_within(dest_resolved, contents_resolved):
                    raise ZipIntakeError(
                        "archive_path_escape",
                        f"Destination {dest_resolved} is outside the workspace contents.",
                    )
                # v2.1.1: ``dest.parent.mkdir(parents=True, exist_ok=True)``
                # is the historical mkdir call, but on Windows
                # the resulting path can exceed ``MAX_PATH``
                # (260) for a long-named repository + full
                # SHA + deep tree. ``_mkdir_parents`` retries
                # the call through the long-path prefix in
                # that case so the extraction does not abort
                # with a confusing ``FileNotFoundError`` on a
                # valid workspace root. ``_open_for_write``
                # applies the same fix to the file handle.
                _mkdir_parents(dest)
                if dest.exists() or dest.is_symlink():
                    raise ZipIntakeError(
                        "archive_overwrite_forbidden",
                        f"Destination {dest} already exists.",
                    )
                src = tf.extractfile(member)
                if src is None:
                    raise ZipIntakeError(
                        "archive_extract_failed",
                        f"Could not extract {name!r}.",
                    )
                with _open_for_write(dest) as out:
                    _copy_capped(src, out, max_bytes=member.size + 1)
    return file_count, uncompressed_total


def intake_tar_gz(
    paths: WorkspacePaths,
    *,
    source: Callable[[int], bytes] | Iterable[bytes],
    limits: ArchiveLimits,
) -> ZipIntakeResult:
    """Run the full intake pipeline for a gzip-compressed tar archive.

    The function is symmetrical with :func:`intake_zip` but uses
    :mod:`tarfile` to read the archive. Each member is validated
    against the same safety contract as ZIP entries; the
    v2.1.3 symlink policy classifies links into
    ``skip`` (safe relative symlinks) or ``reject``
    (anything unsafe). Hardlinks remain a hard
    fail; device files and unsafe paths continue to
    raise.

    LV-009: the member walk enforces the entry count, the per-member
    size, and the archive-wide decompressed budget as it goes, so a
    hostile archive is abandoned at the offending member rather than
    after the whole stream has been inflated.
    """
    paths.ensure()
    archive_path, sha_hex, size = quarantine_archive(paths, source=source, limits=limits)
    try:
        entries = inspect_tar_gz_entries(archive_path, limits=limits)
    except ZipIntakeError:
        cleanup_workspace(paths)
        paths.ensure()
        raise
    except (gzip.BadGzipFile, tarfile.TarError, OSError) as exc:
        cleanup_workspace(paths)
        paths.ensure()
        raise ZipIntakeError(
            "archive_invalid",
            f"Archive is not a valid tar.gz file: {exc}",
        ) from exc

    try:
        collector = validate_zip(entries, limits)
    except Exception as exc:
        code = getattr(exc, "code", "archive_invalid")
        message = getattr(exc, "message", str(exc))
        cleanup_workspace(paths)
        paths.ensure()
        raise ZipIntakeError(code, message) from exc

    # Build the set of normalised entry paths the
    # validator recorded-and-skipped. The extractor
    # never materialises these entries in the workspace
    # because the validator's contract is "skip + do
    # not follow".
    skipped_paths = {record.path for record in collector.skipped_symlinks}

    try:
        file_count, uncompressed_size = _extract_tar_gz(
            archive_path, paths, limits=limits, skipped_paths=skipped_paths
        )
    except ZipIntakeError:
        if paths.contents_dir.exists():
            shutil.rmtree(paths.contents_dir, ignore_errors=True)
            paths.contents_dir.mkdir(parents=True, exist_ok=True)
        raise

    return ZipIntakeResult(
        archive_path=archive_path,
        archive_sha256=sha_hex,
        archive_size=size,
        file_count=file_count,
        uncompressed_size=uncompressed_size,
        contents_dir=paths.contents_dir,
        skipped_symlinks=tuple(collector.skipped_symlinks),
    )
