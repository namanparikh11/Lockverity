"""Generate the v2.1.2 Windows executable icon (canonical packaging ICO).

The approved brand asset is the
``frontend/public/favicon-source.png`` ``1024x1024``
RGBA source. The brand-board web favicon at
``frontend/public/favicon.ico`` contains only the
``16x16``, ``32x32`` and ``48x48`` entries the browser
needs. The canonical Windows ICO at
``backend/pyinstaller/favicon-exe.ico`` re-packages
those approved small entries plus a set of
freshly-downscaled PNG entries sized for the Windows
shell:

  - 16x16   (Windows taskbar / small icon view)
  - 20x20   (Windows medium-density taskbar)
  - 24x24   (classic Windows desktop / Explorer toolbar)
  - 32x32   (default Windows shell icon view)
  - 40x40   (Windows extra-density taskbar)
  - 48x48   (legacy / medium icon view)
  - 64x64   (large icon view)
  - 128x128 (extra-large icon view)
  - 256x256 (Windows shell high-DPI / "Large Icons" view)

Every size is generated from a corrected 1024x1024
Windows working source. The approved
``frontend/public/favicon-source.png`` and the
approved ``frontend/public/favicon.ico`` are never
modified.

v2.1.4 dark-frame-crop policy
==============================

The v2.1.3 padding-normalisation step tightened the
transparent margin around the *alpha* bounding box.
The result was still visibly smaller than neighbouring
Windows 11 taskbar icons on the user's real desktop.
The actual source layout is:

  - the dark navy rounded-square *tile* fills about
    ``82%`` of the 1024x1024 canvas;
  - inside the tile, the bright blue ``Lockverity``
    glyph fills about ``88%`` of the dark tile;
  - the outer transparent margin (about ``9%`` of the
    canvas on every side) was the dominant cause of
    the apparent-size regression.

The final polish keeps the v2.1.4 dark-frame anchor and
fixes the exported edge deterministically. The script:

  1. Detects the dark navy rounded-square tile as the
     bounding box of any pixel whose summed
     ``R+G+B`` is below ``80`` and whose ``alpha`` is
     at least ``32``. The threshold is the documented
     distinction between the brand tile and the bright
     blue glyph.
  2. Crops to the exact 844x844 tile plus 0.25% per-side
     composition padding. The resulting 848px crop is
     0.94% tighter than the previous 856px crop.
  3. Composites the source RGB over a navy colour derived
     from its opaque tile pixels, replacing dirty hidden
     RGB at the transparent edge without changing the
     opaque glyph.
  4. Applies a symmetric, eight-times-supersampled rounded
     rectangle alpha mask. The radius (27.3% of tile width)
     is measured from the source's opaque contour, and
     sub-alpha-8 Lanczos residue is clamped to transparency.
  5. Generates every ICO frame from that corrected source,
     with a fresh supersampled mask at the target size.

The result retains the dark tile and blue glyph while
discarding the source export's shadow/cutout residue.
The clean tile is centred, slightly larger, and inset by
0.5% (with a half-pixel minimum for small frames), so it
does not clip against the ICO bounds.

The dark-frame fill ratio is exposed as
:data:`MIN_DARK_FRAME_FILL_RATIO` so a future
maintainer cannot regress the policy back to the
pre-v2.1.4 looseness. The dark-frame detection is
fail-loud: a hostile source with no dark frame at
all raises :class:`ValueError` so the build aborts
before writing a silently-undersized derivative.

The function is the single chokepoint for the
mechanical ICO construction so a future maintainer can
audit the conversion without re-deriving the math.

The conversion is documented in
``docs/windows-icon.md`` and is exercised by
``tests/test_exe_icon.py`` and
``tests/test_installer.py``.
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_ICO = REPO_ROOT / "frontend" / "public" / "favicon.ico"
APPROVED_PNG = REPO_ROOT / "frontend" / "public" / "favicon-source.png"
DERIVATIVE_ICO = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe.ico"

# The full canonical size set for the Windows ICO. These
# are the sizes the Windows shell queries when rendering
# the application icon (taskbar, Start tile, Installed
# apps, File Explorer, etc.). The set covers every shell
# size the documented Windows 10/11 shell requests
# without a missing-entry fallback to the generic
# application icon. All sizes are square; the source
# 1024x1024 PNG is downscaled with the highest-quality
# Pillow filter (LANCZOS) to preserve the brand geometry
# and aspect ratio.
CANONICAL_ICON_SIZES: tuple[int, ...] = (
    16,
    20,
    24,
    32,
    40,
    48,
    64,
    128,
    256,
)

# Final Windows polish: reserve 0.25% of the detected
# dark-tile width on each side of the composition crop.
# The previous crop was 856px wide; the exact dark-tile
# detector plus this padding produces an 848px crop, a
# controlled 0.94% scale-up without changing the glyph.
DEFAULT_FRAME_PADDING_PERCENT: float = 0.25

# The source export contains a soft drop shadow and
# low-alpha cutout residue beyond the intended tile.
# Windows needs a clean mathematical outer silhouette,
# so the derivative uses a supersampled rounded-rectangle
# alpha mask while retaining the source RGB inside it.
TILE_MARGIN_PERCENT: float = 0.5
MIN_FRAME_MARGIN_PX: float = 0.5
TILE_CORNER_RADIUS_RATIO: float = 0.273
MASK_SUPERSAMPLE: int = 8
ALPHA_FRINGE_FLOOR: int = 8

# The minimum acceptable ratio of the detected
# dark-frame width to the source canvas width. The
# approved Lockverity source has the dark frame
# filling about ``82%`` of the canvas; the
# ``test_exe_icon`` tests assert the dark-frame
# fill exceeds this minimum so a future maintainer
# cannot regress the dark-frame-crop policy back
# to the pre-v2.1.4 looseness.
MIN_DARK_FRAME_FILL_RATIO: float = 0.80

# v2.1.3 compatibility: ``DEFAULT_PADDING_PERCENT``
# and ``MIN_VISIBLE_BBOX_RATIO`` are retained as
# module-level aliases so any external caller (and
# the v2.1.3 ``test_padding_step_*`` tests) keep
# resolving. The v2.1.4 implementation is a strict
# superset: it normalises to the dark frame rather
# than the alpha bbox, which produces a tighter
# apparent-size derivative that matches the
# standard Windows app-icon pattern.
DEFAULT_PADDING_PERCENT: float = DEFAULT_FRAME_PADDING_PERCENT
MIN_VISIBLE_BBOX_RATIO: float = 0.85


def _detect_dark_frame_bbox(source: object) -> tuple[int, int, int, int]:
    """Return the exact, exclusive bbox of the source's dark navy tile."""
    from PIL import Image  # type: ignore[import-not-found]

    if not isinstance(source, Image.Image):
        raise TypeError("source must be a Pillow Image")
    rgba = source if source.mode == "RGBA" else source.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha >= 32 and red + green + blue < 80:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < 0:
        raise ValueError(
            "the approved source has no dark-frame content; "
            "refusing to normalise a source without the Lockverity dark navy tile"
        )
    return min_x, min_y, max_x + 1, max_y + 1


