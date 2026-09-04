"""The player restriction tables: ``PUNI``, ``UPGR``/``PUPx``, ``PTEC``/``PTEx``.

Where the settings tables say what a unit *is*, these say what each player may
*do* with it: which units a player can build, how far they may upgrade, which
technologies they have and which are already researched. They share the settings
tables' byte mechanics -- fixed-size parallel arrays of ``u8`` -- so the
machinery is in :mod:`chklib._tables` and only the meaning lives here.

All five appear in nearly every map: across the 488-map corpus this suite runs
against, ``PUNI`` is in all of them, ``PUPx``/``PTEx`` in 391 and ``UPGR``/
``PTEC`` in 328.

Three traps, each of which produces a wrong map rather than an error:

**Every one of these sections is player-major.** ``player_unit_buildable`` is
``u8[12][228]`` flattened row by row, so player 3's entry for unit 45 lives at
index ``3 * 228 + 45``, not ``45 * 12 + 3``. Indexing it the other way reads a
real value belonging to the wrong player, which is exactly the kind of wrong
that never raises. Use :meth:`_PlayerTable.at` rather than doing the arithmetic
by hand.

**There are 12 players here, not 8.** ``COLR`` has 8 entries and ``OWNR`` has
12; these follow ``OWNR``. Sizing a row at 8 shears every subsequent row.

**The unset value is 1 for most of these arrays, not 0.** As with ``useDefault``
in the settings tables, the "player uses the game's defaults" flags are
constructed *set*, and ``PUNI`` additionally constructs every unit as buildable.
Zero-filling a short section therefore asserts that nothing is available and
nothing uses defaults -- the opposite of an untouched map.

**The layout is settled; the default tables are not.** These are different
claims with different evidence, and conflating them would overstate the weaker
one.

The *layout* is as well attested as anything in this library. openbw reads all
five independently of Chkdraft (``bwgame.h:21481-21531``), llvm-bw's map of
StarCraft.exe's memory pins the counts 61 and 44 a third time
(``Offsets.cpp:409``, ``:432``), and the corpus agrees exactly: of 488 maps,
every one of the 488 ``PUNI``, 328 ``UPGR``, 391 ``PUPx``, 328 ``PTEC`` and 391
``PTEx`` sections is *precisely* its nominal size. Not one is short or
oversized, which is unusual -- ``WAV`` and the terrain grids both have short
cases in the same corpus.

The *default tables* below are weaker and are labelled accordingly. Only
Chkdraft carries them as literals; no other implementation in the reference set
states them at all. Measured against the corpus they are a common map's
contents rather than a universal constant:

===================================  =========================================
``UPGR.default_max_level``           200 of 328 maps match; 98 differ only at
                                     index 18
``PUPx.default_max_level``           230 of 391 match; 71 differ only at index
                                     18
``PTEC.tech_researched_by_default``  39 of 328 match; 258 instead zero all six
                                     entries this table sets
``PTEx.tech_researched_by_default``  100 of 391 match; 248 zero all ten
===================================  =========================================

Both readings are legitimate -- these arrays are map data, and a map saying "no
technology starts researched" is making a choice, not being wrong. Chkdraft's
values are kept as the from-scratch default because they are traceable to a
cited source and are what the dominant editor writes into a new map. They are
not presented as the values a map *ought* to contain, and because no section in
the corpus is short, they are in practice only ever used when synthesising a
section from nothing.
"""

from __future__ import annotations

from typing import ClassVar

from ._tables import ArrayTable, FillMap, Layout, LayoutError, layout_size
from .chk import Chk

__all__ = [
    "UnitRestrictions",
    "UpgradeRestrictions",
    "UpgradeRestrictionsExpansion",
    "TechRestrictions",
    "TechRestrictionsExpansion",
    "RESTRICTION_SECTIONS",
    "TOTAL_PLAYERS",
    "AVAILABLE_NO",
    "AVAILABLE_YES",
    "RESEARCHED_NO",
    "RESEARCHED_YES",
    "restrictions_for",
]

#: ``Sc::Player::Total`` (Chkdraft ``sc.h:119``). Twelve, matching ``OWNR`` --
#: the eight of ``COLR``/``FORC`` are the *playable* slots, not all of them.
TOTAL_PLAYERS = 12

