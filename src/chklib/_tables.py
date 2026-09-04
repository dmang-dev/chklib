"""The parallel-array table machinery shared by the settings and restriction
sections.

Eleven CHK sections have the same shape: a fixed-size payload holding several
parallel arrays, one entry per unit, upgrade, technology, player-and-unit pair,
switch or sound. ``UNIS`` says what a unit's statistics are; ``PUNI`` says which
players may build it. The semantics differ completely, the byte mechanics do
not, so the mechanics live here and each section's meaning lives with it.

Two properties are worth stating once, because both sets of sections depend on
them and neither is the obvious choice:

**A gap is filled with the field's unset value, which is not always zero.** A
section shorter than its layout is a real thing in shipped maps. Filling the
remainder with zero invents data, and for the ``useDefault``-style flags it
invents the *opposite* of the truth, because those flags are constructed set.
:data:`FillMap` lets a field name either a scalar fill or a literal per-index
table -- the game's own default upgrade levels are such a table.

**An untouched table re-emits its original bytes verbatim.** Same rule as the
terrain grids and for the same reason: a short or oversized section must
round-trip exactly. An *edited* table emits its full nominal layout instead,
because splicing an edit back into a short section would silently drop whatever
landed past its end.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import ClassVar

from .chk import Section

__all__ = [
    "ArrayTable",
    "FillMap",
    "Layout",
    "LayoutError",
    "layout_size",
    "pack",
    "unpack",
]

WIDTH = {"B": 1, "H": 2, "I": 4}
LIMIT = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}

#: ``(field name, struct code, element count)`` per array, in file order.
Layout = tuple[tuple[str, str, int], ...]

#: What an unread entry of a field holds: one value for every entry, or a
#: literal table with one value per index.
FillMap = dict[str, "int | tuple[int, ...]"]


class LayoutError(Exception):
    """A layout disagrees with the published section size, or a fill table
    disagrees with the array it fills."""


def layout_size(layout: Layout) -> int:
    return sum(WIDTH[code] * count for _, code, count in layout)


def fill_array(name: str, count: int, fill: FillMap) -> list[int]:
    """The values entries of ``name`` take when the section does not supply them.

    A scalar repeats; a tuple is a literal per-index table and must be exactly
    as long as the array it fills, so a mistyped table raises here rather than
    quietly padding or truncating the field it was meant to describe.
    """
    value = fill.get(name, 0)
    if isinstance(value, int):
        return [value] * count
    if len(value) != count:
        raise LayoutError(
            f"the fill table for {name!r} has {len(value)} entries, "
            f"but the field has {count}"
        )
    return list(value)


def unpack(data: bytes, layout: Layout, fill: FillMap) -> dict[str, list[int]]:
    """Read parallel arrays, tolerating a section shorter than the layout.

    Two details matter for a short section, and both are about not inventing
    data. Entries the section never reached take their field's unset value from
    ``fill`` rather than zero. And a final element the section cuts through is
    zero-*extended* rather than dropped: these are little-endian, so the bytes
    that are present keep the value they had.
    """
    out: dict[str, list[int]] = {}
    offset = 0
    for name, code, count in layout:
        width = WIDTH[code]
        need = width * count
        chunk = data[offset : offset + need]
        whole, part = divmod(len(chunk), width)
        values = list(struct.unpack_from(f"<{whole}{code}", chunk)) if whole else []
        if part:
            # Keep the low-order bytes the file actually contained.
            tail = chunk[whole * width :].ljust(width, b"\x00")
            values.extend(struct.unpack(f"<{code}", tail))
        values.extend(fill_array(name, count, fill)[len(values) :])
        out[name] = values
        offset += need
    return out


def pack(arrays: dict[str, list[int]], layout: Layout) -> bytes:
    parts = []
    for name, code, count in layout:
        try:
            values = arrays[name]
        except KeyError:
            raise ValueError(f"no {name} array to write") from None
        if len(values) != count:
            raise ValueError(
                f"{name} has {len(values)} entries, expected exactly {count}"
            )
        try:
            parts.append(struct.pack(f"<{count}{code}", *values))
        except struct.error as exc:
            # Name the field rather than leaving the caller a format code.
            bad = next(
                (i for i, v in enumerate(values) if not 0 <= v <= LIMIT[code]), None
            )
            if bad is None:
                raise
            raise ValueError(
                f"{name}[{bad}] = {values[bad]} does not fit a {code!r} field "
                f"(0..{LIMIT[code]})"
            ) from exc
    return b"".join(parts)


@dataclass(slots=True)
class ArrayTable:
    """A fixed-size section of parallel arrays."""

    arrays: dict[str, list[int]] = field(default_factory=dict, repr=False)
    raw: bytes = field(default=b"", repr=False)
    section: Section | None = field(default=None, repr=False)
    modified: bool = False

    SECTION: ClassVar[str] = ""
    LAYOUT: ClassVar[Layout] = ()

    #: Fields whose unset value is not zero. Absent means zero.
    FILL: ClassVar[FillMap] = {}

    #: What one index of an ordinary field means: a unit, an upgrade, a sound.
    #: Reports read this rather than inferring it, so the name a table uses for
    #: itself is stated once here instead of being repeated by every consumer.
    ENTITY: ClassVar[str] = "entry"

    #: Fields whose index means something other than this table's own entity.
    #: A consumer cannot infer this from array length, because length does not
    #: separate the two cases: ``UPGx``'s single pad byte is the only field
    #: whose count differs from the upgrade count, so guessing from length
    #: alone calls a changed pad byte a changed weapon.
    INDEXED_BY: ClassVar[dict[str, str]] = {}

    def __post_init__(self) -> None:
        # A table built directly rather than read from a section starts as a
        # valid all-defaults one, so it can be normalized into a new section.
        if not self.arrays and self.LAYOUT:
            self.arrays = {
                name: fill_array(name, count, self.FILL)
                for name, _, count in self.LAYOUT
            }

    @classmethod
    def nominal_size(cls) -> int:
        return layout_size(cls.LAYOUT)

    @classmethod
    def field_offset(cls, name: str) -> int:
        """Byte offset of a field within the section. Used by the tests to pin
        layout *order*, which no round-trip can check."""
        offset = 0
        for field_name, code, count in cls.LAYOUT:
            if field_name == name:
                return offset
            offset += WIDTH[code] * count
        raise KeyError(f"{cls.SECTION} has no {name} field")

    @classmethod
    def index_label(cls, field_name: str, index: int) -> str:
        """How one index of one field reads in a report.

        The table decides, because only the table knows its own shape. Guessing
        from array length instead cannot separate a genuinely differently-indexed
        array from a one-byte pad that merely happens to have a different count.
        An index into a one-element field is dropped: it carries no information.
        """
        for name, _code, count in cls.LAYOUT:
            if name == field_name:
                label = cls.INDEXED_BY.get(field_name, cls.ENTITY)
                return label if count == 1 else f"{label} {index}"
        raise KeyError(f"{cls.SECTION} has no {field_name} field")

    @classmethod
    def from_section(cls, section: Section) -> ArrayTable:
        return cls(unpack(section.data, cls.LAYOUT, cls.FILL), section.data, section)

    def __getitem__(self, name: str) -> list[int]:
        return self.arrays[name]

    def __contains__(self, name: str) -> bool:
        return name in self.arrays

    def _check(self, name: str, index: int) -> str:
        for field_name, code, count in self.LAYOUT:
            if field_name == name:
                if not 0 <= index < count:
                    raise IndexError(
                        f"{self.SECTION}.{name}[{index}] is outside 0..{count - 1}"
                    )
                return code
        raise KeyError(f"{self.SECTION} has no {name} field")

    def set(self, name: str, index: int, value: int) -> None:
        """Assign one entry.

        Use this rather than mutating ``arrays`` directly, so the table knows it
        has to re-emit its full layout on write. The index and the value are
        both checked here: a negative index would otherwise wrap silently to the
        far end of the array, and an oversized value would be accepted now and
        surface much later as a ``struct.error`` from a table that can no longer
        be written at all.
        """
        code = self._check(name, index)
        if not 0 <= value <= LIMIT[code]:
            raise ValueError(
                f"{self.SECTION}.{name}[{index}] = {value} does not fit a "
                f"{code!r} field (0..{LIMIT[code]})"
            )
        self.arrays[name][index] = value
        self.modified = True

    @property
    def is_short(self) -> bool:
        """The section held fewer bytes than the layout, so some entries were
        filled in rather than read."""
        return len(self.raw) < self.nominal_size()

    @property
    def is_oversized(self) -> bool:
        """The section held more bytes than the layout. They are preserved, but
        nothing here interprets them."""
        return len(self.raw) > self.nominal_size()

    @property
    def trailing_bytes(self) -> bytes:
        """Whatever an oversized section carried past the layout."""
        return self.raw[self.nominal_size() :]

    def to_bytes(self, *, normalize: bool = False) -> bytes:
        # The verbatim path exists to preserve a real section's original bytes.
        # A table built directly has no such bytes to preserve, so it packs --
        # otherwise synthesising a section would silently produce an empty one.
        if not normalize and not self.modified and self.section is not None:
            return bytes(self.raw)
        return pack(self.arrays, self.LAYOUT) + self.trailing_bytes
