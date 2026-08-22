"""Tests for the v2.1.5 transparent Windows ICO.

The v2.1.5 Windows icon contract is the **pure blue
Lockverity symbol on a transparent background** — no
dark navy rounded-square tile. The v2.1.2-v2.1.4
dark-tile icon read as "the icon" on the Windows 11
taskbar while the recognisable blue glyph occupied only
about 62% of the frame width, and the tile boundary
produced worn edge artefacts at taskbar size.

The canonical source is the approved standalone product
symbol at ``frontend/public/brand/lockverity-symbol.png``
(the design board's own 1024x1024 RGBA raster extraction;
no SVG source exists — see ``docs/brand-assets.md``).
``scripts/generate_exe_icon.py``:

  1. keeps the source alpha only within 3px of the
     strong (alpha >= 128) core, which deletes the
     export's soft shadow / cutout residue while
     retaining the genuine antialiasing ramp;
  2. replaces the RGB under near-edge transparent
     pixels with the nearest mark colour so resampling
     cannot blend a black/navy halo into the edge;
  3. renders every canonical frame
     ``{16, 20, 24, 32, 40, 48, 64, 128, 256}`` as a
     single Lanczos resample of the cleaned mark at
     90-92% visible occupancy, with a deterministic
     alpha-gamma stroke boost on the 16/20/24px frames;
  4. applies the alpha hygiene pass (sub-8 fringe clamp,
     opaque ceiling clamp, isolated-speck removal) and a
     frame-level nearest-colour edge extension.

The tests below assert the documented contract without
being pixel-perfect:

  1. The derivative ICO has the full canonical Windows
     size set ``{16, 20, 24, 32, 40, 48, 64, 128, 256}``
     with PNG payloads.
  2. The background is genuinely transparent (corners
     empty, a substantial transparent fraction — a tile
     would cover the canvas).
  3. The visible mark is blue-only: no navy tile pixels,
     opaque pixels are blue-dominant.
  4. The mark fills 85-97% of every frame and is centred
     within a sensible optical tolerance.
  5. No clipping: the frame borders stay non-opaque and
     the corners are fully transparent.
  6. No isolated alpha components or sub-floor fringe
     pixels; edge-adjacent transparent pixels carry a
     clean mark colour (not black).
  7. The 1024px committed master is transparent and
     mark-only.
  8. The build is idempotent, never modifies the
     approved source, and fails loud on a hostile
     (empty or tiny-mark) source.

The tests do not require the build script to have been
run; they verify the artefact and the regeneration logic
in isolation. The PE-resource side of the contract is
covered by ``tests/test_exe_icon_pe.py``.
"""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

import pytest
from scripts import generate_exe_icon

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_SYMBOL_PNG = REPO_ROOT / "frontend" / "public" / "brand" / "lockverity-symbol.png"
DERIVATIVE_ICO = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe.ico"
MASTER_PNG = REPO_ROOT / "backend" / "pyinstaller" / "favicon-exe-master.png"


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


def _load_frames(
    ico_path: Path,
) -> dict[int, object]:
    """Return ``{size: RGBA Image}`` for every PNG entry in the ICO."""
    from PIL import Image  # type: ignore[import-not-found]

    frames: dict[int, object] = {}
    for width, _height, _size, body in _parse_ico_sizes(ico_path.read_bytes()):
        with Image.open(io.BytesIO(body)) as image:
            image.load()
            frames[width] = image.convert("RGBA")
    return frames


