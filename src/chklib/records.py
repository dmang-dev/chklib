"""Fixed-size binary records inside CHK sections.

Every record here models **every byte** of its layout, including fields the
sources name only as ``unused`` or ``padding``. That is deliberate and is what
makes ``Record.from_bytes(raw).to_bytes() == raw`` hold unconditionally.

Those fields are not slack. ``Unit.unused``, ``Sprite.unused``,
``Action.padding`` and ``Trigger.current_action`` are all real parts of the
record, are editable in Chkdraft, and appear in real maps. Zeroing any of them on
write is a silent data change (SPEC 8.1).

The same reasoning applies to flag words. Every flag field is stored as a raw
integer, never as a set of booleans, because each one has undocumented bits that
must survive a round-trip (SPEC 8.2). The :mod:`chklib.enums` flag classes
are an interpretation layer over the raw value, not a replacement for it.

Field offsets are asserted at import time against the spec, so a mistranscribed
layout fails immediately rather than corrupting maps.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, fields
from typing import ClassVar, TypeVar

#: Binds ``from_bytes``/``unpack_all`` to the subclass they were called on, so
#: ``Condition.from_bytes(...)`` is a ``Condition`` rather than a bare
#: ``Record``. ``typing.Self`` would say this more directly but is 3.11+, and
#: this package supports 3.10.
_RecordT = TypeVar("_RecordT", bound="Record")

__all__ = [
    "Record", "Unit", "Sprite", "Location", "Condition", "Action", "Trigger",
    "Cuwp", "Doodad",
    "MAX_CONDITIONS", "MAX_ACTIONS", "MAX_OWNERS", "MAX_CUWPS",
    "DOODAD_ENABLED", "DOODAD_DISABLED",
]

MAX_CONDITIONS = 16
MAX_ACTIONS = 64
MAX_OWNERS = 27

#: ``Sc::Unit::MaxCuwps`` (Chkdraft ``sc.h:408``). ``UPRP`` holds exactly this
#: many slots and ``UPUS`` exactly this many used-flags.
MAX_CUWPS = 64

#: ``Chk::Doodad::Enabled`` (Chkdraft ``chk.h:319``). Inverted, like
#: ``UseDefault``: the byte is a *disabled* flag wearing an enabled name, so a
#: doodad written with 1 is switched off. A synthesised doodad wants 0.
DOODAD_ENABLED = 0
DOODAD_DISABLED = 1


class Record:
    """Base for a fixed-size record that round-trips byte-exactly.

    Subclasses are dataclasses whose field order matches ``_STRUCT`` exactly.
    """

    SIZE: ClassVar[int]
    _STRUCT: ClassVar[struct.Struct]

    @classmethod
    def from_bytes(cls: type[_RecordT], raw: bytes) -> _RecordT:
        if len(raw) != cls.SIZE:
            raise ValueError(
                f"{cls.__name__} is {cls.SIZE} bytes, got {len(raw)}"
            )
        return cls(*cls._STRUCT.unpack(raw))

    def to_bytes(self) -> bytes:
        return self._STRUCT.pack(*(getattr(self, f.name) for f in fields(self)))  # type: ignore[arg-type]

    @classmethod
    def unpack_all(cls: type[_RecordT], raw: bytes) -> tuple[list[_RecordT], bytes]:
        """Split ``raw`` into whole records plus any trailing partial bytes.

        A trailing partial record is preserved rather than dropped or padded out
        to a full record; Chkdraft synthesises a whole record on save that was
        never in the input, which is a round-trip hazard (SPEC 7.7, 8.3).
        """
        count, remainder = divmod(len(raw), cls.SIZE)
        records = [
            cls.from_bytes(raw[i * cls.SIZE : (i + 1) * cls.SIZE])
            for i in range(count)
        ]
        return records, raw[len(raw) - remainder :] if remainder else b""


# ---------------------------------------------------------------------------
# Object records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Unit(Record):
    """A placed unit in ``UNIT`` -- 36 bytes (SPEC 4.1). Confidence A, four-way.

    Coordinates are in **pixels**, not tiles.
    """

    SIZE: ClassVar[int] = 36
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<IHHHHHHBBBBIHHII")

    class_id: int
    xc: int
    yc: int
    type: int
    relation_flags: int
    valid_state_flags: int
    valid_field_flags: int
    owner: int
    hitpoint_percent: int
    shield_percent: int
    energy_percent: int
    resource_amount: int
    hangar_amount: int
    state_flags: int
    unused: int
    relation_class_id: int


@dataclass(slots=True)
class Sprite(Record):
    """A ``THG2`` entry -- 10 bytes (SPEC 4.2). Confidence A.

    ``flags`` bit 12 (``DrawAsSprite``) is the discriminator: SET means a pure
    sprite, CLEAR means a sprite-unit, regardless of the ``IsUnit`` bit.
    """

    SIZE: ClassVar[int] = 10
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<HHHBBH")

    type: int
    xc: int
    yc: int
    owner: int
    unused: int
    flags: int

    @property
    def is_sprite_unit(self) -> bool:
        """True when this record is a unit drawn as a sprite (bit 12 clear)."""
        return not (self.flags & 0x1000)


@dataclass(slots=True)
class IsomRect(Record):
    """One ``ISOM`` cell -- 8 bytes, four ``u16`` sides (SPEC 3.3).

    The record framing is well attested: 8 bytes is backed by the only
    compile-time assertion in Chkdraft's header,
    ``static_assert(sizeof(IsomRect) == 8)``.

    The **bit layout inside each side is Confidence C** -- Chkdraft is the only
    witness -- so each side is kept as a raw ``u16`` and the accessors below are
    an interpretation over it, never a replacement:

    ===== ======== ==================================================
    bits  mask     content
    ===== ======== ==================================================
    15    0x8000   ``Visited``, a Chkdraft traversal marker
    14-4  0x7FF0   the ISOM value, stored shifted left by 4
    3-1   0x000E   edge flags
    0     0x0001   ``Modified``, a Chkdraft dirty bit
    ===== ======== ==================================================

    **Both editor flags are written to the file.** ``IsomRect`` is serialized
    wholesale with no masking; Chkdraft merely clears them after each edit pass,
    and nothing in the format guarantees they are clear. Mask on read.

    ISOM is editor-only data -- StarCraft itself reads MTXM -- so a map can carry
    a stale or absent ISOM without any in-game consequence.
    """

    SIZE: ClassVar[int] = 8
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<HHHH")

    #: Bits 14..4 hold the value, stored shifted left by 4.
    VALUE_MASK: ClassVar[int] = 0x7FF0
    VALUE_SHIFT: ClassVar[int] = 4
    #: Bits 3..1.
    EDGE_MASK: ClassVar[int] = 0x000E
    #: Chkdraft-internal, but present in files.
    VISITED: ClassVar[int] = 0x8000
    MODIFIED: ClassVar[int] = 0x0001
    #: Masks off both editor flags, leaving value and edge bits.
    CLEAR_EDITOR_FLAGS: ClassVar[int] = 0x7FFE

    left: int
    top: int
    right: int
    bottom: int

    @property
    def sides(self) -> tuple[int, int, int, int]:
        """The four raw side words, in file order."""
        return (self.left, self.top, self.right, self.bottom)

    @classmethod
    def value_of(cls, side: int) -> int:
        return (side & cls.VALUE_MASK) >> cls.VALUE_SHIFT

    @classmethod
    def edge_flags_of(cls, side: int) -> int:
        return side & cls.EDGE_MASK

    def values(self) -> tuple[int, int, int, int]:
        """The ISOM value of each side, with the editor flags masked off."""
        return tuple(self.value_of(s) for s in self.sides)  # type: ignore[return-value]

    def edge_flags(self) -> tuple[int, int, int, int]:
        return tuple(self.edge_flags_of(s) for s in self.sides)  # type: ignore[return-value]

    @property
    def has_editor_flags(self) -> bool:
        """True when any side carries a ``Visited`` or ``Modified`` bit.

        A Chkdraft-saved map normally has none, so this marks a file written by
        something else -- or mid-edit.
        """
        return any(s & ~self.CLEAR_EDITOR_FLAGS for s in self.sides)

    @property
    def is_empty(self) -> bool:
        return not any(self.sides)


@dataclass(slots=True)
class Location(Record):
    """An ``MRGN`` location -- 20 bytes (SPEC 4.4). Confidence A, five-way.

    Bounds are in **pixels**. Location ids are 1-based: file record ``k``
    corresponds to trigger location id ``k + 1``, so ``Anywhere`` (id 64) is
    record index 63. That is empirically proven across all 65 corpus maps.

    ``elevation_flags`` polarity is settled (a set bit means *excluded*) but the
    nibble assignment is NOT -- Chkdraft and openbw contradict each other on
    which nibble is ground and which is air (SPEC 7.2). No accessor is offered
    here on purpose; read the raw value and decide explicitly.
    """

    SIZE: ClassVar[int] = 20
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<IIIIHH")

    left: int
    top: int
    right: int
    bottom: int
    string_id: int
    elevation_flags: int

    @property
    def is_unused_slot(self) -> bool:
        """True for an all-zero slot. 4,711 of 5,306 corpus records are these."""
        return not (
            self.left or self.top or self.right or self.bottom
            or self.string_id or self.elevation_flags
        )


# ---------------------------------------------------------------------------
# Trigger records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Condition(Record):
    """One trigger condition -- 20 bytes (SPEC 6.6). Confidence A.

    ``mask_flag == 0x4353`` marks an EUD condition, in which case ``player`` is
    a memory address rather than a player id. The corpus contains zero of these.
    """

    SIZE: ClassVar[int] = 20
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<IIIHBBBBH")

    location_id: int
    player: int
    amount: int
    unit_type: int
    comparison: int
    condition_type: int
    type_index: int
    flags: int
    mask_flag: int

    @property
    def is_used(self) -> bool:
        """A slot is in use when ``condition_type`` is non-zero (SPEC 6.18)."""
        return self.condition_type != 0

    @property
    def is_disabled(self) -> bool:
        return bool(self.flags & 0x02)


@dataclass(slots=True)
class Action(Record):
    """One trigger action -- 32 bytes (SPEC 6.7). Confidence A.

    ``action_type`` is interpreted against different enums depending on whether
    the owning trigger came from ``TRIG`` or ``MBRF``; the two id spaces collide
    numerically at 0-9 and mean different things (SPEC 6.12).
    """

    SIZE: ClassVar[int] = 32
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<IIIIIIHBBBBH")

    location_id: int
    string_id: int
    sound_string_id: int
    time: int
    group: int
    number: int
    type: int
    action_type: int
    type2: int
    flags: int
    padding: int
    mask_flag: int

    @property
    def is_used(self) -> bool:
        """A slot is in use when ``action_type`` is non-zero (SPEC 6.18)."""
        return self.action_type != 0

    @property
    def is_disabled(self) -> bool:
        return bool(self.flags & 0x02)


@dataclass(slots=True)
class Trigger:
    """One trigger -- 2400 bytes (SPEC 6.2). Confidence A, three-way.

    Layout is 16 conditions, then 64 actions, then a ``u32`` flag word, then 27
    owner bytes, then a single ``current_action`` byte.

    The tail partition is **27 + 1, not 28**. openbw and eudplib both model the
    tail as a 28-byte player array; copying that shape and writing
    ``owners[27] = 1`` stamps a non-zero execution cursor into a fresh trigger,
    which the game reads as "already executed one action".
    """

    SIZE: ClassVar[int] = 2400
    _TAIL: ClassVar[struct.Struct] = struct.Struct("<I")
    _CONDITIONS_AT: ClassVar[int] = 0
    _ACTIONS_AT: ClassVar[int] = 320
    _FLAGS_AT: ClassVar[int] = 0x0940
    _OWNERS_AT: ClassVar[int] = 0x0944
    _CURRENT_ACTION_AT: ClassVar[int] = 0x095F

    conditions: list[Condition]
    actions: list[Action]
    flags: int
    owners: bytes
    current_action: int

    @classmethod
    def from_bytes(cls, raw: bytes) -> Trigger:
        if len(raw) != cls.SIZE:
            raise ValueError(f"Trigger is {cls.SIZE} bytes, got {len(raw)}")
        conditions = [
            Condition.from_bytes(
                raw[cls._CONDITIONS_AT + i * 20 : cls._CONDITIONS_AT + (i + 1) * 20]
            )
            for i in range(MAX_CONDITIONS)
        ]
        actions = [
            Action.from_bytes(
                raw[cls._ACTIONS_AT + i * 32 : cls._ACTIONS_AT + (i + 1) * 32]
            )
            for i in range(MAX_ACTIONS)
        ]
        (flags,) = cls._TAIL.unpack_from(raw, cls._FLAGS_AT)
        owners = raw[cls._OWNERS_AT : cls._OWNERS_AT + MAX_OWNERS]
        current_action = raw[cls._CURRENT_ACTION_AT]
        return cls(conditions, actions, flags, owners, current_action)

    def to_bytes(self) -> bytes:
        if len(self.conditions) != MAX_CONDITIONS:
            raise ValueError(f"expected {MAX_CONDITIONS} conditions, got {len(self.conditions)}")
        if len(self.actions) != MAX_ACTIONS:
            raise ValueError(f"expected {MAX_ACTIONS} actions, got {len(self.actions)}")
        if len(self.owners) != MAX_OWNERS:
            raise ValueError(f"owners must be {MAX_OWNERS} bytes, got {len(self.owners)}")
        return b"".join(
            [
                *(c.to_bytes() for c in self.conditions),
                *(a.to_bytes() for a in self.actions),
                self._TAIL.pack(self.flags),
                bytes(self.owners),
                bytes((self.current_action,)),
            ]
        )

    @classmethod
    def unpack_all(cls, raw: bytes) -> tuple[list[Trigger], bytes]:
        """Split into whole triggers plus trailing partial bytes.

        Chkdraft's own comment hedges on whether a trailing partial trigger can
        occur, and says nothing about how to handle one, so it is preserved.
        """
        count, remainder = divmod(len(raw), cls.SIZE)
        triggers = [
            cls.from_bytes(raw[i * cls.SIZE : (i + 1) * cls.SIZE])
            for i in range(count)
        ]
        return triggers, raw[len(raw) - remainder :] if remainder else b""

    # -- convenience -------------------------------------------------------

    def used_conditions(self) -> Iterator[Condition]:
        """Conditions up to the first empty slot (SPEC 6.18 scanning rule)."""
        for condition in self.conditions:
            if not condition.is_used:
                return
            yield condition

    def used_actions(self) -> Iterator[Action]:
        """Actions up to the first empty slot (SPEC 6.18 scanning rule)."""
        for action in self.actions:
            if not action.is_used:
                return
            yield action

    def owner_indices(self) -> list[int]:
        """Indices of ``owners`` with a non-zero byte."""
        return [i for i, b in enumerate(self.owners) if b]


@dataclass(slots=True)
class Cuwp(Record):
    """One "create unit with properties" slot in ``UPRP`` -- 20 bytes.

    A ``CreateUnitWithProperties`` trigger action does not carry the properties
    it applies; it carries an *index* into this table. Without ``UPRP`` such an
    action can be named but not explained, which is why these 64 slots matter
    out of proportion to their size.

    The two ``valid_*`` words are masks saying which of the remaining fields the
    game should actually apply -- a slot with ``hitpoint_percent = 100`` and the
    hitpoints bit clear leaves hitpoints alone rather than setting them to 100.

    ``unknown`` is named as Chkdraft names it, and is modelled rather than
    skipped for the usual reason: it is four real bytes that must survive a
    round-trip. Confidence A -- openbw reads the same ten fields in the same
    order (``bwgame.h:21665-21687``), independently of Chkdraft.
    """

    SIZE: ClassVar[int] = 20
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<HHBBBBIHHI")

    valid_state_flags: int
    valid_field_flags: int
    owner: int
    hitpoint_percent: int
    shield_percent: int
    energy_percent: int
    resource_amount: int
    hangar_amount: int
    state_flags: int
    unknown: int

    @property
    def is_used(self) -> bool:
        """Whether this slot carries anything at all.

        A slot's real authority is the matching ``UPUS`` byte; this is the
        weaker structural test, for when ``UPUS`` is absent.
        """
        return self.to_bytes() != bytes(self.SIZE)


@dataclass(slots=True)
class Doodad(Record):
    """A ``DD2`` entry -- 8 bytes.

    Editor-only: no engine reads ``DD2``, and openbw registers no handler for
    it. It is modelled so a map that carries doodads survives an edit with them
    intact, not because the game consults it.

    ``xc``/``yc`` are **pixel** coordinates of the doodad's centre, matching
    ``UNIT`` and ``THG2`` rather than the tile coordinates a doodad's placement
    grid might suggest.

    ``enabled`` is inverted -- see :data:`DOODAD_ENABLED`. Use
    :attr:`is_enabled` rather than the raw byte.
    """

    SIZE: ClassVar[int] = 8
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<HHHBB")

    type: int
    xc: int
    yc: int
    owner: int
    enabled: int

    @property
    def is_enabled(self) -> bool:
        """Whether the doodad is switched on.

        The stored byte is 0 for enabled and 1 for disabled, so ``bool(enabled)``
        is exactly backwards and this is not a convenience wrapper.
        """
        return self.enabled == DOODAD_ENABLED


# ---------------------------------------------------------------------------
# Layout assertions - a mistranscribed offset must fail at import, not at write
# ---------------------------------------------------------------------------

def _assert_layout() -> None:
    for cls in (Unit, Sprite, Location, Condition, Action, Cuwp, Doodad):
        assert cls._STRUCT.size == cls.SIZE, (
            f"{cls.__name__}: struct is {cls._STRUCT.size} bytes, SIZE says {cls.SIZE}"
        )
        assert len(fields(cls)) == len(cls._STRUCT.format.lstrip("<")), (
            f"{cls.__name__}: {len(fields(cls))} dataclass fields vs "
            f"{len(cls._STRUCT.format.lstrip('<'))} struct codes"
        )
    # SPEC 6.21: the record partition must account for every byte.
    assert MAX_CONDITIONS * Condition.SIZE == 320
    assert MAX_ACTIONS * Action.SIZE == 2048
    assert (
        MAX_CONDITIONS * Condition.SIZE
        + MAX_ACTIONS * Action.SIZE
        + 4  # flags
        + MAX_OWNERS
        + 1  # current_action
    ) == Trigger.SIZE
    assert Trigger._ACTIONS_AT == MAX_CONDITIONS * Condition.SIZE
    assert Trigger._FLAGS_AT == Trigger._ACTIONS_AT + MAX_ACTIONS * Action.SIZE
    assert Trigger._OWNERS_AT == Trigger._FLAGS_AT + 4
    assert Trigger._CURRENT_ACTION_AT == Trigger._OWNERS_AT + MAX_OWNERS
    # UPRP is exactly MAX_CUWPS slots and UPUS exactly MAX_CUWPS bytes.
    assert MAX_CUWPS * Cuwp.SIZE == 1280
    assert DOODAD_ENABLED != DOODAD_DISABLED


_assert_layout()