TOTAL_UNITS = 228
TOTAL_UPGRADES_ORIGINAL = 46
TOTAL_UPGRADES_EXPANSION = 61
TOTAL_TECHS_ORIGINAL = 24
TOTAL_TECHS_EXPANSION = 44

#: ``Chk::Available`` and ``Chk::Researched`` (Chkdraft ``chk.h:83``, ``:93``).
#: Unlike ``UseDefault`` these read the way they are named.
AVAILABLE_NO = 0
AVAILABLE_YES = 1
RESEARCHED_NO = 0
RESEARCHED_YES = 1

#: ``Chk::UseDefault`` again, and inverted the same way: set means "ignore the
#: custom data in this entry and use the game's own".
USE_DEFAULT_YES = 1

# --------------------------------------------------------------------------
# From-scratch defaults
#
# What a section synthesised from nothing contains, transcribed from Chkdraft's
# struct initialisers. See the module docstring for how far the corpus actually
# agrees -- for the two researched-technology tables, not very far.
#
# Spelt out index by index rather than computed. These are the one part of this
# module with a single source, and a generating expression would hide a
# transcription error inside plausible-looking code.
# --------------------------------------------------------------------------

#: ``UPGR::defaultMaxLevel`` (Chkdraft ``chk.h:1435-1439``). The first sixteen
#: upgrades go to level 3; most of the rest are one-shot; index 18 and index 45
#: are 0 here, though 98 corpus maps carry 1 at index 18.
DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL: tuple[int, ...] = (
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0,
)

#: ``PUPx::defaultMaxLevel`` (Chkdraft ``chk.h:1609-1614``). The first 46 match
#: the original table; the expansion tail does **not** continue it and is not
#: all zero, so this cannot be derived from the table above by padding.
DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION: tuple[int, ...] = (
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1,
    0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
)

#: ``PTEC::techResearchedByDefault`` (Chkdraft ``chk.h:1454-1461``). Six of the
#: twenty-four original technologies start researched.
DEFAULT_RESEARCHED_ORIGINAL: tuple[int, ...] = (
    0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0,
    1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1,
)

#: ``PTEx::techResearchedByDefault`` (Chkdraft ``chk.h:1629-1641``). The first
#: twenty-four match the original table; four of the expansion technologies
#: start researched as well.
DEFAULT_RESEARCHED_EXPANSION: tuple[int, ...] = (
    0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0,
    1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1,
    0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
)


class _PlayerTable(ArrayTable):
    """A restriction table, whose big arrays are indexed by player *and* item."""

    #: How many items one player's row holds. Set by each subclass.
    PER_PLAYER: ClassVar[int] = 0

    #: The fields laid out as ``[player][item]`` rather than ``[item]``.
    PLAYER_MAJOR: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def index_of(cls, field_name: str, player: int, item: int) -> int:
        """Where player ``player``'s entry for ``item`` sits in a flat array.

        Row-major and player-major, so this is ``player * PER_PLAYER + item``.
        Both bounds are checked, because the two obvious errors -- transposing
        the arguments, and using 8 players instead of 12 -- both land on a real
        entry belonging to something else rather than off the end.
        """
        if field_name not in cls.PLAYER_MAJOR:
            raise KeyError(
                f"{cls.SECTION}.{field_name} is indexed by item alone, not by player"
            )
        if not 0 <= player < TOTAL_PLAYERS:
            raise IndexError(
                f"player {player} is outside 0..{TOTAL_PLAYERS - 1}"
            )
        if not 0 <= item < cls.PER_PLAYER:
            raise IndexError(f"item {item} is outside 0..{cls.PER_PLAYER - 1}")
        return player * cls.PER_PLAYER + item

    def at(self, field_name: str, player: int, item: int) -> int:
        """One player's value for one item."""
        return self.arrays[field_name][self.index_of(field_name, player, item)]

    def set_at(self, field_name: str, player: int, item: int, value: int) -> None:
        """Assign one player's value for one item."""
        self.set(field_name, self.index_of(field_name, player, item), value)

    @classmethod
    def index_label(cls, field_name: str, index: int) -> str:
        """How one index reads in a report.

        A player-major index is split back into its player and item, because
        ``player 3 unit 45`` is actionable and ``index 729`` is not.
        """
        if field_name in cls.PLAYER_MAJOR and cls.PER_PLAYER:
            player, item = divmod(index, cls.PER_PLAYER)
            return f"player {player} {cls.ENTITY} {item}"
        return super().index_label(field_name, index)

    def customised_players(self, flag_field: str) -> list[int]:
        """Players with at least one entry not left to the game's defaults."""
        values = self.arrays[flag_field]
        return [
            player
            for player in range(TOTAL_PLAYERS)
            if not all(values[player * self.PER_PLAYER:(player + 1) * self.PER_PLAYER])
        ]


