"""Semantic comparison of two scenarios.

A byte diff of two ``.chk`` files is nearly useless: inserting one string shifts
every offset after it, and a single new trigger moves 2400 bytes of everything
downstream. So this compares *meaning* -- per section, using whatever notion of
identity that section actually has.

Sections fall into three groups, and each needs different handling:

**Identity by index.** Locations and strings are referenced by id from elsewhere
in the map, so location 7 in one file is location 7 in the other. Compare
position by position.

**No identity at all.** Units and sprites carry no id and their file order is not
meaningful. Compare them as multisets, then pair the leftovers by
``(owner, type)`` so that moving a unit reads as a change rather than as an
unrelated deletion and addition.

**Identity by content and position.** Triggers are the hard case: they have no
ids, but their order *is* execution order, so they cannot be treated as a set
either. Inserting one trigger at the top must not report every later trigger as
modified.

The trigger algorithm is an LCS alignment (``difflib.SequenceMatcher``) over
per-trigger content hashes. Identical triggers align regardless of how much was
inserted around them. Within each ``replace`` block the survivors are paired
greedily by similarity, so an edited trigger is reported as one modification
rather than as a delete plus an add. Anything left over is a genuine add or
delete. This degrades gracefully: in the worst case it reports a whole block as
added and removed, which is correct, just less informative.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .chk import Chk
from .enums import Race, SlotType, Tileset
from .inspect import _action_line, _condition_line, _enum_name, _quote
from .records import Location, Sprite, Trigger, Unit
from .restrictions import restrictions_for
from .settings import SoundPaths, SwitchNames, settings_for
from .views import TYPED_SECTIONS, StringTableView, string_table_for, view_for

__all__ = ["Change", "DiffReport", "diff", "JSON_SCHEMA_VERSION"]

JSON_SCHEMA_VERSION = 1

#: Below this similarity two triggers are treated as unrelated rather than as
#: an edit of one another. Chosen so that triggers sharing only their owners do
#: not get paired, while a trigger with one changed action does.
TRIGGER_PAIR_THRESHOLD = 0.55


@dataclass(frozen=True, slots=True)
class Change:
    """One reported difference."""

    area: str
    """Where it lives: ``map``, ``players``, ``UNIT``, ``TRIG`` ..."""

    kind: str
    """``added`` | ``removed`` | ``changed``"""

    key: str
    """Stable identifier within ``area``, e.g. ``trigger 12`` or ``string 44``."""

    before: str | None = None
    after: str | None = None
    detail: str = ""

    def to_text(self) -> str:
        mark = {"added": "+", "removed": "-", "changed": "~"}.get(self.kind, "?")
        head = f"{mark} {self.area}  {self.key}"
        if self.detail:
            head += f"  {self.detail}"
        lines = [head]
        if self.kind == "changed":
            lines.append(f"    - {self.before}")
            lines.append(f"    + {self.after}")
        elif self.kind == "removed" and self.before is not None:
            lines.append(f"    - {self.before}")
        elif self.kind == "added" and self.after is not None:
            lines.append(f"    + {self.after}")
        return "\n".join(lines)


@dataclass
class DiffReport:
    """The full comparison."""

    changes: list[Change] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def add(self, *args: str, **kwargs: str) -> None:
        self.changes.append(Change(*args, **kwargs))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for change in self.changes:
            out[change.kind] = out.get(change.kind, 0) + 1
        return out

    def to_text(self) -> str:
        if self.is_empty:
            return "no differences\n"
        lines = [c.to_text() for c in self.changes]
        counts = self.counts()
        summary = ", ".join(f"{counts[k]} {k}" for k in sorted(counts))
        lines.append("")
        lines.append(f"{len(self.changes)} differences ({summary})")
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": JSON_SCHEMA_VERSION,
                "summary": {"total": len(self.changes), **self.counts()},
                "changes": [asdict(c) for c in self.changes],
            },
            indent=2,
            sort_keys=False,
        ) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _string(view: StringTableView | None, string_id: int) -> str:
    if string_id == 0:
        return "-"
    if view is None:
        return f"#{string_id}"
    raw = view.get(string_id)
    return f"#{string_id} {_quote(raw)}" if raw is not None else f"#{string_id} <unreadable>"


def _scalar(
    report: DiffReport, area: str, key: str, before: object, after: object
) -> None:
    if before != after:
        report.add(area, "changed", key, before=str(before), after=str(after))


# ---------------------------------------------------------------------------
# Section comparisons
# ---------------------------------------------------------------------------


def _diff_sections(report: DiffReport, a: Chk, b: Chk) -> None:
    """Structural comparison of the section list itself."""
    names_a = [s.label for s in a]
    names_b = [s.label for s in b]
    if names_a != names_b:
        set_a, set_b = set(names_a), set(names_b)
        for name in sorted(set_a - set_b):
            report.add("sections", "removed", name)
        for name in sorted(set_b - set_a):
            report.add("sections", "added", name)
        if set_a == set_b:
            report.add(
                "sections", "changed", "order",
                before=" ".join(names_a), after=" ".join(names_b),
            )
    sizes_a = {s.label: len(s.data) for s in a}
    sizes_b = {s.label: len(s.data) for s in b}
    for name in sorted(set(sizes_a) & set(sizes_b)):
        if sizes_a[name] != sizes_b[name]:
            report.add(
                "sections", "changed", f"{name} size",
                before=f"{sizes_a[name]} bytes", after=f"{sizes_b[name]} bytes",
            )


def _diff_map(report: DiffReport, a: Chk, b: Chk,
              sa: StringTableView | None, sb: StringTableView | None) -> None:
    va, vb = view_for(a, "VER"), view_for(b, "VER")
    if va and vb:
        _scalar(report, "map", "version", f"{va.value} {va.name}", f"{vb.value} {vb.name}")
    ea, eb = view_for(a, "ERA"), view_for(b, "ERA")
    if ea and eb:
        _scalar(report, "map", "tileset",
                f"{ea.value} {_enum_name(Tileset, ea.value)}",
                f"{eb.value} {_enum_name(Tileset, eb.value)}")
    da, db = view_for(a, "DIM"), view_for(b, "DIM")
    if da and db:
        _scalar(report, "map", "dimensions", str(da), str(db))
    pa, pb = view_for(a, "SPRP"), view_for(b, "SPRP")
    if pa and pb:
        _scalar(report, "map", "name",
                _string(sa, pa.name_string_id), _string(sb, pb.name_string_id))
        _scalar(report, "map", "description",
                _string(sa, pa.description_string_id), _string(sb, pb.description_string_id))


def _diff_players(report: DiffReport, a: Chk, b: Chk) -> None:
    for section, label, enum_cls in (
        ("OWNR", "slot", SlotType),
        ("IOWN", "iown slot", SlotType),
        ("SIDE", "race", Race),
    ):
        va, vb = view_for(a, section), view_for(b, section)
        if va is None or vb is None:
            continue
        raw_a = va.slot_types if section != "SIDE" else va.races
        raw_b = vb.slot_types if section != "SIDE" else vb.races
        for player in range(min(len(raw_a), len(raw_b))):
            if raw_a[player] != raw_b[player]:
                report.add(
                    "players", "changed", f"p{player + 1} {label}",
                    before=f"{raw_a[player]} {_enum_name(enum_cls, raw_a[player])}",
                    after=f"{raw_b[player]} {_enum_name(enum_cls, raw_b[player])}",
                )


def _diff_forces(report: DiffReport, a: Chk, b: Chk,
                 sa: StringTableView | None, sb: StringTableView | None) -> None:
    fa, fb = view_for(a, "FORC"), view_for(b, "FORC")
    if fa is None or fb is None:
        return
    for index in range(4):
        _scalar(report, "forces", f"force{index + 1} name",
                _string(sa, fa.force_string_ids[index]),
                _string(sb, fb.force_string_ids[index]))
        _scalar(report, "forces", f"force{index + 1} flags",
                f"0x{fa.flags[index]:02x}", f"0x{fb.flags[index]:02x}")
        _scalar(report, "forces", f"force{index + 1} members",
                ",".join(f"p{p + 1}" for p in fa.players_in(index)) or "none",
                ",".join(f"p{p + 1}" for p in fb.players_in(index)) or "none")


def _diff_strings(report: DiffReport, sa: StringTableView | None,
                  sb: StringTableView | None) -> None:
    """Strings are compared by id, because ids are positional and referenced."""
    if sa is None or sb is None:
        return
    for string_id in range(1, max(sa.count, sb.count) + 1):
        before, after = sa.get(string_id), sb.get(string_id)
        if before == after:
            continue
        if not before and after:
            report.add("strings", "added", f"string {string_id}", after=_quote(after))
        elif before and not after:
            report.add("strings", "removed", f"string {string_id}", before=_quote(before))
        else:
            report.add("strings", "changed", f"string {string_id}",
                       before=_quote(before), after=_quote(after))


def _diff_locations(report: DiffReport, a: Chk, b: Chk,
                    sa: StringTableView | None, sb: StringTableView | None) -> None:
    va, vb = view_for(a, "MRGN"), view_for(b, "MRGN")
    if va is None or vb is None:
        return

    def describe(loc: Location, strings: StringTableView | None) -> str:
        return (
            f"{_string(strings, loc.string_id)} "
            f"({loc.left},{loc.top})-({loc.right},{loc.bottom}) "
            f"elev=0x{loc.elevation_flags:04x}"
        )

    for index in range(max(len(va), len(vb))):
        la = va[index] if index < len(va) else None
        lb = vb[index] if index < len(vb) else None
        used_a = la is not None and not la.is_unused_slot
        used_b = lb is not None and not lb.is_unused_slot
        if not used_a and not used_b:
            continue
        # Location ids are 1-based: file record k is location id k+1.
        key = f"location {index + 1}"
        # `used_a`/`used_b` already imply non-None, but spelling the check out
        # is what lets a reader -- and a type checker -- see that, at no
        # runtime cost.
        if la is not None and used_a and not used_b:
            report.add("MRGN", "removed", key, before=describe(la, sa))
        elif lb is not None and used_b and not used_a:
            report.add("MRGN", "added", key, after=describe(lb, sb))
        elif la is not None and lb is not None and la.to_bytes() != lb.to_bytes():
            report.add("MRGN", "changed", key,
                       before=describe(la, sa), after=describe(lb, sb))


def _unit_key(unit: Unit) -> tuple[int, int]:
    return (unit.owner, unit.type)


def _unit_describe(unit: Unit) -> str:
    bits = [f"p{unit.owner + 1}", f"type={unit.type}", f"at=({unit.xc},{unit.yc})"]
    if unit.valid_field_flags & 0x02:
        bits.append(f"hp={unit.hitpoint_percent}%")
    if unit.valid_field_flags & 0x10:
        bits.append(f"resources={unit.resource_amount}")
    if unit.state_flags:
        bits.append(f"state=0x{unit.state_flags:04x}")
    return "  ".join(bits)


def _diff_units(
    report: DiffReport, a: Chk, b: Chk, section: str,
    # Generic over UNIT and THG2 records, which share owner/type/xc/yc but
    # no subclass beyond Record; the helpers passed in are each specific.
    describe: Callable[[Any], str],
    key_of: Callable[[Any], tuple[int, int]],
) -> None:
    """Multiset comparison, then pair leftovers so a move reads as a change."""
    va, vb = view_for(a, section), view_for(b, section)
    if va is None or vb is None:
        return

    # Identical records cancel out first, so only genuine differences remain.
    remaining_a = list(va)
    remaining_b = list(vb)
    bytes_b: dict[bytes, list[Any]] = {}
    for record in remaining_b:
        bytes_b.setdefault(record.to_bytes(), []).append(record)
    survivors_a = []
    for record in remaining_a:
        bucket = bytes_b.get(record.to_bytes())
        if bucket:
            bucket.pop()
        else:
            survivors_a.append(record)
    survivors_b = [r for bucket in bytes_b.values() for r in bucket]

    # Pair the leftovers within (owner, type) groups, ordered by position so the
    # pairing is deterministic.
    groups: dict[tuple[int, int], tuple[list[Any], list[Any]]] = {}
    for record in survivors_a:
        groups.setdefault(key_of(record), ([], []))[0].append(record)
    for record in survivors_b:
        groups.setdefault(key_of(record), ([], []))[1].append(record)

    for group_key in sorted(groups):
        left, right = groups[group_key]
        left.sort(key=lambda r: (r.xc, r.yc))
        right.sort(key=lambda r: (r.xc, r.yc))
        # Lengths differ whenever a group gained or lost records; the
        # leftovers on each side are reported just below.
        for old, new in zip(left, right, strict=False):
            report.add(section, "changed", describe_key(group_key),
                       before=describe(old), after=describe(new))
        for old in left[len(right):]:
            report.add(section, "removed", describe_key(group_key), before=describe(old))
        for new in right[len(left):]:
            report.add(section, "added", describe_key(group_key), after=describe(new))


def describe_key(group_key: tuple[int, int]) -> str:
    owner, type_id = group_key
    return f"p{owner + 1} type={type_id}"


def _sprite_key(sprite: Sprite) -> tuple[int, int]:
    return (sprite.owner, sprite.type)


def _sprite_describe(sprite: Sprite) -> str:
    kind = "unit" if sprite.is_sprite_unit else "sprite"
    return (
        f"p{sprite.owner + 1}  type={sprite.type}  at=({sprite.xc},{sprite.yc})"
        f"  as={kind}  flags=0x{sprite.flags:04x}"
    )


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def _trigger_signature(trigger: Trigger) -> str:
    """A hashable identity for LCS alignment. Identical triggers hash alike."""
    return hashlib.sha1(trigger.to_bytes()).hexdigest()


def _trigger_tokens(
    trigger: Trigger, strings: StringTableView | None, briefing: bool
) -> list[str]:
    """The comparable content of a trigger, one token per line."""
    tokens = [f"owners={sorted(trigger.owner_indices())}", f"flags={trigger.flags}"]
    tokens += [f"if {_condition_line(c)}" for c in trigger.used_conditions()]
    tokens += [f"do {_action_line(a, strings, briefing)}" for a in trigger.used_actions()]
    return tokens


def _similarity(x: Sequence[str], y: Sequence[str]) -> float:
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()


def _diff_trigger_pair(report: DiffReport, area: str, key: str,
                       tokens_a: list[str], tokens_b: list[str]) -> None:
    """Report the token-level differences between two paired triggers."""
    matcher = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            # A replace opcode can cover ranges of different lengths;
            # extra_a/extra_b below carry whichever side is longer.
            for old, new in zip(tokens_a[i1:i2], tokens_b[j1:j2], strict=False):
                report.add(area, "changed", key, before=old, after=new)
            extra_a = tokens_a[i1 + min(i2 - i1, j2 - j1):i2]
            extra_b = tokens_b[j1 + min(i2 - i1, j2 - j1):j2]
            for old in extra_a:
                report.add(area, "removed", key, before=old)
            for new in extra_b:
                report.add(area, "added", key, after=new)
        elif tag == "delete":
            for old in tokens_a[i1:i2]:
                report.add(area, "removed", key, before=old)
        elif tag == "insert":
            for new in tokens_b[j1:j2]:
                report.add(area, "added", key, after=new)


def _diff_triggers(report: DiffReport, a: Chk, b: Chk, section: str,
                   sa: StringTableView | None, sb: StringTableView | None) -> None:
    va, vb = view_for(a, section), view_for(b, section)
    if va is None or vb is None:
        return
    briefing = section == "MBRF"
    list_a, list_b = list(va), list(vb)
    sig_a = [_trigger_signature(t) for t in list_a]
    sig_b = [_trigger_signature(t) for t in list_b]
    tok_a = [_trigger_tokens(t, sa, briefing) for t in list_a]
    tok_b = [_trigger_tokens(t, sb, briefing) for t in list_b]

    matcher = difflib.SequenceMatcher(None, sig_a, sig_b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for index in range(i1, i2):
                report.add(section, "removed", f"trigger {index}",
                           before=" | ".join(tok_a[index]))
            continue
        if tag == "insert":
            for index in range(j1, j2):
                report.add(section, "added", f"trigger {index}",
                           after=" | ".join(tok_b[index]))
            continue

        # replace: pair survivors greedily by similarity, best matches first.
        candidates = []
        for ia in range(i1, i2):
            for ib in range(j1, j2):
                ratio = _similarity(tok_a[ia], tok_b[ib])
                if ratio >= TRIGGER_PAIR_THRESHOLD:
                    candidates.append((-ratio, ia, ib))
        candidates.sort()
        used_a: set[int] = set()
        used_b: set[int] = set()
        pairs: list[tuple[int, int]] = []
        for _, ia, ib in candidates:
            if ia in used_a or ib in used_b:
                continue
            used_a.add(ia)
            used_b.add(ib)
            pairs.append((ia, ib))
        for ia, ib in sorted(pairs):
            key = f"trigger {ia}" if ia == ib else f"trigger {ia}->{ib}"
            _diff_trigger_pair(report, section, key, tok_a[ia], tok_b[ib])
        for ia in range(i1, i2):
            if ia not in used_a:
                report.add(section, "removed", f"trigger {ia}",
                           before=" | ".join(tok_a[ia]))
        for ib in range(j1, j2):
            if ib not in used_b:
                report.add(section, "added", f"trigger {ib}",
                           after=" | ".join(tok_b[ib]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


#: What one entry of each settings table is called, for readable diff keys.
def _tail(data: bytes) -> str:
    """Describe an uninterpreted trailing tail.

    Nothing in this library decodes the bytes an oversized settings section
    carries past its layout, so this reports a length and a digest rather than a
    decode. The digest is what makes the line say something: a tail that changes
    without changing length would otherwise report "1 bytes" against "1 bytes",
    announcing a difference and then showing none.
    """
    return f"{len(data)} bytes sha={hashlib.sha1(data).hexdigest()[:8]}"


def _diff_tables(report: DiffReport, a: Chk, b: Chk,
                 sa: StringTableView | None, sb: StringTableView | None) -> None:
    """Compare the settings and restriction tables entry by entry.

    Without this a map whose only change is a unit's hitpoints or an upgrade's
    cost diffs as no change at all: ``_diff_sections`` compares section names,
    order and sizes, and a settings edit alters none of the three. ``inspect``
    renders those edits, so leaving them out here would have the two consumers
    of the same views disagree about whether anything happened.

    Fields are grouped by element count so that the unit tables' weapon arrays,
    which are indexed by weapon rather than by unit, report against a weapon
    number instead of being forced into a unit row.
    """
    lookups = [
        (name, settings_for)
        for name in ("UNIS", "UNIx", "UPGS", "UPGx", "TECS", "TECx", "WAV", "SWNM")
    ] + [
        (name, restrictions_for)
        for name in ("PUNI", "UPGR", "PUPx", "PTEC", "PTEx")
    ]
    for name, lookup in lookups:
        table_a = lookup(a, name)
        table_b = lookup(b, name)
        if table_a is None or table_b is None:
            # A section present on only one side is already reported by
            # _diff_sections as an added or removed section.
            continue

        table_cls = type(table_a)
        # Grouped by what one index *means*, which each table declares, rather
        # than by array length. Length cannot tell the cases apart: UPGx's pad
        # byte is the one field whose count differs from the upgrade count, so
        # inferring from length reports a changed pad byte as "weapon 0" in a
        # section that models no weapons at all.
        groups: dict[tuple[str, int], list[str]] = {}
        for field_name, _code, count in table_cls.LAYOUT:
            label = table_cls.INDEXED_BY.get(field_name, table_cls.ENTITY)
            groups.setdefault((label, count), []).append(field_name)

        for (_label, count), fields in groups.items():
            for index in range(count):
                changed = [
                    (f, table_a[f][index], table_b[f][index])
                    for f in fields
                    if table_a[f][index] != table_b[f][index]
                ]
                if not changed:
                    continue
                key = table_cls.index_label(fields[0], index)
                if isinstance(table_a, (SoundPaths, SwitchNames)):
                    _, before_id, after_id = changed[0]
                    report.add(
                        name, "changed", key,
                        before=_string(sa, before_id), after=_string(sb, after_id),
                    )
                    continue
                report.add(
                    name, "changed", key,
                    before=" ".join(f"{f}={x}" for f, x, _ in changed),
                    after=" ".join(f"{f}={y}" for f, _, y in changed),
                )

        if table_a.trailing_bytes != table_b.trailing_bytes:
            report.add(
                name, "changed", "trailing bytes",
                before=_tail(table_a.trailing_bytes),
                after=_tail(table_b.trailing_bytes),
            )


#: Sections some function above compares field by field. Anything typed but
#: absent from this set falls through to _diff_opaque, so no byte change is
#: silently invisible. ``test_diff`` asserts the two sets together cover
#: TYPED_SECTIONS, which is what keeps this list from rotting as sections are
#: added.
_SEMANTICALLY_DIFFED = frozenset({
    "VER", "ERA", "DIM", "SPRP",
    "OWNR", "IOWN", "SIDE", "FORC",
    "STR", "STRx",
    "MRGN", "UNIT", "THG2", "TRIG", "MBRF",
    "UNIS", "UNIx", "UPGS", "UPGx", "TECS", "TECx", "WAV", "SWNM",
    "PUNI", "UPGR", "PUPx", "PTEC", "PTEx",
})


def _diff_opaque(report: DiffReport, a: Chk, b: Chk) -> None:
    """Report a content change in a typed section nothing above compares.

    ``_diff_sections`` notices names, order and sizes, and a great many edits
    change none of the three. A terrain edit is the clearest case: repainting a
    tile rewrites ``MTXM`` in place, so before this the whole map diffed as no
    differences at all. The same held for a moved doodad, a swapped player
    colour and a replaced ``VCOD``.

    These are reported by digest and a differing-byte count rather than decoded.
    That is deliberately weaker than the comparisons above -- it says something
    changed and how much, not what -- but a weak true report beats a confident
    empty one.
    """
    for name in sorted(set(TYPED_SECTIONS) - _SEMANTICALLY_DIFFED):
        section_a, section_b = a.last(name), b.last(name)
        if section_a is None or section_b is None:
            # Present on one side only: already an added or removed section.
            continue
        before, after = bytes(section_a.data), bytes(section_b.data)
        if before == after:
            continue
        differing = sum(1 for x, y in zip(before, after, strict=False) if x != y)
        differing += abs(len(before) - len(after))
        report.add(
            name, "changed", "content",
            before=_tail(before), after=_tail(after),
            detail=f"{differing} of {max(len(before), len(after))} bytes differ",
        )


def diff(a: Chk, b: Chk) -> DiffReport:
    """Compare two scenarios semantically."""
    report = DiffReport()
    sa: StringTableView | None = string_table_for(a)
    sb: StringTableView | None = string_table_for(b)

    _diff_map(report, a, b, sa, sb)
    _diff_players(report, a, b)
    _diff_forces(report, a, b, sa, sb)
    _diff_strings(report, sa, sb)
    _diff_locations(report, a, b, sa, sb)
    _diff_units(report, a, b, "UNIT", _unit_describe, _unit_key)
    _diff_units(report, a, b, "THG2", _sprite_describe, _sprite_key)
    _diff_triggers(report, a, b, "TRIG", sa, sb)
    _diff_triggers(report, a, b, "MBRF", sa, sb)
    _diff_tables(report, a, b, sa, sb)
    _diff_opaque(report, a, b)
    _diff_sections(report, a, b)
    return report
