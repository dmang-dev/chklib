"""Tests for the terrain layers.

Three things here are easy to get wrong in ways that produce a plausible map
rather than an error, so each has a test that would fail loudly:

**Indexing is row-major**, ``y * width + x``. Chkdraft's own header comments
declare these arrays column-major and the same wrong comment appears verbatim on
MTXM, TILE, ISOM and MASK. On a square map the two are indistinguishable, which
is why the tests below use non-square maps.

**MTXM and TILE are distinct layers.** MTXM is what the game reads; TILE is the
editor's ISOM-derived layer. They are byte-identical in only 1 of 65 corpus maps.

**MASK polarity**: a set bit means the tile IS fogged for that player.
"""

from __future__ import annotations

import pathlib
import struct

import pytest

from openstaredit import Chk
from openstaredit.views import FogGrid, TileGrid, terrain_for, view_for


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def build(width: int, height: int, *, mtxm: bytes | None = None,
          tile: bytes | None = None, mask: bytes | None = None) -> Chk:
    parts = [sect(b"DIM ", struct.pack("<HH", width, height))]
    if mtxm is not None:
        parts.append(sect(b"MTXM", mtxm))
    if tile is not None:
        parts.append(sect(b"TILE", tile))
    if mask is not None:
        parts.append(sect(b"MASK", mask))
    return Chk.from_bytes(b"".join(parts))


def tiles(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}H", *values)


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


def test_indexing_is_row_major() -> None:
    """A non-square map: column-major would read the wrong cell entirely."""
    # 4 wide, 2 tall. Cell (1, 1) is index 1*4 + 1 = 5.
    grid = terrain_for(build(4, 2, mtxm=tiles([10, 11, 12, 13, 20, 21, 22, 23])))
    assert grid.get(1, 1) == 21
    assert grid.index(1, 1) == 5
    assert grid.get(3, 0) == 13
    assert grid.get(0, 1) == 20


def test_rows() -> None:
    grid = terrain_for(build(3, 2, mtxm=tiles([1, 2, 3, 4, 5, 6])))
    assert grid.row(0) == [1, 2, 3]
    assert grid.row(1) == [4, 5, 6]


def test_out_of_bounds_raises_rather_than_wrapping() -> None:
    grid = terrain_for(build(4, 2, mtxm=tiles([0] * 8)))
    for x, y in ((4, 0), (0, 2), (-1, 0), (0, -1)):
        with pytest.raises(IndexError):
            grid.get(x, y)


def test_subscript_syntax() -> None:
    grid = terrain_for(build(2, 2, mtxm=tiles([1, 2, 3, 4])))
    assert grid[1, 0] == 2
    grid[1, 0] = 99
    assert grid[1, 0] == 99


# --------------------------------------------------------------------------
# Shape tolerance
# --------------------------------------------------------------------------


def test_grid_is_padded_to_the_map_dimensions() -> None:
    """The cell count comes from the section; the shape comes from DIM."""
    grid = terrain_for(build(4, 4, mtxm=tiles([7, 7])))  # 2 tiles for a 16-tile map
    assert len(grid) == 16
    assert grid.is_short
    assert grid.get(0, 0) == 7
    assert grid.get(3, 3) == 0


def test_a_short_section_round_trips_short() -> None:
    """Chkdraft re-emits at full nominal size, turning short input into padded
    output. Preserving the original length is the whole point of to_bytes()."""
    payload = tiles([7, 7])
    grid = terrain_for(build(4, 4, mtxm=payload))
    assert grid.to_bytes() == payload
    assert len(grid.to_bytes(normalize=True)) == 16 * 2


def test_an_oversized_section_is_clipped_for_reading_but_preserved_on_write() -> None:
    payload = tiles([1] * 20)  # a 2x2 map only has 4 cells
    grid = terrain_for(build(2, 2, mtxm=payload))
    assert len(grid) == 4
    assert grid.to_bytes() == payload


