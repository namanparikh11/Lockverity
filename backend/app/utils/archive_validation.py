"""Archive-entry validation contract.

Lockverity v0.1 does **not** extract archives. It validates the entries
inside a zip / tar stream up front so a malicious upload cannot reach
the filesystem layer. The validation is intentionally aggressive: the
default behavior is to reject anything that does not pass every check.

Validation performed on each archive entry:

1. Path traversal (``../``, ``..\\``)
2. Absolute POSIX paths (``/etc/passwd``)
3. Windows drive-letter paths (``C:\\evil``)
4. UNC paths (``\\\\server\\share``)
5. Symbolic-link and hard-link entries when the format exposes the
   metadata
6. Duplicate normalized entries (zip slip variants that bypass the path
   check via encodings)
7. Excessive directory depth
8. Oversized individual entries
9. Excessive cumulative uncompressed size
10. Suspicious compression ratios (zip bomb heuristic)
11. Excessive file count

v2.1.3 symlink-handling policy
==============================

The historical policy refused any archive containing a
symbolic-link or hard-link entry. The policy was
over-conservative for repository-analysis archives: many
legitimate open-source repositories (``linux``, the
``deepseek-ai/deepseek-harness`` snapshot, etc.)
contain symbolic links and the entire archive was
rejected before any analysis.

The new policy is split:

  * **Hard-fail** on a symbolic link whose *normalised
    target* resolves outside the archive root, on an
    absolute POSIX target, on a Windows drive target,
    on a UNC target, on a malformed target, and on
    any target that would cause the validator to
    dereference the host filesystem. Hardlinks remain
    a hard fail (the policy is unchanged for hardlinks
    because the security boundary is binary, not
    semantic).

  * **Skip + record** every other symbolic link. The
    validator records the entry under the new
    ``archive_symlink_skipped`` warning code with the
    safe archive-relative target, never follows the
    link, and never creates a filesystem symlink. The
    rest of the archive continues through the normal
    validation flow. The skipped entries are reported
    in the intake result so the scan evidence layer
    can mark the analysis as ``partial`` rather than
    ``failed`` when the omission materially affects
    coverage.

  * **Symlink cycles and chains** are bounded by the
    skip policy: the validator never resolves a chain
    because it never follows the first link. The
    chain is recorded by the safe relative target
    only.

The ``archive_symlink_target_missing`` code is raised
*only* when the literal target string is empty or
None. It is **not** raised for "target does not exist
as an archive entry" — that case is recorded as
``archive_symlink_skipped`` because the resolved path
is inside the archive namespace. The classifier
never inspects the archive's other entries, never
dereferences the host filesystem, and never requires
the target to exist as a real entry in the archive.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from app.utils.paths import (
    PathNormalizationError,
    normalize_relative_path,
    windows_collision_key,
)

# Maximum number of characters the validator keeps
# from a symlink target. A target longer than this is
# still validated (we only need the leading bytes to
# detect absolute, drive, and UNC prefixes) but only
# the bounded prefix is recorded in the warning. The
# value is deliberately generous: 1024 bytes is well
# beyond any sane relative target while still
# preventing a hostile archive from bloating the
# intake result.
MAX_RECORDED_TARGET_LENGTH = 1024

# Maximum number of characters the validator keeps
# from a symlink entry path in a warning. The value
# is matched to :data:`MAX_RECORDED_TARGET_LENGTH`
# for consistency.
MAX_RECORDED_PATH_LENGTH = 1024

# Maximum number of bytes a symbolic-link entry may declare for
# its target. A link target is a path string: POSIX ``PATH_MAX``
# is 4096 and the Windows wide API tops out well below that, so
# any link body larger than this is a payload wearing a link's
# metadata rather than a path. The bound is what lets the intake
# layer read a link target with a hard cap instead of an
# unbounded ``read()`` (LV-004).
MAX_SYMLINK_TARGET_BYTES = 4096

# Windows drive-prefix pattern. The pattern matches
# both ``C:`` and ``C:\`` (and the rare ``C:/``
# variant). The validator refuses to consider any
# link whose normalised target contains a drive
# prefix.
_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")

# UNC prefix pattern. The validator refuses to
# consider any link whose normalised target starts
# with two backslashes (Windows) or two forward
# slashes (POSIX UNC). The forward-slash variant
# catches archives produced on POSIX systems that
# encode a Windows-style UNC target.
_UNC_PREFIX = re.compile(r"^[/\\]{2}")


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Configurable limits for archive-entry validation."""

    max_compressed_bytes: int
    max_uncompressed_bytes: int
    max_file_count: int
    max_file_bytes: int
    max_depth: int
    suspicious_ratio: int


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """A single archive entry, decoupled from any specific archive format.

    The scanner layer is responsible for converting the library-specific
    entry (zipfile.ZipInfo, tarfile.TarInfo) into this neutral record.
    """

    name: str
    size: int
    compressed_size: int
    is_symlink: bool = False
    is_hardlink: bool = False
    # Link target for symlink/hardlink entries. Only inspected when
    # ``is_symlink`` or ``is_hardlink`` is true.
    link_target: str | None = None


