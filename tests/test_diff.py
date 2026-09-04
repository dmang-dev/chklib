"""Tests for the semantic diff.

The property that justifies the whole approach is **no cascade**: inserting one
trigger at the top of a map must report one added trigger, not N modified ones.
A positional comparison gets that catastrophically wrong, and it is the reason
triggers are aligned by an LCS over content hashes rather than by index.

The unit tests below build maps by hand so each behaviour is isolated;
``test_corpus_typed.py`` exercises the same code against real maps.
"""

from __future__ import annotations

import copy
import json
import pathlib
import struct
from dataclasses import replace

import pytest

from chklib import Chk
from chklib.cli import main
from chklib.diff import JSON_SCHEMA_VERSION, diff
from chklib.records import Location, Trigger, Unit
from chklib.settings import UnitSettings, UpgradeSettingsExpansion


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def string_table(values: list[bytes]) -> bytes:
    count = len(values)
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    data = bytearray(b"\x00")
    for i, value in enumerate(values, start=1):
        struct.pack_into("<H", header, 2 * i, len(header) + len(data))
        data += value + b"\x00"
    return bytes(header) + bytes(data)


def a_unit(**kw) -> Unit:
    defaults = {
        "class_id": 0, "xc": 100, "yc": 200, "type": 7, "relation_flags": 0, "valid_state_flags": 0,
        "valid_field_flags": 0x02, "owner": 0, "hitpoint_percent": 100, "shield_percent": 100,
        "energy_percent": 100, "resource_amount": 0, "hangar_amount": 0, "state_flags": 0,
        "unused": 0, "relation_class_id": 0,
    }
    defaults.update(kw)
    return Unit(**defaults)


def a_trigger(*, condition_type: int = 22, action_type: int = 1,
              owner: int = 0, location: int = 0) -> Trigger:
    trigger = Trigger.from_bytes(bytes(Trigger.SIZE))
    trigger.conditions[0] = replace(
        trigger.conditions[0], condition_type=condition_type
    )
    trigger.actions[0] = replace(
        trigger.actions[0], action_type=action_type, location_id=location
    )
    trigger.owners = bytes([1 if i == owner else 0 for i in range(27)])
    return trigger


def build(*, version: int = 59, tileset: int = 4, width: int = 64,
          strings: list[bytes] | None = None, units: list[Unit] | None = None,
          triggers: list[Trigger] | None = None,
          locations: list[Location] | None = None,
          owner_slots: bytes | None = None) -> Chk:
    strings = strings if strings is not None else [b"Map", b"Desc"]
    units = units or []
    triggers = triggers or []
    loc_bytes = bytearray(20 * 64)
    for index, loc in enumerate(locations or []):
        loc_bytes[index * 20:(index + 1) * 20] = loc.to_bytes()
    parts = [
        sect(b"VER ", struct.pack("<H", version)),
        sect(b"ERA ", struct.pack("<H", tileset)),
        sect(b"DIM ", struct.pack("<HH", width, 64)),
        sect(b"OWNR", owner_slots or bytes([6] * 8 + [0] * 4)),
        sect(b"SIDE", bytes([1] * 12)),
        sect(b"SPRP", struct.pack("<HH", 1, 2)),
        sect(b"FORC", bytes(8) + struct.pack("<4H", 0, 0, 0, 0) + bytes(4)),
        sect(b"STR ", string_table(strings)),
        sect(b"MRGN", bytes(loc_bytes)),
        sect(b"UNIT", b"".join(u.to_bytes() for u in units)),
        sect(b"TRIG", b"".join(t.to_bytes() for t in triggers)),
    ]
    return Chk.from_bytes(b"".join(parts))


def areas(report) -> list[str]:
    return [c.area for c in report.changes]


def keys(report) -> list[str]:
    return [c.key for c in report.changes]


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def test_identical_maps_have_no_differences() -> None:
    assert diff(build(), build()).is_empty


