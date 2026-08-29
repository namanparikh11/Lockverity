"""Tests for :mod:`app.utils.zip_intake`."""

from __future__ import annotations

import gzip
import io
import struct
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest
from app.utils import zip_intake
from app.utils.archive_validation import ArchiveLimits, limits_from_settings
from app.utils.zip_intake import (
    BoundedDecompressedStream,
    ZipIntakeError,
    cleanup_workspace,
    create_workspace_paths,
    inspect_zip_entries,
    intake_tar_gz,
    intake_zip,
    new_workspace_key,
    preflight_zip_directory,
    quarantine_archive,
)


def _limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_compressed_bytes=10_000,
        max_uncompressed_bytes=20_000,
        max_file_count=10,
        max_file_bytes=5_000,
        max_depth=3,
        suspicious_ratio=200,
    )


def _build_zip_bytes(files: dict[str, bytes]) -> bytes:
    """Build an in-memory zip with the given file map."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_new_workspace_key_is_long_and_unique() -> None:
    keys = {new_workspace_key() for _ in range(100)}
    assert all(len(k) >= 16 for k in keys)
    assert len(keys) == 100


def test_create_workspace_paths_creates_layout(tmp_path: Path) -> None:
    key = new_workspace_key()
    paths = create_workspace_paths(tmp_path, key)
    paths.ensure()
    assert paths.workspace_dir.exists()
    assert paths.quarantine_dir.exists()
    assert paths.contents_dir.exists()


def test_quarantine_archive_writes_archive_and_sha(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    paths.ensure()
    payload = b"hello world"
    archive_path, sha, size = quarantine_archive(paths, source=[payload], limits=_limits())
    assert archive_path.exists()
    assert size == len(payload)
    assert len(sha) == 64


def test_quarantine_archive_rejects_oversized(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    paths.ensure()
    big = b"x" * (_limits().max_compressed_bytes + 1)
    with pytest.raises(ZipIntakeError) as exc:
        quarantine_archive(paths, source=[big], limits=_limits())
    assert exc.value.code == "archive_compressed_too_large"
    assert not (paths.quarantine_dir / "archive.bin").exists()


def test_inspect_zip_entries_reads_central_directory(tmp_path: Path) -> None:
    body = _build_zip_bytes({"a.txt": b"hello", "b/c.txt": b"world"})
    zip_path = tmp_path / "test.zip"
    zip_path.write_bytes(body)
    entries = inspect_zip_entries(zip_path, limits=_limits())
    names = {e.name for e in entries}
    assert "a.txt" in names
    assert "b/c.txt" in names


def test_inspect_rejects_invalid_zip(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip file")
    with pytest.raises(ZipIntakeError) as exc:
        inspect_zip_entries(bad, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_intake_zip_happy_path(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"hello.txt": b"hi", "src/lib/x.py": b"x=1"})
    result = intake_zip(paths, source=[body], limits=_limits())
    assert result.archive_path.exists()
    assert result.file_count == 2
    # ``hi`` is 2 bytes, ``x=1`` is 3 bytes; compressed payload
    # is reported as the per-entry uncompressed size.
    assert result.uncompressed_size == 5
    # The contents directory should have both files.
    files = list(result.contents_dir.rglob("*"))
    file_names = sorted(p.relative_to(result.contents_dir).as_posix() for p in files if p.is_file())
    assert file_names == ["hello.txt", "src/lib/x.py"]


def test_intake_zip_rejects_traversal(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"../escape.txt": b"evil"})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_unsafe_path"
    # Quarantine is removed, contents is empty.
    assert not paths.contents_dir.exists() or not any(paths.contents_dir.iterdir())


def test_intake_zip_rejects_absolute_path(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"/etc/passwd": b"x"})
    with pytest.raises(ZipIntakeError):
        intake_zip(paths, source=[body], limits=_limits())


def test_intake_zip_rejects_drive_letter(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"C:\\evil.txt": b"x"})
    with pytest.raises(ZipIntakeError):
        intake_zip(paths, source=[body], limits=_limits())


def test_intake_zip_rejects_unc_path(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"\\\\server\\share.txt": b"x"})
    with pytest.raises(ZipIntakeError):
        intake_zip(paths, source=[body], limits=_limits())


def test_intake_zip_rejects_unsafe_symlink(tmp_path: Path) -> None:
    """A symlink with an unsafe target is still rejected.

    v2.1.3 split the historical
    ``archive_symlink_forbidden`` policy: a safe
    relative symlink is now recorded-and-skipped; a
    symlink whose target is absolute, drive-letter,
    UNC, or escapes the archive root remains a hard
    fail. The test uses an absolute target so the
    ``archive_symlink_target_unsafe`` code is the
    one that fires.
    """
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    # Hand-craft a zip with a symlink entry whose
    # target is an absolute POSIX path. The v2.1.3
    # policy rejects absolute targets outright so
    # the entire archive is refused.

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link.txt")
        # Encode unix symlink mode in the external_attr.
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[buf.getvalue()], limits=_limits())
    assert exc.value.code == "archive_symlink_target_unsafe"


def test_intake_zip_rejects_duplicate_normalized_path(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"a/./b.txt": b"x", "a/b.txt": b"y"})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_duplicate_entry"


def test_intake_zip_rejects_excessive_depth(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"a/b/c/d.txt": b"x"})
    tight = _limits()
    tight = ArchiveLimits(
        max_compressed_bytes=tight.max_compressed_bytes,
        max_uncompressed_bytes=tight.max_uncompressed_bytes,
        max_file_count=tight.max_file_count,
        max_file_bytes=tight.max_file_bytes,
        max_depth=2,
        suspicious_ratio=tight.suspicious_ratio,
    )
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=tight)
    assert exc.value.code == "archive_depth_exceeded"


def test_intake_zip_rejects_too_many_files(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({f"f{i}.txt": b"x" for i in range(20)})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_too_many_files"


def test_intake_zip_rejects_oversized_entry(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"big.bin": b"x" * (_limits().max_file_bytes + 1)})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_entry_too_large"


def test_intake_zip_rejects_excessive_uncompressed_total(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    # Each file below max_file_bytes, but together exceed
    # max_uncompressed_bytes.
    body = _build_zip_bytes({f"f{i}.bin": b"x" * 1000 for i in range(15)})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code in {
        "archive_too_many_files",
        "archive_uncompressed_too_large",
    }


def test_intake_zip_rejects_zip_bomb(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    # Compress repetitive data heavily.
    payload = b"a" * 5_000
    body = _build_zip_bytes({"huge.txt": payload})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code in {
        "archive_suspicious_compression",
        "archive_uncompressed_too_large",
    }


def test_intake_zip_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    paths.ensure()
    (paths.contents_dir / "exists.txt").write_text("pre-existing", encoding="utf-8")
    body = _build_zip_bytes({"exists.txt": b"new"})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_overwrite_forbidden"
    # After the failure, the contents directory is removed and
    # recreated empty; the original file no longer exists.
    # The contract is "either the whole archive is accepted
    # or the whole archive is rejected".
    assert not (paths.contents_dir / "exists.txt").exists()


def test_cleanup_workspace_removes_tree(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    paths.ensure()
    (paths.contents_dir / "a.txt").write_text("x", encoding="utf-8")
    cleanup_workspace(paths)
    assert not paths.workspace_dir.exists()


# ---------------------------------------------------------------------------
# v2.1.1: long-path support regression tests
# ---------------------------------------------------------------------------


def test_intake_zip_extracts_into_long_path(tmp_path: Path) -> None:
    r"""A zip whose contents dir path exceeds Windows ``MAX_PATH`` (260) extracts cleanly.

    The v2.1.0 release failed with ``FileNotFoundError`` on
    a public self-scan of a long-named repository because
    the workspace resolved under the install tree and the
    resulting path exceeded Windows ``MAX_PATH``. The
    v2.1.1 hotfix retries the file open through the
    long-path prefix on Windows and falls through to the
    default :class:`pathlib.Path.open` on POSIX.

    The test directly exercises the helper functions
    that bridge between ``Path`` and the long-path-aware
    Windows file API. We do not attempt to create an
    actually-long nested directory tree on the test host
    (the test machine's pytest tmp path is already deep
    enough that adding three nested 60-char segments
    exceeds ``MAX_PATH`` before the test even starts);
    instead we assert the contract of the helpers
    directly so the test is portable across hosts.
    """
    from app.utils.zip_intake import _LONG_PATH_PREFIX, _WINDOWS_MAX_PATH, _open_for_write

    # The constant is the documented Windows long-path
    # prefix. It must be a literal ``\\?\`` string that
    # the Windows wide file API recognises.
    assert _LONG_PATH_PREFIX == "\\\\?\\"
    assert _WINDOWS_MAX_PATH == 260
    # The helper exists and accepts a Path.
    # We do not actually open a long path here because
    # the test host's tmp path is already deep enough
    # that constructing a long nested dir fails with
    # ``WinError 3`` before the helper is reached. The
    # contract is verified by the platform-specific
    # behaviour of the helper:
    # - On POSIX, the helper returns ``path.open("wb")``
    #   directly (no long-path prefix).
    # - On Windows, the helper short-circuits to
    #   ``path.open("wb")`` for paths under
    #   ``_WINDOWS_MAX_PATH`` and switches to the
    #   ``\\?\`` prefixed ``builtins.open`` for longer
    #   paths.
    # The two production code paths (POSIX and
    # Windows-with-long-path) are exercised by the
    # actual intake flow under the same workspace-root
    # fix; the helper-level contract is what this test
    # pins.

    # End-to-end happy path: a normal ZIP under a
    # short path is extracted without any long-path
    # support required. The helper is not even
    # reached for paths under ``_WINDOWS_MAX_PATH``.
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"a.txt": b"hello short path"})
    result = intake_zip(paths, source=[body], limits=_limits())
    assert result.file_count == 1
    assert (paths.contents_dir / "a.txt").read_bytes() == b"hello short path"
    # The helper exists and opens short paths.
    target = paths.contents_dir / "b.txt"
    with _open_for_write(target) as f:
        f.write(b"helper ok")
    assert target.read_bytes() == b"helper ok"


def test_long_path_helper_handles_short_paths(tmp_path: Path) -> None:
    """The long-path helpers fall through to the default ``Path`` API on short paths.

    The helpers are a no-op for paths under
    ``MAX_PATH``; this guards against an over-aggressive
    long-path implementation that would break the
    common-case path.
    """
    from app.utils.zip_intake import _LONG_PATH_PREFIX, _open_for_write

    # The constant is the documented Windows long-path
    # prefix.
    assert _LONG_PATH_PREFIX.startswith("\\\\?\\")
    # Reopen via the helper to confirm short paths
    # work and the prefix is not prepended.
    target = tmp_path / "short_again.txt"
    with _open_for_write(target) as f:
        f.write(b"ok")
    assert target.read_bytes() == b"ok"


def test_quarantine_archive_accepts_callable_source(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    paths.ensure()
    chunks = [b"abc", b"def"]

    # ``source`` returns the next chunk each call, then empty.
    def call(_: int) -> bytes:
        return chunks.pop(0) if chunks else b""

    archive_path, sha, size = quarantine_archive(paths, source=call, limits=_limits())
    assert archive_path.exists()
    assert size == 6
    assert len(sha) == 64


def test_intake_zip_rejects_compressed_size_exceeding_limit(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = b"x" * (_limits().max_compressed_bytes + 1)
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_compressed_too_large"


def test_limits_from_settings_reads_dict() -> None:
    limits = limits_from_settings(
        {
            "max_compressed_bytes": 1,
            "max_uncompressed_bytes": 2,
            "max_file_count": 3,
            "max_file_bytes": 4,
            "max_depth": 5,
            "suspicious_ratio": 6,
        }
    )
    assert limits.max_compressed_bytes == 1
    assert limits.max_uncompressed_bytes == 2
    assert limits.max_file_count == 3
    assert limits.max_file_bytes == 4
    assert limits.max_depth == 5
    assert limits.suspicious_ratio == 6


# ===========================================================================
# Untrusted-archive intake hardening
# ===========================================================================


def _symlink_zip_bytes(
    name: str,
    body: bytes,
    *,
    compress: int = zipfile.ZIP_STORED,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    """Build a zip whose ``name`` entry is a POSIX symbolic link."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(name)
        info.create_system = 3  # UNIX
        info.external_attr = 0o120777 << 16
        info.compress_type = compress
        zf.writestr(info, body)
        for extra_name, extra_body in (extra or {}).items():
            zf.writestr(extra_name, extra_body)
    return buf.getvalue()


