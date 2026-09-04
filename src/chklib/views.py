"""Typed views over CHK sections.

A view is exactly that: an interpretation layered over a :class:`~chklib.chk.Section`
whose raw bytes remain the source of truth. Views never replace the container's
bytes, because a typed write cannot reproduce every input (SPEC 8) -- short
sections, compressed string tables and undocumented flag bits all survive only if
the original bytes do.

Every view round-trips byte-exactly when nothing has been modified, including
sections that are shorter or longer than their nominal size. That is not
incidental: MTXM, TILE, ISOM, MASK and FORC can all legally be short, and
Chkdraft re-emits them at full nominal length, so a short input becomes a padded
output (SPEC 8.3). Here, a scalar view splices its packed fields back into a copy
of the original bytes, so the original length and any trailing content survive.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from .chk import Chk, Section
from .records import (
    MAX_CUWPS,
    Cuwp,
    Doodad,
    IsomRect,
    Location,
    Record,
    Sprite,
    Trigger,
    Unit,
)
from .restrictions import RESTRICTION_SECTIONS, restrictions_for
from .settings import SETTINGS_SECTIONS, settings_for

__all__ = [
    "Dimensions", "PlayerSlots", "PlayerRaces", "ScenarioProperties", "Forces",
    "Version", "TilesetRef", "RecordArrayView", "TriggerListView",
    "StringTableView", "StringTable", "TileGrid", "FogGrid", "IsomGrid",
    "terrain_for", "isom_for",
    "ScenarioType", "EditorVersion", "ValidationCode", "PlayerColors",
    "RemasteredColors", "CuwpUsage",
    "view_for", "string_table_for", "settings_for", "restrictions_for",
    "TYPED_SECTIONS",
]

_U16 = struct.Struct("<H")
_U16X2 = struct.Struct("<HH")
_U32 = struct.Struct("<I")

PIXELS_PER_TILE = 32


class _SectionReader(Protocol):
    """What the dispatch tables below hold: a class that reads one section.

    A Protocol rather than a base class because these do not share one --
    ``StringTableView`` is not a ``_ScalarView`` -- and rather than a bare
    ``type``, which loses ``from_section`` entirely and makes every lookup an
    error at the call site.
    """

    def from_section(self, section: Section) -> Any: ...


def _splice(original: bytes, packed: bytes) -> bytes:
    """Overlay ``packed`` onto a copy of ``original``, preserving its length.

    This is what keeps a short or oversized section byte-exact: field edits apply
    to the bytes the section actually has, and everything beyond the modeled
    prefix is carried through untouched.
    """
    buf = bytearray(original)
    n = min(len(packed), len(buf))
    buf[:n] = packed[:n]
    return bytes(buf)


def _padded(data: bytes, size: int) -> bytes:
    """``data`` zero-extended to ``size`` so a short section can still parse."""
    return data if len(data) >= size else bytes(data) + bytes(size - len(data))


@dataclass(slots=True)
class _ScalarView:
    """Base for a small fixed-layout section.

    ``raw`` is the section's original bytes and is what gives ``to_bytes()`` its
    length; the typed fields are spliced over it.
    """

    raw: bytes = field(default=b"", repr=False)
    section: Section | None = field(default=None, repr=False)

    NOMINAL: ClassVar[int] = 0

    def _pack(self) -> bytes:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_bytes(self) -> bytes:
        return _splice(self.raw, self._pack())

    @property
    def is_short(self) -> bool:
        """True when the section is shorter than its nominal size."""
        return len(self.raw) < self.NOMINAL


# ---------------------------------------------------------------------------
# Fixed-size scalar sections
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Dimensions(_ScalarView):
    """``DIM`` -- map size in tiles (SPEC 2.8). Confidence A, five-way.

    No source states any minimum, maximum or legal-multiple constraint on these
    values. The corpus is entirely multiples of 32, but that is StarEdit
    behaviour rather than a format rule, so nothing here validates against it.
    """

    SECTION: ClassVar[str] = "DIM"
    NOMINAL: ClassVar[int] = 4

    tile_width: int = 0
    tile_height: int = 0

    @classmethod
    def from_section(cls, section: Section) -> Dimensions:
        width, height = _U16X2.unpack(_padded(section.data, 4)[:4])
        return cls(raw=section.data, section=section, tile_width=width, tile_height=height)

    def _pack(self) -> bytes:
        return _U16X2.pack(self.tile_width, self.tile_height)

    @property
    def pixel_width(self) -> int:
        return self.tile_width * PIXELS_PER_TILE

    @property
    def pixel_height(self) -> int:
        return self.tile_height * PIXELS_PER_TILE

    @property
    def tile_count(self) -> int:
        return self.tile_width * self.tile_height

    def __str__(self) -> str:
        return f"{self.tile_width}x{self.tile_height}"


@dataclass(slots=True)
class PlayerSlots(_ScalarView):
    """``OWNR`` or ``IOWN`` -- 12 slot-type bytes (SPEC 2.6).

    ``OWNR`` is the one StarCraft reads; openbw ignores ``IOWN`` entirely. No
    source states a precedence rule when the two disagree, so both are kept
    independently rather than one being derived from the other.
    """

    SECTION: ClassVar[str] = "OWNR"
    NOMINAL: ClassVar[int] = 12

    slot_types: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> PlayerSlots:
        return cls(raw=section.data, section=section,
                   slot_types=_padded(section.data, 12)[:12])

    def _pack(self) -> bytes:
        return bytes(self.slot_types)

    def __len__(self) -> int:
        return len(self.slot_types)

    def __getitem__(self, player: int) -> int:
        return self.slot_types[player]


@dataclass(slots=True)
class PlayerRaces(_ScalarView):
    """``SIDE`` -- 12 race bytes (SPEC 2.9).

    The length is 12, not 8. bw-chk's section table declares ``SIDE`` as 8 bytes
    and clamps to it, truncating the races of players 9-12; that is a bug in
    bw-chk, not a format variant.
    """

    SECTION: ClassVar[str] = "SIDE"
    NOMINAL: ClassVar[int] = 12

    races: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> PlayerRaces:
        return cls(raw=section.data, section=section,
                   races=_padded(section.data, 12)[:12])

    def _pack(self) -> bytes:
        return bytes(self.races)

    def __len__(self) -> int:
        return len(self.races)

    def __getitem__(self, player: int) -> int:
        return self.races[player]


@dataclass(slots=True)
class ScenarioProperties(_ScalarView):
    """``SPRP`` -- name and description string ids (SPEC 2.10).

    Name first, description second -- four-way confirmed. String id 0 selects a
    default, though no source states what that default resolves to.
    """

    SECTION: ClassVar[str] = "SPRP"
    NOMINAL: ClassVar[int] = 4

    name_string_id: int = 0
    description_string_id: int = 0

    @classmethod
    def from_section(cls, section: Section) -> ScenarioProperties:
        name, description = _U16X2.unpack(_padded(section.data, 4)[:4])
        return cls(raw=section.data, section=section,
                   name_string_id=name, description_string_id=description)

    def _pack(self) -> bytes:
        return _U16X2.pack(self.name_string_id, self.description_string_id)


@dataclass(slots=True)
class Forces(_ScalarView):
    """``FORC`` -- force settings, 20 bytes nominal (SPEC 2.11).

    ``player_force`` covers only the **8 playable slots**; players 9-12 have no
    entry, unlike ``OWNR``/``SIDE`` which are 12 wide.

    A FORC shorter than 20 bytes is legal per Chkdraft but what it means is
    unresolved (SPEC 7.4), so a short section is parsed zero-extended and
    re-emitted at its original length rather than padded out.
    """

    SECTION: ClassVar[str] = "FORC"
    NOMINAL: ClassVar[int] = 20
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<8B4H4B")

    player_force: bytes = b""
    force_string_ids: tuple[int, ...] = ()
    flags: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> Forces:
        unpacked = cls._STRUCT.unpack(_padded(section.data, 20)[:20])
        return cls(
            raw=section.data,
            section=section,
            player_force=bytes(unpacked[0:8]),
            force_string_ids=tuple(unpacked[8:12]),
            flags=bytes(unpacked[12:16]),
        )

    def _pack(self) -> bytes:
        return self._STRUCT.pack(
            *self.player_force, *self.force_string_ids, *self.flags
        )

    def players_in(self, force: int) -> list[int]:
        """0-based playable slots assigned to ``force`` (0-3)."""
        return [i for i, f in enumerate(self.player_force) if f == force]


@dataclass(slots=True)
class Version(_ScalarView):
    """``VER`` -- file format version (SPEC 2.2). Confidence A."""

    SECTION: ClassVar[str] = "VER"
    NOMINAL: ClassVar[int] = 2

    value: int = 0

    #: Observed meanings. Other values are legal on disk.
    NAMES: ClassVar[dict[int, str]] = {
        59: "StarCraft 1.00",
        63: "Hybrid (Brood War compatible)",
        205: "Brood War",
        206: "Remastered",
    }

    @classmethod
    def from_section(cls, section: Section) -> Version:
        return cls(raw=section.data, section=section,
                   value=_U16.unpack(_padded(section.data, 2)[:2])[0])

    def _pack(self) -> bytes:
        return _U16.pack(self.value)

    @property
    def name(self) -> str:
        return self.NAMES.get(self.value, f"unknown ({self.value})")


@dataclass(slots=True)
class TilesetRef(_ScalarView):
    """``ERA`` -- tileset id (SPEC 2.7). Confidence A.

    The header defines 8 tilesets but states no rule for out-of-range values in
    the section itself, so none is imposed here.
    """

    SECTION: ClassVar[str] = "ERA"
    NOMINAL: ClassVar[int] = 2

    value: int = 0

    @classmethod
    def from_section(cls, section: Section) -> TilesetRef:
        return cls(raw=section.data, section=section,
                   value=_U16.unpack(_padded(section.data, 2)[:2])[0])

    def _pack(self) -> bytes:
        return _U16.pack(self.value)


# ---------------------------------------------------------------------------
# Record-array sections
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RecordArrayView:
    """A section that is a flat array of fixed-size records.

    Used for ``UNIT`` (36), ``THG2`` (10) and ``MRGN`` (20). The record count is
    always derived from the section size, never from the file version -- 1280
    and 5100 are conventions for ``MRGN``, not constraints.

    Any trailing bytes too few to form a whole record are preserved rather than
    dropped or padded out, because Chkdraft synthesises a whole record on save
    that was never in the input (SPEC 7.7).
    """

    record_type: type[Record]
    records: list[Record]
    trailing: bytes = b""
    section: Section | None = field(default=None, repr=False)

    @classmethod
    def from_section(
        cls, section: Section, record_type: type[Record]
    ) -> RecordArrayView:
        records, trailing = record_type.unpack_all(section.data)
        return cls(record_type, records, trailing, section)

    def to_bytes(self) -> bytes:
        return b"".join(r.to_bytes() for r in self.records) + self.trailing

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def __getitem__(self, index: int) -> Record:
        return self.records[index]

    @property
    def has_partial_record(self) -> bool:
        return bool(self.trailing)


@dataclass(slots=True)
class TriggerListView:
    """``TRIG`` or ``MBRF`` -- an array of 2400-byte triggers (SPEC 6.1).

    ``is_briefing`` matters: the two sections share a byte layout but NOT an
    action id space. Ids 0-9 mean entirely different things in each, so an
    action cannot be interpreted without knowing which section it came from.
    """

    triggers: list[Trigger]
    is_briefing: bool = False
    trailing: bytes = b""
    section: Section | None = field(default=None, repr=False)

    @classmethod
    def from_section(cls, section: Section, is_briefing: bool = False) -> TriggerListView:
        triggers, trailing = Trigger.unpack_all(section.data)
        return cls(triggers, is_briefing, trailing, section)

    def to_bytes(self) -> bytes:
        return b"".join(t.to_bytes() for t in self.triggers) + self.trailing

    def __len__(self) -> int:
        return len(self.triggers)

    def __iter__(self) -> Iterator[Trigger]:
        return iter(self.triggers)

    def __getitem__(self, index: int) -> Trigger:
        return self.triggers[index]

    @property
    def has_partial_trigger(self) -> bool:
        return bool(self.trailing)


# ---------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------


def _merge_override(payloads: list[bytes]) -> bytes:
    """Apply the ``Override`` duplicate-section policy (SPEC 1.5).

    Each later instance patches the **prefix**; a longer earlier instance keeps
    its tail. Chkdraft's main parser, bw-chk and openbw all agree on this for
    ``MTXM``, and it is the single most common terrain-protection trick, so
    last-wins visibly corrupts protected maps by zeroing everything past the
    short patch. (Chkdraft's *other* parser, ``lite_scenario.cpp``, fully
    replaces and is the outlier.)

    This is not theoretical. **24 of 423 installed StarCraft maps carry two to
    four ``MTXM`` sections**, including current ladder maps, and last-wins leaves
    them mostly empty -- 1,068 of 16,384 tiles on ``(4)Fighting Spirit.scx``.
    """
    merged = bytearray()
    for payload in payloads:
        if len(payload) >= len(merged):
            merged = bytearray(payload)
        else:
            merged[: len(payload)] = payload
    return bytes(merged)


#: Sections whose duplicates patch the prefix rather than replacing outright.
_OVERRIDE_SECTIONS = frozenset({b"MTXM"})


@dataclass(slots=True)
class _Grid:
    """Base for a row-major grid of fixed-width cells covering the map.

    Indexing is ``y * width + x``. Chkdraft's own header comments declare these
    arrays ``[tileWidth][tileHeight]`` -- column-major -- and the same wrong
    comment appears verbatim on MTXM, TILE, ISOM and MASK. Every accessor in
    that codebase, and four independent implementations, use row-major.

    Cells come from the section; the grid's *shape* comes from ``DIM``. Sections
    can legally be short, long or odd-length, and real maps are: across 488
    scanned maps, MTXM alone has 55 short, 7 long and 29 odd instances.

    **Writing follows one rule, chosen because the alternatives lose data.** An
    untouched grid re-emits its original bytes verbatim, so reading and writing a
    map it does not understand is always byte-exact. A grid that has been edited
    emits the whole ``width * height`` extent, because an edit can land past the
    end of a short section and clipping it back would discard the edit silently.
    """

    cells: list[int] = field(default_factory=list, repr=False)
    width: int = 0
    height: int = 0
    raw: bytes = field(default=b"", repr=False)
    section: Section | None = field(default=None, repr=False)
    source: bytes = field(default=b"", repr=False)
    """The bytes the cells were decoded from -- the merge result when merged."""
    clamped_dimensions: tuple[int, int] | None = None
    """The ``DIM`` values as declared, when the grid had to be capped."""
    merged_sections: int = 1
    """How many duplicate sections were merged to produce these cells."""
    modified: bool = False
    """Set by any successful :meth:`set`, and what selects the write mode."""

    CELL: ClassVar[int] = 1
    SECTION: ClassVar[str] = ""

    #: Cap on how many cells will be materialised. ``DIM`` is attacker-controlled
    #: -- a 22-byte file can declare 65535x65535, which is 4.29 billion cells and
    #: roughly 34 GB -- and no diagnostic is worth dying for. Real maps top out at
    #: 256x256 = 65,536 cells, so this leaves ample headroom while bounding the
    #: pathological case. A deliberate safety limit, not a format rule.
    MAX_CELLS: ClassVar[int] = 262_144

    @classmethod
    def _fit(cls, width: int, height: int) -> tuple[int, int, tuple[int, int] | None]:
        """Cap the grid's size **without changing the row stride**.

        Only ``height`` is reduced. ``width`` is the stride the bytes on disk are
        laid out with, so shrinking it would silently shift every row after the
        first and hand back the wrong tiles.
        """
        if width <= 0 or height <= 0 or width * height <= cls.MAX_CELLS:
            return width, height, None
        return width, max(cls.MAX_CELLS // width, 0), (width, height)

    # -- access ------------------------------------------------------------

    def index(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x}, {y}) is outside a {self.width}x{self.height} map")
        return y * self.width + x

    def get(self, x: int, y: int) -> int:
        return self.cells[self.index(x, y)]

    def set(self, x: int, y: int, value: int) -> None:
        limit = (1 << (self.CELL * 8)) - 1
        if not 0 <= value <= limit:
            raise ValueError(f"{value} does not fit in {self.CELL * 8} bits")
        self.cells[self.index(x, y)] = value
        self.modified = True

    def __getitem__(self, position: tuple[int, int]) -> int:
        return self.get(*position)

    def __setitem__(self, position: tuple[int, int], value: int) -> None:
        self.set(*position, value)

    def __len__(self) -> int:
        return len(self.cells)

    def row(self, y: int) -> list[int]:
        """One row of cells. Empty for a zero-width map rather than raising.

        A ``DIM`` of ``0xN`` is malformed but reachable, and callers that walk
        every row -- the inspect renderer among them -- must not blow up on it.
        """
        if self.width == 0:
            if not 0 <= y < self.height:
                raise IndexError(f"row {y} is outside a {self.width}x{self.height} map")
            return []
        start = self.index(0, y)
        return self.cells[start : start + self.width]

    # -- shape -------------------------------------------------------------

    @property
    def stored_cells(self) -> int:
        """How many cells the source actually held, before padding.

        Derived from :attr:`source`, not :attr:`raw`: for a merged grid those
        differ, and reporting the discarded last fragment's size would describe a
        fully populated grid as nearly empty.
        """
        return -(-len(self.source) // self.CELL)

    @property
    def is_short(self) -> bool:
        return self.stored_cells < self.width * self.height

    @property
    def has_odd_tail(self) -> bool:
        """True when the source length is not a whole number of cells."""
        return bool(len(self.source) % self.CELL)

    # -- writing -----------------------------------------------------------

    def to_bytes(self, *, normalize: bool = False) -> bytes:
        """Serialize.

        An **untouched** grid returns its original bytes verbatim, so a map with
        a short, long, odd or duplicated section survives a read/write cycle
        byte-exactly even where the grid's own model of it is lossy.

        An **edited** grid emits the full ``width * height`` extent. Splicing
        back into the original length instead would silently discard any edit
        landing past a short section's end -- reachable on 55 short and 29
        odd-length MTXM instances among real maps -- and, for an odd tail, would
        write only half of a tile id.

        ``normalize`` forces the full extent regardless.
        """
        if not normalize and not self.modified:
            return bytes(self.raw)
        return self._pack(self.cells)

    def _pack(self, cells: list[int]) -> bytes:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(slots=True)
class TileGrid(_Grid):
    """``MTXM`` or ``TILE`` -- one ``u16`` tile id per map cell (SPEC 3.1, 3.2).

    **MTXM and TILE are two distinct layers, not duplicates.** MTXM is what the
    game reads; TILE is the editor's ISOM-derived layer. They are byte-identical
    in only 1 of 65 corpus maps and differ in the other 64 by a mean of 4.8% of
    tiles, so aliasing one to the other silently corrupts terrain.

    A short, long or odd-length section is read rather than refused. Chkdraft
    rounds an odd size up and pads; openbw writes a lone trailing byte into the
    low half of the next tile and stops; bw-chk substitutes tile 0 past the end.
    blackvrice raises, which would reject real maps -- and they exist: 55 short,
    7 long and 29 odd MTXM instances across 488 scanned maps.
    """

    CELL: ClassVar[int] = 2
    SECTION: ClassVar[str] = "MTXM"

    @classmethod
    def from_section(cls, section: Section, width: int, height: int,
                     *, data: bytes | None = None, merged: int = 1) -> TileGrid:
        raw = section.data
        source = raw if data is None else data
        width, height, clamped = cls._fit(width, height)
        wanted = width * height
        # Unpack only what the grid can hold. A section far larger than the map
        # would otherwise be fully materialised and then thrown away.
        whole = min(len(source) // 2, wanted)
        cells = list(struct.unpack_from(f"<{whole}H", source)) if whole else []
        if len(source) % 2 and len(cells) < wanted:
            # A lone trailing byte becomes the low half of one more tile, which
            # is what the game does with it.
            cells.append(source[-1])
        if len(cells) < wanted:
            cells.extend([0] * (wanted - len(cells)))
        return cls(cells, width, height, raw, section, source, clamped, merged)

    def _pack(self, cells: list[int]) -> bytes:
        return struct.pack(f"<{len(cells)}H", *cells)

    @staticmethod
    def group(tile_id: int) -> int:
        """The megatile group: ``tileId >> 4``."""
        return tile_id >> 4

    @staticmethod
    def group_index(tile_id: int) -> int:
        """The index within the group: ``tileId & 0xF``."""
        return tile_id & 0xF

    def groups(self) -> set[int]:
        """Every distinct megatile group used. A cheap terrain fingerprint."""
        return {t >> 4 for t in self.cells}


@dataclass(slots=True)
class FogGrid(_Grid):
    """``MASK`` -- one ``u8`` fog-of-war byte per map cell (SPEC 3.4).

    **A set bit means the tile IS fogged for that player**, bit 0 being player 1.
    ``0x00`` is visible to everyone and ``0xFF`` is opaque to all eight. The
    polarity is confirmed by Chkdraft's fog brush and is the sort of thing that
    inverts silently if assumed.

    Unlike the tile layers, nothing pads MASK after load in any implementation,
    and openbw explicitly reads only ``min(w*h, bytes available)``. It is absent
    or short on real maps -- 11 short and 7 long instances across 488 scanned.
    """

    CELL: ClassVar[int] = 1
    SECTION: ClassVar[str] = "MASK"

    @classmethod
    def from_section(cls, section: Section, width: int, height: int,
                     *, data: bytes | None = None, merged: int = 1) -> FogGrid:
        raw = section.data
        source = raw if data is None else data
        width, height, clamped = cls._fit(width, height)
        wanted = width * height
        cells = list(source[:wanted])
        if len(cells) < wanted:
            cells.extend([0] * (wanted - len(cells)))
        return cls(cells, width, height, raw, section, source, clamped, merged)

    def _pack(self, cells: list[int]) -> bytes:
        return bytes(cells)

    def is_fogged_for(self, x: int, y: int, player: int) -> bool:
        """``player`` is 0-based, covering players 1-8."""
        if not 0 <= player < 8:
            raise ValueError("MASK covers players 1-8 only")
        return bool(self.get(x, y) & (1 << player))


@dataclass(slots=True)
class IsomGrid:
    """``ISOM`` -- the editor's isometric terrain (SPEC 3.3).

    A grid of 8-byte :class:`~chklib.records.IsomRect` records on its own
    coordinate system, **not** the tile grid: ``isom_width = tileWidth // 2 + 1``
    and ``isom_height = tileHeight + 1``. Indexing is row-major over that grid.

    ISOM is editor-only. StarCraft reads MTXM, so a stale or absent ISOM has no
    in-game effect -- which is exactly why it is the least reliable part of the
    format, and why the values inside each record are exposed rather than
    interpreted (see :class:`~chklib.records.IsomRect`).

    Two traps, both from the reference implementations:

    Chkdraft's ``scenario.cpp`` pads with ``expectedSize - actual`` computed in
    ``size_t`` after testing ``!=``, so an **oversized** ISOM underflows into an
    astronomically large insert. Padding here is short-only and truncation is
    explicit.

    eudplib writes a **decoy ISOM with a length past 0x80000000** as a protection
    marker. The container already stops at a negative section length and keeps
    the remainder verbatim, so such a map simply has no ISOM section here rather
    than a fabricated one.
    """

    rects: list[IsomRect] = field(default_factory=list, repr=False)
    width: int = 0
    height: int = 0
    raw: bytes = field(default=b"", repr=False)
    section: Section | None = field(default=None, repr=False)
    modified: bool = False

    RECORD: ClassVar[int] = 8
    SECTION: ClassVar[str] = "ISOM"
    #: Same rationale as :attr:`_Grid.MAX_CELLS`: ``DIM`` is attacker-controlled.
    MAX_RECORDS: ClassVar[int] = 262_144

    @staticmethod
    def shape_for(tile_width: int, tile_height: int) -> tuple[int, int]:
        """The ISOM grid's own dimensions for a map of this tile size."""
        return (tile_width // 2 + 1, tile_height + 1)

    @classmethod
    def from_section(cls, section: Section, tile_width: int,
                     tile_height: int) -> IsomGrid:
        raw = section.data
        width, height = cls.shape_for(tile_width, tile_height)
        if width <= 0 or height <= 0:
            return cls([], max(width, 0), max(height, 0), raw, section)
        wanted = width * height
        if wanted > cls.MAX_RECORDS:
            height = max(cls.MAX_RECORDS // width, 0)
            wanted = width * height
        available = min(len(raw) // cls.RECORD, wanted)
        rects = [
            IsomRect.from_bytes(raw[i * cls.RECORD : (i + 1) * cls.RECORD])
            for i in range(available)
        ]
        # Pad short, truncate long -- never Chkdraft's underflowing subtraction.
        rects.extend(IsomRect(0, 0, 0, 0) for _ in range(wanted - len(rects)))
        return cls(rects, width, height, raw, section)

    # -- access ------------------------------------------------------------

    def index(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(
                f"({x}, {y}) is outside a {self.width}x{self.height} ISOM grid"
            )
        return y * self.width + x

    def get(self, x: int, y: int) -> IsomRect:
        return self.rects[self.index(x, y)]

    def set(self, x: int, y: int, rect: IsomRect) -> None:
        self.rects[self.index(x, y)] = rect
        self.modified = True

    def __getitem__(self, position: tuple[int, int]) -> IsomRect:
        return self.get(*position)

    def __setitem__(self, position: tuple[int, int], rect: IsomRect) -> None:
        self.set(*position, rect)

    def __len__(self) -> int:
        return len(self.rects)

    def __iter__(self) -> Iterator[IsomRect]:
        return iter(self.rects)

    # -- shape -------------------------------------------------------------

    @property
    def stored_records(self) -> int:
        return len(self.raw) // self.RECORD

    @property
    def is_short(self) -> bool:
        return self.stored_records < self.width * self.height

    @property
    def expected_size(self) -> int:
        return self.width * self.height * self.RECORD

    @property
    def has_editor_flags(self) -> bool:
        """True when any record carries Chkdraft's ``Visited``/``Modified`` bits."""
        return any(r.has_editor_flags for r in self.rects)

    def to_bytes(self, *, normalize: bool = False) -> bytes:
        """Same rule as the tile grids: untouched returns the original bytes."""
        if not normalize and not self.modified:
            return bytes(self.raw)
        return b"".join(r.to_bytes() for r in self.rects)


def isom_for(chk: Chk) -> IsomGrid | None:
    """Return the ``ISOM`` grid sized from the map's ``DIM``, or ``None``."""
    section = chk.last("ISOM")
    dimensions = view_for(chk, "DIM")
    if section is None or dimensions is None:
        return None
    return IsomGrid.from_section(
        section, dimensions.tile_width, dimensions.tile_height
    )


#: The only sections this module knows how to shape as a tile/fog grid.
TERRAIN_SECTIONS: dict[bytes, type[TileGrid] | type[FogGrid]] = {
    b"MTXM": TileGrid,
    b"TILE": TileGrid,
    b"MASK": FogGrid,
}


def terrain_for(chk: Chk, name: str | bytes = "MTXM") -> TileGrid | FogGrid | None:
    """Return a terrain grid sized from the map's ``DIM``, or ``None``.

    ``DIM`` is what gives the grid its shape; the section only supplies cells.
    Without dimensions a grid cannot be indexed, so this returns ``None`` rather
    than guessing a shape.

    Only ``MTXM``, ``TILE`` and ``MASK`` are grids. Any other name returns
    ``None`` instead of decoding, say, ``ISOM`` or ``STR`` as tile ids and
    reporting confident, fabricated megatile groups. ``ISOM`` in particular is
    deliberately unimplemented, and fabricating it here would defeat that.

    Duplicate ``MTXM`` sections are merged under the ``Override`` policy rather
    than resolved last-wins, because that is what the game sees -- and 24 of 423
    installed maps need it.
    """
    # Accept "MTXM", b"MTXM" and the space-padded forms alike, the way the
    # container's own lookups do.
    raw_name = name.encode("ascii") if isinstance(name, str) else bytes(name)
    key = raw_name.rstrip(b" ").ljust(4, b" ")
    grid_cls = TERRAIN_SECTIONS.get(key)
    if grid_cls is None:
        return None

    sections = chk.find(key)
    dimensions = view_for(chk, "DIM")
    if not sections or dimensions is None:
        return None

    data = None
    merged = 1
    if len(sections) > 1 and key in _OVERRIDE_SECTIONS:
        data = _merge_override([s.data for s in sections])
        merged = len(sections)

    grid: TileGrid | FogGrid = grid_cls.from_section(
        sections[-1], dimensions.tile_width, dimensions.tile_height,
        data=data, merged=merged,
    )
    return grid


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StringTableView:
    """``STR`` -- the string table (SPEC 5.1). **Read-only.**

    String ids are 1-based and id 0 means "no string"; there is no offset slot
    for id 0, because the count field occupies the bytes where it would sit.
    Offsets are absolute from the start of the section payload -- not from the
    end of the offset table, and not from the 8-byte section header.

    Strings terminate at the next NUL, scanning forward. They are **not**
    delimited by the following slot's offset: in 30 of the 65 corpus maps the
    string data is not in ascending id order, which makes offset-differencing
    produce 114 negative lengths across 2,200 adjacent pairs (SPEC 8.7).

    Offsets are otherwise unconstrained -- two ids may share one, and an offset
    may point into the middle of another string or into the offset table itself.
    That is what makes the format's documented compression techniques readable,
    so none of it is treated as an error.

    Strings are opaque bytes. No source states a character encoding, so nothing
    is decoded implicitly; use :meth:`text` when you want to supply a guess.

    Read-only on purpose: a typed write cannot reproduce the input in general,
    because writers expand compressed tables, collapse the absent/empty string
    distinction, and drop tail data (SPEC 8.4-8.6). Use :class:`StringTable` to
    build one.

    ``STRx`` (SPEC 5.2) is exactly this with the count and every offset widened
    from ``u16`` to ``u32``, and nothing else changed -- same payload-relative
    origin, same 1-based ids, same NUL termination, same unconstrained offsets.
    ``wide`` selects it.
    """

    SECTION: ClassVar[str] = "STR"

    declared_count: int
    count: int
    offsets: list[int]
    data: bytes = field(repr=False)
    section: Section | None = field(default=None, repr=False)
    wide: bool = False
    """True for ``STRx``, whose count and offsets are 32-bit."""

    @classmethod
    def from_section(cls, section: Section, wide: bool = False) -> StringTableView:
        raw = section.data
        width = 4 if wide else 2
        unpack = _U32 if wide else _U16
        if len(raw) < width:
            return cls(0, 0, [], raw, section, wide)
        declared = unpack.unpack(raw[:width])[0]
        # A declared count larger than the section can physically hold is
        # clamped, not an error (SPEC 5.1 point 6).
        capacity = max(len(raw) // width - 1, 0)
        count = min(declared, capacity)
        offsets = [
            unpack.unpack(raw[width * i : width * (i + 1)])[0]
            for i in range(1, count + 1)
        ]
        return cls(declared, count, offsets, raw, section, wide)

    def to_bytes(self) -> bytes:
        """The original bytes. This view never re-synthesizes a string table."""
        return bytes(self.data)

    def get(self, string_id: int) -> bytes | None:
        """The raw bytes of ``string_id``, or ``None``.

        Returns ``None`` for id 0 ("no string"), for an id past the clamped
        count, and for an offset landing outside the section -- in that last
        case the slot still exists, it is simply unreadable. String ids are
        positional, so a bad entry must never shift the ids that follow it.
        """
        if string_id <= 0 or string_id > self.count:
            return None
        offset = self.offsets[string_id - 1]
        if offset >= len(self.data):
            return None
        end = self.data.find(b"\x00", offset)
        if end == -1:
            # No NUL after the offset: the string runs to the end of the section.
            end = len(self.data)
        return self.data[offset:end]

    def text(self, string_id: int, encoding: str = "cp1252") -> str | None:
        """:meth:`get` decoded. The encoding is a caller's guess, not a fact."""
        raw = self.get(string_id)
        return None if raw is None else raw.decode(encoding, errors="replace")

    def used_ids(self) -> list[int]:
        """Ids whose string is present and non-empty."""
        return [i for i in range(1, self.count + 1) if self.get(i)]

    def __len__(self) -> int:
        return self.count


@dataclass(slots=True)
class StringTable:
    """A mutable string table, for building a ``STR`` section.

    :class:`StringTableView` reads; this writes. Editing a map's name, its
    description, a location name or any trigger text means changing a string, so
    without this the library can inspect a map but not meaningfully edit one.

    Ids are 1-based and **positional**: id 7 is referenced as 7 from ``SPRP``,
    ``MRGN``, ``FORC``, ``TRIG`` and elsewhere. Gaps are therefore preserved
    rather than compacted, because renumbering would silently repoint every
    reference in the map.

    Two limits matter, and getting them wrong corrupts maps silently:

    ``STR`` offsets are ``u16``, so nothing past byte 65535 is addressable.
    Chkdraft's own guard under-counts by one byte per string -- it sums string
    lengths without the terminating NUL that its writer then emits -- so it
    accepts payloads slightly over the limit and writes offsets that wrap modulo
    65536, producing a corrupt map with no error. :meth:`to_bytes` counts the
    NULs and raises instead.

    The offset table alone occupies ``2 + 2N`` bytes, so ``N > 32766`` makes the
    string data unreachable no matter how it is packed. That is a derived
    reachability bound rather than a stated format limit -- the sources give four
    different ceilings across five orders of magnitude -- so it is enforced here
    deliberately and named, not inherited.
    """

    strings: dict[int, bytes] = field(default_factory=dict)
    """Present strings by 1-based id. An absent id means "no string"."""

    declared_count: int = 0
    """What to write as ``numStrings``. Real maps declare 1024 regardless of use."""

    tail: bytes = b""
    """Bytes found after the string data.

    Chkdraft parses these, logs that the map is "most likely a compiled EUD map",
    and then never writes them back -- the one place it destroys data it read
    successfully. They are carried through here instead. No corpus map has any,
    so this path is unverified; preserving unknown bytes is still strictly better
    than dropping them.
    """

    #: Largest byte offset a ``u16`` ``STR`` offset field can name.
    MAX_OFFSET: ClassVar[int] = 0xFFFF
    #: Beyond this the ``STR`` offset table alone fills the addressable space.
    MAX_IDS: ClassVar[int] = 32766
    #: The same two bounds for ``STRx``, whose fields are ``u32``. The id ceiling
    #: is ``(2**32 - 1 - 4) // 4`` -- and Chkdraft's STRx writer independently
    #: enforces exactly this number, which is reassuring for a derived bound.
    MAX_OFFSET_WIDE: ClassVar[int] = 0xFFFFFFFF
    MAX_IDS_WIDE: ClassVar[int] = 1073741822

    @classmethod
    def from_view(cls, view: StringTableView) -> StringTable:
        """Copy a parsed table into an editable one."""
        present = {}
        for string_id in range(1, view.count + 1):
            value = view.get(string_id)
            if value:
                present[string_id] = value
        return cls(present, max(view.declared_count, view.count))

    # -- editing -----------------------------------------------------------

    def get(self, string_id: int) -> bytes | None:
        return self.strings.get(string_id)

    def set(self, string_id: int, value: bytes | str) -> None:
        """Replace or create ``string_id``. Setting ``b""`` removes it."""
        if string_id < 1:
            raise ValueError("string ids are 1-based; 0 means 'no string'")
        raw = value.encode("cp1252") if isinstance(value, str) else bytes(value)
        if raw:
            self.strings[string_id] = raw
            self.declared_count = max(self.declared_count, string_id)
        else:
            self.strings.pop(string_id, None)

    def add(self, value: bytes | str) -> int:
        """Store ``value`` at the lowest free id and return it."""
        string_id = 1
        while string_id in self.strings:
            string_id += 1
        self.set(string_id, value)
        return string_id

    def __getitem__(self, string_id: int) -> bytes | None:
        return self.get(string_id)

    def __setitem__(self, string_id: int, value: bytes | str) -> None:
        self.set(string_id, value)

    def __len__(self) -> int:
        return len(self.strings)

    # -- writing -----------------------------------------------------------

    def to_bytes(self, *, dedupe: bool = False, wide: bool = False) -> bytes:
        """Serialize a ``STR`` section, or a ``STRx`` one when ``wide``.

        ``STRx`` is exactly ``STR`` with the count and every offset widened from
        ``u16`` to ``u32``; nothing else about the layout changes.

        Unused slots point at a single shared NUL placed immediately after the
        offset table, which is what StarEdit and Chkdraft both emit.

        ``dedupe`` points equal strings at one offset. That is legal -- readers
        scan forward to the next NUL, so sharing is exactly the format's
        documented "duplicate recycling" -- but no writer in the reference set
        produces it, so it is off by default rather than risking a tool that
        assumes otherwise.
        """
        width = 4 if wide else 2
        pack = _U32 if wide else _U16
        max_offset = self.MAX_OFFSET_WIDE if wide else self.MAX_OFFSET
        max_ids = self.MAX_IDS_WIDE if wide else self.MAX_IDS
        label = "STRx" if wide else "STR"

        count = max(self.declared_count, max(self.strings, default=0))
        if count > max_ids:
            raise ValueError(
                f"{count} string ids exceeds the reachable maximum of "
                f"{max_ids} for {label}: the offset table alone would occupy "
                f"{width + width * count} bytes of a {max_offset + 1}-byte "
                f"addressable space"
            )

        header_size = width + width * count
        # Count the terminating NUL for every string. Omitting it is the exact
        # arithmetic slip that lets Chkdraft emit wrapped offsets.
        payload = sum(len(v) + 1 for v in self.strings.values())
        total = header_size + 1 + payload  # +1 for the shared NUL
        if total - 1 > max_offset:
            raise ValueError(
                f"string table needs {total} bytes but {label} offsets are "
                f"{width * 8}-bit, so nothing past byte {max_offset} can be "
                f"addressed; remove or shorten strings"
            )

        offsets = [header_size] * count  # default: the shared NUL
        data = bytearray(b"\x00")
        seen: dict[bytes, int] = {}
        for string_id in sorted(self.strings):
            value = self.strings[string_id]
            if dedupe and value in seen:
                offsets[string_id - 1] = seen[value]
                continue
            offset = header_size + len(data)
            offsets[string_id - 1] = offset
            seen[value] = offset
            data += value + b"\x00"

        # Belt and braces: a bug in the arithmetic above must not reach a file.
        for string_id, offset in enumerate(offsets, start=1):
            if offset > max_offset:
                raise ValueError(
                    f"string {string_id} would sit at byte {offset}, past the "
                    f"{width * 8}-bit offset limit"
                )

        return (
            pack.pack(count)
            + b"".join(pack.pack(o) for o in offsets)
            + bytes(data)
            + self.tail
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

TYPED_SECTIONS: dict[str, str] = {
    "DIM": "Dimensions",
    "VER": "Version",
    "ERA": "TilesetRef",
    "OWNR": "PlayerSlots",
    "IOWN": "PlayerSlots",
    "SIDE": "PlayerRaces",
    "SPRP": "ScenarioProperties",
    "FORC": "Forces",
    "UNIT": "RecordArrayView[Unit]",
    "THG2": "RecordArrayView[Sprite]",
    "MRGN": "RecordArrayView[Location]",
    "TRIG": "TriggerListView",
    "MBRF": "TriggerListView (briefing action ids)",
    "STR": "StringTableView (read-only)",
    "STRx": "StringTableView, 32-bit (read-only; supersedes STR)",
    "MTXM": "TileGrid (game terrain)",
    "TILE": "TileGrid (editor terrain)",
    "MASK": "FogGrid",
    "ISOM": "IsomGrid (editor-only)",
    "WAV": "SoundPaths",
    "SWNM": "SwitchNames",
    "UNIS": "UnitSettings",
    "UNIx": "UnitSettings (expansion weapons)",
    "UPGS": "UpgradeSettings",
    "UPGx": "UpgradeSettings (expansion, +1 pad byte)",
    "TECS": "TechSettings",
    "TECx": "TechSettings (expansion)",
    "PUNI": "UnitRestrictions",
    "UPGR": "UpgradeRestrictions",
    "PUPx": "UpgradeRestrictions (expansion)",
    "PTEC": "TechRestrictions",
    "PTEx": "TechRestrictions (expansion)",
    "UPRP": "RecordArrayView[Cuwp] (trigger unit properties)",
    "UPUS": "CuwpUsage",
    "DD2": "RecordArrayView[Doodad] (editor-only)",
    "COLR": "PlayerColors",
    "CRGB": "RemasteredColors",
    "TYPE": "ScenarioType",
    "IVER": "EditorVersion",
    "IVE2": "EditorVersion",
    "VCOD": "ValidationCode",
}
"""Sections this library interprets, and the view each maps to."""

# ---------------------------------------------------------------------------
# Versions, validation and colours
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScenarioType(_ScalarView):
    """``TYPE`` -- which product authored the scenario. 4 bytes.

    A four-character tag rather than a number: ``RAWB`` for Brood War, ``RAWS``
    for vanilla StarCraft. Kept as raw bytes because it is a tag, and comparing
    it to an integer is the sort of thing that works until a third tag appears.

    Present in 177 of the 488 corpus maps, always exactly 4 bytes, and only ever
    one of those two values (161 ``RAWB``, 16 ``RAWS``).
    """

    SECTION: ClassVar[str] = "TYPE"
    NOMINAL: ClassVar[int] = 4

    tag: bytes = b"RAWB"

    BROOD_WAR: ClassVar[bytes] = b"RAWB"
    STARCRAFT: ClassVar[bytes] = b"RAWS"

    @classmethod
    def from_section(cls, section: Section) -> ScenarioType:
        return cls(raw=section.data, section=section,
                   tag=bytes(_padded(section.data, 4)[:4]))

    def _pack(self) -> bytes:
        return self.tag

    @property
    def is_brood_war(self) -> bool:
        return self.tag == self.BROOD_WAR

    def __str__(self) -> str:
        return self.tag.decode("latin-1")


@dataclass(slots=True)
class EditorVersion(_ScalarView):
    """``IVER`` or ``IVE2`` -- the editor's own version stamps. 2 bytes each.

    Neither is the map's *format* version; that is ``VER``. These say which
    StarEdit wrote the file, and StarCraft validates neither. In the corpus each
    is a single constant: ``IVER`` is 10 in all 290 maps that carry it, ``IVE2``
    is 11 in all 359.
    """

    SECTION: ClassVar[str] = "IVER"
    NOMINAL: ClassVar[int] = 2

    version: int = 10

    @classmethod
    def from_section(cls, section: Section) -> EditorVersion:
        value = _U16.unpack(_padded(section.data, 2)[:2])[0]
        return cls(raw=section.data, section=section, version=value)

    def _pack(self) -> bytes:
        return _U16.pack(self.version)


@dataclass(slots=True)
class ValidationCode(_ScalarView):
    """``VCOD`` -- the checksum seed table StarCraft validates a map against.
    1040 bytes: 256 ``u32`` seeds then 16 ``u8`` opcodes.

    Every map carries one and it is very nearly a constant: 483 of the 488
    corpus maps hold byte-identical payloads, and the first seeds and the opcode
    order match Chkdraft's literal exactly. The five that differ are the
    interesting ones -- a non-standard ``VCOD`` is how a map is made to refuse
    to open in an editor that recomputes it.

    :attr:`is_standard` exists so ``pack`` can *check* this section rather than
    copy it blindly. The comparison is against a digest rather than an embedded
    kilobyte of seeds, which keeps the check honest without carrying the table.
    """

    SECTION: ClassVar[str] = "VCOD"
    NOMINAL: ClassVar[int] = 1040
    SEEDS: ClassVar[int] = 256
    OPCODES: ClassVar[int] = 16

    #: SHA-256 of the payload shared by 483 of 488 corpus maps.
    STANDARD_DIGEST: ClassVar[str] = (
        "c13ca25290b5d075eae9705d68a7b8c30af498c36c10e138627f8b49b795392f"
    )

    seeds: list[int] = field(default_factory=list, repr=False)
    opcodes: list[int] = field(default_factory=list, repr=False)

    @classmethod
    def from_section(cls, section: Section) -> ValidationCode:
        data = _padded(section.data, cls.NOMINAL)
        return cls(
            raw=section.data,
            section=section,
            seeds=list(struct.unpack_from(f"<{cls.SEEDS}I", data, 0)),
            opcodes=list(struct.unpack_from(f"<{cls.OPCODES}B", data, cls.SEEDS * 4)),
        )

    def _pack(self) -> bytes:
        return (
            struct.pack(f"<{self.SEEDS}I", *self.seeds)
            + struct.pack(f"<{self.OPCODES}B", *self.opcodes)
        )

    @property
    def is_standard(self) -> bool:
        """Whether this is the ordinary table nearly every map carries."""
        return (
            hashlib.sha256(bytes(self.raw)).hexdigest() == self.STANDARD_DIGEST
        )


@dataclass(slots=True)
class PlayerColors(_ScalarView):
    """``COLR`` -- one colour byte per playable slot. 8 bytes.

    Eight, not twelve: these are the playable slots, as in ``FORC``, while
    ``OWNR`` and the restriction tables run to twelve.

    **The byte is not validated against a named colour.** Chkdraft names 0..11
    plus two specials, but the corpus carries 14 and 15 as well, and a
    Remastered map can carry a custom index above that. Anything here that
    rejected an unnamed value would refuse real maps, so the raw byte is what is
    modelled and naming it is left to the caller.
    """

    SECTION: ClassVar[str] = "COLR"
    NOMINAL: ClassVar[int] = 8
    SLOTS: ClassVar[int] = 8

    colors: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> PlayerColors:
        return cls(raw=section.data, section=section,
                   colors=bytes(_padded(section.data, cls.NOMINAL)[: cls.NOMINAL]))

    def _pack(self) -> bytes:
        return self.colors


@dataclass(slots=True)
class RemasteredColors(_ScalarView):
    """``CRGB`` -- Remastered per-player custom colours. 32 bytes.

    Eight RGB triples (24 bytes) followed by eight setting bytes saying how each
    triple is read: 0 random from a predefined set, 1 the player's own choice,
    2 the custom RGB here, 3 the colour index in the triple's third byte.

    This is the least corroborated section in the library and is labelled so.
    Only Chkdraft describes it, and unusually it does not annotate the size the
    way it does every neighbouring section. The corpus supplies the one
    independent check available: exactly one map of 488 carries a ``CRGB``, it
    is 32 bytes, and its eight trailing bytes are all 1 -- a uniform, valid
    setting value, which is what the 24/8 split predicts and what a wrong split
    would be unlikely to produce.

    The R, G, B order within a triple rests on Chkdraft's accessors alone; the
    one corpus map has all-zero triples and so cannot distinguish RGB from BGR.
    """

    SECTION: ClassVar[str] = "CRGB"
    NOMINAL: ClassVar[int] = 32
    SLOTS: ClassVar[int] = 8

    USE_PREDEFINED: ClassVar[int] = 0
    PLAYER_CHOICE: ClassVar[int] = 1
    CUSTOM_RGB: ClassVar[int] = 2
    USE_ID: ClassVar[int] = 3

    rgb: bytes = b""
    settings: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> RemasteredColors:
        data = _padded(section.data, cls.NOMINAL)
        return cls(raw=section.data, section=section,
                   rgb=bytes(data[: cls.SLOTS * 3]),
                   settings=bytes(data[cls.SLOTS * 3 : cls.NOMINAL]))

    def _pack(self) -> bytes:
        return self.rgb + self.settings

    def color(self, slot: int) -> tuple[int, int, int]:
        """The RGB triple for one slot, whether or not the setting uses it."""
        if not 0 <= slot < self.SLOTS:
            raise IndexError(f"slot {slot} is outside 0..{self.SLOTS - 1}")
        return (self.rgb[slot * 3], self.rgb[slot * 3 + 1], self.rgb[slot * 3 + 2])


@dataclass(slots=True)
class CuwpUsage(_ScalarView):
    """``UPUS`` -- which of ``UPRP``'s 64 property slots are in use. 64 bytes.

    A slot's bytes in ``UPRP`` can look plausible while being stale, so this is
    the authority on which are real. An empty ``UPUS`` is a real thing: 7 of the
    466 corpus maps that carry one declare it zero-length, which reads here as
    no slot in use rather than as an error.
    """

    SECTION: ClassVar[str] = "UPUS"
    NOMINAL: ClassVar[int] = MAX_CUWPS

    used: bytes = b""

    @classmethod
    def from_section(cls, section: Section) -> CuwpUsage:
        return cls(raw=section.data, section=section,
                   used=bytes(_padded(section.data, cls.NOMINAL)[: cls.NOMINAL]))

    def _pack(self) -> bytes:
        return self.used

    def used_slots(self) -> list[int]:
        """Indices of the property slots this map actually uses."""
        return [i for i, b in enumerate(self.used) if b]


_RECORD_SECTIONS: dict[str, type[Record]] = {
    "UNIT": Unit, "THG2": Sprite, "MRGN": Location,
    # UPRP is fixed at 64 slots and DD2 is variable, but both are plain
    # arrays of fixed-size records, so both get the trailing-partial
    # handling that matters: 7 corpus maps carry a 60-byte DD2, which is
    # seven whole doodads and half of an eighth.
    "UPRP": Cuwp, "DD2": Doodad,
}
_SCALAR_SECTIONS: dict[str, _SectionReader] = {
    "DIM": Dimensions,
    "VER": Version,
    "ERA": TilesetRef,
    "OWNR": PlayerSlots,
    "IOWN": PlayerSlots,
    "SIDE": PlayerRaces,
    "SPRP": ScenarioProperties,
    "FORC": Forces,
    "STR": StringTableView,
    "TYPE": ScenarioType,
    "IVER": EditorVersion,
    "IVE2": EditorVersion,
    "VCOD": ValidationCode,
    "COLR": PlayerColors,
    "CRGB": RemasteredColors,
    "UPUS": CuwpUsage,
}


def view_for(chk: Chk, name: str) -> Any:
    """Return a typed view of the *effective* section ``name``, or ``None``.

    The effective section is the last one with that name, matching StarCraft's
    override order (SPEC 1.5).

    This resolves the name it is given literally. To get whichever string table
    a map actually uses, call :func:`string_table_for` instead -- ``STRx``
    supersedes ``STR`` and that rule is not expressible as a section lookup.
    """
    section = chk.last(name)
    if section is None:
        return None
    key = section.key.decode("latin-1")
    if key in _RECORD_SECTIONS:
        return RecordArrayView.from_section(section, _RECORD_SECTIONS[key])
    if key in ("TRIG", "MBRF"):
        return TriggerListView.from_section(section, is_briefing=key == "MBRF")
    if key == "STRx":
        return StringTableView.from_section(section, wide=True)
    if key.rstrip() in ("MTXM", "TILE", "MASK"):
        # Terrain needs the map's dimensions for its shape, which is why this
        # takes the whole Chk rather than a lone section.
        return terrain_for(chk, key)
    if key.rstrip() == "ISOM":
        # ISOM needs the dimensions too, and its own derived grid shape.
        return isom_for(chk)
    if section.name in SETTINGS_SECTIONS:
        return settings_for(chk, section.name)
    if section.name in RESTRICTION_SECTIONS:
        return restrictions_for(chk, section.name)
    view_cls = _SCALAR_SECTIONS.get(key)
    return view_cls.from_section(section) if view_cls else None


def string_table_for(chk: Chk) -> StringTableView | None:
    """The string table a map's references actually resolve against.

    **``STRx`` wins over ``STR``, in either file order** (SPEC 5.2). Chkdraft,
    bw-chk and eudplib agree; Chkdraft gates its ``STR`` reader on the absence of
    ``STRx`` *and* clears the table when reading ``STRx``, so it wins whichever
    comes second.

    Not every implementation does this. blackvrice abstains when both are
    present and leaves every reference unresolved, which means failing to open
    perfectly ordinary Remastered maps that kept a legacy ``STR``.

    Empirically, of 423 installed maps, 24 carry ``STRx`` and **none carries
    both** -- so the precedence rule is real but unexercised by those maps.
    """
    for name in ("STRx", "STR"):
        section = chk.last(name)
        if section is not None:
            return StringTableView.from_section(section, wide=name == "STRx")
    return None
