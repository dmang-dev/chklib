"""View-level tests.

Two things matter here beyond correct parsing: that a view round-trips a section
byte-exactly when unmodified, and that it does so for sections that are shorter
or longer than nominal. Chkdraft re-emits short sections at full nominal length,
turning a short input into a padded output (SPEC 8.3); these tests pin down that
this library does not.
"""

from __future__ import annotations

import struct

import pytest

from chklib import Chk, Section
from chklib.records import Location, Trigger, Unit
from chklib.views import (
    Dimensions,
    PlayerRaces,
    PlayerSlots,
    RecordArrayView,
    ScenarioProperties,
    StringTableView,
    TilesetRef,
    TriggerListView,
    Version,
    view_for,
)


def sect(name: bytes, payload: bytes) -> Section:
    return Section(name, len(payload), payload, 0)


def chk_of(*pairs: tuple[bytes, bytes]) -> Chk:
    raw = b"".join(n + struct.pack("<i", len(p)) + p for n, p in pairs)
    return Chk.from_bytes(raw)


# --------------------------------------------------------------------------
# Scalar views
# --------------------------------------------------------------------------


def test_dimensions() -> None:
    view = Dimensions.from_section(sect(b"DIM ", struct.pack("<HH", 96, 128)))
    assert (view.tile_width, view.tile_height) == (96, 128)
    assert view.pixel_width == 96 * 32
    assert view.pixel_height == 128 * 32
    assert view.tile_count == 96 * 128
    assert str(view) == "96x128"


def test_scenario_properties_name_comes_first() -> None:
    view = ScenarioProperties.from_section(sect(b"SPRP", struct.pack("<HH", 7, 9)))
    assert view.name_string_id == 7
    assert view.description_string_id == 9


def test_version_names() -> None:
    assert Version.from_section(sect(b"VER ", struct.pack("<H", 59))).name.startswith("StarCraft")
    assert Version.from_section(sect(b"VER ", struct.pack("<H", 205))).name == "Brood War"
    assert "unknown" in Version.from_section(sect(b"VER ", struct.pack("<H", 999))).name


def test_side_is_twelve_bytes_not_eight() -> None:
    """bw-chk clamps SIDE to 8 and truncates players 9-12. We must not."""
    payload = bytes(range(12))
    view = PlayerRaces.from_section(sect(b"SIDE", payload))
    assert len(view) == 12
    assert view[11] == 11
    assert view.to_bytes() == payload


def test_player_slots() -> None:
    payload = bytes([6] * 8 + [0] * 4)
    view = PlayerSlots.from_section(sect(b"OWNR", payload))
    assert view[0] == 6 and view[8] == 0
    assert view.to_bytes() == payload


@pytest.mark.parametrize(
    "cls,name,payload",
    [
        (Dimensions, b"DIM ", struct.pack("<HH", 64, 64)),
        (Version, b"VER ", struct.pack("<H", 59)),
        (TilesetRef, b"ERA ", struct.pack("<H", 4)),
        (PlayerSlots, b"OWNR", bytes(range(12))),
        (PlayerRaces, b"SIDE", bytes(range(12))),
        (ScenarioProperties, b"SPRP", struct.pack("<HH", 1, 2)),
    ],
    ids=lambda v: getattr(v, "__name__", None) or (v if isinstance(v, bytes) else ""),
)
def test_scalar_view_round_trips(cls, name, payload) -> None:
    assert cls.from_section(sect(name, payload)).to_bytes() == payload


def test_oversized_section_keeps_its_tail() -> None:
    """Trailing bytes past the modeled prefix must survive untouched."""
    payload = struct.pack("<HH", 64, 64) + b"\xde\xad\xbe\xef"
    view = Dimensions.from_section(sect(b"DIM ", payload))
    assert (view.tile_width, view.tile_height) == (64, 64)
    assert view.to_bytes() == payload


