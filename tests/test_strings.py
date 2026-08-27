"""Tests for writing the string table.

Editing a map's name, its description, a location name or any trigger text means
changing a string, so this is what turns the library from something that reads
maps into something that edits them.

Two failure modes are worth more attention than the rest, because both corrupt a
map silently rather than raising:

**String ids are positional.** Id 7 is referenced as 7 from ``SPRP``, ``MRGN``,
``FORC`` and every trigger. Compacting gaps would repoint every reference in the
map without touching a single trigger.

**Offsets are 16-bit.** Chkdraft's own guard sums string lengths without the
terminating NUL its writer then emits, so it accepts payloads slightly over the
limit and writes offsets that wrap modulo 65536. The tests below pin that this
implementation raises instead.
"""

from __future__ import annotations

import pathlib
import struct

import pytest

from openstaredit import Chk, StringTable
from openstaredit.views import StringTableView, string_table_for, view_for


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def parse(payload: bytes) -> StringTableView:
    return view_for(Chk.from_bytes(sect(b"STR ", payload)), "STR")


def roundtrip(table: StringTable, **kw) -> StringTableView:
    return parse(table.to_bytes(**kw))


# --------------------------------------------------------------------------
# Basics
# --------------------------------------------------------------------------


def test_write_then_read() -> None:
    table = StringTable()
    table[1] = b"first"
    table[2] = b"second"
    view = roundtrip(table)
    assert view.get(1) == b"first"
    assert view.get(2) == b"second"


def test_ids_are_one_based() -> None:
    table = StringTable()
    table[1] = b"one"
    assert roundtrip(table).get(0) is None


def test_id_zero_is_rejected() -> None:
    with pytest.raises(ValueError, match="1-based"):
        StringTable()[0] = b"nope"


def test_gaps_are_preserved_not_compacted() -> None:
    """Compacting would repoint every reference in the map."""
    table = StringTable()
    table[1] = b"one"
    table[5] = b"five"
    view = roundtrip(table)
    assert view.get(1) == b"one"
    assert view.get(5) == b"five"
    for missing in (2, 3, 4):
        assert not view.get(missing), f"id {missing} should be empty"


def test_setting_empty_removes_the_string() -> None:
    table = StringTable()
    table[1] = b"here"
    table[1] = b""
    assert 1 not in table.strings
    assert not roundtrip(table).get(1)


def test_add_uses_the_lowest_free_id() -> None:
    table = StringTable()
    table[1] = b"a"
    table[3] = b"c"
    assert table.add(b"b") == 2
    assert table.add(b"d") == 4


def test_str_values_are_encoded() -> None:
    table = StringTable()
    table[1] = "café"
    assert roundtrip(table).get(1) == "café".encode("cp1252")


def test_declared_count_grows_to_fit() -> None:
    table = StringTable()
    table[40] = b"x"
    assert parse(table.to_bytes()).count >= 40


def test_declared_count_is_honoured_when_larger() -> None:
    """Real maps declare 1024 slots regardless of how many are used."""
    table = StringTable(declared_count=1024)
    table[1] = b"x"
    assert parse(table.to_bytes()).count == 1024


def test_empty_table_still_writes_a_valid_section() -> None:
    view = parse(StringTable(declared_count=4).to_bytes())
    assert view.count == 4
    assert view.used_ids() == []


# --------------------------------------------------------------------------
# The limits that corrupt maps silently
# --------------------------------------------------------------------------


def test_payload_past_the_16_bit_offset_limit_raises() -> None:
    """The exact case Chkdraft mis-guards and writes as wrapped offsets."""
    table = StringTable()
    for index in range(1, 40):
        table[index] = b"x" * 2000  # ~78 KB, past the addressable 64 KB
    with pytest.raises(ValueError, match="16-bit"):
        table.to_bytes()