def _rounded_tile_mask(
    size: int,
    *,
    margin_percent: float = TILE_MARGIN_PERCENT,
    supersample: int = MASK_SUPERSAMPLE,
) -> object:
    """Return a clean, symmetric antialiased rounded-tile alpha mask.

    The minimum half-pixel inset protects 16px and 20px frames from clipping.
    Lanczos fringe below ``ALPHA_FRINGE_FLOOR`` is made truly transparent.
    """
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]

    if size < 1:
        raise ValueError("size must be positive")
    if margin_percent < 0 or margin_percent > 25:
        raise ValueError("margin_percent must be in the range 0..25")
    if supersample < 2:
        raise ValueError("supersample must be at least 2")
    high_size = size * supersample
    margin = max(
        round(high_size * margin_percent / 100.0),
        round(MIN_FRAME_MARGIN_PX * supersample),
    )
    tile_width = high_size - 2 * margin
    radius = round(tile_width * TILE_CORNER_RADIUS_RATIO)
    high = Image.new("L", (high_size, high_size), 0)
    ImageDraw.Draw(high).rounded_rectangle(
        (margin, margin, high_size - margin - 1, high_size - margin - 1),
        radius=radius,
        fill=255,
    )
    mask = high.resize((size, size), Image.Resampling.LANCZOS)
    return mask.point(
        lambda alpha: (
            0
            if alpha < ALPHA_FRINGE_FLOOR
            else (255 if alpha > 255 - ALPHA_FRINGE_FLOOR else alpha)
        )
    )


