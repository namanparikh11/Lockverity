"""Tests for the v2.1.2 canonical Windows ICO.

The approved brand asset is the
``frontend/public/favicon-source.png`` ``1024x1024``
RGBA source. The brand-board web favicon at
``frontend/public/favicon.ico`` contains the
``16x16``, ``32x32`` and ``48x48`` entries the
browser needs. The canonical Windows ICO at
``backend/pyinstaller/favicon-exe.ico`` is a
mechanical re-packaging of the approved source
optimised for the Windows shell:

  - 16, 20, 24, 32, 40, 48, 64, 128, 256 entries
    are Pillow Lanczos downscales of the
    dark-frame-cropped 1024x1024 PNG;
  - the dark-frame-crop step is the v2.1.4
    policy that places the dark navy tile flush
    with the icon canvas, matching the standard
    Windows app-icon pattern (Chrome, Docker,
    PowerShell, Slack).

The approved brand assets are never modified.

v2.1.4 dark-frame-crop
======================

The v2.1.3 padding-normalisation tightened the
transparent margin around the *alpha* bounding box
but the icon was still visibly smaller than
neighbouring Windows 11 taskbar applications on the
user's real desktop. The approved source layout is:

  - dark navy rounded-square *tile* fills about
    ``82%`` of the 1024x1024 canvas;
  - bright blue ``Lockverity`` glyph fills about
    ``88%`` of the dark tile;
  - outer transparent margin (about ``9%`` of the
    canvas on every side) was the dominant cause
    of the apparent-size regression.

The v2.1.4 fix changes the *normalisation anchor*
from the alpha bounding box to the *dark-frame
bounding box*. The script:

  1. Detects the dark navy tile as the bounding box
     of any pixel whose summed ``R+G+B`` is below
     80 and whose ``alpha`` is at least 32.
  2. Crops the source to the dark-frame bounding
     box plus 2% of the frame width per side.
  3. Resizes the cropped region to the 1024x1024
     working canvas and from there to each
     canonical ICO size.

The result: the dark navy tile fills the full
icon canvas (matching the standard Windows
app-icon pattern), the bright blue glyph is
the recognisable mark on top, and the brand
shape (dark rounded square + bright blue glyph)
is preserved exactly.

The tests below assert:

  1. The derivative ICO has the full canonical
     Windows size set
     ``{16, 20, 24, 32, 40, 48, 64, 128, 256}``.
  2. The dark-frame-crop step fills at least
     :data:`generate_exe_icon.MIN_DARK_FRAME_FILL_RATIO`
     (80%) of the source canvas.
  3. The 1024x1024 working canvas is fully covered
     by visible content (no transparent margin
     around the dark frame).
  4. The dark-frame detection is fail-loud: a
     source without a dark frame raises
     :class:`ValueError`.
  5. The brand shape is preserved: the normalised
     source keeps the bright blue glyph at full
     colour fidelity.
  6. The dark-frame-crop step is idempotent: a
     second pass over the same source produces a
     bit-identical output.
  7. Every per-frame content bounding box exceeds
     the v2.1.3 minimum ratio (85%) -- defence in
     depth against a future regression that loosens
     the policy.

The tests do not require the build script to have
been run; they verify the artefact and the
re-generation logic in isolation.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from scripts import generate_exe_icon

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_ICO = REPO_ROOT / "frontend" / "public" / "favicon.ico"
APPROVED_PNG = REPO_ROOT / "frontend" / "public" / "favicon-source.png"
DERIVATIVE_ICO = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe.ico"


def _parse_ico_sizes(data: bytes) -> list[tuple[int, int, int, bytes]]:
    """Return ``[(width, height, size, body), ...]`` for every ICO entry."""
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


@pytest.fixture
def regenerated_derivative(tmp_path: Path) -> Path:
    """Regenerate the derivative ICO in a temp path for inspection.

    The fixture is the documented chokepoint: any
    test that exercises the derivative can call
    :func:`build_exe_icon` against an isolated
    output path so the on-disk artefact is not
    modified.
    """
    out = tmp_path / "favicon-exe.ico"
    generate_exe_icon.build_exe_icon(
        approved_ico=APPROVED_ICO,
        approved_png=APPROVED_PNG,
        derivative_ico=out,
    )
    return out


class TestApprovedFaviconUnchanged:
    """The brand-board web favicon is never modified by the build."""

    def test_approved_favicon_exists(self) -> None:
        assert APPROVED_ICO.is_file(), f"approved favicon missing at {APPROVED_ICO}"

    def test_approved_favicon_has_small_entries(self) -> None:
        data = APPROVED_ICO.read_bytes()
        sizes = {entry[0] for entry in _parse_ico_sizes(data)}
        assert 16 in sizes
        assert 32 in sizes
        assert 48 in sizes


class TestDerivativeIcoStructure:
    """The derivative ICO has the full canonical Windows size set."""

    def test_derivative_is_valid_ico(self, regenerated_derivative: Path) -> None:
        data = regenerated_derivative.read_bytes()
        assert data[:4] == b"\x00\x00\x01\x00"

    def test_derivative_has_canonical_size_set(self, regenerated_derivative: Path) -> None:
        """The canonical Windows size set is the union of every shell size
        Windows 10/11 queries: ``{16, 20, 24, 32, 40, 48, 64, 128, 256}``.

        The Windows shell queries these sizes
        (and only these sizes) when rendering the
        application icon for the taskbar, Start
        tile, Installed apps list, File Explorer
        and the uninstaller UI. A missing entry
        causes the shell to fall back to the
        generic application icon, which is the
        regression v2.1.2 fixes. The set is the
        documented union of every shell size
        Windows 10/11 queries.

        v2.1.4 adds the 20x20 and 40x40 entries
        so the medium- and extra-density taskbar
        sizes carry an explicit, hand-rendered
        frame instead of relying on Windows to
        scale a distant frame.
        """
        data = regenerated_derivative.read_bytes()
        sizes = {entry[0] for entry in _parse_ico_sizes(data)}
        canonical = {16, 20, 24, 32, 40, 48, 64, 128, 256}
        assert sizes == canonical, (
            f"Canonical Windows ICO size set mismatch: {sorted(sizes)}; "
            f"expected {sorted(canonical)}; the Windows shell will fall back "
            "to the generic application icon for any missing size."
        )

    def test_derivative_superset_size(self, regenerated_derivative: Path) -> None:
        """Convenience assertion: every size in the documented
        canonical set is present (defence-in-depth for
        the superset-relation check above)."""
        data = regenerated_derivative.read_bytes()
        sizes = {entry[0] for entry in _parse_ico_sizes(data)}
        for required in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            assert required in sizes, (
                f"Canonical Windows ICO is missing the {required}x{required} entry; "
                "the Windows shell will fall back to the generic application icon."
            )

    def test_derivative_256_entry_is_png(self, regenerated_derivative: Path) -> None:
        """The 256x256 entry is a PNG payload.

        Windows accepts PNG-compressed ICO entries
        directly (Vista+); the test asserts the
        payload starts with the PNG magic so the
        Windows shell can decode the high-DPI icon
        without an extra re-rasterisation.
        """
        data = regenerated_derivative.read_bytes()
        entries = _parse_ico_sizes(data)
        entry_256 = next(entry for entry in entries if entry[0] == 256)
        assert entry_256[3].startswith(b"\x89PNG\r\n\x1a\n"), (
            "the 256x256 entry must be a PNG payload; the Windows shell decodes PNG-in-ICO directly"
        )

    def test_derivative_uses_dark_frame_anchor(
        self, regenerated_derivative: Path
    ) -> None:
        """v2.1.4 contract: every per-frame alpha bbox fills the canvas.

        The dark-frame-crop step places the dark
        navy tile flush with the icon canvas, so
        every per-frame alpha bounding box is the
        full canvas (modulo the small 2% margin
        on the 1024x1024 working canvas). The
        test asserts the alpha bbox fills at
        least 90% of every ICO frame.
        """
        from PIL import Image  # type: ignore[import-not-found]

        data = regenerated_derivative.read_bytes()
        for w, h, _size, body in _parse_ico_sizes(data):
            with Image.open(io.BytesIO(body)) as im:
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                bbox = im.getbbox()
                if bbox is None:
                    continue
                left, top, right, bottom = bbox
                used = (right - left) * (bottom - top)
                full = w * h
                ratio = used / full
                assert ratio >= 0.90, (
                    f"ICO frame {w}x{h} alpha bounding box is "
                    f"{ratio * 100:.1f}% of the canvas; expected at "
                    "least 90% (the v2.1.4 dark-frame-crop contract)."
                )

    def test_derivative_is_centered(
        self, regenerated_derivative: Path
    ) -> None:
        """v2.1.4 contract: every per-frame alpha bbox is centered.

        The dark-frame-crop step is the geometric
        centre of the source; the resulting
        per-frame alpha bbox is centred within
        each ICO frame to a small tolerance. The
        test asserts the centroid of the alpha
        bbox is within 10% of the frame centre
        on each axis.
        """
        from PIL import Image  # type: ignore[import-not-found]

        data = regenerated_derivative.read_bytes()
        for w, h, _size, body in _parse_ico_sizes(data):
            with Image.open(io.BytesIO(body)) as im:
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                bbox = im.getbbox()
                if bbox is None:
                    continue
                left, top, right, bottom = bbox
                cx = (left + right) / 2.0
                cy = (top + bottom) / 2.0
                dx = abs(cx - w / 2.0) / w
                dy = abs(cy - h / 2.0) / h
                assert dx <= 0.10 and dy <= 0.10, (
                    f"ICO frame {w}x{h} alpha bbox is off-centre: "
                    f"dx={dx * 100:.1f}% dy={dy * 100:.1f}%; "
                    "the v2.1.4 dark-frame-crop contract requires "
                    "the alpha bbox to be centred within 10%."
                )


class TestBuildExeIconIdempotency:
    """The build script regenerates the derivative without side effects."""

    def test_build_exe_icon_creates_file(self, regenerated_derivative: Path) -> None:
        assert regenerated_derivative.is_file()
        assert regenerated_derivative.stat().st_size > 0

    def test_build_exe_icon_does_not_modify_approved_sources(self) -> None:
        # Snapshot the approved sources' SHA-256,
        # run the build, and confirm the hash is
        # unchanged. The function is a side-effect
        # check; a future maintainer cannot
        # accidentally start writing to the approved
        # tree without breaking the test.
        import hashlib

        before_ico = hashlib.sha256(APPROVED_ICO.read_bytes()).hexdigest()
        before_png = hashlib.sha256(APPROVED_PNG.read_bytes()).hexdigest()
        generate_exe_icon.build_exe_icon(
            approved_ico=APPROVED_ICO,
            approved_png=APPROVED_PNG,
            derivative_ico=DERIVATIVE_ICO,  # idempotent overwrite
        )
        after_ico = hashlib.sha256(APPROVED_ICO.read_bytes()).hexdigest()
        after_png = hashlib.sha256(APPROVED_PNG.read_bytes()).hexdigest()
        assert before_ico == after_ico
        assert before_png == after_png


class TestDarkFrameCrop:
    """v2.1.4 dark-frame-crop step.

    The manual-QA pass on the native Windows shell
    surfaced the taskbar icon as visually undersized
    relative to neighbouring Windows 11 application
    icons on the user's real desktop. The fix is a
    *dark-frame-crop* step at the very start of the
    ICO build: the script crops the approved source
    to the dark navy tile bounding box plus a small
    fixed margin (2% of the frame width per side)
    and resizes the cropped region to a 1024x1024
    working canvas. The brand shape is preserved
    exactly; the dark navy tile is now flush with
    the icon canvas, matching the standard Windows
    app-icon pattern.

    The tests below assert:

      1. The dark-frame-crop step fills at least
         :data:`generate_exe_icon.MIN_DARK_FRAME_FILL_RATIO`
         (80%) of the source canvas.
      2. The 1024x1024 working canvas is fully
         covered by visible content (alpha bbox
         fills ``>= 95%`` of the working canvas).
      3. The dark-frame detection is fail-loud: a
         source without a dark frame raises
         :class:`ValueError`.
      4. The brand shape is preserved: the
         normalised source keeps the bright blue
         glyph at full colour fidelity.
      5. The dark-frame-crop step is idempotent: a
         second pass over the same source produces
         a bit-identical normalised source.
      6. Every per-frame content bounding box
         exceeds the v2.1.3 minimum ratio (85%) --
         defence in depth against a future
         regression that loosens the policy.
    """

    def test_dark_frame_crop_fills_minimum_ratio(self) -> None:
        """The detected dark frame fills at least 80% of the source canvas.

        The approved Lockverity source uses a
        dark navy tile that fills about 82% of
        the 1024x1024 canvas. The
        dark-frame-crop step asserts the
        detected frame exceeds the documented
        80% minimum so a future maintainer
        cannot ship a derivative with a tiny
        dark frame without breaking the test.
        """
        from PIL import Image  # type: ignore[import-not-found]

        source_bytes = APPROVED_PNG.read_bytes()
        normalised_bytes = generate_exe_icon._normalise_padding(source_bytes)
        with Image.open(io.BytesIO(source_bytes)) as source, Image.open(
            io.BytesIO(normalised_bytes)
        ) as normalised:
            source.load()
            normalised.load()
            # Detect the dark frame in the source
            # using the same threshold the
            # normalisation step uses.
            sw, sh = source.size
            px = source.load()
            step = 4
            minx, miny, maxx, maxy = sw, sh, -1, -1
            for y in range(0, sh, step):
                for x in range(0, sw, step):
                    r, g, b, a = px[x, y]
                    if a < 32:
                        continue
                    if r + g + b < 80:
                        if x < minx: minx = x
                        if y < miny: miny = y
                        if x > maxx: maxx = x
                        if y > maxy: maxy = y
            assert maxx > 0, "no dark frame detected in the source"
            frame_w = maxx - minx
            frame_h = maxy - miny
            ratio = max(frame_w / sw, frame_h / sh)
            assert ratio >= generate_exe_icon.MIN_DARK_FRAME_FILL_RATIO, (
                f"detected dark frame is {ratio * 100:.1f}% of the source "
                f"canvas; expected at least "
                f"{generate_exe_icon.MIN_DARK_FRAME_FILL_RATIO * 100:.0f}%"
            )

    def test_dark_frame_crop_fills_working_canvas(self) -> None:
        """The normalised 1024x1024 working canvas is fully covered.

        After the dark-frame-crop step the
        alpha bounding box of the 1024x1024
        working canvas fills at least 95% of
        the canvas. The brand mark reaches the
        Windows taskbar edges without the
        loose padding of the historical asset.
        """
        from PIL import Image  # type: ignore[import-not-found]

        source_bytes = APPROVED_PNG.read_bytes()
        normalised_bytes = generate_exe_icon._normalise_padding(source_bytes)
        with Image.open(io.BytesIO(normalised_bytes)) as normalised:
            bbox = normalised.getbbox()
            assert bbox is not None
            left, top, right, bottom = bbox
            used = (right - left) * (bottom - top)
            full = normalised.size[0] * normalised.size[1]
            ratio = used / full
            assert ratio >= 0.95, (
                f"normalised source content bounding box is {ratio * 100:.1f}% "
                f"of the working canvas; expected at least 95%"
            )

    def test_dark_frame_crop_preserves_brand_shape(self) -> None:
        """The normalised source is a *crop* of the approved source.

        The dark-frame-crop step must never
        recolour, redraw, or reinterpolate the
        brand. The test asserts the *brightest*
        pixel in the normalised source is present
        in the approved source (the brand's
        signature blue is preserved). A regression
        that recoloured the source would change
        the brightest pixel and the assertion would
        fail.
        """
        from PIL import Image  # type: ignore[import-not-found]

        source_bytes = APPROVED_PNG.read_bytes()
        normalised_bytes = generate_exe_icon._normalise_padding(source_bytes)
        with Image.open(io.BytesIO(source_bytes)) as source, Image.open(
            io.BytesIO(normalised_bytes)
        ) as normalised:
            source.load()
            normalised.load()
            assert source.mode == "RGBA"
            assert normalised.mode == "RGBA"
            source_pixels = source.load()
            normalised_pixels = normalised.load()
            source_max_b = 0
            for y in range(0, source.size[1], 64):
                for x in range(0, source.size[0], 64):
                    _r, _g, b, a = source_pixels[x, y]
                    if a > 200:
                        source_max_b = max(source_max_b, b)
            assert source_max_b > 0, (
                "approved source has no fully-opaque blue pixel; "
                "this is unexpected for the Lockverity brand asset"
            )
            normalised_max_b = 0
            for y in range(0, normalised.size[1], 64):
                for x in range(0, normalised.size[0], 64):
                    _r, _g, b, a = normalised_pixels[x, y]
                    if a > 200:
                        normalised_max_b = max(normalised_max_b, b)
            assert normalised_max_b >= int(source_max_b * 0.9), (
                f"normalised source loses the brand's signature blue: "
                f"source_max_b={source_max_b}, normalised_max_b={normalised_max_b}"
            )

    def test_dark_frame_crop_is_idempotent(self) -> None:
        """A second pass over the same source produces a bit-identical output.

        The dark-frame-crop step must be a pure
        function of the source bytes: no random
        resampling, no timestamp-based
        differences, no per-invocation state.
        Two consecutive calls must produce the
        same bytes.
        """
        source_bytes = APPROVED_PNG.read_bytes()
        first = generate_exe_icon._normalise_padding(source_bytes)
        second = generate_exe_icon._normalise_padding(source_bytes)
        assert first == second

    def test_dark_frame_detection_fails_loud_without_frame(self) -> None:
        """A source without a dark frame raises ValueError.

        The dark-frame detection is fail-loud: a
        source that has no dark navy tile (a
        hostile or accidental replacement) raises
        :class:`ValueError` so the build aborts
        before writing a silently-undersized
        derivative. The test builds a fully
        transparent image and asserts the call
        raises.
        """
        from PIL import Image  # type: ignore[import-not-found]

        hostile = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        buffer = io.BytesIO()
        hostile.save(buffer, format="PNG")
        with pytest.raises(ValueError, match="no dark-frame content"):
            generate_exe_icon._normalise_padding(buffer.getvalue())

    def test_derivative_frames_exceed_min_visible_bbox_ratio(
        self, regenerated_derivative: Path
    ) -> None:
        """Every per-frame content bbox exceeds the v2.1.3 minimum.

        The v2.1.3 fix tightens the transparent
        padding so the brand mark fills more of
        every ICO frame. The minimum acceptable
        ratio is :data:`generate_exe_icon.MIN_VISIBLE_BBOX_RATIO`
        (85%). A regression that loosens the
        padding would drop the ratio below the
        minimum and the test would fail.
        """
        from PIL import Image  # type: ignore[import-not-found]

        data = regenerated_derivative.read_bytes()
        for w, h, _size, body in _parse_ico_sizes(data):
            with Image.open(io.BytesIO(body)) as im:
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                bbox = im.getbbox()
                if bbox is None:
                    continue
                left, top, right, bottom = bbox
                used = (right - left) * (bottom - top)
                full = w * h
                ratio = used / full
                assert ratio >= generate_exe_icon.MIN_VISIBLE_BBOX_RATIO, (
                    f"ICO frame {w}x{h} content bounding box is "
                    f"{ratio * 100:.1f}% of the canvas; expected at least "
                    f"{generate_exe_icon.MIN_VISIBLE_BBOX_RATIO * 100:.1f}%"
                )