def test_a_table_just_under_the_limit_still_writes() -> None:
    """The guard must not be so conservative that it refuses valid tables."""
    table = StringTable(declared_count=2)
    table[1] = b"x" * 60000
    data = table.to_bytes()
    assert parse(data).get(1) == b"x" * 60000


def test_the_guard_counts_the_terminating_nul() -> None:
    """Off-by-one-per-string is precisely the upstream bug being avoided.

    Many short strings whose lengths alone fit, but whose NULs push the total
    over, must be refused rather than written with wrapped offsets.
    """
    table = StringTable(declared_count=1)
    # 2 + 2*1 header, 1 shared NUL, then one string of exactly the remaining room
    room = 0xFFFF - (2 + 2 * 1) - 1
    table[1] = b"y" * room
    table.to_bytes()  # exactly fits
    table[1] = b"y" * (room + 1)
    with pytest.raises(ValueError, match="16-bit"):
        table.to_bytes()


def test_too_many_ids_raises_with_the_reason() -> None:
    table = StringTable(declared_count=40000)
    with pytest.raises(ValueError, match="reachable maximum"):
        table.to_bytes()


def test_the_id_ceiling_is_the_offset_table_size() -> None:
    """32766 is derived from 2 + 2N filling the addressable space, not inherited."""
    assert StringTable.MAX_IDS == 32766
    assert 2 + 2 * StringTable.MAX_IDS <= StringTable.MAX_OFFSET


# --------------------------------------------------------------------------
# Optional behaviours
# --------------------------------------------------------------------------


def test_dedupe_shares_one_offset_for_equal_strings() -> None:
    table = StringTable()
    for index in range(1, 6):
        table[index] = b"identical"
    plain = table.to_bytes()
    shared = table.to_bytes(dedupe=True)
    assert len(shared) < len(plain)
    view = parse(shared)
    assert all(view.get(i) == b"identical" for i in range(1, 6))
    assert len(set(view.offsets)) < len(view.offsets)


def test_dedupe_is_off_by_default() -> None:
    table = StringTable()
    table[1] = table[2] = b"same"
    view = parse(table.to_bytes())
    assert view.offsets[0] != view.offsets[1]


def test_tail_data_is_carried_through() -> None:
    """Chkdraft parses tail data then drops it; that loses map content."""
    table = StringTable(tail=b"\xde\xad\xbe\xef")
    table[1] = b"x"
    assert table.to_bytes().endswith(b"\xde\xad\xbe\xef")


def test_unused_slots_share_one_nul() -> None:
    """What StarEdit and Chkdraft both emit: a single NUL after the table."""
    table = StringTable(declared_count=8)
    table[1] = b"only"
    view = parse(table.to_bytes())
    header_end = 2 + 2 * 8
    assert view.offsets[1] == header_end
    assert len({view.offsets[i] for i in range(1, 8)}) == 1


# --------------------------------------------------------------------------
# Chk section replacement
# --------------------------------------------------------------------------


def test_replace_section_targets_the_effective_duplicate() -> None:
    """The last section wins, so that is the one an edit must hit."""
    chk = Chk.from_bytes(sect(b"DIM ", b"aaaa") + sect(b"DIM ", b"bbbb"))
    chk.replace_section("DIM", b"cccc")
    assert [s.data for s in chk.find("DIM")] == [b"aaaa", b"cccc"]
    assert chk.last("DIM").data == b"cccc"


def test_replace_section_updates_the_declared_size() -> None:
    chk = Chk.from_bytes(sect(b"STR ", b"aa"))
    chk.replace_section("STR", b"aaaaaa")
    assert chk.last("STR").declared_size == 6
    assert Chk.from_bytes(chk.to_bytes()).last("STR").data == b"aaaaaa"


def test_replace_missing_section_raises() -> None:
    with pytest.raises(KeyError):
        Chk.from_bytes(sect(b"DIM ", b"aaaa")).replace_section("TRIG", b"")


