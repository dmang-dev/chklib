"""Record-level tests.

The gate here is the same one the container has, one level down: for any bytes of
the right length, ``Record.from_bytes(raw).to_bytes() == raw``. That holds because
every byte of every record is modeled as a field, including the ones the format
sources only call ``unused`` or ``padding``.
"""

from __future__ import annotations

import random
import struct

import pytest

from chklib.records import (
    MAX_ACTIONS,
    MAX_CONDITIONS,
    MAX_OWNERS,
    Action,
    Condition,
    Location,
    Sprite,
    Trigger,
    Unit,
)

FLAT_RECORDS = [Unit, Sprite, Location, Condition, Action]


# --------------------------------------------------------------------------
# Layout, asserted against the spec
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,size",
    [(Unit, 36), (Sprite, 10), (Location, 20), (Condition, 20), (Action, 32)],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_record_size(cls, size) -> None:
    assert cls.SIZE == size
    assert cls._STRUCT.size == size


def test_trigger_partition_accounts_for_every_byte() -> None:
    assert MAX_CONDITIONS * Condition.SIZE == 320
    assert MAX_ACTIONS * Action.SIZE == 2048
    # The tail is 27 owners + 1 currentAction, NOT a 28-byte owner array.
    assert 320 + 2048 + 4 + MAX_OWNERS + 1 == Trigger.SIZE == 2400


def test_trigger_field_offsets() -> None:
    assert Trigger._CONDITIONS_AT == 0
    assert Trigger._ACTIONS_AT == 0x0140
    assert Trigger._FLAGS_AT == 0x0940
    assert Trigger._OWNERS_AT == 0x0944
    assert Trigger._CURRENT_ACTION_AT == 0x095F


def test_struct_formats_are_little_endian_and_unpadded() -> None:
    """CHK has no padding anywhere; only '<' guarantees that in struct."""
    for cls in FLAT_RECORDS:
        assert cls._STRUCT.format.startswith("<"), cls.__name__
        # Sum of individual field widths must equal the whole, i.e. no padding.
        widths = sum(struct.calcsize("<" + c) for c in cls._STRUCT.format[1:])
        assert widths == cls.SIZE, cls.__name__


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", FLAT_RECORDS, ids=lambda c: c.__name__)
def test_record_round_trip_on_random_bytes(cls) -> None:
    """Every byte is a field, so any input of the right length survives."""
    rng = random.Random(0x5C)
    for _ in range(300):
        raw = bytes(rng.randrange(256) for _ in range(cls.SIZE))
        assert cls.from_bytes(raw).to_bytes() == raw


def test_trigger_round_trip_on_random_bytes() -> None:
    rng = random.Random(0x960)
    for _ in range(30):
        raw = bytes(rng.randrange(256) for _ in range(Trigger.SIZE))
        assert Trigger.from_bytes(raw).to_bytes() == raw


@pytest.mark.parametrize("cls", FLAT_RECORDS, ids=lambda c: c.__name__)
def test_wrong_length_rejected(cls) -> None:
    with pytest.raises(ValueError):
        cls.from_bytes(b"\x00" * (cls.SIZE - 1))
    with pytest.raises(ValueError):
        cls.from_bytes(b"\x00" * (cls.SIZE + 1))


@pytest.mark.parametrize("cls", FLAT_RECORDS, ids=lambda c: c.__name__)
def test_unpack_all_preserves_trailing_partial(cls) -> None:
    """A trailing partial record is kept, never dropped or padded to full."""
    rng = random.Random(7)
    body = bytes(rng.randrange(256) for _ in range(cls.SIZE * 3))
    tail = bytes(rng.randrange(256) for _ in range(cls.SIZE - 1))
    records, trailing = cls.unpack_all(body + tail)
    assert len(records) == 3
    assert trailing == tail
    assert b"".join(r.to_bytes() for r in records) + trailing == body + tail


def test_unpack_all_exact_multiple_has_no_trailing() -> None:
    records, trailing = Unit.unpack_all(b"\x00" * (Unit.SIZE * 2))
    assert len(records) == 2 and trailing == b""


# --------------------------------------------------------------------------
# Fields the sources call "unused" are still real data
# --------------------------------------------------------------------------


def test_unit_unused_field_is_preserved() -> None:
    raw = bytearray(Unit.SIZE)
    struct.pack_into("<I", raw, 28, 0xDEADBEEF)
    unit = Unit.from_bytes(bytes(raw))
    assert unit.unused == 0xDEADBEEF
    assert unit.to_bytes() == bytes(raw)