def test_diff_is_deterministic() -> None:
    a, b = build(units=[a_unit(xc=1)]), build(units=[a_unit(xc=2)])
    assert diff(a, b).to_text() == diff(a, b).to_text()


def test_empty_report_text() -> None:
    assert diff(build(), build()).to_text() == "no differences\n"


# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------


def test_version_change() -> None:
    report = diff(build(version=59), build(version=205))
    change = next(c for c in report.changes if c.key == "version")
    assert "59" in change.before and "205" in change.after


def test_tileset_and_dimensions() -> None:
    report = diff(build(tileset=4), build(tileset=0))
    assert any("Badlands" in (c.after or "") for c in report.changes)
    report = diff(build(width=64), build(width=96))
    assert any(c.key == "dimensions" for c in report.changes)


def test_map_name_change_resolves_the_string() -> None:
    report = diff(build(strings=[b"Old", b"D"]), build(strings=[b"New", b"D"]))
    assert any(c.key == "name" and '"New"' in (c.after or "") for c in report.changes)


def test_player_slot_change() -> None:
    report = diff(
        build(owner_slots=bytes([6] * 8 + [0] * 4)),
        build(owner_slots=bytes([6] * 7 + [5] + [0] * 4)),
    )
    change = next(c for c in report.changes if c.area == "players")
    assert change.key == "p8 slot"
    assert "Computer" in change.after


# --------------------------------------------------------------------------
# Strings and locations - identity by index
# --------------------------------------------------------------------------


def test_string_added_removed_changed() -> None:
    report = diff(build(strings=[b"a", b"b"]), build(strings=[b"a", b"CHANGED"]))
    change = next(c for c in report.changes if c.area == "strings")
    assert change.key == "string 2" and change.kind == "changed"

    report = diff(build(strings=[b"a"]), build(strings=[b"a", b"b"]))
    assert any(c.area == "strings" and c.kind == "added" for c in report.changes)


def test_location_compared_by_one_based_id() -> None:
    before = [Location(0, 0, 10, 10, 1, 0)]
    after = [Location(0, 0, 20, 20, 1, 0)]
    report = diff(build(locations=before), build(locations=after))
    change = next(c for c in report.changes if c.area == "MRGN")
    # File record 0 is location id 1.
    assert change.key == "location 1"
    assert "(0,0)-(20,20)" in change.after


def test_location_added_and_removed() -> None:
    report = diff(build(locations=[]), build(locations=[Location(0, 0, 5, 5, 0, 0)]))
    assert any(c.area == "MRGN" and c.kind == "added" for c in report.changes)
    report = diff(build(locations=[Location(0, 0, 5, 5, 0, 0)]), build(locations=[]))
    assert any(c.area == "MRGN" and c.kind == "removed" for c in report.changes)


# --------------------------------------------------------------------------
# Units - no identity, multiset with pairing
# --------------------------------------------------------------------------


def test_moved_unit_reads_as_one_change_not_a_delete_plus_add() -> None:
    report = diff(build(units=[a_unit(xc=100)]), build(units=[a_unit(xc=300)]))
    unit_changes = [c for c in report.changes if c.area == "UNIT"]
    assert len(unit_changes) == 1
    assert unit_changes[0].kind == "changed"
    assert "at=(100,200)" in unit_changes[0].before
    assert "at=(300,200)" in unit_changes[0].after


def test_added_and_removed_units() -> None:
    report = diff(build(units=[a_unit()]), build(units=[a_unit(), a_unit(xc=500)]))
    unit_changes = [c for c in report.changes if c.area == "UNIT"]
    assert [c.kind for c in unit_changes] == ["added"]

    report = diff(build(units=[a_unit(), a_unit(xc=500)]), build(units=[a_unit()]))
    unit_changes = [c for c in report.changes if c.area == "UNIT"]
    assert [c.kind for c in unit_changes] == ["removed"]


def test_reordering_units_is_not_a_difference() -> None:
    """UNIT file order is not meaningful, so a permutation must be silent."""
    units = [a_unit(xc=100), a_unit(xc=200), a_unit(xc=300)]
    report = diff(build(units=units), build(units=list(reversed(units))))
    assert not [c for c in report.changes if c.area == "UNIT"]