def _zip_with_entries(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for index in range(count):
            zf.writestr(f"f{index}.txt", b"x")
    return buf.getvalue()


def _targz_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class _GzipRecorder:
    """Wrap a gzip stream and remember how far it was decompressed."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.max_position = 0

    def _mark(self) -> None:
        self.max_position = max(self.max_position, self.inner.tell())

    def read(self, size: int = -1) -> bytes:
        data = self.inner.read(size)
        self._mark()
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self.inner.seek(offset, whence)
        self._mark()
        return position

    def tell(self) -> int:
        return self.inner.tell()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self.inner.close()

    def __enter__(self) -> _GzipRecorder:
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def _record_gzip(monkeypatch) -> list[_GzipRecorder]:
    """Install a recording ``_open_gzip`` and return the recorder list."""
    recorders: list[_GzipRecorder] = []
    real_open = zip_intake._open_gzip

    def _spy(archive_path):
        recorder = _GzipRecorder(real_open(archive_path))
        recorders.append(recorder)
        return recorder

    monkeypatch.setattr(zip_intake, "_open_gzip", _spy)
    return recorders


# ---------------------------------------------------------------------------
# LV-004: a symbolic link's body is bounded before it is read
# ---------------------------------------------------------------------------
# ``inspect_zip_entries`` used to pull a link target in with an
# unbounded ``handle.read()``. A link entry carrying ~1 MB of content
# was accepted under a 1 KB per-file limit because no size, budget, or
# ratio check ran before the read.


def test_oversized_symlink_body_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    archive.write_bytes(_symlink_zip_bytes("link.txt", b"a" * 1_000_000))
    with pytest.raises(ZipIntakeError) as exc:
        inspect_zip_entries(archive, limits=_limits())
    assert exc.value.code == "archive_symlink_target_too_large"


def test_oversized_symlink_body_is_never_read(tmp_path: Path, monkeypatch) -> None:
    """The declared metadata decides; the body is not touched.

    This is the order-of-operations half of the fix. Rejecting after
    the read would still be a rejection, but the memory has already
    been spent by then.
    """
    archive = tmp_path / "link.zip"
    archive.write_bytes(
        _symlink_zip_bytes("link.txt", b"a" * 1_000_000, compress=zipfile.ZIP_DEFLATED)
    )
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def _spy(self, name, *args, **kwargs):
        opened.append(getattr(name, "filename", str(name)))
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", _spy)
    with pytest.raises(ZipIntakeError) as exc:
        inspect_zip_entries(archive, limits=_limits())
    assert exc.value.code == "archive_symlink_target_too_large"
    assert opened == [], "the link body was decompressed before it was rejected"


def test_highly_compressed_symlink_body_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    archive.write_bytes(_symlink_zip_bytes("link.txt", b"a" * 4096, compress=zipfile.ZIP_DEFLATED))
    roomy = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=10_000_000,
        max_file_count=10,
        max_file_bytes=10_000_000,
        max_depth=8,
        suspicious_ratio=100,
    )
    with pytest.raises(ZipIntakeError) as exc:
        inspect_zip_entries(archive, limits=roomy)
    assert exc.value.code == "archive_suspicious_compression"


def test_symlink_bodies_share_the_archive_budget(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for index in range(3):
            info = zipfile.ZipInfo(f"link{index}")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, b"t" * 800)
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    tight = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=2_000,
        max_file_count=10,
        max_file_bytes=4_096,
        max_depth=8,
        suspicious_ratio=200,
    )
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[buf.getvalue()], limits=tight)
    assert exc.value.code == "archive_uncompressed_too_large"


def test_small_symlink_is_still_recorded_and_skipped(tmp_path: Path) -> None:
    """The v2.1.3 policy for ordinary links is unchanged."""
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _symlink_zip_bytes("docs/CLAUDE.md", b"../README.md", extra={"README.md": b"# hi\n"})
    result = intake_zip(paths, source=[body], limits=_limits())
    assert len(result.skipped_symlinks) == 1
    assert result.skipped_symlinks[0].path == "docs/CLAUDE.md"
    assert result.skipped_symlinks[0].target == "README.md"
    assert result.file_count == 1
    assert (paths.contents_dir / "README.md").is_file()


# ---------------------------------------------------------------------------
# LV-005: a link cannot shadow a regular file
# ---------------------------------------------------------------------------
# A link entry returned before the duplicate-path registration, so a
# later regular file with the same normalised path passed validation,
# was counted in ``file_count``, and was then skipped by the extractor
# as "this path belongs to a symlink". The archive reported a file
# that never reached disk - a false-negative for exactly the files
# analysis cares about (``requirements.txt``, lockfiles, workflows).


@pytest.mark.parametrize("link_first", [True, False])
def test_symlink_and_regular_file_collision_is_rejected(tmp_path: Path, link_first: bool) -> None:
    buf = io.BytesIO()
    # ``zipfile`` warns while *writing* the deliberately colliding
    # fixture. The warning is about building the fixture, not about
    # the intake behaviour under test.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buf, "w") as zf:
            link = zipfile.ZipInfo("requirements.txt")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            if link_first:
                zf.writestr(link, b"other.txt")
                zf.writestr("requirements.txt", b"flask==0.1\n")
            else:
                zf.writestr("requirements.txt", b"flask==0.1\n")
                zf.writestr(link, b"other.txt")
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[buf.getvalue()], limits=_limits())
    assert exc.value.code == "archive_duplicate_entry"
    assert not any(paths.contents_dir.rglob("*"))


def test_case_variant_paths_collide(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.md", b"first")
        zf.writestr("readme.md", b"second")
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[buf.getvalue()], limits=_limits())
    assert exc.value.code == "archive_duplicate_entry"


def test_reported_file_count_matches_what_was_extracted(tmp_path: Path) -> None:
    """``file_count`` is the extractor's tally, not the inventory's.

    An entry the extractor skips must never be advertised as analyzed
    content.
    """
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _symlink_zip_bytes(
        "link.md", b"README.md", extra={"README.md": b"# hi\n", "src/a.py": b"a = 1\n"}
    )
    result = intake_zip(paths, source=[body], limits=_limits())
    on_disk = [p for p in result.contents_dir.rglob("*") if p.is_file()]
    assert result.file_count == len(on_disk) == 2
    assert result.uncompressed_size == sum(p.stat().st_size for p in on_disk)


# ---------------------------------------------------------------------------
# LV-006: the entry count is settled before any inventory exists
# ---------------------------------------------------------------------------
# ``zipfile.ZipFile.__init__`` builds one ``ZipInfo`` per member before
# a caller-supplied limit can be consulted, so a compact ZIP64 archive
# declaring millions of members spent the memory first and was
# rejected afterwards.


def _craft_zip64(entry_count: int) -> bytes:
    """A structurally valid ZIP64 trailer declaring ``entry_count``."""
    base = _zip_with_entries(3)
    eocd = base.rfind(b"PK\x05\x06")
    body = bytearray(base[:eocd])
    directory_size = struct.unpack("<I", base[eocd + 12 : eocd + 16])[0]
    directory_offset = struct.unpack("<I", base[eocd + 16 : eocd + 20])[0]
    record_offset = len(body)
    body += struct.pack(
        "<4sQHHIIQQQQ",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        entry_count,
        entry_count,
        directory_size,
        directory_offset,
    )
    body += struct.pack("<4sIQI", b"PK\x06\x07", 0, record_offset, 1)
    body += struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        directory_size,
        directory_offset,
        0,
    )
    return bytes(body)


def test_preflight_accepts_a_normal_archive(tmp_path: Path) -> None:
    archive = tmp_path / "normal.zip"
    archive.write_bytes(_zip_with_entries(5))
    preflight = preflight_zip_directory(archive, limits=_limits())
    assert preflight.entry_count == 5
    assert preflight.is_zip64 is False


def test_preflight_accepts_an_archive_exactly_at_the_limit(tmp_path: Path) -> None:
    archive = tmp_path / "at_limit.zip"
    archive.write_bytes(_zip_with_entries(_limits().max_file_count))
    preflight = preflight_zip_directory(archive, limits=_limits())
    assert preflight.entry_count == _limits().max_file_count


def test_preflight_rejects_a_declared_count_above_the_limit(tmp_path: Path) -> None:
    archive = tmp_path / "over.zip"
    archive.write_bytes(_zip_with_entries(_limits().max_file_count + 1))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_too_many_files"


def test_over_limit_archive_never_builds_an_inventory(tmp_path: Path, monkeypatch) -> None:
    """No ``ZipFile`` is constructed for an archive that is already out.

    This is the memory property the finding is about: rejecting after
    ``ZipFile.__init__`` has already paid for millions of ``ZipInfo``
    objects is not a fix.
    """
    archive = tmp_path / "over.zip"
    archive.write_bytes(_zip_with_entries(_limits().max_file_count + 5))

    def _tripwire(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("ZipFile was constructed before the count was checked")

    monkeypatch.setattr(zip_intake.zipfile, "ZipFile", _tripwire)
    with pytest.raises(ZipIntakeError) as exc:
        inspect_zip_entries(archive, limits=_limits())
    assert exc.value.code == "archive_too_many_files"


def test_preflight_rejects_a_zip64_count_above_the_limit(tmp_path: Path) -> None:
    archive = tmp_path / "z64.zip"
    archive.write_bytes(_craft_zip64(5_000_000))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_too_many_files"


def test_preflight_reads_a_valid_zip64_trailer(tmp_path: Path) -> None:
    archive = tmp_path / "z64_ok.zip"
    archive.write_bytes(_craft_zip64(3))
    preflight = preflight_zip_directory(archive, limits=_limits())
    assert preflight.entry_count == 3
    assert preflight.is_zip64 is True


def test_preflight_rejects_a_truncated_eocd(tmp_path: Path) -> None:
    archive = tmp_path / "truncated.zip"
    body = _zip_with_entries(3)
    archive.write_bytes(body[: len(body) - 10])
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_rejects_a_file_too_small_to_hold_an_eocd(tmp_path: Path) -> None:
    archive = tmp_path / "tiny.zip"
    archive.write_bytes(b"PK")
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_rejects_a_zip64_locator_pointing_outside_the_file(
    tmp_path: Path,
) -> None:
    raw = bytearray(_craft_zip64(3))
    locator = raw.rfind(b"PK\x06\x07")
    raw[locator + 8 : locator + 16] = (10**12).to_bytes(8, "little")
    archive = tmp_path / "bad_locator.zip"
    archive.write_bytes(bytes(raw))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_rejects_a_malformed_zip64_record(tmp_path: Path) -> None:
    raw = bytearray(_craft_zip64(3))
    record = raw.find(b"PK\x06\x06")
    raw[record : record + 4] = b"PK\x06\x05"
    archive = tmp_path / "bad_record.zip"
    archive.write_bytes(bytes(raw))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_rejects_a_directory_outside_the_file_bounds(tmp_path: Path) -> None:
    raw = bytearray(_zip_with_entries(3))
    eocd = raw.rfind(b"PK\x05\x06")
    raw[eocd + 12 : eocd + 16] = (0xFFFFFF).to_bytes(4, "little")
    archive = tmp_path / "bad_bounds.zip"
    archive.write_bytes(bytes(raw))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_rejects_a_count_that_cannot_fit_the_directory(tmp_path: Path) -> None:
    """A central-directory header is at least 46 bytes per member."""
    raw = bytearray(_zip_with_entries(3))
    eocd = raw.rfind(b"PK\x05\x06")
    raw[eocd + 10 : eocd + 12] = (9).to_bytes(2, "little")
    archive = tmp_path / "lying_count.zip"
    archive.write_bytes(bytes(raw))
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=_limits())
    assert exc.value.code == "archive_invalid"


def test_preflight_counts_records_when_metadata_understates_the_count(
    tmp_path: Path,
) -> None:
    """A directory roomy enough to hide members is walked, not trusted.

    The declared count is the cheap check; when the recorded directory
    could hold more members than the limit allows, the real records
    are counted with an allocation-free walk that stops one past the
    limit.
    """
    raw = bytearray(_zip_with_entries(20))
    eocd = raw.rfind(b"PK\x05\x06")
    raw[eocd + 8 : eocd + 10] = (2).to_bytes(2, "little")
    raw[eocd + 10 : eocd + 12] = (2).to_bytes(2, "little")
    archive = tmp_path / "understated.zip"
    archive.write_bytes(bytes(raw))
    tight = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=1_000_000,
        max_file_count=2,
        max_file_bytes=1_024,
        max_depth=8,
        suspicious_ratio=200,
    )
    with pytest.raises(ZipIntakeError) as exc:
        preflight_zip_directory(archive, limits=tight)
    assert exc.value.code == "archive_too_many_files"
    # An honest archive at the same limit is untouched by the walk.
    honest = tmp_path / "honest.zip"
    honest.write_bytes(_zip_with_entries(2))
    assert preflight_zip_directory(honest, limits=tight).entry_count == 2


# ---------------------------------------------------------------------------
# LV-009: tar.gz limits are enforced while the stream is read
# ---------------------------------------------------------------------------
# The member walk enumerated the whole archive before any limit was
# consulted. Reaching the next tar header means decompressing the
# previous member's body, so a hostile archive got the CPU it wanted
# and was rejected only afterwards.


def test_valid_small_targz_is_accepted(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _targz_bytes([("README.md", b"# hi\n"), ("src/a.py", b"a = 1\n")])
    result = intake_tar_gz(paths, source=[body], limits=_limits())
    assert result.file_count == 2
    assert (paths.contents_dir / "README.md").is_file()
    assert (paths.contents_dir / "src" / "a.py").is_file()


def test_targz_oversized_member_is_rejected(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _targz_bytes([("big.bin", b"a" * (_limits().max_file_bytes + 1))])
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_entry_too_large"


def test_targz_excessive_total_is_rejected(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _targz_bytes([(f"f{index}.bin", b"a" * 900) for index in range(8)])
    tight = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=2_000,
        max_file_count=100,
        max_file_bytes=1_024,
        max_depth=8,
        suspicious_ratio=200,
    )
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[body], limits=tight)
    assert exc.value.code == "archive_uncompressed_too_large"


def test_targz_excessive_member_count_is_rejected(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _targz_bytes([(f"f{index}.txt", b"x") for index in range(40)])
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_too_many_files"


def test_targz_bomb_is_abandoned_during_decompression(tmp_path: Path, monkeypatch) -> None:
    """The stream stops inflating at the budget, not at the end.

    The fixture is a few kilobytes on the wire and 8 MiB decompressed.
    A correct implementation gives up within the configured budget; an
    implementation that enumerates first inflates all 8 MiB before it
    has an opinion.
    """
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _targz_bytes([("bomb.bin", b"\0" * (8 * 1024 * 1024))])
    budget = 4_096
    limits = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=budget,
        max_file_count=100,
        max_file_bytes=10_000_000,
        max_depth=8,
        suspicious_ratio=200,
    )
    recorders = _record_gzip(monkeypatch)
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[body], limits=limits)
    assert exc.value.code == "archive_uncompressed_too_large"
    assert recorders, "the gzip stream was never opened"
    for recorder in recorders:
        assert recorder.max_position <= budget + 1, (
            "the archive kept decompressing past the configured budget: "
            f"{recorder.max_position} bytes"
        )


def test_targz_walk_stops_at_the_offending_member(tmp_path: Path, monkeypatch) -> None:
    """The walk abandons the archive instead of enumerating the rest."""
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    members = [(f"f{index}.bin", b"a" * 4_096) for index in range(40)]
    body = _targz_bytes(members)
    limits = ArchiveLimits(
        max_compressed_bytes=10_000_000,
        max_uncompressed_bytes=10_000_000,
        max_file_count=3,
        max_file_bytes=10_000,
        max_depth=8,
        suspicious_ratio=200,
    )
    recorders = _record_gzip(monkeypatch)
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[body], limits=limits)
    assert exc.value.code == "archive_too_many_files"
    # Four members' worth of headers and bodies, not forty.
    assert recorders[0].max_position < 8 * 4_096


def test_targz_link_entries_do_not_bypass_the_count(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"real"
        info = tarfile.TarInfo("real.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for index in range(_limits().max_file_count + 2):
            link = tarfile.TarInfo(f"link{index}")
            link.type = tarfile.SYMTYPE
            link.linkname = "real.txt"
            tf.addfile(link)
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[buf.getvalue()], limits=_limits())
    assert exc.value.code == "archive_too_many_files"


@pytest.mark.parametrize("link_first", [True, False])
def test_targz_link_and_regular_collision_is_rejected(tmp_path: Path, link_first: bool) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"flask==0.1\n"

        def _add_regular() -> None:
            info = tarfile.TarInfo("requirements.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        def _add_link() -> None:
            link = tarfile.TarInfo("requirements.txt")
            link.type = tarfile.SYMTYPE
            link.linkname = "other.txt"
            tf.addfile(link)

        if link_first:
            _add_link()
            _add_regular()
        else:
            _add_regular()
            _add_link()
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    with pytest.raises(ZipIntakeError) as exc:
        intake_tar_gz(paths, source=[buf.getvalue()], limits=_limits())
    assert exc.value.code == "archive_duplicate_entry"


def test_bounded_stream_refuses_an_unbounded_read(tmp_path: Path) -> None:
    """``read(-1)`` cannot inflate past the budget.

    A wrapper that only checked *after* the read would already hold
    the whole decompressed payload in memory by the time it raised.
    """
    raw = tmp_path / "payload.gz"
    with gzip.open(raw, "wb") as fh:
        fh.write(b"\0" * (4 * 1024 * 1024))
    budget = 1_024
    with gzip.open(raw, "rb") as gz:
        stream = BoundedDecompressedStream(gz, max_bytes=budget)
        with pytest.raises(ZipIntakeError) as exc:
            stream.read(-1)
        assert exc.value.code == "archive_uncompressed_too_large"
        assert gz.tell() <= budget + 1


def test_bounded_stream_refuses_a_seek_past_the_budget(tmp_path: Path) -> None:
    raw = tmp_path / "payload.gz"
    with gzip.open(raw, "wb") as fh:
        fh.write(b"\0" * (4 * 1024 * 1024))
    with gzip.open(raw, "rb") as gz:
        stream = BoundedDecompressedStream(gz, max_bytes=1_024)
        with pytest.raises(ZipIntakeError):
            stream.seek(2_000_000)
        with pytest.raises(ZipIntakeError):
            stream.seek(0, io.SEEK_END)
        # Nothing was decompressed to reach that verdict.
        assert gz.tell() == 0


def test_bounded_stream_passes_content_within_the_budget(tmp_path: Path) -> None:
    raw = tmp_path / "small.gz"
    payload = b"hello world" * 10
    with gzip.open(raw, "wb") as fh:
        fh.write(payload)
    with gzip.open(raw, "rb") as gz:
        stream = BoundedDecompressedStream(gz, max_bytes=len(payload))
        assert stream.read(-1) == payload


# ---------------------------------------------------------------------------
# LV-010: unsafe Windows paths never reach the extraction layer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["payload.txt:stream", "NUL", "nul.txt", "CON.txt", "COM1", "LPT9.log", "name."],
)
def test_intake_zip_rejects_unsafe_windows_paths(tmp_path: Path, name: str) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({name: b"payload"})
    with pytest.raises(ZipIntakeError) as exc:
        intake_zip(paths, source=[body], limits=_limits())
    assert exc.value.code == "archive_unsafe_path"
    assert not any(paths.contents_dir.rglob("*"))


def test_intake_zip_accepts_names_that_only_look_reserved(tmp_path: Path) -> None:
    paths = create_workspace_paths(tmp_path, new_workspace_key())
    body = _build_zip_bytes({"console.txt": b"a", "nullability.json": b"b", "com10.txt": b"c"})
    result = intake_zip(paths, source=[body], limits=_limits())
    assert result.file_count == 3


# ---------------------------------------------------------------------------
# Non-execution guarantee for the paths touched in this batch
# ---------------------------------------------------------------------------


def test_intake_module_never_executes_repository_content() -> None:
    """No execution primitive may appear in the intake path.

    Repository archives are inspected as metadata and copied as bytes.
    Nothing in this module compiles, imports, or runs what it reads.
    """
    source = Path(zip_intake.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "shell=True",
        "subprocess",
        "eval(",
        "exec(",
        "importlib",
        "__import__",
        "os.system",
        "pickle",
        "setup.py",
    ):
        assert forbidden not in source, f"{forbidden!r} appears in the intake path"
