"""Milestone-2 gate: typed round-trip over real maps.

For every section this library interprets, parsing it into a typed view and
writing it straight back must reproduce the section's bytes exactly. That is the
property that separates a library you can edit maps with from one that quietly
rewrites the parts it did not understand.

The second half re-checks, through this implementation, the empirical claims the
format research made about this corpus. Those claims were produced by scripts
that parse the bytes independently, so agreement is a genuine cross-check rather
than a restatement.

Fixtures are gitignored; run ``tools/extract_fixtures.py`` first or these skip.
"""

from __future__ import annotations

import pathlib

import pytest

from chklib import Chk
from chklib.records import Location, Sprite, Trigger, Unit
from chklib.views import TYPED_SECTIONS, StringTableView, view_for

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "corpus"
MAPS = sorted(CORPUS.glob("*.chk")) if CORPUS.is_dir() else []

pytestmark = pytest.mark.skipif(
    not MAPS,
    reason=f"no fixtures in {CORPUS}; run tools/extract_fixtures.py",
)

IDS = [p.stem for p in MAPS]


def load(path: pathlib.Path) -> Chk:
    return Chk.from_bytes(path.read_bytes())


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", MAPS, ids=IDS)
def test_typed_round_trip_is_byte_exact(path: pathlib.Path) -> None:
    chk = load(path)
    checked = 0
    for name in TYPED_SECTIONS:
        section = chk.last(name)
        if section is None:
            continue
        view = view_for(chk, name)
        assert view is not None, f"{path.stem}: {name} has no view"
        assert view.to_bytes() == section.data, (
            f"{path.stem}: {name} did not round-trip "
            f"({len(view.to_bytes())} bytes out vs {len(section.data)} in)"
        )
        checked += 1
    assert checked >= 10, f"{path.stem}: only {checked} typed sections found"


#: Sections this corpus cannot reach, with the reason. Keeping the list explicit
#: is the point: it stays a statement about the corpus rather than a hole in the
#: gate, and anything that drifts into it has to be justified here.
NOT_IN_THIS_CORPUS = {
    # The sc64 scenarios are all VER 59/63, pre-Remastered, so they carry STR.
    # STRx is covered instead by tests/test_strings.py against installed
    # Remastered maps, 24 of which use it.
    "STRx",
}


def test_gate_covers_every_typed_section_at_least_once() -> None:
    """Guard against a green gate that silently skipped a whole section type."""
    seen: set[str] = set()
    for path in MAPS:
        chk = load(path)
        seen.update(n for n in TYPED_SECTIONS if n in chk)
    missing = set(TYPED_SECTIONS) - seen - NOT_IN_THIS_CORPUS
    assert not missing, f"never exercised: {sorted(missing)}"


def test_the_corpus_exclusion_list_stays_honest() -> None:
    """An excluded section must genuinely be absent, not merely assumed absent.

    If one of these ever shows up, the exclusion is stale and the gate should
    start covering it.
    """
    for name in NOT_IN_THIS_CORPUS:
        present = [p.stem for p in MAPS if name in load(p)]
        assert not present, f"{name} is in the corpus after all: {present[:3]}"


# --------------------------------------------------------------------------
# Cross-checking the research against this implementation
# --------------------------------------------------------------------------


def test_mrgn_anywhere_sits_at_record_index_63() -> None:
    """Location ids are 1-based: 'Anywhere' is id 64, so file record 63.

    The research found the full-map rect at record 63 in 65 of 65 maps. If ids
    were 0-based it would be at record 64, which does not exist in a 1280-byte
    MRGN.
    """
    hits = 0
    for path in MAPS:
        chk = load(path)
        dim, mrgn = view_for(chk, "DIM"), view_for(chk, "MRGN")
        if mrgn is None or len(mrgn) <= 63:
            continue
        anywhere = mrgn[63]
        if (anywhere.left, anywhere.top) == (0, 0) and (
            anywhere.right,
            anywhere.bottom,
        ) == (dim.pixel_width, dim.pixel_height):
            hits += 1
    assert hits == len(MAPS), f"full-map rect at record 63 in only {hits}/{len(MAPS)}"


def test_unit_group_pseudo_types_appear_and_exceed_228() -> None:
    """Why unitType must never be range-checked against 228.

    AnyUnit(229), Men(230) and Buildings(231) are used by Blizzard's own campaign
    maps; a `unitType < 228` validator would reject them.
    """
    observed: set[int] = set()
    for path in MAPS:
        trig = view_for(load(path), "TRIG")
        if trig is None:
            continue
        for trigger in trig:
            for condition in trigger.used_conditions():
                if condition.flags & 0x10:  # UnitTypeUsed
                    observed.add(condition.unit_type)
    assert observed & {229, 230, 231}, "expected group pseudo-types in the corpus"


def test_briefing_and_game_action_id_spaces_are_distinct() -> None:
    """MBRF action ids stay <= 8 while TRIG ids reach far higher.

    Same 2400-byte layout, different meaning -- which is why a view carries
    ``is_briefing``.
    """
    max_trig = max_mbrf = -1
    for path in MAPS:
        chk = load(path)
        for name, is_briefing in (("TRIG", False), ("MBRF", True)):
            view = view_for(chk, name)
            if view is None:
                continue
            assert view.is_briefing == is_briefing
            for trigger in view:
                for action in trigger.used_actions():
                    if is_briefing:
                        max_mbrf = max(max_mbrf, action.action_type)
                    else:
                        max_trig = max(max_trig, action.action_type)
    assert max_mbrf <= 9, f"MBRF action id {max_mbrf} outside the briefing space"
    assert max_trig > 9, "expected TRIG to use ids beyond the briefing range"