def test_units_of_different_types_are_not_paired() -> None:
    """A marine deleted and a zergling added is not 'a unit changed type'."""
    report = diff(build(units=[a_unit(type=0)]), build(units=[a_unit(type=37)]))
    kinds = sorted(c.kind for c in report.changes if c.area == "UNIT")
    assert kinds == ["added", "removed"]


# --------------------------------------------------------------------------
# Triggers - the cascade problem
# --------------------------------------------------------------------------


def test_inserting_a_trigger_at_the_top_does_not_cascade() -> None:
    """The property the whole trigger algorithm exists for.

    A positional comparison would report all N triggers as modified. The LCS
    alignment over content hashes reports exactly one addition.
    """
    original = [a_trigger(location=i) for i in range(1, 21)]
    inserted = [a_trigger(location=99), *original]
    report = diff(build(triggers=original), build(triggers=inserted))
    trigger_changes = [c for c in report.changes if c.area == "TRIG"]
    assert len(trigger_changes) == 1, [c.to_text() for c in trigger_changes]
    assert trigger_changes[0].kind == "added"
    assert trigger_changes[0].key == "trigger 0"


def test_deleting_a_trigger_in_the_middle_does_not_cascade() -> None:
    original = [a_trigger(location=i) for i in range(1, 21)]
    removed = original[:10] + original[11:]
    report = diff(build(triggers=original), build(triggers=removed))
    trigger_changes = [c for c in report.changes if c.area == "TRIG"]
    assert len(trigger_changes) == 1
    assert trigger_changes[0].kind == "removed"


def test_editing_one_action_reports_only_that_line() -> None:
    original = [a_trigger(location=i) for i in range(1, 11)]
    edited = copy.deepcopy(original)
    edited[4].actions[0] = replace(edited[4].actions[0], location_id=777)
    report = diff(build(triggers=original), build(triggers=edited))
    trigger_changes = [c for c in report.changes if c.area == "TRIG"]
    assert len(trigger_changes) == 1
    assert trigger_changes[0].kind == "changed"
    assert "loc=777" in trigger_changes[0].after


def test_insert_plus_edit_reports_the_index_shift() -> None:
    """An edited trigger that also moved is reported as ``trigger a->b``."""
    original = [a_trigger(location=i) for i in range(1, 11)]
    edited = [a_trigger(location=500), *copy.deepcopy(original)]
    edited[6].actions[0] = replace(edited[6].actions[0], location_id=888)
    report = diff(build(triggers=original), build(triggers=edited))
    trigger_changes = [c for c in report.changes if c.area == "TRIG"]
    kinds = sorted(c.kind for c in trigger_changes)
    assert kinds == ["added", "changed"]
    changed = next(c for c in trigger_changes if c.kind == "changed")
    assert "->" in changed.key, changed.key


def test_reordering_triggers_is_a_difference() -> None:
    """Unlike units, trigger order IS meaningful -- it is execution order."""
    triggers = [a_trigger(location=1), a_trigger(location=2)]
    report = diff(build(triggers=triggers), build(triggers=list(reversed(triggers))))
    assert [c for c in report.changes if c.area == "TRIG"]


def test_wholly_unrelated_triggers_are_not_paired() -> None:
    """Below the similarity threshold, a replacement is an add plus a remove."""
    before = [a_trigger(condition_type=22, action_type=1, owner=0)]
    after = [a_trigger(condition_type=15, action_type=44, owner=5, location=9)]
    report = diff(build(triggers=before), build(triggers=after))
    kinds = sorted(c.kind for c in report.changes if c.area == "TRIG")
    assert kinds == ["added", "removed"]