class UnitRestrictions(_PlayerTable):
    """``PUNI`` -- which units each player may build. 5700 bytes.

    Every array is constructed as ``Available::Yes``, so an untouched map says
    every player may build everything and every player uses the global default.
    """

    SECTION: ClassVar[str] = "PUNI"
    ENTITY: ClassVar[str] = "unit"
    PER_PLAYER: ClassVar[int] = TOTAL_UNITS
    PLAYER_MAJOR: ClassVar[frozenset[str]] = frozenset(
        {"player_unit_buildable", "player_unit_uses_default"}
    )
    LAYOUT: ClassVar[Layout] = (
        ("player_unit_buildable", "B", TOTAL_PLAYERS * TOTAL_UNITS),
        ("default_unit_buildable", "B", TOTAL_UNITS),
        ("player_unit_uses_default", "B", TOTAL_PLAYERS * TOTAL_UNITS),
    )
    FILL: ClassVar[FillMap] = {
        "player_unit_buildable": AVAILABLE_YES,
        "default_unit_buildable": AVAILABLE_YES,
        "player_unit_uses_default": USE_DEFAULT_YES,
    }
    INDEXED_BY: ClassVar[dict[str, str]] = {"default_unit_buildable": "default unit"}

    def buildable(self, player: int, unit: int) -> bool:
        """Whether ``player`` may build ``unit``, following the default flag.

        A player whose ``uses_default`` flag is set ignores its own row, so
        reading ``player_unit_buildable`` alone reports a value the game does
        not use.
        """
        if self.at("player_unit_uses_default", player, unit):
            return bool(self.arrays["default_unit_buildable"][unit])
        return bool(self.at("player_unit_buildable", player, unit))

    def customised_units(self) -> list[int]:
        """Units some player restricts rather than leaving to the default."""
        flags = self.arrays["player_unit_uses_default"]
        return [
            unit
            for unit in range(TOTAL_UNITS)
            if not all(
                flags[player * TOTAL_UNITS + unit] for player in range(TOTAL_PLAYERS)
            )
        ]


class UpgradeRestrictions(_PlayerTable):
    """``UPGS``'s companion: ``UPGR`` -- per-player upgrade levels. 1748 bytes."""

    SECTION: ClassVar[str] = "UPGR"
    ENTITY: ClassVar[str] = "upgrade"
    PER_PLAYER: ClassVar[int] = TOTAL_UPGRADES_ORIGINAL
    PLAYER_MAJOR: ClassVar[frozenset[str]] = frozenset(
        {
            "player_max_upgrade_level",
            "player_start_upgrade_level",
            "player_upgrade_uses_default",
        }
    )
    DEFAULT_MAX: ClassVar[tuple[int, ...]] = DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL
    LAYOUT: ClassVar[Layout] = (
        ("player_max_upgrade_level", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_ORIGINAL),
        ("player_start_upgrade_level", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_ORIGINAL),
        ("default_max_level", "B", TOTAL_UPGRADES_ORIGINAL),
        ("default_start_level", "B", TOTAL_UPGRADES_ORIGINAL),
        ("player_upgrade_uses_default", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_ORIGINAL),
    )
    FILL: ClassVar[FillMap] = {
        "default_max_level": DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL,
        "player_upgrade_uses_default": USE_DEFAULT_YES,
    }
    INDEXED_BY: ClassVar[dict[str, str]] = {
        "default_max_level": "default upgrade",
        "default_start_level": "default upgrade",
    }

    def max_level(self, player: int, upgrade: int) -> int:
        """How far ``player`` may take ``upgrade``, following the default flag."""
        if self.at("player_upgrade_uses_default", player, upgrade):
            return self.arrays["default_max_level"][upgrade]
        return self.at("player_max_upgrade_level", player, upgrade)

    def start_level(self, player: int, upgrade: int) -> int:
        """What level ``player`` starts ``upgrade`` at, following the flag."""
        if self.at("player_upgrade_uses_default", player, upgrade):
            return self.arrays["default_start_level"][upgrade]
        return self.at("player_start_upgrade_level", player, upgrade)