def test_add_section() -> None:
    chk = Chk.from_bytes(sect(b"DIM ", b"aaaa"))
    chk.add_section("MTXM", b"\x01\x02")
    assert chk.last("MTXM").data == b"\x01\x02"
    assert Chk.from_bytes(chk.to_bytes()).last("MTXM").data == b"\x01\x02"


# --------------------------------------------------------------------------
# Against the real corpus
# --------------------------------------------------------------------------

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "corpus"
MAPS = sorted(CORPUS.glob("*.chk")) if CORPUS.is_dir() else []


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
@pytest.mark.parametrize("path", MAPS, ids=[p.stem for p in MAPS])
def test_rebuilding_a_real_table_preserves_every_string(path: pathlib.Path) -> None:
    """Byte-exactness is not achievable -- the original layout is not recoverable
    -- but every string must survive at its own id."""
    chk = Chk.from_bytes(path.read_bytes())
    view = view_for(chk, "STR")
    before = {i: view.get(i) for i in view.used_ids()}

    chk.replace_section("STR", StringTable.from_view(view).to_bytes())
    after_view = view_for(chk, "STR")
    after = {i: after_view.get(i) for i in after_view.used_ids()}
    assert before == after


@pytest.mark.skipif(not MAPS, reason="no fixtures; run tools/extract_fixtures.py")
def test_renaming_a_map_leaves_other_references_intact() -> None:
    """The whole point of preserving ids: locations must still resolve."""
    chk = Chk.from_bytes(MAPS[0].read_bytes())
    sprp, view = view_for(chk, "SPRP"), view_for(chk, "STR")
    locations = [
        loc.string_id for loc in view_for(chk, "MRGN") if loc.string_id
    ]
    named_before = {sid: view.get(sid) for sid in locations}

    table = StringTable.from_view(view)
    table[sprp.name_string_id] = b"A Completely New Name"
    chk.replace_section("STR", table.to_bytes())

    after = view_for(chk, "STR")
    assert after.get(sprp.name_string_id) == b"A Completely New Name"
    assert {sid: after.get(sid) for sid in locations} == named_before


# --------------------------------------------------------------------------
# STRx - the Remastered 32-bit table
# --------------------------------------------------------------------------


def parse_wide(payload: bytes) -> StringTableView:
    return view_for(Chk.from_bytes(sect(b"STRx", payload)), "STRx")


def test_strx_is_str_with_32_bit_fields() -> None:
    table = StringTable()
    table[1] = b"first"
    table[2] = b"second"
    view = parse_wide(table.to_bytes(wide=True))
    assert view.wide
    assert view.get(1) == b"first"
    assert view.get(2) == b"second"


def test_strx_header_is_four_bytes_per_entry() -> None:
    table = StringTable(declared_count=3)
    table[1] = b"x"
    data = table.to_bytes(wide=True)
    assert struct.unpack_from("<I", data, 0)[0] == 3
    # Unused slots point at the shared NUL right after the offset table.
    assert struct.unpack_from("<I", data, 8)[0] == 4 + 4 * 3


def test_the_two_widths_are_not_interchangeable() -> None:
    """Reading a STRx table as STR yields garbage, not an error.

    That is exactly why the width has to be decided by the section name rather
    than sniffed: nothing about a mis-read table announces itself. On real
    Remastered maps the wrong width produces empty strings; on this small
    fixture it produces a stray byte. Either way it is not the string.
    """
    table = StringTable()
    table[1] = b"hello"
    wide_bytes = table.to_bytes(wide=True)
    assert parse_wide(wide_bytes).get(1) == b"hello"
    assert parse(wide_bytes).get(1) != b"hello"


def test_strx_has_no_16_bit_ceiling() -> None:
    """A payload that STR must refuse is fine as STRx."""
    table = StringTable()
    for index in range(1, 40):
        table[index] = b"x" * 2000  # ~78 KB
    with pytest.raises(ValueError, match="16-bit"):
        table.to_bytes()
    view = parse_wide(table.to_bytes(wide=True))
    assert view.get(1) == b"x" * 2000
    assert view.get(39) == b"x" * 2000