def test_short_section_is_not_padded_to_nominal() -> None:
    """A short section must round-trip short, not grow to its nominal size."""
    payload = b"\x40\x00"  # 2 bytes of a nominally 4-byte DIM
    view = Dimensions.from_section(sect(b"DIM ", payload))
    assert view.is_short
    assert view.tile_width == 64
    assert view.tile_height == 0  # zero-extended for parsing only
    assert view.to_bytes() == payload
    assert len(view.to_bytes()) == 2


def test_short_section_edit_applies_within_available_bytes() -> None:
    view = Dimensions.from_section(sect(b"DIM ", b"\x40\x00"))
    view.tile_width = 0x0102
    assert view.to_bytes() == b"\x02\x01"


def test_edit_then_write() -> None:
    view = Dimensions.from_section(sect(b"DIM ", struct.pack("<HH", 64, 64)))
    view.tile_height = 256
    assert view.to_bytes() == struct.pack("<HH", 64, 256)


# --------------------------------------------------------------------------
# Record arrays
# --------------------------------------------------------------------------


def test_record_array_round_trip_and_count() -> None:
    payload = bytes(Unit.SIZE * 3)
    view = RecordArrayView.from_section(sect(b"UNIT", payload), Unit)
    assert len(view) == 3
    assert not view.has_partial_record
    assert view.to_bytes() == payload


def test_record_array_preserves_partial_trailing_record() -> None:
    payload = bytes(Unit.SIZE * 2) + b"\x01\x02\x03"
    view = RecordArrayView.from_section(sect(b"UNIT", payload), Unit)
    assert len(view) == 2
    assert view.has_partial_record
    assert view.trailing == b"\x01\x02\x03"
    assert view.to_bytes() == payload


def test_mrgn_count_comes_from_size_not_version() -> None:
    """1280 and 5100 are conventions, not constraints."""
    for count in (64, 255, 7):
        view = RecordArrayView.from_section(sect(b"MRGN", bytes(20 * count)), Location)
        assert len(view) == count


def test_trigger_list_round_trip() -> None:
    payload = bytes(Trigger.SIZE * 2)
    view = TriggerListView.from_section(sect(b"TRIG", payload))
    assert len(view) == 2
    assert view.to_bytes() == payload


def test_briefing_flag_is_carried() -> None:
    assert TriggerListView.from_section(sect(b"MBRF", b""), is_briefing=True).is_briefing
    assert not TriggerListView.from_section(sect(b"TRIG", b"")).is_briefing


# --------------------------------------------------------------------------
# Strings - the scan-to-NUL rule
# --------------------------------------------------------------------------


def build_str(strings: dict[int, bytes], count: int = 4) -> bytes:
    """Build a STR payload placing each string at a chosen id."""
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    data = bytearray(b"\x00")  # shared NUL for empty slots
    base = len(header)
    for sid in range(1, count + 1):
        if sid in strings:
            struct.pack_into("<H", header, 2 * sid, base + len(data))
            data += strings[sid] + b"\x00"
        else:
            struct.pack_into("<H", header, 2 * sid, base)  # shared NUL
    return bytes(header) + bytes(data)


def test_string_ids_are_one_based() -> None:
    payload = build_str({1: b"first", 2: b"second"})
    view = StringTableView.from_section(sect(b"STR ", payload))
    assert view.get(0) is None          # id 0 means "no string"
    assert view.get(1) == b"first"
    assert view.get(2) == b"second"


def test_strings_terminate_at_nul_not_by_offset_differencing() -> None:
    """Ids laid out in descending address order still read correctly.

    Offset-differencing yields a negative length here; scanning to NUL does not.
    This is the shape that breaks 30 of the 65 corpus maps.
    """
    count = 2
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    base = len(header)
    # id 1 placed AFTER id 2 in the data area.
    data = b"\x00" + b"second\x00" + b"first\x00"
    off_second = base + 1
    off_first = base + 1 + len(b"second\x00")
    struct.pack_into("<H", header, 2, off_first)
    struct.pack_into("<H", header, 4, off_second)
    view = StringTableView.from_section(sect(b"STR ", bytes(header) + data))
    assert view.offsets[0] > view.offsets[1]        # descending: the hazard shape
    assert view.get(1) == b"first"
    assert view.get(2) == b"second"


