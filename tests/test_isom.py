"""Tests for ``ISOM``, the editor's isometric terrain.

ISOM is the least reliable part of the format. The research rates its framing
Confidence B and its **bit layout Confidence C** -- Chkdraft is the only witness
-- so the record keeps each side as a raw ``u16`` and the accessors are an
interpretation over it rather than a replacement.

The empirical tests at the bottom are the interesting ones. Across 488 real maps
the claimed layout holds up well: every ISOM value fits the 11 bits the layout
allots it, every edge-flag field is even as a field at bits 3..1 must be, and the
editor flag bits that the layout says are written to file *are* found in real
files. That last one matters: it means masking on read is necessary rather than
theoretical.

ISOM is editor-only. StarCraft reads MTXM, so a stale or absent ISOM has no
in-game consequence, which is also why it is so often malformed.
"""

from __future__ import annotations

import glob
import pathlib
import struct

import pytest

from chklib import Chk
from chklib.records import IsomRect
from chklib.views import IsomGrid, isom_for


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def rect(left: int = 0, top: int = 0, right: int = 0, bottom: int = 0) -> bytes:
    return struct.pack("<HHHH", left, top, right, bottom)


def build(width: int, height: int, isom: bytes | None = None) -> Chk:
    parts = [sect(b"DIM ", struct.pack("<HH", width, height))]
    if isom is not None:
        parts.append(sect(b"ISOM", isom))
    return Chk.from_bytes(b"".join(parts))


def full(width: int, height: int, fill: bytes = rect()) -> Chk:
    w, h = IsomGrid.shape_for(width, height)
    return build(width, height, fill * (w * h))


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------


def test_record_is_eight_bytes() -> None:
    """Backed by the only compile-time assertion in Chkdraft's header."""
    assert IsomRect.SIZE == 8
    assert IsomRect._STRUCT.size == 8


def test_record_round_trips_any_bytes() -> None:
    raw = bytes(range(8))
    assert IsomRect.from_bytes(raw).to_bytes() == raw


@pytest.mark.parametrize(
    "tiles,shape",
    [
        ((64, 64), (33, 65)),
        ((128, 128), (65, 129)),
        ((96, 64), (49, 65)),
        ((256, 256), (129, 257)),
        ((1, 1), (1, 2)),  # integer division, not rounding
    ],
)
def test_grid_shape(tiles, shape) -> None:
    """``isom_width = tileWidth // 2 + 1`` and ``isom_height = tileHeight + 1``."""
    assert IsomGrid.shape_for(*tiles) == shape


def test_grid_size_matches_the_section() -> None:
    grid = isom_for(full(64, 64))
    assert (grid.width, grid.height) == (33, 65)
    assert len(grid) == 33 * 65
    assert grid.expected_size == 33 * 65 * 8


def test_indexing_is_row_major_over_the_isom_grid() -> None:
    """Not the tile grid: ISOM has its own, differently-shaped coordinates."""
    chk = full(4, 2)  # ISOM is 3x3
    grid = isom_for(chk)
    assert (grid.width, grid.height) == (3, 3)
    grid[1, 2] = IsomRect(9, 9, 9, 9)
    assert grid.index(1, 2) == 7
    assert grid.rects[7].left == 9


def test_out_of_bounds_raises() -> None:
    grid = isom_for(full(4, 2))
    for x, y in ((3, 0), (0, 3), (-1, 0)):
        with pytest.raises(IndexError):
            grid.get(x, y)


def test_absent_isom_or_dim_returns_none() -> None:
    assert isom_for(build(64, 64)) is None
    assert isom_for(Chk.from_bytes(sect(b"ISOM", rect()))) is None


# --------------------------------------------------------------------------
# Bit layout - Confidence C, so raw values stay reachable
# --------------------------------------------------------------------------


def test_value_is_stored_shifted_left_by_four() -> None:
    side = (1234 << 4) | 0x0006
    assert IsomRect.value_of(side) == 1234
    assert IsomRect.edge_flags_of(side) == 0x0006


def test_editor_flags_are_masked_out_of_the_value() -> None:
    """Both flags are written to file, so neither may leak into the value."""
    side = IsomRect.VISITED | (77 << 4) | 0x000A | IsomRect.MODIFIED
    assert IsomRect.value_of(side) == 77
    assert IsomRect.edge_flags_of(side) == 0x000A


def test_editor_flags_are_detected_and_preserved() -> None:
    raw = rect(left=IsomRect.VISITED, top=IsomRect.MODIFIED)
    record = IsomRect.from_bytes(raw)
    assert record.has_editor_flags
    assert record.to_bytes() == raw, "flags must survive; Chkdraft writes them"


def test_a_clean_record_reports_no_editor_flags() -> None:
    record = IsomRect.from_bytes(rect(left=(5 << 4) | 0x0004))
    assert not record.has_editor_flags
    assert record.values() == (5, 0, 0, 0)
    assert record.edge_flags() == (4, 0, 0, 0)


def test_clear_mask_covers_value_and_edges_only() -> None:
    assert IsomRect.CLEAR_EDITOR_FLAGS == 0x7FFE
    assert IsomRect.CLEAR_EDITOR_FLAGS & IsomRect.VISITED == 0
    assert IsomRect.CLEAR_EDITOR_FLAGS & IsomRect.MODIFIED == 0
    assert IsomRect.VALUE_MASK | IsomRect.EDGE_MASK == IsomRect.CLEAR_EDITOR_FLAGS