def test_strx_id_ceiling_is_derived_and_matches_chkdraft() -> None:
    assert StringTable.MAX_IDS_WIDE == (0xFFFFFFFF - 4) // 4 == 1073741822
    with pytest.raises(ValueError, match="reachable maximum"):
        StringTable(declared_count=StringTable.MAX_IDS_WIDE + 1).to_bytes(wide=True)


def test_strx_supersedes_str_in_either_order() -> None:
    """Chkdraft, bw-chk and eudplib agree; blackvrice abstains and so fails to
    open ordinary Remastered maps that kept a legacy STR."""
    narrow = StringTable()
    narrow[1] = b"from STR"
    wide = StringTable()
    wide[1] = b"from STRx"

    for order in (
        sect(b"STR ", narrow.to_bytes()) + sect(b"STRx", wide.to_bytes(wide=True)),
        sect(b"STRx", wide.to_bytes(wide=True)) + sect(b"STR ", narrow.to_bytes()),
    ):
        table = string_table_for(Chk.from_bytes(order))
        assert table.wide
        assert table.get(1) == b"from STRx"


def test_string_table_for_falls_back_to_str() -> None:
    table = StringTable()
    table[1] = b"only STR"
    chk = Chk.from_bytes(sect(b"STR ", table.to_bytes()))
    resolved = string_table_for(chk)
    assert resolved is not None and not resolved.wide
    assert resolved.get(1) == b"only STR"


def test_string_table_for_returns_none_when_absent() -> None:
    assert string_table_for(Chk.from_bytes(sect(b"DIM ", b"aaaa"))) is None


# --------------------------------------------------------------------------
# STRx against real Remastered maps
# --------------------------------------------------------------------------

import glob  # noqa: E402

_INSTALLED = sorted(set(glob.glob(r"I:/Blizzard/StarCraft/Maps/**/*.sc[mx]", recursive=True)))


def _strx_maps() -> list[bytes]:
    from openstaredit.mpq import MpqArchive, SCENARIO_PATH

    out = []
    for path in _INSTALLED:
        try:
            raw = MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
        except Exception:  # noqa: BLE001
            continue
        if b"STRx" in raw[:64] or "STRx" in Chk.from_bytes(raw):
            out.append(raw)
    return out


STRX_MAPS = _strx_maps() if _INSTALLED else []


@pytest.mark.skipif(not STRX_MAPS, reason="no installed maps using STRx")
def test_real_strx_maps_resolve_to_text() -> None:
    """If the field width were wrong, every string would come back empty."""
    from openstaredit.views import view_for as _view_for

    resolved = 0
    for raw in STRX_MAPS:
        chk = Chk.from_bytes(raw)
        table = string_table_for(chk)
        assert table.wide, "a map with STRx must resolve through the wide table"
        sprp = _view_for(chk, "SPRP")
        name = table.get(sprp.name_string_id)
        assert name, "scenario name did not resolve"
        resolved += 1
    assert resolved == len(STRX_MAPS)


@pytest.mark.skipif(not STRX_MAPS, reason="no installed maps using STRx")
def test_rebuilding_a_real_strx_table_preserves_every_string() -> None:
    for raw in STRX_MAPS:
        chk = Chk.from_bytes(raw)
        table = string_table_for(chk)
        before = {i: table.get(i) for i in table.used_ids()}
        chk.replace_section("STRx", StringTable.from_view(table).to_bytes(wide=True))
        after_view = string_table_for(chk)
        assert {i: after_view.get(i) for i in after_view.used_ids()} == before


@pytest.mark.skipif(not STRX_MAPS, reason="no installed maps using STRx")
def test_no_installed_map_carries_both_tables() -> None:
    """An empirical note: the precedence rule is real but unexercised here."""
    both = [raw for raw in STRX_MAPS if "STR" in Chk.from_bytes(raw)]
    assert not both, f"{len(both)} maps carry both STR and STRx"