def test_an_odd_trailing_byte_becomes_a_low_half_tile() -> None:
    """What the game does with it, per openbw. Chkdraft rounds up and pads."""
    payload = tiles([0x1234]) + b"\x56"
    grid = terrain_for(build(4, 1, mtxm=payload))
    assert grid.has_odd_tail
    assert grid.get(0, 0) == 0x1234
    assert grid.get(1, 0) == 0x56
    assert grid.to_bytes() == payload


def test_malformed_terrain_never_raises() -> None:
    for payload in (b"", b"\x01", b"\xff" * 3):
        grid = terrain_for(build(8, 8, mtxm=payload))
        assert len(grid) == 64
        assert grid.to_bytes() == payload


def test_terrain_without_dimensions_returns_none() -> None:
    """A grid with no shape cannot be indexed, so guessing one would be worse."""
    chk = Chk.from_bytes(sect(b"MTXM", tiles([1, 2, 3, 4])))
    assert terrain_for(chk) is None


def test_absent_section_returns_none() -> None:
    assert terrain_for(build(4, 4, mtxm=tiles([0] * 16)), "MASK") is None


# --------------------------------------------------------------------------
# Tile ids
# --------------------------------------------------------------------------


def test_tile_decomposition() -> None:
    assert TileGrid.group(2148) == 134
    assert TileGrid.group_index(2148) == 4
    assert TileGrid.group(0xFFFF) == 0xFFF
    assert TileGrid.group_index(0xFFFF) == 0xF


def test_groups_fingerprint() -> None:
    grid = terrain_for(build(2, 2, mtxm=tiles([0x10, 0x11, 0x20, 0x20])))
    assert grid.groups() == {1, 2}


def test_setting_a_value_too_wide_raises() -> None:
    grid = terrain_for(build(2, 2, mtxm=tiles([0] * 4)))
    with pytest.raises(ValueError, match="16 bits"):
        grid.set(0, 0, 0x10000)


def test_editing_then_writing() -> None:
    chk = build(2, 2, mtxm=tiles([1, 2, 3, 4]))
    grid = terrain_for(chk)
    grid[0, 1] = 0xBEEF
    chk.replace_section("MTXM", grid.to_bytes())
    assert terrain_for(Chk.from_bytes(chk.to_bytes()))[0, 1] == 0xBEEF


# --------------------------------------------------------------------------
# MTXM vs TILE
# --------------------------------------------------------------------------


def test_mtxm_and_tile_are_separate_layers() -> None:
    chk = build(2, 2, mtxm=tiles([1, 1, 1, 1]), tile=tiles([9, 9, 9, 9]))
    assert terrain_for(chk, "MTXM").cells == [1, 1, 1, 1]
    assert terrain_for(chk, "TILE").cells == [9, 9, 9, 9]


def test_editing_one_layer_leaves_the_other_alone() -> None:
    chk = build(2, 2, mtxm=tiles([1] * 4), tile=tiles([9] * 4))
    grid = terrain_for(chk, "MTXM")
    grid[0, 0] = 5
    chk.replace_section("MTXM", grid.to_bytes())
    assert terrain_for(chk, "TILE").cells == [9, 9, 9, 9]


# --------------------------------------------------------------------------
# MASK
# --------------------------------------------------------------------------


def test_mask_is_one_byte_per_tile() -> None:
    grid = terrain_for(build(3, 2, mask=bytes([1, 2, 3, 4, 5, 6])), "MASK")
    assert isinstance(grid, FogGrid)
    assert len(grid) == 6
    assert grid.get(2, 1) == 6


def test_mask_polarity_a_set_bit_means_fogged() -> None:
    grid = terrain_for(build(2, 1, mask=bytes([0x00, 0xFF])), "MASK")
    assert not any(grid.is_fogged_for(0, 0, p) for p in range(8))
    assert all(grid.is_fogged_for(1, 0, p) for p in range(8))


def test_mask_per_player_bits() -> None:
    grid = terrain_for(build(1, 1, mask=bytes([0b00000101])), "MASK")
    assert grid.is_fogged_for(0, 0, 0)       # player 1
    assert not grid.is_fogged_for(0, 0, 1)   # player 2
    assert grid.is_fogged_for(0, 0, 2)       # player 3