def test_owner_change_is_reported() -> None:
    report = diff(
        build(triggers=[a_trigger(owner=0)]),
        build(triggers=[a_trigger(owner=3)]),
    )
    assert any("owners" in (c.before or "") for c in report.changes if c.area == "TRIG")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def test_json_shape() -> None:
    report = diff(build(units=[a_unit(xc=1)]), build(units=[a_unit(xc=2)]))
    payload = json.loads(report.to_json())
    assert payload["schema"] == JSON_SCHEMA_VERSION
    assert payload["summary"]["total"] == len(payload["changes"])
    entry = payload["changes"][0]
    assert set(entry) == {"area", "kind", "key", "before", "after", "detail"}


def test_text_output_marks_kinds() -> None:
    text = diff(build(units=[]), build(units=[a_unit()])).to_text()
    assert text.startswith("+ UNIT")
    assert "differences" in text


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _write(tmp_path: pathlib.Path, name: str, chk: Chk) -> pathlib.Path:
    path = tmp_path / name
    path.write_bytes(chk.to_bytes())
    return path


def test_cli_diff_exit_codes(tmp_path: pathlib.Path, capsys) -> None:
    """Follows diff(1): 0 identical, 1 different."""
    same_a = _write(tmp_path, "a.chk", build())
    same_b = _write(tmp_path, "b.chk", build())
    assert main(["diff", str(same_a), str(same_b)]) == 0
    assert "no differences" in capsys.readouterr().out

    other = _write(tmp_path, "c.chk", build(units=[a_unit()]))
    assert main(["diff", str(same_a), str(other)]) == 1
    assert "+ UNIT" in capsys.readouterr().out