class UpgradeRestrictionsExpansion(UpgradeRestrictions):
    """``PUPx`` -- the same for the expansion's 61 upgrades. 2318 bytes.

    Note the default table is not the original one padded with zeros: six of
    the fifteen expansion upgrades are researchable and the tail is mixed.
    """

    SECTION: ClassVar[str] = "PUPx"
    PER_PLAYER: ClassVar[int] = TOTAL_UPGRADES_EXPANSION
    DEFAULT_MAX: ClassVar[tuple[int, ...]] = DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION
    LAYOUT: ClassVar[Layout] = (
        ("player_max_upgrade_level", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_EXPANSION),
        ("player_start_upgrade_level", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_EXPANSION),
        ("default_max_level", "B", TOTAL_UPGRADES_EXPANSION),
        ("default_start_level", "B", TOTAL_UPGRADES_EXPANSION),
        ("player_upgrade_uses_default", "B", TOTAL_PLAYERS * TOTAL_UPGRADES_EXPANSION),
    )
    FILL: ClassVar[FillMap] = {
        "default_max_level": DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION,
        "player_upgrade_uses_default": USE_DEFAULT_YES,
    }


class TechRestrictions(_PlayerTable):
    """``PTEC`` -- per-player technology availability. 912 bytes."""

    SECTION: ClassVar[str] = "PTEC"
    ENTITY: ClassVar[str] = "tech"
    PER_PLAYER: ClassVar[int] = TOTAL_TECHS_ORIGINAL
    PLAYER_MAJOR: ClassVar[frozenset[str]] = frozenset(
        {
            "tech_available_for_player",
            "tech_researched_for_player",
            "player_uses_defaults_for_tech",
        }
    )
    DEFAULT_RESEARCHED: ClassVar[tuple[int, ...]] = DEFAULT_RESEARCHED_ORIGINAL
    LAYOUT: ClassVar[Layout] = (
        ("tech_available_for_player", "B", TOTAL_PLAYERS * TOTAL_TECHS_ORIGINAL),
        ("tech_researched_for_player", "B", TOTAL_PLAYERS * TOTAL_TECHS_ORIGINAL),
        ("tech_available_by_default", "B", TOTAL_TECHS_ORIGINAL),
        ("tech_researched_by_default", "B", TOTAL_TECHS_ORIGINAL),
        ("player_uses_defaults_for_tech", "B", TOTAL_PLAYERS * TOTAL_TECHS_ORIGINAL),
    )
    FILL: ClassVar[FillMap] = {
        "tech_available_by_default": AVAILABLE_YES,
        "tech_researched_by_default": DEFAULT_RESEARCHED_ORIGINAL,
        "player_uses_defaults_for_tech": USE_DEFAULT_YES,
    }
    INDEXED_BY: ClassVar[dict[str, str]] = {
        "tech_available_by_default": "default tech",
        "tech_researched_by_default": "default tech",
    }

    def available(self, player: int, tech: int) -> bool:
        """Whether ``player`` may research ``tech``, following the default flag."""
        if self.at("player_uses_defaults_for_tech", player, tech):
            return bool(self.arrays["tech_available_by_default"][tech])
        return bool(self.at("tech_available_for_player", player, tech))

    def researched(self, player: int, tech: int) -> bool:
        """Whether ``player`` starts with ``tech``, following the default flag."""
        if self.at("player_uses_defaults_for_tech", player, tech):
            return bool(self.arrays["tech_researched_by_default"][tech])
        return bool(self.at("tech_researched_for_player", player, tech))


