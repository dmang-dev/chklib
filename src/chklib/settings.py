"""The settings tables: ``WAV``, ``SWNM``, ``UNIS``/``UNIx``, ``UPGS``/``UPGx``,
``TECS``/``TECx``.

Every one of these has the same shape -- a fixed-size section holding several
parallel arrays, one entry per unit, upgrade, technology, switch or sound. They
are handled together because that shape is the whole of it; what differs is only
the element widths and the counts.

They matter because this is where a map's *custom* unit statistics live, and
where several of its string references live. ``Action.soundStringId`` and the
entries of ``WAV`` are indices into the same string table, which is a common
thing to get wrong: ``WAV`` is a parallel table of sound *paths*, not a table
that trigger actions index into.

Four traps, each of which silently produces a wrong map rather than an error:

**``useDefault`` is inverted from the obvious reading.** ``No = 0``, ``Yes = 1``
(Chkdraft ``chk.h:88``), so a *set* flag means "use the game's defaults and
ignore the custom data here", which makes the custom name id at offset 3192
dead. Reading it as "this entry is customised" inverts every unit in the map.

**The unset value is 1, not 0.** Every one of these structs is constructed with
``memset(&useDefault, UseDefault::Yes, ...)``, so an entry a short section does
not reach uses defaults. Zero-filling the gap -- the obvious way to pad -- says
the exact opposite, and on the first edit writes that inversion to disk.

**``UPGx`` carries one pad byte after its flag array and ``UPGS`` does not.**
Miss it and every subsequent array is one byte out. ``TECS``/``TECx`` have no
such byte, so the rule cannot be generalised.

**Unit hitpoints are stored at 256x the displayed value.** The field is a ``u32``
for that reason. Both directions are provided, because a caller who reads with
:meth:`UnitSettings.displayed_hitpoints` and writes the value back unconverted
stores a unit with 1/256th the health they asked for.

Field order here is not inferred: it is transcribed from Chkdraft's ``REFLECT``
declarations (``chk.h:1542-1710``), whose sizes for all six statistics tables are
marked "validated". A round-trip test cannot check field order -- packing and
unpacking walk the same layout, so any permutation round-trips perfectly -- so
that transcription and the byte-offset tests are what hold it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

__all__ = [
    "SoundPaths",
    "SwitchNames",
    "UnitSettings",
    "UnitSettingsExpansion",
    "UpgradeSettings",
    "UpgradeSettingsExpansion",
    "TechSettings",
    "TechSettingsExpansion",
    "SETTINGS_SECTIONS",
    "USE_DEFAULT_NO",
    "USE_DEFAULT_YES",
    "LayoutError",
    "settings_for",
]

from ._tables import ArrayTable as _ArrayTable
from ._tables import FillMap, Layout, LayoutError
from .chk import Chk

#: ``Chk::UseDefault`` (Chkdraft ``chk.h:88``). Note which one is which.
USE_DEFAULT_NO = 0   # this entry carries custom statistics
USE_DEFAULT_YES = 1  # the game's built-in statistics win; custom data is dead

#: One entry per unit type, upgrade, technology and so on.
TOTAL_UNITS = 228
TOTAL_WEAPONS_ORIGINAL = 100
TOTAL_WEAPONS_EXPANSION = 130
TOTAL_UPGRADES_ORIGINAL = 46
TOTAL_UPGRADES_EXPANSION = 61
TOTAL_TECHS_ORIGINAL = 24
TOTAL_TECHS_EXPANSION = 44
TOTAL_SOUNDS = 512
TOTAL_SWITCHES = 256

#: The flag array is the one field in these tables whose unset value is not
#: zero. It is set on construction, so an entry a short section never reached
#: reads as "uses the game's defaults" rather than as an all-zero customisation.
_FLAG_FIELD = "use_default"
_SETTINGS_FILL: FillMap = {_FLAG_FIELD: USE_DEFAULT_YES}


# ---------------------------------------------------------------------------
# String-referencing tables
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SoundPaths(_ArrayTable):
    """``WAV`` -- 512 ``u32`` string ids, one per sound slot (SPEC 5.5).

    These are indices into ``STR``/``STRx``, the same namespace a trigger
    action's ``soundStringId`` uses. An action does **not** index into this
    table; both simply hold string ids. Three sources agree, and the opposite
    assumption is a common one.
    """

    SECTION: ClassVar[str] = "WAV"
    ENTITY: ClassVar[str] = "sound"
    LAYOUT: ClassVar[Layout] = (("sound_string_ids", "I", TOTAL_SOUNDS),)

    @property
    def sound_string_ids(self) -> list[int]:
        return self.arrays["sound_string_ids"]

    def used_slots(self) -> list[int]:
        """Slot indices holding a string id. Id 0 means "no string"."""
        return [i for i, v in enumerate(self.sound_string_ids) if v]


@dataclass(slots=True)
class SwitchNames(_ArrayTable):
    """``SWNM`` -- 256 ``u32`` string ids, one per switch (SPEC 5.6)."""

    SECTION: ClassVar[str] = "SWNM"
    ENTITY: ClassVar[str] = "switch"
    LAYOUT: ClassVar[Layout] = (("switch_string_ids", "I", TOTAL_SWITCHES),)

    @property
    def switch_string_ids(self) -> list[int]:
        return self.arrays["switch_string_ids"]

    def named_switches(self) -> list[int]:
        return [i for i, v in enumerate(self.switch_string_ids) if v]


# ---------------------------------------------------------------------------
# Unit, upgrade and technology statistics
# ---------------------------------------------------------------------------


def _unit_layout(weapons: int) -> Layout:
    # Chkdraft chk.h -- REFLECT(UNIS/UNIx, useDefault, hitpoints, shieldPoints,
    # armorLevel, buildTime, mineralCost, gasCost, nameStringId, baseDamage,
    # upgradeDamage). The two differ only in the weapon count.
    return (
        (_FLAG_FIELD, "B", TOTAL_UNITS),
        ("hitpoints", "I", TOTAL_UNITS),
        ("shield_points", "H", TOTAL_UNITS),
        ("armor_level", "B", TOTAL_UNITS),
        ("build_time", "H", TOTAL_UNITS),
        ("mineral_cost", "H", TOTAL_UNITS),
        ("gas_cost", "H", TOTAL_UNITS),
        ("name_string_ids", "H", TOTAL_UNITS),
        ("base_damage", "H", weapons),
        ("upgrade_damage", "H", weapons),
    )


@dataclass(slots=True)
class UnitSettings(_ArrayTable):
    """``UNIS`` (4048 bytes) or ``UNIx`` (4168 bytes) -- per-unit statistics.

    The two share an identical prefix and differ only in the trailing weapon
    arrays: 100 weapons for ``UNIS``, 130 for ``UNIx``. Confidence A -- the
    3192 name-id offset and both totals are confirmed four ways.

    Which one is live when a map carries both is **unresolved** (SPEC 7.6);
    openbw's answer depends on the file version, and for pre-BroodWar versions
    applies both in file order. Nothing here picks a winner.
    """

    SECTION: ClassVar[str] = "UNIS"
    ENTITY: ClassVar[str] = "unit"
    FILL: ClassVar[FillMap] = _SETTINGS_FILL
    LAYOUT: ClassVar[Layout] = _unit_layout(TOTAL_WEAPONS_ORIGINAL)

    #: The damage arrays are indexed by weapon, not by unit, and are shorter
    #: than the rest of the table for that reason.
    INDEXED_BY: ClassVar[dict[str, str]] = {
        "base_damage": "weapon",
        "upgrade_damage": "weapon",
    }

    #: Byte offset of the name-id array. Checked at import for both ``UNIS`` and
    #: ``UNIx``; it is the single most cross-checked number in this section.
    NAME_STRING_OFFSET: ClassVar[int] = 3192

    @property
    def name_string_ids(self) -> list[int]:
        return self.arrays["name_string_ids"]

    def uses_defaults(self, unit_type: int) -> bool:
        """True when the game's built-in stats win for this unit.

        Note the polarity: **a set flag means the custom data here is ignored.**
        """
        self._check(_FLAG_FIELD, unit_type)
        return bool(self.arrays[_FLAG_FIELD][unit_type])

    def customised_units(self) -> list[int]:
        """Unit types whose stats in this map are custom rather than default."""
        return [i for i, v in enumerate(self.arrays[_FLAG_FIELD]) if not v]

    def custom_name_id(self, unit_type: int) -> int:
        """The custom name's string id, or 0 when defaults apply.

        A name id sitting behind a set ``useDefault`` flag is dead data --
        bw-chk only harvests it when the flag is clear, and eudplib zeroes it
        when the flag is set -- so returning it would invent a name the game
        never shows.
        """
        if self.uses_defaults(unit_type):
            return 0
        return self.name_string_ids[unit_type]

    @staticmethod
    def displayed_hitpoints(stored: int) -> int:
        """Convert a stored hitpoint value to the one the game displays.

        Takes the *stored value*, not a unit type -- ``displayed_hitpoints(7)``
        is 0, not unit 7's health.
        """
        return stored // 256

    @staticmethod
    def stored_hitpoints(displayed: int) -> int:
        """Inverse of :meth:`displayed_hitpoints`, for writing.

        Without this the obvious round-trip -- read displayed, write it back --
        stores 1/256th of the intended health with no error anywhere.
        """
        return displayed * 256


@dataclass(slots=True)
class UnitSettingsExpansion(UnitSettings):
    """``UNIx`` -- as ``UNIS`` but with 130 weapons instead of 100."""

    SECTION: ClassVar[str] = "UNIx"
    LAYOUT: ClassVar[Layout] = _unit_layout(TOTAL_WEAPONS_EXPANSION)


def _upgrade_layout(count: int, pad: int) -> Layout:
    # Chkdraft chk.h -- REFLECT(UPGS, useDefault, baseMineralCost,
    # mineralCostFactor, baseGasCost, gasCostFactor, baseResearchTime,
    # researchTimeFactor); UPGx is the same with `unused` second.
    layout: list[tuple[str, str, int]] = [(_FLAG_FIELD, "B", count)]
    if pad:
        # UPGx has exactly one pad byte *here*, between the flags and the costs,
        # and UPGS has none. The position is the whole of it: put it anywhere
        # else and the size still comes to 794 while every array below shifts.
        layout.append(("unused", "B", pad))
    layout += [
        ("base_mineral_cost", "H", count),
        ("mineral_cost_factor", "H", count),
        ("base_gas_cost", "H", count),
        ("gas_cost_factor", "H", count),
        ("base_research_time", "H", count),
        ("research_time_factor", "H", count),
    ]
    return tuple(layout)


@dataclass(slots=True)
class UpgradeSettings(_ArrayTable):
    """``UPGS`` -- 46 upgrades, 598 bytes (SPEC 5.8)."""

    SECTION: ClassVar[str] = "UPGS"
    ENTITY: ClassVar[str] = "upgrade"
    FILL: ClassVar[FillMap] = _SETTINGS_FILL
    LAYOUT: ClassVar[Layout] = _upgrade_layout(TOTAL_UPGRADES_ORIGINAL, pad=0)

    def uses_defaults(self, upgrade: int) -> bool:
        self._check(_FLAG_FIELD, upgrade)
        return bool(self.arrays[_FLAG_FIELD][upgrade])

    def customised_upgrades(self) -> list[int]:
        return [i for i, v in enumerate(self.arrays[_FLAG_FIELD]) if not v]


@dataclass(slots=True)
class UpgradeSettingsExpansion(UpgradeSettings):
    """``UPGx`` -- 61 upgrades, 794 bytes, **with one pad byte** after the flags.

    Chkdraft declares it and eudplib derives it independently. Missing it puts
    every subsequent array one byte out, which reads as plausible garbage rather
    than as an error.
    """

    SECTION: ClassVar[str] = "UPGx"
    LAYOUT: ClassVar[Layout] = _upgrade_layout(TOTAL_UPGRADES_EXPANSION, pad=1)

    #: The pad byte is not indexed by anything -- it is one byte, not an array
    #: with one entry per upgrade.
    INDEXED_BY: ClassVar[dict[str, str]] = {"unused": "pad byte"}


def _tech_layout(count: int) -> Layout:
    # Chkdraft chk.h -- REFLECT(TECS/TECx, useDefault, mineralCost, gasCost,
    # researchTime, energyCost). No pad byte in either.
    return (
        (_FLAG_FIELD, "B", count),
        ("mineral_cost", "H", count),
        ("gas_cost", "H", count),
        ("research_time", "H", count),
        ("energy_cost", "H", count),
    )


@dataclass(slots=True)
class TechSettings(_ArrayTable):
    """``TECS`` -- 24 technologies, 216 bytes (SPEC 5.9).

    Unlike ``UPGx`` there is **no pad byte** in either variant, so the upgrade
    tables' rule does not carry over.
    """

    SECTION: ClassVar[str] = "TECS"
    ENTITY: ClassVar[str] = "tech"
    FILL: ClassVar[FillMap] = _SETTINGS_FILL
    LAYOUT: ClassVar[Layout] = _tech_layout(TOTAL_TECHS_ORIGINAL)

    def uses_defaults(self, tech: int) -> bool:
        self._check(_FLAG_FIELD, tech)
        return bool(self.arrays[_FLAG_FIELD][tech])

    def customised_techs(self) -> list[int]:
        return [i for i, v in enumerate(self.arrays[_FLAG_FIELD]) if not v]


@dataclass(slots=True)
class TechSettingsExpansion(TechSettings):
    """``TECx`` -- 44 technologies, 396 bytes. No pad byte."""

    SECTION: ClassVar[str] = "TECx"
    LAYOUT: ClassVar[Layout] = _tech_layout(TOTAL_TECHS_EXPANSION)


SETTINGS_SECTIONS: dict[bytes, type[_ArrayTable]] = {
    b"WAV ": SoundPaths,
    b"SWNM": SwitchNames,
    b"UNIS": UnitSettings,
    b"UNIx": UnitSettingsExpansion,
    b"UPGS": UpgradeSettings,
    b"UPGx": UpgradeSettingsExpansion,
    b"TECS": TechSettings,
    b"TECx": TechSettingsExpansion,
}


def settings_for(chk: Chk, name: str | bytes) -> _ArrayTable | None:
    """Return the settings table for section ``name``, or ``None``.

    Duplicates resolve last-wins. These sections are not in SPEC 1.5's
    ``Override`` list -- only ``MTXM`` is -- so the prefix-patch merge the
    terrain grids use deliberately does not apply here. Lookup is
    case-sensitive, because ``UNIS`` and ``UNIx`` differ only in the case of
    their last character.
    """
    raw_name = name.encode("ascii") if isinstance(name, str) else bytes(name)
    key = raw_name.rstrip(b" ").ljust(4, b" ")
    table_cls = SETTINGS_SECTIONS.get(key)
    if table_cls is None:
        return None
    section = chk.last(key)
    return table_cls.from_section(section) if section is not None else None


def _check_layouts() -> None:
    """The published sizes are the check on every offset in this module.

    Raises rather than asserting, so that ``python -O`` -- which strips ``assert``
    outright -- cannot turn a layout typo into silently misaligned reads.
    """
    expected = {
        SoundPaths: 2048,
        SwitchNames: 1024,
        UnitSettings: 4048,
        UnitSettingsExpansion: 4168,
        UpgradeSettings: 598,
        UpgradeSettingsExpansion: 794,
        TechSettings: 216,
        TechSettingsExpansion: 396,
    }
    for table_cls, size in expected.items():
        actual = table_cls.nominal_size()
        if actual != size:
            raise LayoutError(
                f"{table_cls.SECTION} layout is {actual} bytes, spec says {size}"
            )
    # The name-id array must land exactly where four sources independently say,
    # in *both* unit tables -- checking only UNIS leaves UNIx free to drift.
    for table_cls in (UnitSettings, UnitSettingsExpansion):
        offset = table_cls.field_offset("name_string_ids")
        if offset != UnitSettings.NAME_STRING_OFFSET:
            raise LayoutError(
                f"{table_cls.SECTION} name_string_ids at {offset}, "
                f"expected {UnitSettings.NAME_STRING_OFFSET}"
            )
    # The pad byte's position, not merely its presence, is what UPGx gets wrong.
    if UpgradeSettingsExpansion.field_offset("base_mineral_cost") != (
        TOTAL_UPGRADES_EXPANSION + 1
    ):
        raise LayoutError("UPGx pad byte is not between the flags and the costs")


_check_layouts()