def test_cli_diff_json(tmp_path: pathlib.Path, capsys) -> None:
    a = _write(tmp_path, "a.chk", build())
    b = _write(tmp_path, "b.chk", build(units=[a_unit()]))
    assert main(["diff", "--json", str(a), str(b)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"][0]["area"] == "UNIT"


def test_cli_diff_missing_file(tmp_path: pathlib.Path) -> None:
    a = _write(tmp_path, "a.chk", build())
    with pytest.raises(SystemExit):
        main(["diff", str(a), str(tmp_path / "nope.chk")])


def test_cli_diff_reads_map_archives_directly() -> None:
    """diff takes .scm/.scx, which is what makes it usable on a repository."""
    import glob

    maps = sorted(glob.glob(r"I:\projects\sc64-maps\gamedata\maps\*.scm"))
    if len(maps) < 2:
        pytest.skip("need two map archives")
    assert main(["diff", maps[0], maps[0]]) == 0   # identical
    assert main(["diff", maps[0], maps[1]]) == 1   # different


def test_cli_diff_reports_an_unreadable_archive(tmp_path: pathlib.Path) -> None:
    a = _write(tmp_path, "a.chk", build())
    mpq = tmp_path / "b.scx"
    mpq.write_bytes(b"MPQ" + bytes(100))
    with pytest.raises(SystemExit) as excinfo:
        main(["diff", str(a), str(mpq)])
    assert str(mpq) in str(excinfo.value)



def test_broken_pipe_is_handled(tmp_path: pathlib.Path, monkeypatch) -> None:
    """`chkdiff diff a b | head` must not traceback.

    A downstream consumer closing the pipe is normal usage. Python would
    otherwise raise again while flushing stdout at shutdown and print a second
    traceback, so main() swallows it and returns the conventional status.
    """
    a = _write(tmp_path, "a.chk", build())
    b = _write(tmp_path, "b.chk", build(units=[a_unit()]))

    class ClosedPipe:
        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr("chklib.cli.sys.stdout", ClosedPipe())
    assert main(["diff", str(a), str(b)]) == 141  # 128 + SIGPIPE


def test_broken_pipe_is_handled_for_inspect(tmp_path: pathlib.Path, monkeypatch) -> None:
    a = _write(tmp_path, "a.chk", build())

    class ClosedPipe:
        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr("chklib.cli.sys.stdout", ClosedPipe())
    assert main(["inspect", str(a)]) == 141


# ---------------------------------------------------------------------------
# Settings tables
# ---------------------------------------------------------------------------


def _one_section(name: str, payload: bytes) -> Chk:
    return Chk.from_bytes(sect(name.encode("ascii").ljust(4), payload))


def _upgx(*, pad: int = 0, tail: bytes = b"") -> Chk:
    table = UpgradeSettingsExpansion()
    table.arrays["unused"][0] = pad
    return _one_section("UPGx", table.to_bytes(normalize=True) + tail)


def _unis(field: str, index: int, value: int) -> Chk:
    table = UnitSettings()
    table.arrays[field][index] = value
    return _one_section("UNIS", table.to_bytes(normalize=True))


def test_upgx_pad_byte_is_not_called_a_weapon() -> None:
    """``UPGx`` models 61 upgrades and no weapons at all.

    Its one pad byte is the only field whose element count differs from the
    upgrade count, so choosing the label by comparing counts -- which is how a
    unit table tells its weapon arrays from its unit arrays -- reported a
    changed pad byte as ``weapon 0`` in a section that has no weapon 0. Each
    table now declares what an index means instead of it being inferred.
    """
    text = diff(_upgx(pad=0), _upgx(pad=7)).to_text()
    assert "unused=7" in text
    assert "weapon" not in text
    # One byte is not an array, so an index into it would be noise.
    assert "pad byte\n" in text and "pad byte 0" not in text


def test_unit_weapon_arrays_are_still_indexed_by_weapon() -> None:
    """The other half of the same fix: the damage arrays really are indexed by
    weapon rather than by unit, and must keep saying so."""
    assert "weapon 3" in diff(
        _unis("base_damage", 3, 0), _unis("base_damage", 3, 40)
    ).to_text()
    assert "unit 151" in diff(
        _unis("hitpoints", 151, 1024000), _unis("hitpoints", 151, 384000)
    ).to_text()


def test_changed_trailing_bytes_render_differently() -> None:
    """Reporting only the length announced a difference and then showed none.

    An oversized section whose tail changes without changing size rendered as
    ``1 bytes`` against ``1 bytes``. Nothing here decodes those bytes, so the
    line carries a digest -- enough to show that the two sides really differ.
    """
    report = diff(_upgx(tail=b"\x01"), _upgx(tail=b"\x02"))
    change = next(c for c in report.changes if c.key == "trailing bytes")
    assert change.before != change.after
    assert change.before.startswith("1 bytes sha=")


def test_every_typed_section_is_either_diffed_or_falls_through() -> None:
    """Nothing typed may be invisible to ``diff``.

    ``_diff_sections`` compares names, order and sizes only, so a section that
    is neither compared field by field nor caught by ``_diff_opaque`` changes
    silently. That is what used to happen to terrain: repainting a tile rewrites
    MTXM in place, and the whole map diffed as no differences at all.
    """
    from chklib.diff import _SEMANTICALLY_DIFFED
    from chklib.views import TYPED_SECTIONS

    assert set(TYPED_SECTIONS) >= _SEMANTICALLY_DIFFED, (
        "a section is listed as diffed but is not typed: "
        f"{_SEMANTICALLY_DIFFED - set(TYPED_SECTIONS)}"
    )
    # The fallback covers the rest by construction, so together they are total.
    assert set(TYPED_SECTIONS) - _SEMANTICALLY_DIFFED, "the fallback covers nothing"


def test_a_terrain_edit_is_reported() -> None:
    """It was not, before ``_diff_opaque``: a tile change alters no size."""
    def grid(tile: int) -> Chk:
        return Chk.from_bytes(
            sect(b"VER ", struct.pack("<H", 59))
            + sect(b"DIM ", struct.pack("<HH", 2, 2))
            + sect(b"MTXM", struct.pack("<4H", tile, 1, 2, 3))
        )

    report = diff(grid(0), grid(99))
    changes = [c for c in report.changes if c.area == "MTXM"]
    assert changes, "a repainted tile must not diff as no differences"
    assert changes[0].key == "content"
    assert "1 of 8 bytes differ" in changes[0].detail