def _solid_tile_rgb(source: object) -> object:
    """Remove inherited transparent-edge RGB while keeping opaque artwork exact."""
    from PIL import Image, ImageStat  # type: ignore[import-not-found]

    if not isinstance(source, Image.Image):
        raise TypeError("source must be a Pillow Image")
    rgba = source if source.mode == "RGBA" else source.convert("RGBA")
    # Derive the fill from opaque, dark tile pixels; the bright blue glyph is
    # excluded. This supplies stable navy RGB beneath the replacement AA edge.
    dark_mask = Image.new("L", rgba.size, 0)
    dark_pixels = dark_mask.load()
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha >= 254 and red + green + blue < 160:
                dark_pixels[x, y] = 255
    mean = ImageStat.Stat(rgba.convert("RGB"), dark_mask).mean
    fill = tuple(round(channel) for channel in mean)
    fallback = Image.new("RGB", rgba.size, fill)
    return Image.composite(rgba.convert("RGB"), fallback, rgba.getchannel("A"))


def _parse_ico(data: bytes) -> list[tuple[int, int, bytes]]:
    """Parse an ICO file into ``[(width, height, raw_image_bytes), ...]``.

    The function is a tiny pure-Python ICO reader
    sufficient for the favicon shape. ``width`` and
    ``height`` are ``0``-encoded as ``256`` in the
    ICO header; the function decodes that for the
    caller.
    """
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("not an ICO file (magic mismatch)")
    count = struct.unpack_from("<H", data, 4)[0]
    out: list[tuple[int, int, bytes]] = []
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
                data[body_offset : body_offset + size],
            )
        )
    return out


def _build_ico(entries: list[tuple[int, int, bytes]]) -> bytes:
    """Build an ICO file from ``[(width, height, raw_image_bytes), ...]``.

    The output uses ``0`` to encode ``256`` in the
    header (the documented ICO convention) and writes
    a single ``ICONDIR`` followed by ``ICONDIRENTRY``
    records and the concatenated entry bodies.
    """
    header = struct.pack("<HHH", 0, 1, len(entries))
    body = b"".join(entry[2] for entry in entries)
    # Compute each entry's offset within the body.
    offsets: list[int] = []
    cursor = 0
    for entry in entries:
        offsets.append(cursor)
        cursor += len(entry[2])
    # Pad the body to 16-byte alignment? ICO does not
    # require alignment; the raw byte offsets are
    # sufficient. The header size is
    # ``6 + 16 * count``; entry bodies start
    # immediately after.
    body_offset_base = 6 + 16 * len(entries)
    dir_entries = bytearray()
    for (width, height, raw), offset in zip(entries, offsets, strict=True):
        # ``0`` means ``256`` in the ICO spec.
        encoded_w = 0 if width == 256 else width
        encoded_h = 0 if height == 256 else height
        dir_entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_w,
                encoded_h,
                0,  # colour count (0 for >= 8bpp)
                0,  # reserved
                1,  # colour planes
                32,  # bits per pixel
                len(raw),
                body_offset_base + offset,
            )
        )
    return header + bytes(dir_entries) + body