def test_mask_covers_players_one_to_eight_only() -> None:
    grid = terrain_for(build(1, 1, mask=bytes([0xFF])), "MASK")
    for player in (-1, 8, 11):
        with pytest.raises(ValueError, match="players 1-8"):
            grid.is_fogged_for(0, 0, player)


def test_short_mask_is_tolerated() -> None:
    """Nothing pads MASK in any implementation; openbw reads what is there."""
    grid = terrain_for(build(4, 4, mask=bytes([0xFF, 0xFF])), "MASK")
    assert len(grid) == 16
    assert grid.get(0, 0) == 0xFF
    assert grid.get(3, 3) == 0
    assert grid.to_bytes() == bytes([0xFF, 0xFF])


# --------------------------------------------------------------------------
# Real maps
# --------------------------------------------------------------------------

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "corpus"
MAPS = sorted(CORPUS.glob("*.chk")) if CORPUS.is_dir() else []


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
@pytest.mark.parametrize("path", MAPS, ids=[p.stem for p in MAPS])
def test_terrain_round_trips_byte_exactly(path: pathlib.Path) -> None:
    chk = Chk.from_bytes(path.read_bytes())
    for name in ("MTXM", "TILE", "MASK"):
        grid = terrain_for(chk, name)
        if grid is None:
            continue
        assert grid.to_bytes() == chk.last(name).data, name


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
@pytest.mark.parametrize("path", MAPS, ids=[p.stem for p in MAPS])
def test_grid_matches_declared_dimensions(path: pathlib.Path) -> None:
    chk = Chk.from_bytes(path.read_bytes())
    dimensions = view_for(chk, "DIM")
    grid = terrain_for(chk, "MTXM")
    assert len(grid) == dimensions.tile_count
    assert (grid.width, grid.height) == (dimensions.tile_width, dimensions.tile_height)


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
def test_mtxm_and_tile_genuinely_differ_in_real_maps() -> None:
    """Aliasing TILE to MTXM would silently corrupt terrain in 64 of 65 maps."""
    identical = 0
    worst = 0.0
    for path in MAPS:
        chk = Chk.from_bytes(path.read_bytes())
        game, editor = terrain_for(chk, "MTXM"), terrain_for(chk, "TILE")
        if game.cells == editor.cells:
            identical += 1
        else:
            differing = sum(a != b for a, b in zip(game.cells, editor.cells))
            worst = max(worst, differing / len(game.cells))
    assert identical <= 1, f"{identical} maps have identical layers; expected at most 1"
    assert worst > 0.05, f"worst divergence only {worst:.2%}"


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
def test_no_corpus_map_has_short_or_odd_terrain() -> None:
    """Records that the tolerance paths above have no corpus datapoint."""
    for path in MAPS:
        chk = Chk.from_bytes(path.read_bytes())
        for name in ("MTXM", "TILE", "MASK"):
            grid = terrain_for(chk, name)
            if grid is not None:
                assert not grid.is_short and not grid.has_odd_tail, f"{path.stem} {name}"


# --------------------------------------------------------------------------
# Hardening found by adversarial review
# --------------------------------------------------------------------------


def test_duplicate_mtxm_patches_the_prefix_rather_than_replacing() -> None:
    """SPEC 1.5: MTXM duplicates use Override, not last-wins.

    This is the most common terrain-protection trick. Last-wins zeroes
    everything past the short patch, visibly corrupting the map.
    """
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 4, 4))
        + sect(b"MTXM", tiles(list(range(100, 116))))
        + sect(b"MTXM", tiles([1, 2]))
    )
    grid = terrain_for(chk)
    assert grid.cells[:2] == [1, 2]
    assert grid.cells[2:] == list(range(102, 116)), "the earlier tail must survive"
    assert grid.merged_sections == 2


