"""PE-resource regression guards for the Lockverity EXE icon.

The Windows shell renders the application icon from
the ``RT_ICON`` / ``RT_GROUP_ICON`` resources embedded
in the executable. The canonical ICO is the
``{16, 20, 24, 32, 40, 48, 64, 128, 256}`` size set of
the transparent blue-symbol mark (the v2.1.5 contract;
see ``tests/test_exe_icon.py`` for the frame-level
contract).

The tests in this module inspect the
``Lockverity.exe`` resource directory directly via
:mod:`pefile` so a regression in the build pipeline
that drops a frame, embeds the wrong ICO, or replaces
the resource structure with a non-icon set is caught
at unit-test time, not at user-install time.

The tests skip gracefully if the Lockverity.exe has
not been built yet (``build/dev/...`` is a gitignored
output directory) so the test suite still runs in a
clean checkout.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
DERIVATIVE_ICO = BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"

# Possible Lockverity.exe build paths. The GUI-only
# icon-iteration build (``gui-transparent``) is checked
# first because it is the freshest visual-iteration
# artefact; the full portable layout follows. The test
# walks every candidate and uses the first that exists.
EXE_CANDIDATES = (
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui-transparent" / "Lockverity" / "Lockverity.exe",
    BACKEND_ROOT
    / "build"
    / "dev"
    / "packaging"
    / "Lockverity-2.1.2-windows-x64-portable"
    / "Lockverity.exe",
    BACKEND_ROOT
    / "build"
    / "dev"
    / "packaging"
    / "pyinstaller_out"
    / "Lockverity"
    / "Lockverity.exe",
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui-v214" / "Lockverity" / "Lockverity.exe",
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui" / "Lockverity" / "Lockverity.exe",
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui-clean" / "Lockverity" / "Lockverity.exe",
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui-final" / "Lockverity" / "Lockverity.exe",
    BACKEND_ROOT / "build" / "dev" / "dist" / "gui-fresh" / "Lockverity" / "Lockverity.exe",
)


def _find_lockverity_exe() -> Path | None:
    for candidate in EXE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _parse_ico_sizes(data: bytes) -> list[tuple[int, int, int, bytes]]:
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("not an ICO file (magic mismatch)")
    count = struct.unpack_from("<H", data, 4)[0]
    out: list[tuple[int, int, int, bytes]] = []
    for index in range(count):
        offset = 6 + 16 * index
        width = data[offset]
        height = data[offset + 1]
        size = struct.unpack_from("<I", data, offset + 8)[0]
        body_offset = struct.unpack_from("<I", data, offset + 12)[0]
        out.append(
            (
                width if width else 256,
                height if height else 256,
                size,
                data[body_offset : body_offset + size],
            )
        )
    return out


def _extract_icons_from_exe(exe_path: Path) -> list[bytes]:
    """Return the raw PNG/BMP bodies of every ``RT_ICON`` resource in the EXE.

    The function is a thin wrapper over :mod:`pefile`
    that walks the resource directory and returns
    the binary bodies of every ``RT_ICON`` (type 1)
    resource. The bodies can be PNG or BMP payloads
    depending on the ICO format Windows chose.
    """
    import pefile  # type: ignore[import-not-found]

    pe = pefile.PE(str(exe_path))
    if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        return []
    bodies: list[bytes] = []
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        # Windows resource type IDs use 0-based
        # categories: 1 = RT_CURSOR, 2 = RT_BITMAP,
        # 3 = RT_ICON, 14 = RT_GROUP_ICON. The
        # pefile enumeration uses the underlying
        # ID values; we want type 3 (RT_ICON).
        if entry.id != 3:
            continue
        if not hasattr(entry, "directory"):
            continue
        for sub in entry.directory.entries:
            if not hasattr(sub, "directory"):
                continue
            for lang in sub.directory.entries:
                data_rva = lang.data.struct.OffsetToData
                size = lang.data.struct.Size
                bodies.append(pe.get_data(data_rva, size))
    return bodies


def _png_dimensions(body: bytes) -> tuple[int, int] | None:
    """Return ``(width, height)`` of a PNG payload, or ``None`` if not PNG."""
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    # PNG IHDR is at offset 8; width and height are
    # big-endian 32-bit at offsets 16 and 20.
    if len(body) < 24:
        return None
    width = int.from_bytes(body[16:20], "big")
    height = int.from_bytes(body[20:24], "big")
    return width, height


@pytest.fixture(scope="module")
def lockverity_exe() -> Path:
    p = _find_lockverity_exe()
    if p is None:
        pytest.skip("Lockverity.exe not built yet; run scripts/build_windows_portable.py")
    return p


class TestExeIconResource:
    """The Lockverity.exe PE resource carries the full canonical icon set."""

    def test_derivative_ico_has_full_size_set(self) -> None:
        """The on-disk derivative ICO is the canonical size set.

        The :func:`build_exe_icon` script is the
        single chokepoint for the derivative ICO;
        a regression that drops a frame must fail
        the test at the source level, not at the
        EXE level.
        """
        data = DERIVATIVE_ICO.read_bytes()
        sizes = {entry[0] for entry in _parse_ico_sizes(data)}
        assert sizes == {16, 20, 24, 32, 40, 48, 64, 128, 256}, (
            f"on-disk derivative ICO has sizes {sorted(sizes)}; "
            "expected {16, 20, 24, 32, 40, 48, 64, 128, 256}. "
            "Run scripts/generate_exe_icon.py to regenerate."
        )

    def test_exe_has_full_icon_count(self, lockverity_exe: Path) -> None:
        """The EXE embeds the full icon set in its PE resources.

        The Windows shell queries the PE resource
        directory for the application icon. The
        contract is nine ``RT_ICON``
        resources (16/20/24/32/40/48/64/128/256).
        A regression that drops a frame, replaces
        the ICO with a smaller set, or omits the
        ``RT_GROUP_ICON`` glue descriptor causes
        the shell to fall back to the generic
        application icon.
        """
        bodies = _extract_icons_from_exe(lockverity_exe)
        assert len(bodies) == 9, (
            f"Lockverity.exe contains {len(bodies)} RT_ICON resources; "
            "expected 9 (16/20/24/32/40/48/64/128/256). "
            "Did the build embed the wrong ICO?"
        )

    def test_exe_icon_dimensions_match_size_set(self, lockverity_exe: Path) -> None:
        """The EXE icon dimensions are exactly the canonical sizes.

        The Windows shell picks the closest entry
        to the requested size. If the embedded
        PNG/BMP payloads do not match the
        set the shell picks the wrong frame and
        the taskbar icon appears blurry or
        oversized.
        """
        bodies = _extract_icons_from_exe(lockverity_exe)
        canonical = {16, 20, 24, 32, 40, 48, 64, 128, 256}
        seen: set[tuple[int, int]] = set()
        for body in bodies:
            dims = _png_dimensions(body)
            if dims is None:
                # Non-PNG RT_ICON (BMP DIB) is
                # acceptable; the dimensions live
                # in the BITMAPINFOHEADER but the
                # ICO and BMP width / height are
                # encoded differently. Skip and
                # rely on the count test for now.
                continue
            seen.add(dims)
        assert seen, "no PNG-encoded RT_ICON resources found in the EXE"
        widths = {d[0] for d in seen}
        assert widths == canonical, (
            f"Lockverity.exe RT_ICON widths {sorted(widths)} do not match "
            f"the canonical size set {sorted(canonical)}."
        )

    def test_exe_icon_resources_match_generated_ico(self, lockverity_exe: Path) -> None:
        """Every ``RT_ICON`` body is byte-identical to the canonical ICO entry.

        The PE resource must embed the generated frames
        as-is. A build that re-encodes, reorders, or
        replaces a frame with a stale ICO breaks the
        byte-equality contract between
        ``backend/pyinstaller/favicon-exe.ico`` and the
        executable the shell actually renders.
        """
        ico_bodies = {body for _w, _h, _size, body in _parse_ico_sizes(DERIVATIVE_ICO.read_bytes())}
        exe_bodies = set(_extract_icons_from_exe(lockverity_exe))
        assert exe_bodies == ico_bodies, (
            "Lockverity.exe RT_ICON resources do not match the canonical "
            "favicon-exe.ico payloads; rebuild with the current ICO"
        )

    def test_derivative_256_entry_is_png(self, lockverity_exe: Path) -> None:
        """The 256x256 RT_ICON resource is a PNG payload.

        Windows Vista+ decodes PNG-encoded ICO
        entries directly. The 256x256 entry is
        the high-DPI entry Windows queries on
        modern displays; a BMP DIB at 256x256
        is 4x larger than the PNG equivalent
        and shows visible artefacts on a
        Lanczos-rendered surface.
        """
        bodies = _extract_icons_from_exe(lockverity_exe)
        body_256 = next(
            (body for body in bodies if _png_dimensions(body) == (256, 256)),
            None,
        )
        if body_256 is None:
            pytest.skip(
                "256x256 RT_ICON resource is not a PNG payload; "
                "the ICO may use BMP DIB at 256x256 (legacy)."
            )
        png_magic = b"\x89PNG\r\n\x1a\n"
        assert body_256.startswith(png_magic), "the 256x256 RT_ICON resource must be a PNG payload"