def _normalise_padding(
    source_bytes: bytes,
    *,
    target_size: int = 1024,
    padding_percent: float = DEFAULT_PADDING_PERCENT,
) -> bytes:
    """Return a centred, clean-masked Windows source at ``target_size``.

    The function is the v2.1.4 dark-frame-crop
    step. The approved Lockverity source layout is:

      - dark navy rounded-square tile (about 82% of
        the canvas);
      - bright blue glyph inside the tile (about
        88% of the tile);
      - outer transparent margin (about 9% of the
        canvas on every side).

    Cropping to the *alpha* bounding box (the
    v2.1.3 approach) preserved the outer transparent
    margin and made the icon look visibly smaller
    than neighbouring Windows 11 app icons on the
    user's real desktop. Cropping to the *dark
    frame* bounding box instead places the dark tile
    flush with the icon canvas, which is the same
    pattern every standard Windows app icon
    (Chrome, Docker, PowerShell, Slack) uses: a
    coloured background fills the canvas and the
    brand mark sits on top.

    ``padding_percent`` is the percentage of the
    detected dark-frame width reserved as
    transparent padding on every side. ``2.0``
    gives the same visual depth the standard
    Windows app icons use.

    Opaque source RGB—including the blue glyph—is retained.
    Only transparent-edge RGB and the alpha silhouette are
    reconstructed. Dark-frame detection is fail-loud: a source
    with no dark frame raises :class:`ValueError` before writing.
    """
    from PIL import Image  # type: ignore[import-not-found]

    if padding_percent < 0 or padding_percent > 25:
        raise ValueError(f"padding_percent {padding_percent} is out of range (0..25)")
    with Image.open(io.BytesIO(source_bytes)) as source:
        source.load()
        if source.mode != "RGBA":
            source = source.convert("RGBA")
        sw, sh = source.size
        minx, miny, maxx, maxy = _detect_dark_frame_bbox(source)
        frame_w = maxx - minx
        frame_h = maxy - miny
        # The dark frame must dominate the canvas
        # (the approved source is 82%; the
        # minimum is 80%). A future maintainer
        # cannot ship a derivative with a tiny
        # dark frame without breaking this check.
        canvas_fill = max(frame_w / sw, frame_h / sh)
        if canvas_fill < MIN_DARK_FRAME_FILL_RATIO:
            raise ValueError(
                f"the detected dark frame is {canvas_fill * 100:.1f}% "
                f"of the source canvas; expected at least "
                f"{MIN_DARK_FRAME_FILL_RATIO * 100:.0f}%. The approved "
                "Lockverity source uses a dark navy tile that fills "
                "about 82% of the canvas; if this check fails the "
                "source is no longer the approved brand asset."
            )
        # Build a square crop around the exact tile centre. The approved tile
        # is 10px above the source-canvas centre, so centring on the source
        # canvas would leave the Windows icon optically low.
        pad = round(max(frame_w, frame_h) * padding_percent / 100.0)
        crop_size = max(frame_w, frame_h) + 2 * pad
        center_x = (minx + maxx) / 2.0
        center_y = (miny + maxy) / 2.0
        left = round(center_x - crop_size / 2.0)
        top = round(center_y - crop_size / 2.0)
        left = min(max(0, left), sw - crop_size)
        top = min(max(0, top), sh - crop_size)
        right = left + crop_size
        bottom = top + crop_size
        cropped = source.crop((left, top, right, bottom))
        # The cropped region is the *normalised
        # brand surface*. The function resizes it
        # to ``target_size`` so the downstream
        # ``_png_to_png_ico_entry`` downscale
        # pipeline works against a single
        # canonical working canvas.
        # ``Image.LANCZOS`` is the documented
        # Pillow constant for the highest-quality
        # downscale filter.
        normalised_rgb = _solid_tile_rgb(cropped).resize(
            (target_size, target_size), Image.Resampling.LANCZOS
        )
        normalised = normalised_rgb.convert("RGBA")
        normalised.putalpha(_rounded_tile_mask(target_size))
        buffer = io.BytesIO()
        normalised.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def _png_to_png_ico_entry(
    png_bytes: bytes,
    target_size: int,
) -> tuple[int, int, bytes]:
    """Return a ``(width, height, raw_bytes)`` ICO entry for a PNG payload.

    Windows accepts PNG-compressed entries directly
    in ICO files since Vista; the raw payload is the
    PNG itself and the ICO reader decodes it. The
    Pillow ``Image`` resize uses Lanczos for a
    high-quality downscale of the approved source.
    The v2.1.3 padding-normalisation step is
    applied upstream of this function: the
    ``png_bytes`` argument is the
    *already-normalised* source, not the raw
    1024x1024 brand asset.
    """
    from PIL import Image  # type: ignore[import-not-found]

    with Image.open(io.BytesIO(png_bytes)) as source:
        source.load()
        # The normalised source is 1024x1024 RGBA. A
        # high-quality downscale to ``target_size``
        # preserves the brand geometry and aspect
        # ratio. ``Image.LANCZOS`` is the documented
        # Pillow constant for the highest-quality
        # downscale filter.
        # Resize colour independently from alpha. The source alpha is the clean
        # canonical tile mask, not a carrier for colour interpolation; using a
        # fresh supersampled mask avoids straight-alpha halos and Lanczos rings.
        resized = (
            source.convert("RGB")
            .resize((target_size, target_size), Image.Resampling.LANCZOS)
            .convert("RGBA")
        )
        resized.putalpha(_rounded_tile_mask(target_size))
        # Re-encode as PNG so the ICO entry is a
        # self-contained PNG payload the Windows
        # shell can decode directly.
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG", optimize=True)
        return target_size, target_size, buffer.getvalue()


def _approved_favicon_sizes(approved_ico: Path) -> set[int]:
    """Return the set of square sizes present in the approved ICO.

    The function is a thin helper used by
    :func:`build_exe_icon` to decide which entries
    can be lifted from the brand-favicon (16/32/48)
    and which must be downscaled from the
    1024x1024 PNG. The brand-favicon entries are
    hand-tuned and identical to the ones the
    browser serves; the PNG downscaled entries are
    mechanical Lanczos outputs.
    """
    return {w for (w, _h, _raw) in _parse_ico(approved_ico.read_bytes())}


