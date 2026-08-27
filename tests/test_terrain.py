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