def test_sprite_unused_byte_is_preserved() -> None:
    raw = bytearray(Sprite.SIZE)
    raw[7] = 0xAB
    sprite = Sprite.from_bytes(bytes(raw))
    assert sprite.unused == 0xAB
    assert sprite.to_bytes() == bytes(raw)


def test_action_padding_is_preserved() -> None:
    raw = bytearray(Action.SIZE)
    raw[29] = 0x5A
    action = Action.from_bytes(bytes(raw))
    assert action.padding == 0x5A
    assert action.to_bytes() == bytes(raw)


def test_trigger_current_action_is_preserved_not_forced_to_zero() -> None:
    raw = bytearray(Trigger.SIZE)
    raw[0x95F] = 3
    trigger = Trigger.from_bytes(bytes(raw))
    assert trigger.current_action == 3
    assert trigger.to_bytes() == bytes(raw)


def test_owners_is_27_bytes_and_excludes_current_action() -> None:
    raw = bytearray(Trigger.SIZE)
    raw[0x944 + 26] = 1     # last real owner slot
    raw[0x95F] = 9          # currentAction, must NOT be read as owners[27]
    trigger = Trigger.from_bytes(bytes(raw))
    assert len(trigger.owners) == 27
    assert trigger.owners[26] == 1
    assert trigger.current_action == 9
    assert trigger.owner_indices() == [26]


def test_undocumented_trigger_flag_bits_survive() -> None:
    """Bits 7-31 are unnamed by every source and must not be masked off."""
    raw = bytearray(Trigger.SIZE)
    struct.pack_into("<I", raw, 0x940, 0xFFFFFFFF)
    trigger = Trigger.from_bytes(bytes(raw))
    assert trigger.flags == 0xFFFFFFFF
    assert trigger.to_bytes() == bytes(raw)


# --------------------------------------------------------------------------
# Slot scanning and accessors
# --------------------------------------------------------------------------


def _trigger_with(condition_types, action_types) -> Trigger:
    raw = bytearray(Trigger.SIZE)
    for i, t in enumerate(condition_types):
        raw[i * 20 + 15] = t          # Condition.conditionType @15
    for i, t in enumerate(action_types):
        raw[320 + i * 32 + 26] = t    # Action.actionType @26
    return Trigger.from_bytes(bytes(raw))


def test_used_slots_stop_at_first_empty() -> None:
    trigger = _trigger_with([22, 1, 0, 5], [1, 3, 0, 4])
    assert [c.condition_type for c in trigger.used_conditions()] == [22, 1]
    assert [a.action_type for a in trigger.used_actions()] == [1, 3]


def test_all_slots_remain_addressable_past_the_sentinel() -> None:
    """Scanning stops at the sentinel, but the slots themselves are still there."""
    trigger = _trigger_with([22, 0, 5], [])
    assert len(trigger.conditions) == MAX_CONDITIONS
    assert trigger.conditions[2].condition_type == 5


def test_disabled_flag() -> None:
    raw = bytearray(Condition.SIZE)
    raw[17] = 0x02  # Condition.flags @17
    assert Condition.from_bytes(bytes(raw)).is_disabled


def test_sprite_unit_discriminator_polarity() -> None:
    """Bit 12 SET means a pure sprite; CLEAR means a sprite-unit."""
    pure = Sprite(type=1, xc=0, yc=0, owner=0, unused=0, flags=0x1000)
    unit = Sprite(type=1, xc=0, yc=0, owner=0, unused=0, flags=0x0000)
    assert not pure.is_sprite_unit
    assert unit.is_sprite_unit
    # IsUnit (bit 13) must not change the answer.
    assert Sprite(1, 0, 0, 0, 0, 0x3000).is_sprite_unit is False
    assert Sprite(1, 0, 0, 0, 0, 0x2000).is_sprite_unit is True


def test_location_unused_slot_detection() -> None:
    assert Location(0, 0, 0, 0, 0, 0).is_unused_slot
    assert not Location(0, 0, 1, 1, 0, 0).is_unused_slot


def test_trigger_rejects_wrong_slot_counts() -> None:
    trigger = Trigger.from_bytes(bytes(Trigger.SIZE))
    trigger.conditions = trigger.conditions[:5]
    with pytest.raises(ValueError):
        trigger.to_bytes()