def test_an_untouched_merged_grid_still_writes_its_original_bytes() -> None:
    """Duplicate MTXM is not malformed; it is the documented Override case, and
    24 of 423 installed maps use it. Refusing to serialize would raise on real
    ladder maps and break the never-raise contract."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 2, 2))
        + sect(b"MTXM", tiles([1, 2, 3, 4]))
        + sect(b"MTXM", tiles([9]))
    )
    grid = terrain_for(chk)
    assert grid.merged_sections == 2
    assert grid.to_bytes() == chk.last("MTXM").data
    assert len(grid.to_bytes(normalize=True)) == 4 * 2


def test_a_merged_grid_reports_the_merged_shape_not_the_last_fragment() -> None:
    """stored_cells and friends must describe the cells that exist, not the
    discarded tail section. Otherwise a fully populated grid reads as nearly
    empty."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 2, 2))
        + sect(b"MTXM", tiles([1, 2, 3, 4]))
        + sect(b"MTXM", tiles([9]))
    )
    grid = terrain_for(chk)
    assert grid.stored_cells == 4
    assert not grid.is_short
    assert not grid.has_odd_tail

def test_only_mtxm_gets_the_override_policy() -> None:
    """TILE and MASK are Standard: last instance wins entirely."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 2, 2))
        + sect(b"TILE", tiles([1, 2, 3, 4]))
        + sect(b"TILE", tiles([9]))
    )
    grid = terrain_for(chk, "TILE")
    assert grid.merged_sections == 1
    assert grid.cells == [9, 0, 0, 0]


def test_a_hostile_dim_cannot_exhaust_memory() -> None:
    """DIM is attacker-controlled: a 22-byte file can declare 65535x65535,
    which is 4.29 billion cells and roughly 34 GB."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 65535, 65535)) + sect(b"MTXM", b"\x01\x02")
    )
    grid = terrain_for(chk)
    assert len(grid) <= TileGrid.MAX_CELLS
    assert grid.clamped_dimensions == (65535, 65535)
    assert grid.to_bytes() == b"\x01\x02", "capping must not break round-trip"