def test_two_ids_may_share_one_offset() -> None:
    count = 2
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    base = len(header)
    struct.pack_into("<H", header, 2, base)
    struct.pack_into("<H", header, 4, base)
    view = StringTableView.from_section(sect(b"STR ", bytes(header) + b"shared\x00"))
    assert view.get(1) == view.get(2) == b"shared"


def test_offset_into_the_middle_of_another_string_is_legal() -> None:
    """Sub-string recycling: a documented compression technique, not an error."""
    count = 2
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    base = len(header)
    struct.pack_into("<H", header, 2, base)
    struct.pack_into("<H", header, 4, base + 5)
    view = StringTableView.from_section(sect(b"STR ", bytes(header) + b"HelloWorld\x00"))
    assert view.get(1) == b"HelloWorld"
    assert view.get(2) == b"World"


def test_declared_count_larger_than_section_is_clamped_not_an_error() -> None:
    payload = struct.pack("<H", 9999) + struct.pack("<HH", 6, 6) + b"hi\x00"
    view = StringTableView.from_section(sect(b"STR ", payload))
    assert view.declared_count == 9999
    assert view.count == len(payload) // 2 - 1
    assert view.get(1) == b"hi"


def test_out_of_bounds_offset_keeps_the_slot_but_returns_none() -> None:
    """Ids are positional: a bad entry must not shift the ids after it."""
    count = 3
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    base = len(header)
    data = b"\x00" + b"one\x00" + b"three\x00"
    struct.pack_into("<H", header, 2, base + 1)
    struct.pack_into("<H", header, 4, 0xFFFF)          # out of bounds
    struct.pack_into("<H", header, 6, base + 1 + 4)
    view = StringTableView.from_section(sect(b"STR ", bytes(header) + data))
    assert view.get(1) == b"one"
    assert view.get(2) is None
    assert view.get(3) == b"three"    # id 3 did NOT shift down


def test_unterminated_string_runs_to_end_of_section() -> None:
    count = 1
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    struct.pack_into("<H", header, 2, len(header))
    view = StringTableView.from_section(sect(b"STR ", bytes(header) + b"no nul here"))
    assert view.get(1) == b"no nul here"


def test_string_view_round_trips_and_is_read_only() -> None:
    payload = build_str({1: b"abc"})
    view = StringTableView.from_section(sect(b"STR ", payload))
    assert view.to_bytes() == payload


def test_text_decoding_is_explicit() -> None:
    view = StringTableView.from_section(sect(b"STR ", build_str({1: b"caf\xe9"})))
    assert view.get(1) == b"caf\xe9"          # opaque bytes by default
    assert view.text(1, "cp1252") == "café"


def test_used_ids_skips_empty_slots() -> None:
    view = StringTableView.from_section(sect(b"STR ", build_str({2: b"x"}, count=4)))
    assert view.used_ids() == [2]


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def test_view_for_dispatches_by_name() -> None:
    chk = chk_of(
        (b"DIM ", struct.pack("<HH", 64, 64)),
        (b"UNIT", bytes(Unit.SIZE)),
        (b"MBRF", bytes(Trigger.SIZE)),
    )
    assert isinstance(view_for(chk, "DIM"), Dimensions)
    assert isinstance(view_for(chk, "UNIT"), RecordArrayView)
    briefing = view_for(chk, "MBRF")
    assert isinstance(briefing, TriggerListView) and briefing.is_briefing
    assert view_for(chk, "TRIG") is None      # absent
    assert view_for(chk, "VCOD") is None      # present-but-uninterpreted would be None too


def test_view_for_uses_the_last_duplicate() -> None:
    """StarCraft's override order: the effective section is the last one."""
    chk = chk_of(
        (b"DIM ", struct.pack("<HH", 64, 64)),
        (b"DIM ", struct.pack("<HH", 96, 96)),
    )
    assert str(view_for(chk, "DIM")) == "96x96"
