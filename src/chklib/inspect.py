"""A stable, deterministic textual rendering of a CHK.

This is the output behind ``chkdiff inspect``, and it is shaped by its main
consumer: ``git config diff.scx.textconv``. Git runs its ordinary line diff over
two of these, so the format's job is to make a small change to a map produce a
small change in the text.

Three rules follow from that.

**Determinism.** The same bytes must always produce the same characters. No
paths, no timestamps, no iteration-order accidents, no locale-dependent
formatting. Two runs on two machines must agree.

**One fact per line.** A moved unit should be one changed line, not a reflowed
block.

**Canonical ordering where order carries no meaning.** Units and sprites are
sorted by content, so inserting one produces a single added line rather than
renumbering everything after it. Triggers, locations and strings keep their file
order, because for them the index *is* the identity -- trigger order is execution
order, and location and string indices are referenced by id from elsewhere in the
map.

Nothing here invents information. Unit and sprite types are printed as numbers
because naming them requires ``units.dat`` from a StarCraft installation, which
this library does not read.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Iterable

from ._tables import ArrayTable
from .chk import Chk
from .enums import (
    ActionType,
    BriefingActionType,
    ConditionType,
    Race,
    SlotType,
    Tileset,
)
from .records import Action, Condition, Trigger
from .restrictions import TOTAL_PLAYERS, restrictions_for
from .settings import SoundPaths, SwitchNames, UnitSettings, settings_for
from .views import (
    Dimensions,
    Forces,
    StringTableView,
    TileGrid,
    isom_for,
    string_table_for,
    terrain_for,
    view_for,
)

__all__ = ["render", "FORMAT_VERSION"]

FORMAT_VERSION = 7
"""Bumped when the output shape changes in a way that would churn every diff.