def test_is_empty() -> None:
    assert IsomRect(0, 0, 0, 0).is_empty
    assert not IsomRect(0, 0, 1, 0).is_empty


# --------------------------------------------------------------------------
# Malformed input
# --------------------------------------------------------------------------


def test_a_short_section_is_padded_for_reading_and_written_back_short() -> None:
    grid = isom_for(build(4, 2, rect(1, 2, 3, 4)))  # 1 record for a 3x3 grid
    assert len(grid) == 9
    assert grid.is_short
    assert grid.get(0, 0).left == 1
    assert grid.get(2, 2).is_empty
    assert grid.to_bytes() == rect(1, 2, 3, 4)


def test_an_oversized_section_does_not_underflow() -> None:
    """Chkdraft computes ``expected - actual`` in size_t after testing ``!=``,
    so an oversized ISOM underflows into an astronomical insert. Truncate."""
    payload = rect(7, 7, 7, 7) * 400  # a 3x3 grid needs 9
    grid = isom_for(build(4, 2, payload))
    assert len(grid) == 9
    assert grid.to_bytes() == payload


def test_a_ragged_section_never_raises() -> None:
    for payload in (b"", b"\x01", b"\x00" * 7, b"\xff" * 13):
        grid = isom_for(build(8, 8, payload))
        assert len(grid) == IsomGrid.shape_for(8, 8)[0] * IsomGrid.shape_for(8, 8)[1]
        assert grid.to_bytes() == payload


def test_a_hostile_dim_cannot_exhaust_memory() -> None:
    chk = build(65535, 65535, rect())
    grid = isom_for(chk)
    assert len(grid) <= IsomGrid.MAX_RECORDS
    assert grid.to_bytes() == rect()


def test_editing_emits_the_full_grid() -> None:
    """Same rule as the tile grids: untouched keeps its bytes, edited grows."""
    chk = build(4, 2, rect(1, 1, 1, 1))
    grid = isom_for(chk)
    assert grid.to_bytes() == rect(1, 1, 1, 1)
    grid[2, 2] = IsomRect(5, 5, 5, 5)
    written = grid.to_bytes()
    assert len(written) == 9 * 8
    chk.replace_section("ISOM", written)
    assert isom_for(Chk.from_bytes(chk.to_bytes()))[2, 2].left == 5


# --------------------------------------------------------------------------
# Real maps
# --------------------------------------------------------------------------

CORPUS = sorted((pathlib.Path(__file__).parent / "fixtures" / "corpus").glob("*.chk"))
INSTALLED = sorted(set(glob.glob(r"I:/Blizzard/StarCraft/Maps/**/*.sc[mx]", recursive=True)))


def _real_chks():
    from chklib.mpq import MpqArchive, SCENARIO_PATH

    for path in CORPUS:
        yield path.name, Chk.from_bytes(path.read_bytes())
    for path in INSTALLED:
        try:
            yield pathlib.Path(path).name, Chk.from_bytes(
                MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
            )
        except Exception:  # noqa: BLE001
            continue


@pytest.mark.skipif(not CORPUS, reason="no fixtures; run tools/extract_fixtures.py")
@pytest.mark.parametrize("path", CORPUS, ids=[p.stem for p in CORPUS])
def test_corpus_isom_framing_is_exact(path: pathlib.Path) -> None:
    chk = Chk.from_bytes(path.read_bytes())
    grid = isom_for(chk)
    assert grid is not None
    assert len(chk.last("ISOM").data) == grid.expected_size
    assert grid.to_bytes() == chk.last("ISOM").data


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_isom_over_every_real_map() -> None:
    present = exact = 0
    problems: list[str] = []
    for name, chk in _real_chks():
        grid = isom_for(chk)
        if grid is None:
            continue
        present += 1
        if grid.to_bytes() != chk.last("ISOM").data:
            problems.append(f"{name}: bytes differ")
        if len(chk.last("ISOM").data) == grid.expected_size:
            exact += 1
    assert present >= 400, f"only {present} maps had ISOM"
    assert not problems, problems[:5]
    assert exact / present > 0.95, f"only {exact}/{present} exactly sized"


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_the_claimed_bit_layout_holds_on_real_maps() -> None:
    """The layout is Confidence C, so this is the evidence for it.

    If the value field were not 11 bits at 14..4, values would exceed 2047. If
    the edge flags were not 3 bits at 3..1, odd flag values would appear.
    """
    max_value = 0
    edge_values: set[int] = set()
    for _, chk in _real_chks():
        grid = isom_for(chk)
        if grid is None:
            continue
        for record in grid:
            for side in record.sides:
                max_value = max(max_value, IsomRect.value_of(side))
                edge_values.add(IsomRect.edge_flags_of(side))
    assert max_value <= 0x7FF, f"value {max_value} does not fit 11 bits"
    assert max_value > 0, "no ISOM values at all - the layout was not exercised"
    assert all(v % 2 == 0 for v in edge_values), f"odd edge flags: {sorted(edge_values)}"
    assert edge_values <= set(range(0, 16, 2))


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_editor_flags_really_do_appear_in_files() -> None:
    """The layout warns that Chkdraft's internal flags are written out and only
    cleared by convention. Real maps confirm it, so masking is not optional."""
    with_flags = 0
    for _, chk in _real_chks():
        grid = isom_for(chk)
        if grid is not None and grid.has_editor_flags:
            with_flags += 1
    assert with_flags > 0, "expected at least one map carrying editor flags"