class TechRestrictionsExpansion(TechRestrictions):
    """``PTEx`` -- the same for the expansion's 44 technologies. 1672 bytes."""

    SECTION: ClassVar[str] = "PTEx"
    PER_PLAYER: ClassVar[int] = TOTAL_TECHS_EXPANSION
    DEFAULT_RESEARCHED: ClassVar[tuple[int, ...]] = DEFAULT_RESEARCHED_EXPANSION
    LAYOUT: ClassVar[Layout] = (
        ("tech_available_for_player", "B", TOTAL_PLAYERS * TOTAL_TECHS_EXPANSION),
        ("tech_researched_for_player", "B", TOTAL_PLAYERS * TOTAL_TECHS_EXPANSION),
        ("tech_available_by_default", "B", TOTAL_TECHS_EXPANSION),
        ("tech_researched_by_default", "B", TOTAL_TECHS_EXPANSION),
        ("player_uses_defaults_for_tech", "B", TOTAL_PLAYERS * TOTAL_TECHS_EXPANSION),
    )
    FILL: ClassVar[FillMap] = {
        "tech_available_by_default": AVAILABLE_YES,
        "tech_researched_by_default": DEFAULT_RESEARCHED_EXPANSION,
        "player_uses_defaults_for_tech": USE_DEFAULT_YES,
    }


#: Section name to the class that reads it. Keyed by the four-byte, space-padded
#: name a section header actually carries, matching ``SETTINGS_SECTIONS``, so a
#: caller holding a raw ``Section.name`` can look up with it directly. Case
#: matters: ``PUPx`` and ``PTEx`` are mixed-case on disk.
RESTRICTION_SECTIONS: dict[bytes, type[_PlayerTable]] = {
    b"PUNI": UnitRestrictions,
    b"UPGR": UpgradeRestrictions,
    b"PUPx": UpgradeRestrictionsExpansion,
    b"PTEC": TechRestrictions,
    b"PTEx": TechRestrictionsExpansion,
}

#: The size each section is documented to be, kept separate from the layouts so
#: the check below compares two independent statements rather than one with
#: itself. Chkdraft marks all five "validated", meaning StarCraft enforces them.
PUBLISHED_SIZES = {
    b"PUNI": 5700,
    b"UPGR": 1748,
    b"PUPx": 2318,
    b"PTEC": 912,
    b"PTEx": 1672,
}


def restrictions_for(chk: Chk, name: str | bytes) -> _PlayerTable | None:
    """The restriction table for ``name``, or ``None`` if the map has no such
    section.

    Last-wins on duplicates, per the standard section policy: only ``MTXM``
    takes the prefix-patch merge, and none of these are it. Lookup is
    case-sensitive, as it is for the settings tables and for the same reason:
    ``PUPx`` and ``PTEx`` carry a lowercase ``x`` that distinguishes them.
    """
    raw_name = name.encode("ascii") if isinstance(name, str) else bytes(name)
    key = raw_name.rstrip(b" ").ljust(4, b" ")
    table_cls = RESTRICTION_SECTIONS.get(key)
    if table_cls is None:
        return None
    section = chk.last(key)
    if section is None:
        return None
    table = table_cls.from_section(section)
    assert isinstance(table, _PlayerTable)
    return table


def _check_layouts() -> None:
    """Every layout must total its published size, and every fill table must be
    as long as the array it fills.

    Raised rather than asserted so it still runs under ``python -O``: a layout
    that silently disagrees with the format is the one failure mode here that
    corrupts maps instead of reporting anything.
    """
    for key, table_cls in RESTRICTION_SECTIONS.items():
        # Decoded for the messages below: these keys are bytes, and an
        # interpolated one reads as b'PUNI' rather than PUNI.
        name = key.decode('ascii')
        total = layout_size(table_cls.LAYOUT)
        published = PUBLISHED_SIZES[key]
        if total != published:
            raise LayoutError(
                f"{name} layout totals {total} bytes, but the section is {published}"
            )
        counts = {field: count for field, _code, count in table_cls.LAYOUT}
        for field, value in table_cls.FILL.items():
            if field not in counts:
                raise LayoutError(f"{name} has a fill for absent field {field!r}")
            if not isinstance(value, int) and len(value) != counts[field]:
                raise LayoutError(
                    f"{name}.{field} fill table has {len(value)} entries, "
                    f"but the field has {counts[field]}"
                )
        for field in table_cls.PLAYER_MAJOR:
            if counts.get(field) != TOTAL_PLAYERS * table_cls.PER_PLAYER:
                raise LayoutError(
                    f"{name}.{field} is declared player-major but holds "
                    f"{counts.get(field)} entries, not "
                    f"{TOTAL_PLAYERS * table_cls.PER_PLAYER}"
                )


_check_layouts()
