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

Every non-approved size is a Pillow Lanczos downscale
of the approved 1024x1024 source. The approved
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

The v2.1.4 fix changes the *normalisation anchor* from
the alpha bounding box to the *dark-frame bounding
box*. The script:

  1. Detects the dark navy rounded-square tile as the
     bounding box of any pixel whose summed
     ``R+G+B`` is below ``80`` and whose ``alpha`` is
     at least ``32``. The threshold is the documented
     distinction between the brand tile and the bright
     blue glyph.
  2. Crops the source to the dark-frame bounding box
     plus :data:`DEFAULT_FRAME_PADDING_PERCENT` of the
     frame width per side. The small consistent
     transparent margin is what every standard Windows
     app icon carries (Chrome, Docker, PowerShell,
     Slack) so the ``Lockverity`` mark sits at the
     same visual depth as its neighbours.
  3. Resizes the cropped region to the
     ``1024x1024`` working canvas and from there to
     each canonical ICO size.

The result: the dark navy tile fills the full icon
canvas (matching the standard Windows app-icon
pattern: a coloured background fills the canvas and
the brand mark sits on top). The bright blue glyph is
the recognisable mark on top of the dark tile, at the
same optical position Chrome / Docker / PowerShell
use. The brand shape (dark rounded square + bright
blue glyph) is preserved exactly; the only thing
that changes is the position of the transparent
margin in the source canvas.

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

# v2.1.4 dark-frame-crop: the percentage of the
# detected dark-frame width reserved as transparent
# padding on every side of the cropped source.
# ``2.0`` reserves 2% of the frame width on each
# side, which is the same visual depth Chrome /
# Docker / PowerShell / Slack use for their
# standard Windows app icons.
DEFAULT_FRAME_PADDING_PERCENT: float = 2.0

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
    """Return ``source_bytes`` cropped to the dark-frame bbox plus a
    small consistent edge margin, then resized to ``target_size``.

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

    The function never recolours, redraws, or
    reinterpolates the brand; it is a pure crop +
    resize of the approved source. The dark-frame
    detection is fail-loud: a source with no dark
    frame at all raises :class:`ValueError` so the
    build aborts before writing a silently
    undersized derivative.
    """
    from PIL import Image  # type: ignore[import-not-found]

    if padding_percent < 0 or padding_percent > 25:
        raise ValueError(
            f"padding_percent {padding_percent} is out of range (0..25)"
        )
    with Image.open(io.BytesIO(source_bytes)) as source:
        source.load()
        if source.mode != "RGBA":
            source = source.convert("RGBA")
        # Detect the dark navy tile bounding box.
        # Pixels with summed R+G+B < 80 and alpha
        # >= 32 are the dark tile; everything else
        # (bright blue glyph, transparent margin)
        # is excluded. The threshold is the
        # documented distinction between the brand
        # tile and the bright blue glyph in the
        # approved source.
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
        if maxx < 0:
            raise ValueError(
                "the approved source has no dark-frame content; "
                "refusing to normalise a source without the "
                "Lockverity dark navy tile"
            )
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
        # The crop window is the dark-frame
        # bounding box expanded by a small
        # consistent margin. The margin is sized
        # to the frame width so the result is
        # proportional to the tile, not to the
        # outer source canvas.
        pad_x = round(frame_w * (padding_percent / 100.0) / 2.0)
        pad_y = round(frame_h * (padding_percent / 100.0) / 2.0)
        left = max(0, minx - pad_x)
        top = max(0, miny - pad_y)
        right = min(sw, maxx + pad_x)
        bottom = min(sh, maxy + pad_y)
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
        normalised = cropped.resize(
            (target_size, target_size), Image.Resampling.LANCZOS
        )
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
        resized = source.resize((target_size, target_size), Image.Resampling.LANCZOS)
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
        entries.append(
            _png_to_png_ico_entry(normalised_png_bytes, target_size)
        )
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
            "v2.1.3 padding-normalisation: percentage of the source "
            "canvas to reserve as transparent padding on every side "
            "of the normalised brand surface. The default ``2.0`` "
            "tightens the historical 5% padding so the brand mark "
            "fills more of every downstream ICO frame."
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
