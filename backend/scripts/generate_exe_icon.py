"""Generate the Windows executable icon (canonical packaging ICO).

v2.1.5 transparent-mark policy
==============================

The Windows taskbar icon is the **pure blue Lockverity
symbol on a transparent background**. The v2.1.2-v2.1.4
pipeline shipped the dark navy rounded-square tile with
the blue glyph inside it; on a real Windows 11 taskbar
the tile read as "the icon" and the recognisable blue
mark occupied only about 62% of the frame width, which
looked visibly smaller than neighbouring professional
taskbar icons (Chrome, Docker, VS Code, PowerShell).
The tile boundary also produced worn/jagged edge
artefacts at taskbar size. v2.1.5 drops the tile
entirely.

Approved source
---------------

The canonical blue mark is the approved standalone
product symbol at ``frontend/public/brand/lockverity-
symbol.png`` — the design board's own 1024x1024 RGBA
raster extraction of the primary symbol (see
``docs/brand-assets.md``). No SVG/vector source exists;
the approved symbol PNG is the highest-quality
transparent raster of the mark and is used as-is. The
approved branding files are never modified by the
build; the Windows derivative (the ICO frames and the
committed 1024px master) is generated separately.

The approved export carries a soft drop shadow and
low-alpha cutout residue: every pixel with alpha >= 85
lies within ~2px of a strong (alpha >= 128) mark
pixel, while beyond that radius the content is faint
junk (alpha <= 84, dirty RGB including pure black and
stray cyan). The pipeline therefore:

  1. builds the *core* mask of alpha >= 128 pixels;
  2. keeps source alpha only within
     :data:`EDGE_KEEP_RADIUS` (3px) of the core, which
     retains the genuine antialiasing ramp and deletes
     every distant residue/shadow pixel;
  3. replaces the RGB under transparent pixels near the
     mark edge with the nearest dominant mark colour
     (``ModeFilter`` edge extension) so later
     resampling cannot blend black/navy halo into the
     antialiased edge; fully transparent pixels far
     from the mark keep zeroed RGB;
  4. crops the cleaned mark to its exact alpha
     bounding box — the canonical cleaned mark.

Frames
------

Every ICO frame is a **single Lanczos resample** of the
cleaned mark at its native ~862px resolution (never a
downscale of an already-downscaled bitmap), scaled so
the visible mark fills the frame:

  - 16/20/24 px: 90% occupancy plus a deterministic
    alpha-gamma stroke boost so the thin tapering
    strokes stay crisp and recognisable instead of
    muddy; the boost is applied only to existing
    antialiasing values (the silhouette is not
    redrawn);
  - 32/40/48 px: 90% occupancy;
  - 64/128/256 px: 91-92% occupancy.

Each frame then gets the alpha hygiene pass: sub-8
resampling residue is clamped to true transparency,
near-opaque ringing is clamped to full opacity, and
isolated speck components below the per-size minimum
are removed. The result carries exactly the mark's two
interlocked components with smooth, halo-free,
antialiased edges on a fully transparent background.

The 1024px transparent master is written next to the
ICO (:data:`MASTER_PNG`) so maintainers and tests can
inspect the committed Windows working source directly.

The conversion is documented in ``docs/windows-icon.md``
and is exercised by ``tests/test_exe_icon.py`` and
``tests/test_exe_icon_pe.py``.
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_SYMBOL_PNG = REPO_ROOT / "frontend" / "public" / "brand" / "lockverity-symbol.png"
DERIVATIVE_ICO = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe.ico"
MASTER_PNG = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe-master.png"

# The full canonical size set for the Windows ICO. These
# are the sizes the Windows shell queries when rendering
# the application icon (taskbar, Start tile, Installed
# apps, File Explorer, etc.). The set covers every shell
# size the documented Windows 10/11 shell requests
# without a missing-entry fallback to the generic
# application icon.
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

# Alpha level at which a source pixel belongs to the
# strong mark core. The approved export's genuine
# antialiasing ramp lives within ~2px of this core;
# everything fainter and further out is the documented
# shadow/cutout residue.
ALPHA_CORE_THRESHOLD = 128

# Radius (in source pixels) around the strong core
# within which source alpha is retained. 3px keeps the
# whole antialiasing ramp (measured: all real content
# is within 2px of the core) while deleting every
# distant residue pixel (measured max alpha 84).
EDGE_KEEP_RADIUS = 3

# Post-resample fringe control: alpha strictly below
# this is Lanczos ringing/residue and is clamped to
# true transparency; alpha above the symmetric ceiling
# is clamped to full opacity.
ALPHA_FRINGE_FLOOR = 8
ALPHA_OPAQUE_CEILING = 255 - ALPHA_FRINGE_FLOOR

# Visible-mark occupancy (max bbox dimension / frame
# dimension) per frame size. 32/40/48 target the
# documented 88-92% band; 64+ go slightly larger where
# the geometry permits; 16/20/24 are tuned so the mark
# reads at professional weight without the thin
# tapering strokes clipping or turning muddy.
FRAME_OCCUPANCY: dict[int, float] = {
    16: 0.90,
    20: 0.90,
    24: 0.90,
    32: 0.90,
    40: 0.90,
    48: 0.90,
    64: 0.91,
    128: 0.92,
    256: 0.92,
}
DEFAULT_SYMBOL_OCCUPANCY: float = 0.91

# Deterministic optical tuning for the small frames:
# an alpha gamma < 1 raises only the existing
# antialiasing values, which slightly fattens the
# strokes so they survive 16/20/24px rendering. The
# silhouette is never redrawn; a gamma of 1.0 (or no
# entry) applies no boost.
FRAME_ALPHA_GAMMA: dict[int, float] = {
    16: 0.82,
    20: 0.86,
    24: 0.90,
}

# Isolated alpha components smaller than this many
# pixels are removed from the rendered frame. The real
# mark is two large interlocked components (each tens
# of pixels even at 16px); anything tiny is resampling
# residue (the 20px frame of the first v2.1.5 pass
# carried a 2-pixel alpha-10 fragment that this rule
# now removes).
MIN_COMPONENT_PX: dict[int, int] = {
    16: 3,
    20: 3,
    24: 3,
    32: 3,
    40: 3,
    48: 3,
    64: 4,
    128: 6,
    256: 8,
}
MASTER_COMPONENT_PX = 32

# Fail-loud lower bound for the cleaned mark's bbox
# fill of the approved source canvas. The approved
# symbol's strong core fills about 84% of the 1024px
# canvas width; a source whose mark is dramatically
# smaller is not the approved brand asset and the
# build aborts instead of shipping a silently
# undersized derivative.
MIN_MARK_FILL_RATIO = 0.60

# RGB edge extension: the RGB under transparent pixels
# near the mark is replaced with the colour of the
# nearest opaque mark pixel (a multi-source flood fill
# from the silhouette), so later resampling cannot
# blend black or navy halo into the antialiased edge.
# Transparent pixels farther than the extension radius
# keep zeroed RGB. A radius of 8 source pixels is far
# more than the reach of any downstream resampling
# kernel.
RGB_EDGE_EXTENSION_RADIUS = 8

# Frame-level edge extension radius per frame size.
# The source-level extension cannot survive a ~30x
# Lanczos downscale (the skirt shrinks below one
# destination pixel), so each rendered frame re-extends
# the RGB around its own final silhouette. This keeps
# the RGB under edge-adjacent transparent pixels a
# clean mark colour, so a non-premultiplied resampling
# path in the Windows shell cannot blend black halo
# into the antialiased edge. Far from the mark the RGB
# stays zeroed.
FRAME_EDGE_EXTENSION_RADIUS: dict[int, int] = {
    16: 2,
    20: 2,
    24: 2,
    32: 2,
    40: 2,
    48: 2,
    64: 3,
    128: 3,
    256: 4,
}
MASTER_EDGE_EXTENSION_RADIUS = 8


def _clean_symbol_source(source: object) -> object:
    """Return the tight-cropped, residue-free RGBA mark.

    The approved symbol export contains a soft shadow
    and low-alpha cutout residue beyond the real mark.
    The function keeps the source alpha only within
    :data:`EDGE_KEEP_RADIUS` of the strong core (the
    genuine antialiasing ramp) and zeroes everything
    else. The RGB under near-edge transparent pixels is
    replaced with the nearest dominant mark colour so
    later resampling cannot blend black or navy halo
    into the antialiased edge; fully transparent pixels
    far from the mark keep zeroed RGB.

    Raises :class:`ValueError` when the source has no
    usable mark (a hostile or accidental replacement),
    or when the cleaned mark is dramatically smaller
    than the approved symbol.
    """
    from PIL import Image, ImageChops, ImageFilter  # type: ignore[import-not-found]

    if not isinstance(source, Image.Image):
        raise TypeError("source must be a Pillow Image")
    rgba = source if source.mode == "RGBA" else source.convert("RGBA")
    width, height = rgba.size
    alpha = rgba.getchannel("A")
    core = alpha.point(lambda value: 255 if value >= ALPHA_CORE_THRESHOLD else 0)
    keep = core.filter(ImageFilter.MaxFilter(2 * EDGE_KEEP_RADIUS + 1))
    cleaned_alpha = ImageChops.multiply(alpha, keep)
    bbox = cleaned_alpha.getbbox()
    if bbox is None:
        raise ValueError(
            "the approved source has no symbol content; refusing to "
            "build a Windows icon from an empty or fully transparent source"
        )
    fill_ratio = max((bbox[2] - bbox[0]) / width, (bbox[3] - bbox[1]) / height)
    if fill_ratio < MIN_MARK_FILL_RATIO:
        raise ValueError(
            f"the cleaned symbol fills only {fill_ratio * 100:.1f}% of the "
            f"source canvas; expected at least {MIN_MARK_FILL_RATIO * 100:.0f}%. "
            "The approved Lockverity symbol fills about 84% of the canvas; "
            "if this check fails the source is no longer the approved "
            "brand asset."
        )
    # Edge-extend the RGB so the transparent skirt around the mark carries
    # the nearest mark colour instead of the export's dirty hidden RGB (or
    # raw black). Every downstream resample blends this skirt into the
    # antialiased edge, so a clean skirt is what keeps the edge halo-free.
    # Distant transparent pixels stay zeroed (the flood fill is bounded).
    rgba.load()
    cleaned = rgba.copy()
    cleaned.putalpha(cleaned_alpha)
    cleaned = _nearest_color_rgb(cleaned, RGB_EDGE_EXTENSION_RADIUS)
    return cleaned.crop(bbox)


def _remove_specks(alpha: object, min_pixels: int) -> object:
    """Remove isolated alpha components smaller than ``min_pixels``.

    The real mark is two large interlocked components;
    anything tiny is resampling residue. The function
    keeps every component with at least ``min_pixels``
    pixels and zeroes the rest. Connectivity is
    4-neighbour; the pass is a plain flood fill over
    the frame (at most 256x256 for ICO frames).
    """
    from PIL import Image, ImageChops  # type: ignore[import-not-found]

    if not isinstance(alpha, Image.Image):
        raise TypeError("alpha must be a Pillow Image")
    if min_pixels <= 1:
        return alpha
    width, height = alpha.size
    pixels = alpha.load()
    keep = Image.new("L", alpha.size, 0)
    keep_pixels = keep.load()
    visited = bytearray(width * height)

    def flood(start_x: int, start_y: int) -> list[tuple[int, int]] | None:
        stack = [(start_x, start_y)]
        visited[start_y * width + start_x] = 1
        component: list[tuple[int, int]] = []
        while stack:
            x, y = stack.pop()
            component.append((x, y))
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    index = ny * width + nx
                    if not visited[index] and pixels[nx, ny] > 0:
                        visited[index] = 1
                        stack.append((nx, ny))
        return component

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or pixels[x, y] == 0:
                continue
            component = flood(x, y)
            if component is not None and len(component) >= min_pixels:
                for cx, cy in component:
                    keep_pixels[cx, cy] = 255
    return ImageChops.multiply(alpha, keep)


def _hygiene_pass(frame_rgba: object, min_component_px: int) -> object:
    """Apply the documented alpha hygiene to a rendered frame.

    Sub-:data:`ALPHA_FRINGE_FLOOR` resampling residue is
    clamped to true transparency, ringing above the
    symmetric ceiling is clamped to full opacity, and
    isolated speck components are removed. The pass
    never touches the mark's RGB.
    """

    alpha = frame_rgba.getchannel("A")
    alpha = alpha.point(
        lambda value: (
            0 if value < ALPHA_FRINGE_FLOOR else (255 if value > ALPHA_OPAQUE_CEILING else value)
        )
    )
    alpha = _remove_specks(alpha, min_component_px)
    cleaned = frame_rgba.copy()
    cleaned.putalpha(alpha)
    return cleaned


def _nearest_color_rgb(rgba: object, radius: int) -> object:
    """Return the frame with transparent-near-edge RGB replaced by the
    colour of the nearest opaque pixel.

    The fill is a multi-source flood from every opaque
    pixel (4-neighbour breadth-first), so each
    transparent pixel within ``radius`` of the mark
    receives the RGB of its truly nearest mark pixel —
    never black, never an invented per-channel mix.
    Transparent pixels farther than ``radius`` keep
    zeroed RGB. The visible mark pixels are never
    touched, and ties resolve deterministically in scan
    order.
    """
    from collections import deque

    width, height = rgba.size
    alpha = rgba.getchannel("A")
    alpha_pixels = alpha.load()
    out = rgba.copy()
    out_pixels = out.load()
    queue: deque[tuple[int, int, int]] = deque()
    queued = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            if alpha_pixels[x, y] > 0:
                queued[y * width + x] = 1
                queue.append((x, y, 0))
    while queue:
        x, y, depth = queue.popleft()
        if depth >= radius:
            continue
        red, green, blue, _alpha = out_pixels[x, y]
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                index = ny * width + nx
                if not queued[index]:
                    queued[index] = 1
                    out_pixels[nx, ny] = (red, green, blue, 0)
                    queue.append((nx, ny, depth + 1))
    return out


def _render_mark_frame(
    cleaned_mark: object,
    frame_size: int,
    *,
    occupancy: float,
    alpha_gamma: float = 1.0,
    min_component_px: int = 2,
    edge_radius: int | None = None,
) -> object:
    """Render the cleaned mark centred on a transparent square frame.

    The mark is resampled once (Lanczos) from its native
    resolution to the target width implied by
    ``occupancy``, optically boosted with
    ``alpha_gamma`` when the frame is small, cleaned by
    the hygiene pass, and pasted centred on the
    transparent ``frame_size`` square. The frame's RGB
    is then edge-extended past the final silhouette so
    transparent pixels near the mark carry a clean mark
    colour instead of resampled black. The frame keeps a
    transparent safety margin on every side; the mark
    never touches the frame bounds.
    """
    from PIL import Image  # type: ignore[import-not-found]

    if not 0.5 <= occupancy <= 0.99:
        raise ValueError(f"occupancy {occupancy} is out of range (0.5..0.99)")
    if alpha_gamma <= 0:
        raise ValueError("alpha_gamma must be positive")
    mark_w, mark_h = cleaned_mark.size
    target_w = max(1, round(frame_size * occupancy))
    target_h = max(1, round(target_w * mark_h / mark_w))
    if target_w > frame_size or target_h > frame_size:
        raise ValueError(f"mark at occupancy {occupancy} exceeds the {frame_size}px frame")
    resized = cleaned_mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
    if alpha_gamma != 1.0:
        resized.putalpha(
            resized.getchannel("A").point(
                lambda value: round(255.0 * (value / 255.0) ** alpha_gamma)
            )
        )
    resized = _hygiene_pass(resized, min_component_px)
    frame = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    offset_x = (frame_size - target_w) // 2
    offset_y = (frame_size - target_h) // 2
    frame.alpha_composite(resized, (offset_x, offset_y))
    radius = (
        edge_radius
        if edge_radius is not None
        else (FRAME_EDGE_EXTENSION_RADIUS.get(frame_size) or RGB_EDGE_EXTENSION_RADIUS)
    )
    return _nearest_color_rgb(frame, radius)


def build_symbol_master(
    *,
    approved_symbol_png: Path = APPROVED_SYMBOL_PNG,
    size: int = 1024,
    occupancy: float = DEFAULT_SYMBOL_OCCUPANCY,
) -> object:
    """Return the transparent Windows master: blue symbol, no tile.

    The master is the cleaned approved symbol scaled to
    ``occupancy`` of a transparent ``size`` square. It
    carries no navy background, no rounded-square tile,
    no shadow, no glow, no border, and no artificial
    corner mask — only the approved blue mark geometry
    and gradient, optically centred.
    """
    from PIL import Image  # type: ignore[import-not-found]

    with Image.open(approved_symbol_png) as source:
        source.load()
        cleaned = _clean_symbol_source(source)
    return _render_mark_frame(
        cleaned,
        size,
        occupancy=occupancy,
        min_component_px=MASTER_COMPONENT_PX,
        edge_radius=MASTER_EDGE_EXTENSION_RADIUS,
    )


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
    header (the documented ICO convention) and writes a
    single ``ICONDIR`` followed by ``ICONDIRENTRY``
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


def build_exe_icon(
    *,
    approved_symbol_png: Path = APPROVED_SYMBOL_PNG,
    derivative_ico: Path = DERIVATIVE_ICO,
    master_png: Path | None = MASTER_PNG,
    sizes: tuple[int, ...] = CANONICAL_ICON_SIZES,
    occupancy: float | None = None,
) -> Path:
    """Build ``derivative_ico`` (and ``master_png``) from the approved symbol.

    The function is the documented entry point. It
    cleans the approved blue-symbol source once, writes
    the transparent 1024px Windows master, and renders
    every canonical frame as a single Lanczos resample
    of the cleaned mark with the per-size optical
    tuning and the alpha hygiene pass documented in the
    module docstring. The approved branding files are
    never modified; the output ICO and master are
    mechanical derivatives.

    ``occupancy`` overrides the per-size
    :data:`FRAME_OCCUPANCY` table with one fixed value
    (used by the tests to exercise boundary geometry).
    """
    from PIL import Image  # type: ignore[import-not-found]

    if not approved_symbol_png.is_file():
        raise FileNotFoundError(f"approved symbol PNG not found: {approved_symbol_png}")
    if not sizes:
        raise ValueError("sizes must contain at least one entry")
    for size in sizes:
        if size < 1 or size > 256:
            raise ValueError(f"size {size} is out of range (1..256)")
    with Image.open(approved_symbol_png) as source:
        source.load()
        cleaned = _clean_symbol_source(source)
    if master_png is not None:
        master = _render_mark_frame(
            cleaned,
            1024,
            occupancy=occupancy or DEFAULT_SYMBOL_OCCUPANCY,
            min_component_px=MASTER_COMPONENT_PX,
        )
        master_png.parent.mkdir(parents=True, exist_ok=True)
        master.save(master_png, format="PNG", optimize=True)
    entries: list[tuple[int, int, bytes]] = []
    for target_size in sizes:
        frame = _render_mark_frame(
            cleaned,
            target_size,
            occupancy=occupancy or FRAME_OCCUPANCY.get(target_size, DEFAULT_SYMBOL_OCCUPANCY),
            alpha_gamma=FRAME_ALPHA_GAMMA.get(target_size, 1.0),
            min_component_px=MIN_COMPONENT_PX.get(target_size, 2),
        )
        # Re-encode as PNG so the ICO entry is a
        # self-contained payload the Windows shell can
        # decode directly.
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", optimize=True)
        entries.append((target_size, target_size, buffer.getvalue()))
    ico_bytes = _build_ico(entries)
    derivative_ico.parent.mkdir(parents=True, exist_ok=True)
    derivative_ico.write_bytes(ico_bytes)
    return derivative_ico


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    The function regenerates the derivative ICO and the
    1024px master in place. The default paths are the
    canonical approved-source-to-derivative mapping; the
    CLI flags are documented for maintainer overrides.
    """
    parser = argparse.ArgumentParser(prog="generate_exe_icon")
    parser.add_argument(
        "--approved-symbol-png",
        type=Path,
        default=APPROVED_SYMBOL_PNG,
        help="Path to the approved transparent blue-symbol PNG source.",
    )
    parser.add_argument(
        "--derivative-ico",
        type=Path,
        default=DERIVATIVE_ICO,
        help="Path to write the packaging-derivative ICO.",
    )
    parser.add_argument(
        "--master-png",
        type=Path,
        default=MASTER_PNG,
        help="Path to write the transparent 1024px Windows master PNG.",
    )
    args = parser.parse_args(argv)
    written = build_exe_icon(
        approved_symbol_png=args.approved_symbol_png,
        derivative_ico=args.derivative_ico,
        master_png=args.master_png,
    )
    sys.stderr.write(f"wrote {written} ({written.stat().st_size} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