class ArchiveValidationError(ValueError):
    """Raised when an archive fails validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ArchiveValidationError(code={self.code!r}, message={self.message!r})"


@dataclass(frozen=True, slots=True)
class SkippedSymlink:
    """A symbolic link that was intentionally skipped for safety.

    The intake layer persists one
    :class:`SkippedSymlink` per archived symbolic link
    so the scan evidence layer can surface the
    omission to the operator. The recorded path and
    target are the *archive-relative* values; the
    validator never dereferences the link, never
    reads the target through the host filesystem, and
    never creates a filesystem symlink.
    """

    path: str
    target: str
    reason: str = "symbolic link intentionally not followed for archive safety"


@dataclass(frozen=True, slots=True)
class SkippedHardlink:
    """A hardlink that was rejected for safety.

    Hardlinks retain the historical hard-fail policy
    because the security boundary is binary: a
    hardlink lets an attacker alias an existing file
    under a new name and there is no safe way to
    materialise the alias without exposing the
    extraction layer to the hardlink semantics. The
    skipped record keeps the entry path and the
    reason for diagnostics.
    """

    path: str
    target: str
    reason: str = "hard link intentionally not materialised for archive safety"


class ArchiveValidationCollector:
    """Stateful collector for archive-entry validation.

    Use this when you want to collect every violation rather than fail
    fast. The collector is intentionally explicit - you call
    :meth:`ok` once the iteration completes to assert the archive is
    safe.

    The collector also accumulates :class:`SkippedSymlink`
    records for benign symbolic links that were
    intentionally skipped. The records are exposed via
    :attr:`skipped_symlinks` and are persisted into the
    scan evidence layer so the operator sees an honest
    ``partial`` outcome when one or more entries were
    skipped for safety.
    """

    @dataclass(frozen=True, slots=True)
    class _SymlinkVerdict:
        action: Literal["skip", "reject"]
        code: str
        message: str
        path: str
        target: str

    def __init__(self, limits: ArchiveLimits) -> None:
        self.limits = limits
        self._errors: list[ArchiveValidationError] = []
        self._skipped_symlinks: list[SkippedSymlink] = []
        self._skipped_hardlinks: list[SkippedHardlink] = []
        # Maps the Win32-equivalence collision key to the first
        # normalised path that claimed it, so a collision message
        # can name both spellings.
        self._seen_paths: dict[str, str] = {}
        self._entry_count = 0
        self._file_count = 0
        self._uncompressed_total = 0
        self._compressed_total = 0

    @property
    def errors(self) -> tuple[ArchiveValidationError, ...]:
        return tuple(self._errors)

    @property
    def skipped_symlinks(self) -> tuple[SkippedSymlink, ...]:
        return tuple(self._skipped_symlinks)

    @property
    def skipped_hardlinks(self) -> tuple[SkippedHardlink, ...]:
        return tuple(self._skipped_hardlinks)

    @property
    def entry_count(self) -> int:
        """Every entry that claimed a destination, links included."""
        return self._entry_count

    @property
    def file_count(self) -> int:
        """Entries that carry analyzable content (links excluded)."""
        return self._file_count

    @property
    def uncompressed_total(self) -> int:
        return self._uncompressed_total

    @property
    def compressed_total(self) -> int:
        return self._compressed_total

    def check_entry(self, entry: ArchiveEntry) -> None:
        """Validate ``entry`` and record any violations."""
        # 1. Path normalization handles traversal, absolute paths, drive
        # letters, UNC paths, and the Win32 component spellings
        # (alternate data streams, reserved device names, trailing
        # dot/space) in one go.
        try:
            normalized = normalize_relative_path(entry.name)
        except PathNormalizationError as exc:
            self._errors.append(ArchiveValidationError("archive_unsafe_path", str(exc)))
            return

        depth = normalized.count("/") + 1
        if depth > self.limits.max_depth:
            self._errors.append(
                ArchiveValidationError(
                    "archive_depth_exceeded",
                    f"Entry {entry.name!r} depth {depth} exceeds "
                    f"max_depth={self.limits.max_depth}.",
                )
            )
            return

        # 2. Destination identity (LV-005).
        #
        # Every entry registers the destination it claims *before* any
        # type-specific early return. The historical order registered
        # the path only on the regular-file branch, so a link entry
        # could claim a destination silently and a later regular file
        # with the same normalised path passed the duplicate check, was
        # counted as analyzed, and was then skipped by the extractor as
        # "this path belongs to a symlink". The archive reported a file
        # that never reached the filesystem, which is exactly how a
        # hostile archive hides a ``requirements.txt`` or a lockfile
        # from analysis.
        #
        # The identity is the Win32-equivalence key so the collision
        # semantics match what the extraction layer ultimately does on
        # disk: case-insensitive, trailing dot/space folded, separators
        # normalised. Collisions are detected across *all* entry-type
        # pairs (regular/regular, regular/symlink, symlink/regular,
        # symlink/symlink, directory/file) because the registration is
        # unconditional.
        key = windows_collision_key(normalized)
        previous = self._seen_paths.get(key)
        if previous is not None:
            detail = (
                f"Duplicate normalized path {normalized!r}."
                if previous == normalized
                else (
                    f"Entry {normalized!r} collides with earlier entry "
                    f"{previous!r} after Windows path normalization."
                )
            )
            self._errors.append(ArchiveValidationError("archive_duplicate_entry", detail))
            return
        self._seen_paths[key] = normalized

        # 3. Entry-count limit. Every entry that claims a destination
        # counts, links included, so a link-heavy archive cannot walk
        # past ``max_file_count``.
        self._entry_count += 1
        if self._entry_count > self.limits.max_file_count:
            self._errors.append(
                ArchiveValidationError(
                    "archive_too_many_files",
                    f"Archive exceeds max_file_count={self.limits.max_file_count}.",
                )
            )
            return

        # 4. Symlink / hardlink detection. The historical policy was
        # to reject the entire archive; the v2.1.3 policy splits
        # the cases:
        #
        #   - A hardlink is always rejected (no safe materialisation).
        #   - A symlink whose normalised target is unsafe (absolute,
        #     drive, UNC, escapes the archive root) is rejected.
        #   - A safe relative symlink is recorded and the validator
        #     moves on. The extractor never follows the link.
        if entry.is_hardlink:
            self._errors.append(
                ArchiveValidationError(
                    "archive_hardlink_forbidden",
                    f"Entry {entry.name!r} is a hard link; not accepted.",
                )
            )
            return
        if entry.is_symlink:
            # LV-004: a link entry's body is archive content like any
            # other. It is charged against the per-entry cap, the
            # cumulative uncompressed and compressed budgets, and the
            # compression-ratio heuristic *before* the target is
            # classified, so an oversized or highly compressed link
            # body cannot buy an exemption by being a link.
            if not self._charge_resources(entry):
                return
            verdict = self._classify_symlink_target(entry, normalized)
            if verdict.action == "reject":
                self._errors.append(ArchiveValidationError(verdict.code, verdict.message))
                return
            # The verdict is ``skip``: the link is safe
            # to record and ignore. The extractor
            # never creates a filesystem symlink, the
            # intake result never claims the linked
            # content was parsed, and the validation
            # continues to the next entry.
            self._skipped_symlinks.append(
                SkippedSymlink(
                    path=verdict.path,
                    target=verdict.target,
                )
            )
            return

        # 5. Resource accounting for an entry that carries content.
        if not self._charge_resources(entry):
            return
        self._file_count += 1

    def _charge_resources(self, entry: ArchiveEntry) -> bool:
        """Charge ``entry`` against every size budget.

        Returns ``True`` when the entry is within every limit and
        ``False`` once a violation has been recorded. The same
        accounting runs for entries that carry content and for link
        entries, so a link body cannot evade the per-file cap, the
        cumulative uncompressed and compressed budgets, or the
        compression-ratio heuristic (LV-004).
        """
        if entry.size < 0:
            self._errors.append(
                ArchiveValidationError(
                    "archive_negative_size",
                    f"Entry {entry.name!r} has negative size.",
                )
            )
            return False
        if entry.size > self.limits.max_file_bytes:
            self._errors.append(
                ArchiveValidationError(
                    "archive_entry_too_large",
                    f"Entry {entry.name!r} is {entry.size} bytes; "
                    f"max is {self.limits.max_file_bytes}.",
                )
            )
            return False
        # A link target is a path string. Anything past the longest
        # path any host filesystem accepts is a payload wearing a
        # link's metadata, so it is refused independently of the
        # (much larger) per-file cap.
        if entry.is_symlink and entry.size > MAX_SYMLINK_TARGET_BYTES:
            self._errors.append(
                ArchiveValidationError(
                    "archive_symlink_target_too_large",
                    f"Symbolic link {entry.name!r} declares a {entry.size}-byte "
                    f"target; max is {MAX_SYMLINK_TARGET_BYTES}.",
                )
            )
            return False

        self._uncompressed_total += entry.size
        if self._uncompressed_total > self.limits.max_uncompressed_bytes:
            self._errors.append(
                ArchiveValidationError(
                    "archive_uncompressed_too_large",
                    "Cumulative uncompressed size exceeds limit.",
                )
            )
            return False

        self._compressed_total += max(0, entry.compressed_size)
        if self._compressed_total > self.limits.max_compressed_bytes:
            self._errors.append(
                ArchiveValidationError(
                    "archive_compressed_too_large",
                    "Cumulative compressed size exceeds limit.",
                )
            )
            return False

        # Compression-ratio heuristic. We only flag a ratio when both
        # numerator and denominator are non-zero, so a single zero-byte
        # entry does not produce a divide-by-zero or a false positive.
        if (
            entry.compressed_size > 0
            and entry.size > 0
            and entry.size >= self.limits.suspicious_ratio * entry.compressed_size
        ):
            self._errors.append(
                ArchiveValidationError(
                    "archive_suspicious_compression",
                    f"Entry {entry.name!r} compresses "
                    f"{entry.size // entry.compressed_size}x; "
                    f"ratio above {self.limits.suspicious_ratio}x is suspicious.",
                )
            )
            return False
        return True

    def _classify_symlink_target(
        self, entry: ArchiveEntry, normalized_entry: str
    ) -> _SymlinkVerdict:
        """Classify a symbolic-link target into ``skip`` or ``reject``.

        The function never dereferences the link. The
        decision is based on the *literal* target
        string the archive provided and on the
        archived entry path. The classifier rejects
        targets that would expose the extraction layer
        to the host filesystem, even if the operator
        intended the link to be safe.

        The recorded path is the normalised entry
        path; the recorded target is the leading
        bounded prefix of the literal target string
        so a hostile archive cannot bloat the intake
        result. The recorded values are never used
        for filesystem operations; they are
        evidence-only.

        Symlink-target contract
        ======================

        The classifier's contract is purely about
        the *literal target string* and the
        *archive-namespace safety*. The classifier:

          - **Never** inspects the archive's other
            entries. ``archive_symlink_target_missing``
            is **not** raised for "target does not
            exist as an archive entry" — that case
            is recorded as ``archive_symlink_skipped``
            because the resolved path is inside the
            archive namespace.

          - **Never** dereferences the host filesystem
            or calls ``os.path.realpath``. The
            resolution is purely a
            :func:`posixpath.normpath` calculation
            over the entry's parent + the literal
            target.

          - **Never** records the literal target
            string. The recorded value is the
            :func:`posixpath.normpath` result of
            ``posixpath.join(parent, literal_target)``.

        The deepseek-harness regression fixture
        mirrors the real repository exactly: the
        ``README.md`` lives at the repo root, the
        symlink entry lives at
        ``deepseek-harness/.agents/notes/implemented/CLAUDE.md``,
        and the literal target is ``../../README.md``.
        The classifier normalises this to
        ``deepseek-harness/.agents/README.md`` and
        records it as ``archive_symlink_skipped`` —
        the same code as for any other safe relative
        symlink. The classifier does not flag the
        fact that the real DeepSeek Harness symlink
        is technically "broken" (its target is not
        an existing entry in the archive); the
        v2.1.3 contract is *namespace safety*, not
        *target existence*.
        """
        literal_target = entry.link_target or ""
        # The path stored in the verdict is the
        # normalised entry path. The function bounds
        # the length so a hostile archive cannot
        # bloat the verdict.
        bounded_path = normalized_entry[:MAX_RECORDED_PATH_LENGTH]
        bounded_target = literal_target[:MAX_RECORDED_TARGET_LENGTH]
        if not literal_target:
            return self._SymlinkVerdict(
                action="reject",
                code="archive_symlink_target_missing",
                message=(
                    f"Entry {entry.name!r} is a symbolic link with an empty target; not accepted."
                ),
                path=bounded_path,
                target=bounded_target,
            )
        # POSIX-absolute targets are unsafe under the
        # current policy. ``C:\...`` and ``\\server\share``
        # are caught by the Windows drive / UNC checks
        # below, but a leading ``/`` on POSIX must be
        # rejected independently of the host OS so the
        # validator is portable.
        if literal_target.startswith("/"):
            return self._SymlinkVerdict(
                action="reject",
                code="archive_symlink_target_unsafe",
                message=(f"Symbolic link {entry.name!r} has an absolute target; not accepted."),
                path=bounded_path,
                target=bounded_target,
            )
        # Windows drive-letter target (with or
        # without a leading separator). The check is
        # case-insensitive; the pattern matches both
        # ``C:foo`` and ``C:\foo``.
        if _WINDOWS_DRIVE_PREFIX.match(literal_target):
            return self._SymlinkVerdict(
                action="reject",
                code="archive_symlink_target_unsafe",
                message=(f"Symbolic link {entry.name!r} has a Windows drive target; not accepted."),
                path=bounded_path,
                target=bounded_target,
            )
        # UNC target (``\\server\share`` or
        # ``//server/share``).
        if _UNC_PREFIX.match(literal_target):
            return self._SymlinkVerdict(
                action="reject",
                code="archive_symlink_target_unsafe",
                message=(f"Symbolic link {entry.name!r} has a UNC target; not accepted."),
                path=bounded_path,
                target=bounded_target,
            )
        # Relative target that escapes the archive
        # root when resolved against the entry's
        # parent directory. ``posixpath.normpath``
        # collapses ``..`` segments without touching
        # the filesystem, then we reject any
        # resolved path that starts with ``..`` or
        # is absolute. The result is the safe
        # archive-relative target we record; we
        # never call ``os.path.realpath`` and never
        # read the host filesystem.
        parent = posixpath.dirname(normalized_entry)
        candidate = posixpath.normpath(
            posixpath.join(parent, literal_target) if parent else literal_target
        )
        if candidate == ".." or candidate.startswith("../"):
            return self._SymlinkVerdict(
                action="reject",
                code="archive_symlink_target_unsafe",
                message=(
                    f"Symbolic link {entry.name!r} has a target that "
                    "escapes the archive root; not accepted."
                ),
                path=bounded_path,
                target=bounded_target,
            )
        # The relative target normalises to a value
        # within the archive namespace. The link is
        # safe to record and ignore.
        return self._SymlinkVerdict(
            action="skip",
            code="archive_symlink_skipped",
            message=(
                f"Symbolic link {entry.name!r} was skipped for safety; "
                f"target={candidate!r} is within the archive root."
            ),
            path=bounded_path,
            target=candidate[:MAX_RECORDED_TARGET_LENGTH],
        )

    def ok(self) -> None:
        """Raise :class:`ArchiveValidationError` if any errors are pending."""
        if self._errors:
            first = self._errors[0]
            raise ArchiveValidationError(first.code, first.message)


def validate_entries(
    entries: Iterable[ArchiveEntry],
    limits: ArchiveLimits,
) -> ArchiveValidationCollector:
    """Validate every entry in ``entries`` and return the collector.

    The function intentionally does **not** call
    :meth:`ArchiveValidationCollector.ok`; the caller is
    expected to inspect the errors *and* the
    ``skipped_symlinks`` collection. The intake layer
    treats a populated ``skipped_symlinks`` as a
    warning, not a hard failure; the scan evidence
    layer downgrades the outcome to ``partial``
    when the omission materially affects coverage.
    """
    collector = ArchiveValidationCollector(limits)
    for entry in entries:
        collector.check_entry(entry)
    return collector


def limits_from_settings(values: Mapping[str, int]) -> ArchiveLimits:
    """Build an :class:`ArchiveLimits` from a settings-like mapping."""
    return ArchiveLimits(
        max_compressed_bytes=int(values["max_compressed_bytes"]),
        max_uncompressed_bytes=int(values["max_uncompressed_bytes"]),
        max_file_count=int(values["max_file_count"]),
        max_file_bytes=int(values["max_file_bytes"]),
        max_depth=int(values["max_depth"]),
        suspicious_ratio=int(values["suspicious_ratio"]),
    )