def test_trigger_flags_are_zero_throughout_this_corpus() -> None:
    """Documents a known blind spot rather than asserting correctness.

    Every flag bit in the Trigger.flags table is unvalidated by this corpus. If
    this ever fails, the corpus gained coverage and the table can be checked.
    """
    values: set[int] = set()
    for path in MAPS:
        trig = view_for(load(path), "TRIG")
        if trig is not None:
            values.update(t.flags for t in trig)
    assert values == {0}, f"corpus now exercises Trigger.flags: {sorted(values)}"


def test_owners_bytes_are_only_zero_or_one_here() -> None:
    """Also a blind spot: the byte is not strictly boolean, but this corpus is."""
    values: set[int] = set()
    for path in MAPS:
        trig = view_for(load(path), "TRIG")
        if trig is not None:
            for trigger in trig:
                values.update(trigger.owners)
    assert values <= {0, 1}


def test_string_table_is_one_based_and_sprp_points_into_it() -> None:
    for path in MAPS:
        chk = load(path)
        strings, sprp = view_for(chk, "STR"), view_for(chk, "SPRP")
        assert isinstance(strings, StringTableView)
        assert sprp.name_string_id != 0, f"{path.stem}: research says no map uses id 0"
        assert strings.get(sprp.name_string_id) is not None, path.stem


def test_string_data_is_not_in_ascending_id_order_in_many_maps() -> None:
    """The reason strings must terminate by scanning to NUL.

    Research found inversions in 30 of 65 maps; differencing adjacent offsets
    produces negative lengths. This asserts the hazard is real in this corpus.
    """
    inverted_maps = 0
    for path in MAPS:
        strings = view_for(load(path), "STR")
        present = [
            (i, strings.offsets[i - 1])
            for i in range(1, strings.count + 1)
            if strings.get(i)
        ]
        if any(b[1] < a[1] for a, b in zip(present, present[1:])):
            inverted_maps += 1
    assert inverted_maps > 0, "expected out-of-order string data in this corpus"


def test_unit_owner_spans_the_full_twelve_player_range() -> None:
    """Neutral (11) is ~42% of corpus units; an 8-wide table drops half of them."""
    owners: set[int] = set()
    for path in MAPS:
        units = view_for(load(path), "UNIT")
        if units is not None:
            owners.update(u.owner for u in units)
    assert 11 in owners, "expected Neutral-owned units"


def test_no_partial_records_in_this_corpus() -> None:
    for path in MAPS:
        chk = load(path)
        for name in ("UNIT", "THG2", "MRGN", "TRIG", "MBRF"):
            view = view_for(chk, name)
            if view is None:
                continue
            trailing = getattr(view, "trailing", b"")
            assert trailing == b"", f"{path.stem}: {name} has a partial record"


# --------------------------------------------------------------------------
# Inspect rendering over real maps
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", MAPS, ids=IDS)
def test_inspect_is_deterministic_across_reparses(path: pathlib.Path) -> None:
    """Two independent parses of the same bytes must render identically.

    A git textconv driver that is not deterministic shows spurious churn on
    every diff, which is worse than having no driver at all.
    """
    from chklib.inspect import render

    raw = path.read_bytes()
    assert render(Chk.from_bytes(raw)) == render(Chk.from_bytes(raw))


@pytest.mark.parametrize("path", MAPS, ids=IDS)
def test_inspect_never_leaks_a_path(path: pathlib.Path) -> None:
    from chklib.inspect import render

    out = render(Chk.from_bytes(path.read_bytes()))
    assert "# source" not in out
    assert str(path) not in out


def test_editing_one_unit_produces_a_local_diff() -> None:
    """The whole point: a small map change must be a small text change."""
    import difflib
    from dataclasses import replace

    from chklib.inspect import render
    from chklib.views import view_for as _view_for

    chk = load(MAPS[0])
    before = render(chk)

    units = _view_for(chk, "UNIT")
    assert len(units) > 3
    units.records[3].xc += 64
    section = chk.last("UNIT")
    chk.sections[chk.sections.index(section)] = replace(section, data=units.to_bytes())
    after = render(chk)

    changed = [
        line for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert len(changed) == 2, f"expected one line replaced, got {changed}"


# --------------------------------------------------------------------------
# Semantic diff over real maps
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", MAPS, ids=IDS)
def test_map_diffed_against_itself_is_empty(path: pathlib.Path) -> None:
    """The most basic correctness check a differ can fail."""
    from chklib.diff import diff

    assert diff(load(path), load(path)).is_empty


def test_diffing_two_real_maps_is_deterministic_and_non_empty() -> None:
    from chklib.diff import diff

    a, b = load(MAPS[0]), load(MAPS[1])
    first, second = diff(a, b).to_text(), diff(a, b).to_text()
    assert first == second
    assert "no differences" not in first


def test_inserting_a_trigger_into_a_real_map_does_not_cascade() -> None:
    """The cascade property, on a map with real triggers rather than a fixture."""
    import copy
    from dataclasses import replace

    from chklib.diff import diff
    from chklib.views import view_for as _view_for

    original = load(MAPS[0])
    trig = _view_for(original, "TRIG")
    assert len(trig) >= 10, "need a map with a real trigger list"

    edited = load(MAPS[0])
    view = _view_for(edited, "TRIG")
    view.triggers.insert(0, copy.deepcopy(view.triggers[3]))
    section = edited.last("TRIG")
    chk_index = edited.sections.index(section)
    edited.sections[chk_index] = replace(section, data=view.to_bytes())

    report = diff(original, edited)
    trigger_changes = [c for c in report.changes if c.area == "TRIG"]
    assert len(trigger_changes) == 1, [c.to_text() for c in trigger_changes]
    assert trigger_changes[0].kind == "added"
