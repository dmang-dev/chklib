"""Typed views over CHK sections.

A view is exactly that: an interpretation layered over a :class:`~openstaredit.chk.Section`
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

import struct
from dataclasses import dataclass, field
from typing import ClassVar, Iterator

from .chk import Chk, Section
from .records import Action, Condition, Location, Sprite, Trigger, Unit

__all__ = [
    "Dimensions", "PlayerSlots", "PlayerRaces", "ScenarioProperties", "Forces",
    "Version", "TilesetRef", "RecordArrayView", "TriggerListView",
    "StringTableView", "StringTable", "view_for", "string_table_for",
    "TYPED_SECTIONS",
]

_U16 = struct.Struct("<H")
_U16X2 = struct.Struct("<HH")
_U32 = struct.Struct("<I")

PIXELS_PER_TILE = 32


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
    def from_section(cls, section: Section) -> "Dimensions":
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
    def from_section(cls, section: Section) -> "PlayerSlots":
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
    def from_section(cls, section: Section) -> "PlayerRaces":
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
    def from_section(cls, section: Section) -> "ScenarioProperties":
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
    def from_section(cls, section: Section) -> "Forces":
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
    def from_section(cls, section: Section) -> "Version":
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
    def from_section(cls, section: Section) -> "TilesetRef":
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

    record_type: type
    records: list
    trailing: bytes = b""
    section: Section | None = field(default=None, repr=False)

    @classmethod
    def from_section(cls, section: Section, record_type: type) -> "RecordArrayView":
        records, trailing = record_type.unpack_all(section.data)
        return cls(record_type, records, trailing, section)

    def to_bytes(self) -> bytes:
        return b"".join(r.to_bytes() for r in self.records) + self.trailing

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator:
        return iter(self.records)

    def __getitem__(self, index):
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
    def from_section(cls, section: Section, is_briefing: bool = False) -> "TriggerListView":
        triggers, trailing = Trigger.unpack_all(section.data)
        return cls(triggers, is_briefing, trailing, section)

    def to_bytes(self) -> bytes:
        return b"".join(t.to_bytes() for t in self.triggers) + self.trailing

    def __len__(self) -> int:
        return len(self.triggers)

    def __iter__(self) -> Iterator[Trigger]:
        return iter(self.triggers)

    def __getitem__(self, index) -> Trigger:
        return self.triggers[index]

    @property
    def has_partial_trigger(self) -> bool:
        return bool(self.trailing)


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
    def from_section(cls, section: Section, wide: bool = False) -> "StringTableView":
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
    def from_view(cls, view: StringTableView) -> "StringTable":
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
}
"""Sections this library interprets, and the view each maps to."""

_RECORD_SECTIONS: dict[str, type] = {"UNIT": Unit, "THG2": Sprite, "MRGN": Location}
_SCALAR_SECTIONS: dict[str, type] = {
    "DIM": Dimensions,
    "VER": Version,
    "ERA": TilesetRef,
    "OWNR": PlayerSlots,
    "IOWN": PlayerSlots,
    "SIDE": PlayerRaces,
    "SPRP": ScenarioProperties,
    "FORC": Forces,
    "STR": StringTableView,
}


def view_for(chk: Chk, name: str):
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