def test_capping_preserves_the_row_stride() -> None:
    """Only height is reduced. Shrinking width would silently shift every row
    after the first, handing back the wrong tiles with no error."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 65535, 65535)) + sect(b"MTXM", b"\x01\x02")
    )
    grid = terrain_for(chk)
    assert grid.width == 65535, "width is the on-disk stride and must not change"
    assert grid.height < 65535

    # A grid wider than a hypothetical square cap must still decode correctly.
    wide = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 257, 3))
        + sect(b"MTXM", struct.pack("<771H", *range(771)))
    )
    g = terrain_for(wide)
    assert (g.width, g.height) == (257, 3)
    assert g.get(0, 1) == 257 and g.get(0, 2) == 514

def test_ordinary_dimensions_are_never_capped() -> None:
    """Real maps top out at 256x256; nothing about them should trip the cap."""
    grid = terrain_for(build(256, 256, mtxm=b"\x00\x00"))
    assert grid.clamped_dimensions is None
    assert (grid.width, grid.height) == (256, 256)

def test_a_zero_width_map_does_not_crash_row_walking() -> None:
    """The inspect renderer walks every row; DIM 0xN is malformed but reachable."""
    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 0, 8)) + sect(b"MTXM", b"\x01\x02" * 40)
    )
    grid = terrain_for(chk)
    assert grid.row(0) == []
    with pytest.raises(IndexError):
        grid.row(8)
    from openstaredit.inspect import render

    assert "[terrain]" in render(chk)


def test_an_oversized_section_is_not_fully_materialised() -> None:
    """A 4 MB section on a 2x2 map must not unpack two million tiles."""
    payload = b"\xAB" * 4_000_000
    grid = terrain_for(build(2, 2, mtxm=payload))
    assert len(grid) == 4
    assert grid.to_bytes() == payload


def test_inspect_flags_unaddressable_terrain() -> None:
    """A malformed DIM would otherwise render a full section as an empty grid."""
    from openstaredit.inspect import render

    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 1, 1)) + sect(b"MTXM", tiles([1] * 50))
    )
    assert "not addressable" in render(chk)


def test_inspect_flags_a_clamped_dim() -> None:
    from openstaredit.inspect import render

    chk = Chk.from_bytes(
        sect(b"DIM ", struct.pack("<HH", 4000, 4000)) + sect(b"MTXM", b"\x01\x02")
    )
    assert "clamped" in render(chk)


# --------------------------------------------------------------------------
# Installed maps: the corpus the sc64 fixtures do not represent
# --------------------------------------------------------------------------
#
# Every terrain defect found by the adversarial review was invisible to the
# fixture corpus, whose 65 scenarios have no duplicate sections and are all
# exactly w*h*2. The installed maps are not: across 488 scanned maps, MTXM alone
# has 55 short, 7 long, 29 odd and 24 duplicated instances. These tests exist so
# that the gap that hid those defects cannot reopen.

import glob  # noqa: E402

INSTALLED = sorted(set(glob.glob(r"I:/Blizzard/StarCraft/Maps/**/*.sc[mx]", recursive=True)))


def _installed_chks():
    from openstaredit.mpq import MpqArchive, SCENARIO_PATH

    for path in INSTALLED:
        try:
            yield path, Chk.from_bytes(
                MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
            )
        except Exception:  # noqa: BLE001 - MPQ failures are not this module's concern
            continue


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_installed_maps_round_trip_and_never_raise() -> None:
    """The gate that would have caught the duplicate-MTXM regression."""
    checked = 0
    problems: list[str] = []
    for path, chk in _installed_chks():
        for name in ("MTXM", "TILE", "MASK"):
            if name not in chk:
                continue
            try:
                grid = terrain_for(chk, name)
                data = grid.to_bytes()
            except Exception as exc:  # noqa: BLE001 - that is the failure
                problems.append(f"{pathlib.Path(path).name} {name}: {exc}")
                continue
            if data != chk.last(name).data:
                problems.append(f"{pathlib.Path(path).name} {name}: bytes differ")
            checked += 1
    assert checked >= 300, f"only {checked} sections checked"
    assert not problems, f"{len(problems)} problems: {problems[:8]}"


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_duplicate_mtxm_occurs_in_real_maps_and_is_merged() -> None:
    """Not a hypothetical: real ladder maps ship two to four MTXM sections.

    Last-wins leaves them mostly empty, which is the whole point of Override.
    """
    duplicated = 0
    for _, chk in _installed_chks():
        instances = chk.find("MTXM")
        if len(instances) < 2:
            continue
        duplicated += 1
        grid = terrain_for(chk, "MTXM")
        assert grid.merged_sections == len(instances)
        last_only = sum(1 for c in TileGrid.from_section(
            instances[-1], grid.width, grid.height).cells if c)
        merged = sum(1 for c in grid.cells if c)
        assert merged >= last_only, "merging must not lose tiles"
    assert duplicated >= 5, f"expected duplicated MTXM in real maps, found {duplicated}"


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_short_and_odd_terrain_occurs_in_real_maps() -> None:
    """The tolerance paths are exercised by real data, not just by fixtures.

    The format research recorded them as "real code with no corpus datapoint",
    which was true of the 65 fixture scenarios and false of the installed set.
    """
    short = odd = 0
    for _, chk in _installed_chks():
        for name in ("MTXM", "TILE", "MASK"):
            if name not in chk:
                continue
            grid = terrain_for(chk, name)
            short += grid.is_short
            odd += grid.has_odd_tail
    assert short or odd, "expected short or odd terrain sections among installed maps"


def test_inspect_notices_a_change_in_the_unaddressable_tail() -> None:
    """A bare count would not move when those bytes change, so a diff of an
    edit out past the map would show nothing at all."""
    from openstaredit.inspect import render

    def with_tail(tail_value: int) -> str:
        chk = Chk.from_bytes(
            sect(b"DIM ", struct.pack("<HH", 1, 1))
            + sect(b"MTXM", tiles([1] + [tail_value] * 20))
        )
        return render(chk)

    assert "not addressable" in with_tail(7)
    assert with_tail(7) != with_tail(8), "the clipped tail must affect the digest"
