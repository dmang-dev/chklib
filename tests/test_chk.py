"""Container tests.

The milestone-1 gate is byte-exact round-trip: for any input at all, well-formed
or not, ``Chk.from_bytes(raw).to_bytes() == raw``. Everything else here exists to
pin down the three properties the container is for -- order, duplicates and raw
bytes -- and to prove malformed input is described rather than rejected.
"""

from __future__ import annotations

import dataclasses
import random
import struct

import pytest

from chklib import Chk, Section


def sec(name: bytes, payload: bytes, size: int | None = None) -> bytes:
    """Build one raw section. ``size`` overrides the declared length."""
    return name + struct.pack("<i", len(payload) if size is None else size) + payload


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="empty"),
        pytest.param(sec(b"VER ", b"\x3b\x00"), id="single"),
        pytest.param(sec(b"VER ", b"\x3b\x00") + sec(b"DIM ", b"@\x00@\x00"), id="two"),
        pytest.param(sec(b"VER ", b"a") * 3, id="duplicates"),
        pytest.param(sec(b"MTXM", b""), id="zero-length"),
        pytest.param(b"VER \x02", id="header-fragment"),
        pytest.param(sec(b"VER ", b"ab") + b"xyz", id="trailing-fragment"),
        pytest.param(sec(b"VER ", b"ab", size=999), id="truncated"),
        pytest.param(sec(b"VER ", b"ab") + sec(b"JMP\x00", b"", size=-40), id="negative"),
        pytest.param(sec(b"\x00\x01\x02\x03", b"z"), id="non-printable-name"),
        pytest.param(sec(b"VER ", bytes(range(256))), id="binary-payload"),
    ],
)
def test_round_trip_is_byte_exact(raw: bytes) -> None:
    assert Chk.from_bytes(raw).to_bytes() == raw


def test_round_trip_on_random_bytes() -> None:
    """Fuzz: arbitrary bytes must still round-trip, never raise."""
    rng = random.Random(0xC4C0DE)
    for _ in range(500):
        raw = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        assert Chk.from_bytes(raw).to_bytes() == raw


def test_round_trip_on_random_well_formed_sections() -> None:
    rng = random.Random(1998)
    for _ in range(200):
        parts = []
        for _ in range(rng.randrange(0, 12)):
            name = bytes(rng.randrange(0x41, 0x5B) for _ in range(4))
            payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 64)))
            parts.append(sec(name, payload))
        raw = b"".join(parts)
        chk = Chk.from_bytes(raw)
        assert chk.to_bytes() == raw
        assert not chk.has_errors


# --------------------------------------------------------------------------
# Order, duplicates, raw bytes
# --------------------------------------------------------------------------


def test_order_is_preserved() -> None:
    raw = sec(b"AAAA", b"1") + sec(b"BBBB", b"2") + sec(b"AAAA", b"3")
    chk = Chk.from_bytes(raw)
    assert [s.label for s in chk] == ["AAAA", "BBBB", "AAAA"]


def test_duplicates_are_all_kept() -> None:
    raw = sec(b"VER ", b"1") + sec(b"VER ", b"2") + sec(b"VER ", b"3")
    chk = Chk.from_bytes(raw)
    assert len(chk) == 3
    assert [s.data for s in chk.find("VER")] == [b"1", b"2", b"3"]
    assert chk.duplicated_names == [b"VER "]


def test_last_wins_matches_starcraft_override_order() -> None:
    """StarCraft applies sections in order, so the last one is the effective one."""
    raw = sec(b"VER ", b"old") + sec(b"VER ", b"new")
    chk = Chk.from_bytes(raw)
    assert chk.last("VER").data == b"new"
    assert chk.last("VER ").data == b"new"
    assert chk.last(b"VER ").data == b"new"


def test_absent_section() -> None:
    chk = Chk.from_bytes(sec(b"VER ", b"1"))
    assert chk.last("TRIG") is None
    assert "TRIG" not in chk
    assert "VER" in chk
    assert chk.find("TRIG") == []


def test_unknown_sections_survive() -> None:
    raw = sec(b"WTF?", b"\xde\xad\xbe\xef")
    chk = Chk.from_bytes(raw)
    assert chk.last("WTF?").data == b"\xde\xad\xbe\xef"
    assert chk.to_bytes() == raw


def test_name_longer_than_four_rejected() -> None:
    chk = Chk.from_bytes(sec(b"VER ", b"1"))
    with pytest.raises(ValueError):
        chk.find("TOOLONG")


# --------------------------------------------------------------------------
# Malformed input is described, not raised
# --------------------------------------------------------------------------


def test_truncated_section_is_reported_and_preserved() -> None:
    raw = sec(b"MTXM", b"ab", size=999)
    chk = Chk.from_bytes(raw)
    assert chk.to_bytes() == raw
    assert chk.has_errors
    assert [d.code for d in chk.diagnostics] == ["truncated-section"]
    section = chk.last("MTXM")
    assert section.is_truncated
    assert section.declared_size == 999
    assert section.data == b"ab"


def test_negative_length_stops_parsing_and_preserves_remainder() -> None:
    tail = sec(b"JMP\x00", b"", size=-40) + b"whatever follows"
    raw = sec(b"VER ", b"ok") + tail
    chk = Chk.from_bytes(raw)
    assert chk.to_bytes() == raw
    assert len(chk) == 1
    assert chk.last("VER").data == b"ok"
    assert chk.trailing == tail
    assert [d.code for d in chk.diagnostics] == ["negative-section-length"]


def test_trailing_fragment_is_reported() -> None:
    raw = sec(b"VER ", b"ab") + b"xyz"
    chk = Chk.from_bytes(raw)
    assert chk.trailing == b"xyz"
    assert "trailing-bytes" in {d.code for d in chk.diagnostics}
    assert chk.to_bytes() == raw


def test_non_printable_name_is_a_warning_not_an_error() -> None:
    chk = Chk.from_bytes(sec(b"\x00\x01\x02\x03", b"z"))
    codes = {d.code for d in chk.diagnostics}
    assert "non-printable-section-name" in codes
    assert not chk.has_errors
    assert chk.sections[0].label == "...."


def test_empty_input() -> None:
    chk = Chk.from_bytes(b"")
    assert len(chk) == 0
    assert chk.diagnostics == []
    assert chk.to_bytes() == b""


# --------------------------------------------------------------------------
# Section value semantics
# --------------------------------------------------------------------------


def test_section_key_strips_padding() -> None:
    chk = Chk.from_bytes(sec(b"VER ", b"1"))
    assert chk.sections[0].key == b"VER"
    assert chk.sections[0].name == b"VER "


def test_section_offset_points_at_its_header() -> None:
    raw = sec(b"AAAA", b"12") + sec(b"BBBB", b"345")
    chk = Chk.from_bytes(raw)
    assert chk.sections[0].offset == 0
    assert chk.sections[1].offset == 10
    assert raw[chk.sections[1].offset : chk.sections[1].offset + 4] == b"BBBB"


def test_sections_are_immutable() -> None:
    chk = Chk.from_bytes(sec(b"VER ", b"1"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        chk.sections[0].data = b"nope"


def test_section_len_is_payload_len() -> None:
    chk = Chk.from_bytes(sec(b"VER ", b"abcd"))
    assert len(chk.sections[0]) == 4


def test_manual_construction_round_trips() -> None:
    chk = Chk(sections=[Section(b"VER ", 2, b"\x3b\x00", 0)])
    assert Chk.from_bytes(chk.to_bytes()).to_bytes() == chk.to_bytes()