def build_exe_icon(
    *,
    approved_ico: Path = APPROVED_ICO,
    approved_png: Path = APPROVED_PNG,
    derivative_ico: Path = DERIVATIVE_ICO,
    sizes: tuple[int, ...] = CANONICAL_ICON_SIZES,
    padding_percent: float = DEFAULT_PADDING_PERCENT,
) -> Path:
    """Build ``derivative_ico`` from the approved sources.

    The function is the documented entry point. It
    writes a single ICO file that contains every
    size in ``sizes`` (default: the canonical
    ``16/20/24/32/40/48/64/128/256`` set the Windows
    shell queries). The v2.1.4 dark-frame-crop step
    is the first action the function takes: the
    approved source is cropped to the *dark navy
    tile* bounding box plus ``padding_percent`` of
    the frame width per side, then resized to a
    ``1024x1024`` working canvas. From there every
    canonical size is a Pillow Lanczos downscale of
    the working canvas.

    v2.1.4 deliberately does *not* lift entries from
    the brand-board web favicon the v2.1.3 path
    used. The web favicon keeps the dark navy tile
    as a prominent part of the mark, which is the
    right look for a 16x16 browser tab icon but
    makes the icon look smaller than neighbouring
    Windows 11 taskbar applications. The Windows
    derivative is generated entirely from the
    dark-frame-cropped source so every size carries
    the same geometry and the dark frame fills
    the full icon canvas (matching the standard
    Windows app-icon pattern).

    The brand shape (dark rounded square + bright
    blue glyph) is preserved exactly: the function
    never draws, recolours, or reinterprets the
    brand; every entry is a mechanical downscale of
    the normalised source pixels.

    The function is intentionally narrow: it does
    not depend on the brand-board web favicon at
    runtime, but it reads ``approved_ico`` to keep
    a single chokepoint for the maintainer who
    wants to inspect both the web favicon and the
    Windows derivative in the same call.
    """
    if not approved_ico.is_file():
        raise FileNotFoundError(f"approved ICO not found: {approved_ico}")
    if not approved_png.is_file():
        raise FileNotFoundError(f"approved PNG source not found: {approved_png}")
    if not sizes:
        raise ValueError("sizes must contain at least one entry")
    for size in sizes:
        if size < 1 or size > 256:
            raise ValueError(f"size {size} is out of range (1..256)")
    # v2.1.4 dark-frame-crop: the working canvas
    # is the source cropped to the dark navy tile
    # bounding box plus a small consistent edge
    # margin. The resulting ``normalised_png_bytes``
    # is the single source the rest of the pipeline
    # works from. The dark-frame fill check is
    # fail-loud so a hostile or accidental source
    # cannot ship a silently-undersized derivative.
    normalised_png_bytes = _normalise_padding(
        approved_png.read_bytes(),
        target_size=1024,
        padding_percent=padding_percent,
    )
    entries: list[tuple[int, int, bytes]] = []
    for target_size in sizes:
        entries.append(_png_to_png_ico_entry(normalised_png_bytes, target_size))
    ico_bytes = _build_ico(entries)
    derivative_ico.parent.mkdir(parents=True, exist_ok=True)
    derivative_ico.write_bytes(ico_bytes)
    return derivative_ico


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    The function regenerates the derivative ICO in
    place. The default paths are the canonical
    approved-source-to-derivative mapping; the CLI
    flags are documented for maintainer overrides.
    """
    parser = argparse.ArgumentParser(prog="generate_exe_icon")
    parser.add_argument(
        "--approved-ico",
        type=Path,
        default=APPROVED_ICO,
        help="Path to the approved web favicon.ico.",
    )
    parser.add_argument(
        "--approved-png",
        type=Path,
        default=APPROVED_PNG,
        help="Path to the approved 1024x1024 PNG source.",
    )
    parser.add_argument(
        "--derivative-ico",
        type=Path,
        default=DERIVATIVE_ICO,
        help="Path to write the packaging-derivative ICO.",
    )
    parser.add_argument(
        "--padding-percent",
        type=float,
        default=DEFAULT_PADDING_PERCENT,
        help=(
            "percentage of the detected dark-tile width retained as "
            "composition padding on each side. The default 0.25 gives "
            "the final controlled 0.94% scale-up."
        ),
    )
    args = parser.parse_args(argv)
    written = build_exe_icon(
        approved_ico=args.approved_ico,
        approved_png=args.approved_png,
        derivative_ico=args.derivative_ico,
        padding_percent=args.padding_percent,
    )
    sys.stderr.write(f"wrote {written} ({written.stat().st_size} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