@pytest.fixture(scope="module")
def regenerated_derivative(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the derivative ICO into an isolated temp path once.

    The fixture is the documented chokepoint: every test
    inspects the same isolated build so the on-disk
    artefact is not modified by the suite.
    """
    out = tmp_path_factory.mktemp("exe-icon") / "favicon-exe.ico"
    generate_exe_icon.build_exe_icon(
        approved_symbol_png=APPROVED_SYMBOL_PNG,
        derivative_ico=out,
        master_png=None,
    )
    return out


@pytest.fixture(scope="module")
def regenerated_master(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the 1024px transparent master into an isolated path."""
    tmp = tmp_path_factory.mktemp("exe-icon-master")
    generate_exe_icon.build_exe_icon(
        approved_symbol_png=APPROVED_SYMBOL_PNG,
        derivative_ico=tmp / "favicon-exe.ico",
        master_png=tmp / "favicon-exe-master.png",
    )
    return tmp / "favicon-exe-master.png"


class TestApprovedSymbolUnchanged:
    """The approved brand symbol is never modified by the build."""

    def test_approved_symbol_exists(self) -> None:
        assert APPROVED_SYMBOL_PNG.is_file(), f"approved symbol missing at {APPROVED_SYMBOL_PNG}"

    def test_build_does_not_modify_approved_symbol(self, tmp_path: Path) -> None:
        before = hashlib.sha256(APPROVED_SYMBOL_PNG.read_bytes()).hexdigest()
        generate_exe_icon.build_exe_icon(
            approved_symbol_png=APPROVED_SYMBOL_PNG,
            derivative_ico=tmp_path / "favicon-exe.ico",
            master_png=None,
        )
        after = hashlib.sha256(APPROVED_SYMBOL_PNG.read_bytes()).hexdigest()
        assert before == after


class TestDerivativeIcoStructure:
    """The derivative ICO has the full canonical Windows size set."""

    def test_derivative_is_valid_ico(self, regenerated_derivative: Path) -> None:
        data = regenerated_derivative.read_bytes()
        assert data[:4] == b"\x00\x00\x01\x00"

    def test_derivative_has_canonical_size_set(self, regenerated_derivative: Path) -> None:
        """The canonical Windows size set is ``{16, 20, 24, 32, 40, 48, 64,
        128, 256}``.

        The Windows shell queries these sizes when
        rendering the application icon for the taskbar,
        Start tile, Installed apps list, File Explorer
        and the uninstaller UI. A missing entry causes
        the shell to fall back to the generic
        application icon.
        """
        data = regenerated_derivative.read_bytes()
        sizes = {entry[0] for entry in _parse_ico_sizes(data)}
        canonical = {16, 20, 24, 32, 40, 48, 64, 128, 256}
        assert sizes == canonical, (
            f"Canonical Windows ICO size set mismatch: {sorted(sizes)}; "
            f"expected {sorted(canonical)}; the Windows shell will fall back "
            "to the generic application icon for any missing size."
        )

    def test_derivative_entries_are_png_payloads(self, regenerated_derivative: Path) -> None:
        """Every entry is a PNG payload the Windows shell decodes directly."""
        data = regenerated_derivative.read_bytes()
        for width, _height, _size, body in _parse_ico_sizes(data):
            assert body.startswith(b"\x89PNG\r\n\x1a\n"), (
                f"the {width}x{width} entry must be a PNG payload; "
                "the Windows shell decodes PNG-in-ICO directly (Vista+)"
            )


class TestTransparentBackgroundContract:
    """v2.1.5 contract: transparent background, blue mark only."""

    def test_corners_are_fully_transparent(self, regenerated_derivative: Path) -> None:

        for size, frame in _load_frames(regenerated_derivative).items():
            pixels = frame.load()
            for corner in (
                (0, 0),
                (size - 1, 0),
                (0, size - 1),
                (size - 1, size - 1),
            ):
                assert pixels[corner][3] == 0, (
                    f"ICO frame {size}x{size} corner {corner} has alpha "
                    f"{pixels[corner][3]}; the transparent-background contract "
                    "requires fully transparent corners"
                )

    def test_background_is_substantially_transparent(self, regenerated_derivative: Path) -> None:
        """A genuine transparent background, not a shrunken tile.

        The dark-tile icons filled ~88-95% of every frame
        with the navy rounded square. The transparent
        contract keeps a clear majority-transparent
        canvas: the mark is line art, so even the 256px
        frame stays well below half coverage.
        """
        for size, frame in _load_frames(regenerated_derivative).items():
            alpha = frame.getchannel("A")
            transparent = alpha.histogram()[0]
            ratio = transparent / (size * size)
            assert ratio >= 0.40, (
                f"ICO frame {size}x{size} is only {ratio * 100:.1f}% "
                "transparent; the v2.1.5 contract is a mark on a transparent "
                "background, not a filled tile"
            )

    def test_mark_has_no_navy_tile_pixels(self, regenerated_derivative: Path) -> None:
        """No opaque pixel may carry the dark navy tile colour.

        The v2.1.4 tile was detected in the source as
        ``R+G+B < 160``. The blue->teal gradient mark's
        darkest stop sums to ~339, so any dark cluster is
        leftover tile geometry and fails the contract.
        """
        for size, frame in _load_frames(regenerated_derivative).items():
            pixels = frame.load()
            dark = sum(
                1
                for y in range(size)
                for x in range(size)
                if pixels[x, y][3] >= 128 and sum(pixels[x, y][:3]) < 200
            )
            assert dark == 0, (
                f"ICO frame {size}x{size} contains {dark} dark tile-like "
                "pixels; the v2.1.5 contract has no navy tile"
            )

    def test_mark_is_blue_dominant(self, regenerated_derivative: Path) -> None:
        """The visible mark keeps the approved blue->teal gradient.

        Every strongly-opaque pixel must have
        ``blue > red`` (the gradient runs #2563EB ->
        #14B8A6; both stops satisfy the relation).
        """
        for size, frame in _load_frames(regenerated_derivative).items():
            pixels = frame.load()
            opaque = [
                pixels[x, y] for y in range(size) for x in range(size) if pixels[x, y][3] >= 200
            ]
            assert len(opaque) > size, f"ICO frame {size}x{size} has almost no opaque mark pixels"
            bad = sum(1 for r, _g, b, _a in opaque if b <= r)
            assert bad == 0, (
                f"ICO frame {size}x{size} has {bad} opaque pixels that are "
                "not blue-dominant; the mark must keep the approved "
                "blue->teal gradient"
            )


class TestMarkFillAndCentering:
    """The mark fills the frame at professional weight and is centred."""

    def test_mark_fills_canvas_in_documented_band(self, regenerated_derivative: Path) -> None:
        """Visible-mark occupancy stays in the 85-97% band.

        The documented target is 88-92% for the
        taskbar sizes and up to ~93% for 64px+; the
        test band allows for rounding at 16px without
        being pixel-perfect. The old dark-tile glyph
        occupied ~62% of the frame width and read as
        visibly undersized next to Chrome/Docker.
        """
        for size, frame in _load_frames(regenerated_derivative).items():
            bbox = frame.getchannel("A").getbbox()
            assert bbox is not None, f"frame {size} is empty"
            width_ratio = (bbox[2] - bbox[0]) / size
            height_ratio = (bbox[3] - bbox[1]) / size
            occupancy = max(width_ratio, height_ratio)
            assert 0.85 <= occupancy <= 0.97, (
                f"ICO frame {size}x{size} visible-mark occupancy is "
                f"{occupancy * 100:.1f}%; expected the 85-97% band "
                "(professional taskbar weight, no clipping)"
            )

    def test_mark_is_optically_centered(self, regenerated_derivative: Path) -> None:
        """The mark's bounding box is centred within 6% per axis."""
        for size, frame in _load_frames(regenerated_derivative).items():
            bbox = frame.getchannel("A").getbbox()
            assert bbox is not None
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            dx = abs(cx - size / 2.0) / size
            dy = abs(cy - size / 2.0) / size
            assert dx <= 0.06 and dy <= 0.06, (
                f"ICO frame {size}x{size} mark is off-centre: "
                f"dx={dx * 100:.1f}% dy={dy * 100:.1f}%; "
                "the contract requires optical centring within 6%"
            )

    def test_mark_is_not_clipped_at_borders(self, regenerated_derivative: Path) -> None:
        """The frame borders stay non-opaque: the mark never touches them."""
        for size, frame in _load_frames(regenerated_derivative).items():
            alpha = frame.getchannel("A").load()
            border = (
                [alpha[x, 0] for x in range(size)]
                + [alpha[x, size - 1] for x in range(size)]
                + [alpha[0, y] for y in range(1, size - 1)]
                + [alpha[size - 1, y] for y in range(1, size - 1)]
            )
            assert max(border) <= 96, (
                f"ICO frame {size}x{size} reaches alpha {max(border)} at "
                "its border; the mark may be clipped by the frame bounds"
            )


class TestAlphaEdgeQuality:
    """Smooth, halo-free, residue-free alpha edges at every size."""

    def test_no_isolated_alpha_components(self, regenerated_derivative: Path) -> None:
        """Every frame carries only the mark's own large components.

        The approved symbol is two interlocked components
        (they may merge into one at 16px). Anything small
        is resampling residue and must have been removed
        by the hygiene pass.
        """
        floor = generate_exe_icon.ALPHA_FRINGE_FLOOR
        for size, frame in _load_frames(regenerated_derivative).items():
            alpha = frame.getchannel("A")
            pixels = alpha.load()
            active = {(x, y) for y in range(size) for x in range(size) if pixels[x, y] >= floor}
            components: list[int] = []
            unseen = set(active)
            while unseen:
                seed = unseen.pop()
                stack = [seed]
                count = 0
                while stack:
                    x, y = stack.pop()
                    count += 1
                    for neighbour in (
                        (x - 1, y),
                        (x + 1, y),
                        (x, y - 1),
                        (x, y + 1),
                    ):
                        if neighbour in unseen:
                            unseen.remove(neighbour)
                            stack.append(neighbour)
                components.append(count)
            assert 1 <= len(components) <= 3, (
                f"ICO frame {size}x{size} has {len(components)} alpha "
                "components; isolated fringe components are forbidden "
                "(the mark is two interlocked components, merging to one "
                "at the smallest sizes)"
            )
            assert min(components) >= 8, (
                f"ICO frame {size}x{size} smallest alpha component is "
                f"{min(components)}px; isolated specks are forbidden"
            )

    def test_no_subfloor_fringe_alpha(self, regenerated_derivative: Path) -> None:
        """No alpha value survives in the 1..fringe-floor-1 ringing band.

        The hygiene pass clamps sub-floor residue to true
        zero, so any surviving sub-floor alpha would mean
        the pass regressed.
        """
        floor = generate_exe_icon.ALPHA_FRINGE_FLOOR
        for size, frame in _load_frames(regenerated_derivative).items():
            histogram = frame.getchannel("A").histogram()
            fringe = sum(histogram[1:floor])
            assert fringe == 0, (
                f"ICO frame {size}x{size} carries {fringe} sub-floor "
                f"(1..{floor - 1}) fringe pixels; residue must be truly "
                "transparent"
            )

    def test_edge_adjacent_transparent_rgb_is_mark_coloured(
        self, regenerated_derivative: Path
    ) -> None:
        """Transparent pixels next to the mark carry a clean mark colour.

        The nearest-colour edge extension guarantees the
        RGB under edge-adjacent transparent pixels is a
        mark blue (never raw black or navy), so a
        non-premultiplied resample in the shell cannot
        blend a dark halo into the antialiased edge.
        """
        for size, frame in _load_frames(regenerated_derivative).items():
            pixels = frame.load()
            checked = 0
            for y in range(size):
                for x in range(size):
                    if pixels[x, y][3] != 0:
                        continue
                    near_mark = any(
                        0 <= x + dx < size and 0 <= y + dy < size and pixels[x + dx, y + dy][3] > 0
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    )
                    if not near_mark:
                        continue
                    checked += 1
                    red, green, blue, _alpha = pixels[x, y]
                    assert red + green + blue >= 120, (
                        f"ICO frame {size}x{size} transparent edge pixel "
                        f"({x}, {y}) carries dark RGB "
                        f"({red}, {green}, {blue}); resampling could blend "
                        "a dark halo into the mark edge"
                    )
            assert checked > 0, (
                f"ICO frame {size}x{size} has no edge-adjacent transparent "
                "pixels to verify; unexpected for the mark geometry"
            )


class TestTransparentMaster:
    """The generated 1024px master is a transparent mark-only canvas."""

    def test_master_is_transparent_mark_only(self, regenerated_master: Path) -> None:
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(regenerated_master) as image:
            frame = image.convert("RGBA")
        size = frame.size[0]
        assert frame.size == (1024, 1024)
        pixels = frame.load()
        for corner in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
            assert pixels[corner][3] == 0
        bbox = frame.getchannel("A").getbbox()
        assert bbox is not None
        occupancy = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / size
        assert 0.85 <= occupancy <= 0.97
        dark = sum(
            1
            for y in range(size)
            for x in range(size)
            if pixels[x, y][3] >= 128 and sum(pixels[x, y][:3]) < 200
        )
        assert dark == 0, "the master must not contain navy tile pixels"


class TestBuildSafety:
    """The build is idempotent and fails loud on hostile sources."""

    def test_build_is_idempotent(self, tmp_path: Path) -> None:
        first = tmp_path / "first.ico"
        second = tmp_path / "second.ico"
        generate_exe_icon.build_exe_icon(
            approved_symbol_png=APPROVED_SYMBOL_PNG,
            derivative_ico=first,
            master_png=None,
        )
        generate_exe_icon.build_exe_icon(
            approved_symbol_png=APPROVED_SYMBOL_PNG,
            derivative_ico=second,
            master_png=None,
        )
        assert first.read_bytes() == second.read_bytes()

    def test_fail_loud_on_empty_source(self, tmp_path: Path) -> None:
        from PIL import Image  # type: ignore[import-not-found]

        hostile = tmp_path / "empty.png"
        Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(hostile, format="PNG")
        with pytest.raises(ValueError, match="no symbol content"):
            generate_exe_icon.build_exe_icon(
                approved_symbol_png=hostile,
                derivative_ico=tmp_path / "out.ico",
                master_png=None,
            )

    def test_fail_loud_on_tiny_mark_source(self, tmp_path: Path) -> None:
        """A source whose mark fills far less than the approved symbol
        aborts the build instead of shipping an undersized derivative."""
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]

        hostile = tmp_path / "tiny.png"
        image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((462, 462, 661, 661), fill=(37, 99, 235, 255))
        image.save(hostile, format="PNG")
        with pytest.raises(ValueError, match="fills only"):
            generate_exe_icon.build_exe_icon(
                approved_symbol_png=hostile,
                derivative_ico=tmp_path / "out.ico",
                master_png=None,
            )

    def test_cleaned_source_is_residue_free(self) -> None:
        """The cleaned mark keeps no distant low-alpha residue.

        Every kept non-core pixel must sit within the
        documented keep radius of a strong core pixel;
        the approved export's shadow lives beyond that
        radius with alpha <= 84 and must be gone.
        """
        from PIL import Image, ImageFilter  # type: ignore[import-not-found]

        with Image.open(APPROVED_SYMBOL_PNG) as source:
            source.load()
            cleaned = generate_exe_icon._clean_symbol_source(source)
        alpha = cleaned.getchannel("A")
        core = alpha.point(
            lambda value: 255 if value >= generate_exe_icon.ALPHA_CORE_THRESHOLD else 0
        )
        near = core.filter(ImageFilter.MaxFilter(2 * generate_exe_icon.EDGE_KEEP_RADIUS + 1))
        alpha_pixels = alpha.load()
        near_pixels = near.load()
        outside = sum(
            1
            for y in range(cleaned.size[1])
            for x in range(cleaned.size[0])
            if alpha_pixels[x, y] > 0 and not near_pixels[x, y]
        )
        assert outside == 0, (
            "the cleaned mark retains alpha outside the documented "
            "core+radius neighbourhood; shadow/residue survived the clean"
        )