v2 added the ``[terrain]`` block; v3 added ISOM to it; v4 renamed the
project, which changes the header line on every rendering; v5 added
``[settings]``.
"""

_FORCE_FLAG_NAMES = (
    (0x01, "RandomizeStartLocation"),
    (0x02, "RandomAllies"),
    (0x04, "AlliedVictory"),
    (0x08, "SharedVision"),
)


def _quote(raw: bytes | None) -> str:
    """Render bytes as a deterministic quoted string.

    Printable ASCII passes through; everything else becomes ``\\xNN``. No
    encoding is guessed, because no source states one.
    """
    if raw is None:
        return "-"
    out = ['"']
    for b in raw:
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append("\\\\")
        elif b == 0x09:
            out.append("\\t")
        elif b == 0x0A:
            out.append("\\n")
        elif b == 0x0D:
            out.append("\\r")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    out.append('"')
    return "".join(out)


def _enum_name(enum_cls: type[enum.Enum], value: int) -> str:
    try:
        return str(enum_cls(value).name)
    except ValueError:
        return f"Unknown{value}"


def _flag_names(value: int, table: Iterable[tuple[int, str]]) -> str:
    names = [name for bit, name in table if value & bit]
    known = 0
    for bit, _ in table:
        known |= bit
    if value & ~known:
        names.append(f"unknown:0x{value & ~known:02x}")
    return "|".join(names) if names else "none"


def _string_of(strings: StringTableView | None, string_id: int) -> str:
    if string_id == 0:
        return "-"
    if strings is None:
        return f"#{string_id}"
    raw = strings.get(string_id)
    return f"#{string_id} {_quote(raw)}" if raw is not None else f"#{string_id} <unreadable>"


def _condition_line(condition: Condition) -> str:
    name = _enum_name(ConditionType, condition.condition_type)
    parts = []
    if condition.player:
        parts.append(f"player={condition.player}")
    if condition.amount:
        parts.append(f"amount={condition.amount}")
    if condition.unit_type:
        parts.append(f"unit={condition.unit_type}")
    if condition.location_id:
        parts.append(f"loc={condition.location_id}")
    if condition.comparison:
        parts.append(f"cmp={condition.comparison}")
    if condition.type_index:
        parts.append(f"index={condition.type_index}")
    if condition.flags:
        parts.append(f"flags=0x{condition.flags:02x}")
    if condition.mask_flag:
        parts.append(f"mask=0x{condition.mask_flag:04x}")
    return f"{name}({', '.join(parts)})"


def _action_line(action: Action, strings: StringTableView | None, briefing: bool) -> str:
    enum_cls = BriefingActionType if briefing else ActionType
    name = _enum_name(enum_cls, action.action_type)
    parts = []
    if action.string_id:
        parts.append(f"text={_string_of(strings, action.string_id)}")
    if action.sound_string_id:
        parts.append(f"sound={_string_of(strings, action.sound_string_id)}")
    if action.location_id:
        parts.append(f"loc={action.location_id}")
    if action.group:
        parts.append(f"group={action.group}")
    if action.number:
        parts.append(f"number={action.number}")
    if action.time:
        parts.append(f"time={action.time}")
    if action.type:
        parts.append(f"type={action.type}")
    if action.type2:
        parts.append(f"type2={action.type2}")
    if action.flags:
        parts.append(f"flags=0x{action.flags:02x}")
    if action.padding:
        parts.append(f"padding=0x{action.padding:02x}")
    if action.mask_flag:
        parts.append(f"mask=0x{action.mask_flag:04x}")
    return f"{name}({', '.join(parts)})"


def _owners_summary(trigger: Trigger) -> str:
    from .enums import OWNER_SLOT_LABELS

    parts = []
    for index in trigger.owner_indices():
        label = (
            OWNER_SLOT_LABELS[index]
            if index < len(OWNER_SLOT_LABELS)
            else f"slot{index}"
        )
        value = trigger.owners[index]
        parts.append(label if value == 1 else f"{label}:{value}")
    return ",".join(parts) if parts else "none"


def render(chk: Chk, *, source: str | None = None) -> str:
    """Render ``chk`` as deterministic text.

    ``source`` is included only when given. Leave it out for anything a diff
    consumes -- git passes a temporary filename that changes every invocation.
    """
    out: list[str] = [f"# chklib inspect v{FORMAT_VERSION}"]
    if source is not None:
        out.append(f"# source {source}")

    strings = string_table_for(chk)
    dim: Dimensions | None = view_for(chk, "DIM")
    version = view_for(chk, "VER")
    era = view_for(chk, "ERA")
    sprp = view_for(chk, "SPRP")

    # -- map ---------------------------------------------------------------
    out += ["", "[map]"]
    if version is not None:
        out.append(f"version      {version.value}  {version.name}")
    if era is not None:
        out.append(f"tileset      {era.value}  {_enum_name(Tileset, era.value)}")
    if dim is not None:
        out.append(
            f"dimensions   {dim.tile_width}x{dim.tile_height} tiles"
            f"  ({dim.pixel_width}x{dim.pixel_height} px)"
        )
    if sprp is not None:
        out.append(f"name         {_string_of(strings, sprp.name_string_id)}")
        out.append(f"description  {_string_of(strings, sprp.description_string_id)}")

    # -- players -----------------------------------------------------------
    ownr = view_for(chk, "OWNR")
    iown = view_for(chk, "IOWN")
    side = view_for(chk, "SIDE")
    forces: Forces | None = view_for(chk, "FORC")
    if ownr is not None or side is not None:
        out += ["", "[players]"]
        for player in range(12):
            bits = [f"p{player + 1:<2}"]
            if ownr is not None and player < len(ownr):
                bits.append(f"slot={ownr[player]} {_enum_name(SlotType, ownr[player])}")
            if side is not None and player < len(side):
                bits.append(f"race={side[player]} {_enum_name(Race, side[player])}")
            if forces is not None and player < len(forces.player_force):
                bits.append(f"force={forces.player_force[player] + 1}")
            out.append("  ".join(bits))
        if (iown is not None and ownr is not None
                and bytes(iown.slot_types) != bytes(ownr.slot_types)):
            # No source states a precedence rule, so a disagreement is surfaced.
            out.append(f"note         IOWN differs from OWNR: {list(iown.slot_types)}")

    # -- forces ------------------------------------------------------------
    if forces is not None:
        out += ["", "[forces]"]
        for index in range(4):
            name = _string_of(strings, forces.force_string_ids[index])
            flags = forces.flags[index]
            members = ",".join(f"p{p + 1}" for p in forces.players_in(index)) or "none"
            out.append(
                f"force{index + 1}  {name}  flags=0x{flags:02x} "
                f"{_flag_names(flags, _FORCE_FLAG_NAMES)}  players={members}"
            )

    # -- sections ----------------------------------------------------------
    out += ["", f"[sections] {len(chk)}"]
    for section in chk:
        marker = "  TRUNCATED" if section.is_truncated else ""
        out.append(f"{section.label:<5} {len(section.data):>8}{marker}")
    if chk.trailing:
        out.append(f"{'<tail>':<5} {len(chk.trailing):>8}")
    for diagnostic in chk.diagnostics:
        out.append(f"! {diagnostic}")

    # -- strings -----------------------------------------------------------
    if strings is not None:
        used = strings.used_ids()
        out += ["", f"[strings] {len(used)} used of {strings.count} slots"]
        for string_id in used:
            out.append(f"{string_id:>5}  {_quote(strings.get(string_id))}")

    # -- locations ---------------------------------------------------------
    mrgn = view_for(chk, "MRGN")
    if mrgn is not None:
        used_locations = [
            (i, loc) for i, loc in enumerate(mrgn) if not loc.is_unused_slot
        ]
        out += ["", f"[locations] {len(used_locations)} used of {len(mrgn)} slots"]
        for index, loc in used_locations:
            # Location ids are 1-based: file record k is trigger location k+1.
            out.append(
                f"{index + 1:>5}  {_string_of(strings, loc.string_id):<28}"
                f"  ({loc.left},{loc.top})-({loc.right},{loc.bottom})"
                f"  elev=0x{loc.elevation_flags:04x}"
            )

    # -- terrain -----------------------------------------------------------
    # A 256x256 map has 65,536 tiles, so dumping them all would bury every other
    # change in the file. Instead each row gets a short digest: editing one tile
    # changes exactly one line, and the line number localises the edit to a row.
    # The summary above it is what a human actually reads.
    terrain_lines: list[str] = []
    for name in ("MTXM", "TILE", "MASK"):
        grid = terrain_for(chk, name)
        if grid is None:
            continue
        addressable = grid.width * grid.height
        notes = []
        if grid.is_short:
            notes.append(f"short: {grid.stored_cells} of {addressable} cells")
        if grid.stored_cells > addressable:
            # Without this a malformed DIM makes a full section render as an
            # empty grid, with nothing to say its content exists at all. The
            # digest matters as much as the count: a bare count does not move
            # when the unreachable bytes themselves change, so a diff would show
            # nothing for an edit out there.
            tail = grid.source[addressable * grid.CELL :]
            notes.append(
                f"{grid.stored_cells - addressable} cells beyond the map are not "
                f"addressable, digest {hashlib.sha1(tail).hexdigest()[:8]}"
            )
        if grid.has_odd_tail:
            notes.append("odd trailing byte")
        if grid.clamped_dimensions:
            declared = "x".join(str(v) for v in grid.clamped_dimensions)
            notes.append(f"DIM declared {declared}, clamped")
        if grid.merged_sections > 1:
            notes.append(f"merged from {grid.merged_sections} sections")
        summary = f"{name}  {grid.width}x{grid.height}"
        if isinstance(grid, TileGrid):
            summary += f"  {len(grid.groups())} megatile groups"
        summary += f"  {len(set(grid.cells))} distinct values"
        if notes:
            summary += "  [" + "; ".join(notes) + "]"
        terrain_lines.append(summary)
        for y in range(grid.height):
            digest = hashlib.sha1(
                b",".join(b"%d" % v for v in grid.row(y))
            ).hexdigest()[:8]
            terrain_lines.append(f"  {name} row {y:>3}  {digest}")
    isom = isom_for(chk)
    if isom is not None:
        notes = []
        if isom.is_short:
            notes.append(f"short: {isom.stored_records} of {len(isom)} records")
        if len(isom.raw) > isom.expected_size:
            notes.append(f"{len(isom.raw) - isom.expected_size} bytes past the grid")
        if isom.has_editor_flags:
            # Chkdraft clears these after every edit pass, so their presence
            # says the file came from somewhere else, or was saved mid-edit.
            notes.append("carries editor flags")
        summary = f"ISOM  {isom.width}x{isom.height} records"
        if notes:
            summary += "  [" + "; ".join(notes) + "]"
        terrain_lines.append(summary)
        for y in range(isom.height):
            row = isom.rects[y * isom.width : (y + 1) * isom.width]
            digest = hashlib.sha1(b"".join(r.to_bytes() for r in row)).hexdigest()[:8]
            terrain_lines.append(f"  ISOM row {y:>3}  {digest}")

    if terrain_lines:
        out += ["", "[terrain]", *terrain_lines]

    # -- settings ----------------------------------------------------------
    # Only customised entries are listed. These tables are ~4 KB of mostly
    # defaults, and printing 228 unchanged unit rows would bury the handful a
    # mapper actually altered.
    #
    # Every section also carries a digest of its bytes, because the rows alone
    # cannot be complete: the weapon damage arrays are indexed by weapon rather
    # than by unit and have no flag to select interesting ones, and an oversized
    # section's trailing bytes are not modelled at all. Without the digest a
    # change to either diffs as no change, which for a textconv driver is worse
    # than being verbose.
    settings_lines: list[str] = []

    def _digest(table: ArrayTable) -> str:
        return hashlib.sha1(table.to_bytes()).hexdigest()[:8]

    def _note(table: ArrayTable) -> str:
        if table.is_short:
            return "  [short]"
        if table.is_oversized:
            return f"  [+{len(table.trailing_bytes)} trailing]"
        return ""

    for name in ("UNIS", "UNIx", "UPGS", "UPGx", "TECS", "TECx"):
        table = settings_for(chk, name)
        if table is None:
            continue
        use_default = table["use_default"]
        custom = [i for i, v in enumerate(use_default) if not v]
        settings_lines.append(
            f"{name}  {len(custom)} of {len(use_default)} customised"
            f"{_note(table)}  sha={_digest(table)}"
        )
        for index in custom:
            bits = [f"  {name} {index:>3}"]
            if isinstance(table, UnitSettings):
                bits.append(
                    f"hp={UnitSettings.displayed_hitpoints(table['hitpoints'][index])}"
                )
                bits.append(f"shield={table['shield_points'][index]}")
                bits.append(f"armor={table['armor_level'][index]}")
                bits.append(
                    f"cost={table['mineral_cost'][index]}m/{table['gas_cost'][index]}g"
                )
                bits.append(f"build={table['build_time'][index]}")
                named = table.custom_name_id(index)
                if named:
                    bits.append(f"name={_string_of(strings, named)}")
            elif "base_mineral_cost" in table:
                # Upgrades: each cost is a base plus a per-level increment, and
                # a map that alters only the increment is a real edit.
                bits.append(
                    f"mineral={table['base_mineral_cost'][index]}"
                    f"+{table['mineral_cost_factor'][index]}"
                )
                bits.append(
                    f"gas={table['base_gas_cost'][index]}"
                    f"+{table['gas_cost_factor'][index]}"
                )
                bits.append(
                    f"time={table['base_research_time'][index]}"
                    f"+{table['research_time_factor'][index]}"
                )
            else:
                bits.append(f"mineral={table['mineral_cost'][index]}")
                bits.append(f"gas={table['gas_cost'][index]}")
                bits.append(f"time={table['research_time'][index]}")
                bits.append(f"energy={table['energy_cost'][index]}")
            settings_lines.append("  ".join(bits))

    for name, label in (("WAV", "sounds"), ("SWNM", "switches")):
        table = settings_for(chk, name)
        if table is None:
            continue
        # Through the named accessors rather than the arrays dict, whose first
        # entry is only coincidentally the one wanted.
        if isinstance(table, SoundPaths):
            ids, used = table.sound_string_ids, table.used_slots()
        elif isinstance(table, SwitchNames):
            ids, used = table.switch_string_ids, table.named_switches()
        else:  # pragma: no cover - the loop only visits those two
            continue
        settings_lines.append(
            f"{name}  {len(used)} {label} named{_note(table)}  sha={_digest(table)}"
        )
        for index in used:
            settings_lines.append(
                f"  {name} {index:>3}  {_string_of(strings, ids[index])}"
            )

    if settings_lines:
        out += ["", "[settings]", *settings_lines]

    # -- restrictions ------------------------------------------------------
    # Per-player, and mostly defaults, so the same rule as the settings block:
    # summarise, then name only what a mapper actually changed. A player is
    # "customised" when some entry of theirs is not left to the game's default.
    restriction_lines: list[str] = []
    for name in ("PUNI", "UPGR", "PUPx", "PTEC", "PTEx"):
        table = restrictions_for(chk, name)
        if table is None:
            continue
        flag_field = next(
            f for f, _c, _n in type(table).LAYOUT if f.endswith("uses_default")
            or f.startswith("player_uses_defaults")
        )
        custom = table.customised_players(flag_field)
        restriction_lines.append(
            f"{name}  {len(custom)} of {TOTAL_PLAYERS} players customised"
            f"{_note(table)}  sha={_digest(table)}"
        )
        for player in custom:
            player_row = table[flag_field][
                player * table.PER_PLAYER:(player + 1) * table.PER_PLAYER
            ]
            entries = [i for i, v in enumerate(player_row) if not v]
            shown = ", ".join(str(i) for i in entries[:12])
            more = f" (+{len(entries) - 12} more)" if len(entries) > 12 else ""
            restriction_lines.append(
                f"  {name} player {player:>2}  "
                f"{len(entries)} custom {table.ENTITY}(s): {shown}{more}"
            )

    if restriction_lines:
        out += ["", "[restrictions]", *restriction_lines]

    # -- everything else ---------------------------------------------------
    # A digest per remaining typed section. These carry no field a reader would
    # scan for, but they do change, and for a textconv driver an invisible
    # change is worse than a noisy one -- a repainted doodad or a swapped player
    # colour has to show up as *something*.
    other_lines: list[str] = []
    for name in ("TYPE", "IVER", "IVE2", "VCOD", "COLR", "CRGB",
                 "UPRP", "UPUS", "DD2"):
        other = chk.last(name)
        if other is None:
            continue
        data = bytes(other.data)
        note = ""
        if name == "VCOD":
            view = view_for(chk, "VCOD")
            note = "  standard" if view is not None and view.is_standard else "  CUSTOM"
        elif name == "DD2":
            whole, part = divmod(len(data), 8)
            note = f"  {whole} doodads" + (f" +{part} trailing bytes" if part else "")
        elif name == "UPUS":
            view = view_for(chk, "UPUS")
            note = f"  {len(view.used_slots())} slots used" if view is not None else ""
        elif name == "TYPE":
            note = f"  {data.decode('latin-1', 'replace')}"
        other_lines.append(
            f"{name:<5} {len(data):>5} bytes  "
            f"sha={hashlib.sha1(data).hexdigest()[:8]}{note}"
        )

    if other_lines:
        out += ["", "[other sections]", *other_lines]

    # -- units -------------------------------------------------------------
    units = view_for(chk, "UNIT")
    if units is not None:
        out += ["", f"[units] {len(units)}  (sorted by owner,type,x,y for diff stability)"]
        for unit in sorted(
            units, key=lambda u: (u.owner, u.type, u.xc, u.yc, u.class_id)
        ):
            bits = [
                f"p{unit.owner + 1:<3}",
                f"type={unit.type:<4}",
                f"at=({unit.xc},{unit.yc})",
            ]
            if unit.valid_field_flags & 0x02:
                bits.append(f"hp={unit.hitpoint_percent}%")
            if unit.valid_field_flags & 0x04:
                bits.append(f"shield={unit.shield_percent}%")
            if unit.valid_field_flags & 0x08:
                bits.append(f"energy={unit.energy_percent}%")
            if unit.valid_field_flags & 0x10:
                bits.append(f"resources={unit.resource_amount}")
            if unit.valid_field_flags & 0x20:
                bits.append(f"hangar={unit.hangar_amount}")
            if unit.state_flags:
                bits.append(f"state=0x{unit.state_flags:04x}")
            if unit.relation_flags:
                bits.append(
                    f"relation=0x{unit.relation_flags:04x}->{unit.relation_class_id}"
                )
            if unit.unused:
                bits.append(f"unused=0x{unit.unused:08x}")
            out.append("  ".join(bits))
        if units.has_partial_record:
            out.append(f"! trailing partial record: {len(units.trailing)} bytes")

    # -- sprites -----------------------------------------------------------
    sprites = view_for(chk, "THG2")
    if sprites is not None:
        out += ["", f"[sprites] {len(sprites)}  (sorted by owner,type,x,y)"]
        for sprite in sorted(
            sprites, key=lambda s: (s.owner, s.type, s.xc, s.yc, s.flags)
        ):
            kind = "unit" if sprite.is_sprite_unit else "sprite"
            bits = [
                f"p{sprite.owner + 1:<3}",
                f"type={sprite.type:<4}",
                f"at=({sprite.xc},{sprite.yc})",
                f"as={kind}",
                f"flags=0x{sprite.flags:04x}",
            ]
            if sprite.unused:
                bits.append(f"unused=0x{sprite.unused:02x}")
            out.append("  ".join(bits))

    # -- triggers ----------------------------------------------------------
    for name, label, item in (
        ("TRIG", "triggers", "trigger"),
        ("MBRF", "briefing", "briefing"),
    ):
        view = view_for(chk, name)
        if view is None:
            continue
        out += ["", f"[{label}] {len(view)}"]
        for index, trigger in enumerate(view):
            head = f"{item} {index}  owners={_owners_summary(trigger)}"
            if trigger.flags:
                head += f"  flags=0x{trigger.flags:08x}"
            if trigger.current_action:
                head += f"  currentAction={trigger.current_action}"
            out.append(head)
            for condition in trigger.used_conditions():
                prefix = "  if- " if condition.is_disabled else "  if  "
                out.append(prefix + _condition_line(condition))
            for action in trigger.used_actions():
                prefix = "  do- " if action.is_disabled else "  do  "
                out.append(prefix + _action_line(action, strings, view.is_briefing))
        if view.has_partial_trigger:
            out.append(f"! trailing partial trigger: {len(view.trailing)} bytes")

    out.append("")
    return "\n".join(out)
