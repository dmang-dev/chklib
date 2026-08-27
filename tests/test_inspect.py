"""Tests for the inspect rendering and CLI.

The property that matters most is determinism. This output is meant to be a git
textconv driver, so the same bytes must always produce the same characters -- if
they do not, every ``git diff`` shows spurious churn and the feature is worse
than useless.

The second property is diff locality: a small change to a map must produce a
small change in the text. That is what the ordering rules in
:mod:`chklib.inspect` are for, and it is tested here by actually diffing.
"""

from __future__ import annotations

import difflib
import glob
import pathlib
import struct

import pytest

from chklib import Chk
from chklib.cli import main
from chklib.inspect import FORMAT_VERSION, render
from chklib.records import Location, Trigger, Unit


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def minimal_map(*, units: list[Unit] | None = None, width: int = 64) -> Chk:
    """A small but structurally real CHK."""
    units = units if units is not None else []
    strings = _string_table([b"Test Map", b"A description", b"Force A"])
    parts = [
        sect(b"VER ", struct.pack("<H", 59)),
        sect(b"ERA ", struct.pack("<H", 4)),
        sect(b"DIM ", struct.pack("<HH", width, 64)),
        sect(b"OWNR", bytes([6] * 8 + [0] * 4)),
        sect(b"SIDE", bytes([1] * 12)),
        sect(b"SPRP", struct.pack("<HH", 1, 2)),
        sect(b"FORC", bytes(8) + struct.pack("<4H", 3, 0, 0, 0) + bytes([0x0F, 0, 0, 0])),
        sect(b"STR ", strings),
        sect(b"MRGN", bytes(20 * 64)),
        sect(b"UNIT", b"".join(u.to_bytes() for u in units)),
        sect(b"TRIG", bytes(Trigger.SIZE)),
    ]
    return Chk.from_bytes(b"".join(parts))


def _string_table(values: list[bytes]) -> bytes:
    count = len(values)
    header = bytearray(2 + 2 * count)
    struct.pack_into("<H", header, 0, count)
    data = bytearray(b"\x00")
    for i, value in enumerate(values, start=1):
        struct.pack_into("<H", header, 2 * i, len(header) + len(data))
        data += value + b"\x00"
    return bytes(header) + bytes(data)


def a_unit(**kw) -> Unit:
    defaults = dict(
        class_id=0, xc=100, yc=200, type=7, relation_flags=0,
        valid_state_flags=0, valid_field_flags=0, owner=0,
        hitpoint_percent=100, shield_percent=100, energy_percent=100,
        resource_amount=0, hangar_amount=0, state_flags=0, unused=0,
        relation_class_id=0,
    )
    defaults.update(kw)
    return Unit(**defaults)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_render_is_deterministic() -> None:
    chk = minimal_map(units=[a_unit(xc=x) for x in (300, 100, 200)])
    assert render(chk) == render(chk)


def test_render_is_deterministic_across_fresh_parses() -> None:
    """Two independent parses of the same bytes must render identically."""
    raw = minimal_map(units=[a_unit(xc=x) for x in (300, 100, 200)]).to_bytes()
    assert render(Chk.from_bytes(raw)) == render(Chk.from_bytes(raw))


def test_source_is_omitted_unless_given() -> None:
    chk = minimal_map()
    assert "# source" not in render(chk)
    assert "# source /tmp/x.chk" in render(chk, source="/tmp/x.chk")


def test_format_version_is_in_the_header() -> None:
    assert render(minimal_map()).startswith(f"# chklib inspect v{FORMAT_VERSION}")


def test_output_ends_with_a_newline() -> None:
    """Text without a trailing newline makes git report 'no newline at EOF'."""
    assert render(minimal_map()).endswith("\n")


# --------------------------------------------------------------------------
# Diff locality
# --------------------------------------------------------------------------


def _changed_line_count(before: str, after: str) -> int:
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0
    )
    return sum(
        1 for line in diff
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )


def test_moving_one_unit_changes_one_line_pair() -> None:
    units = [a_unit(xc=100), a_unit(xc=200), a_unit(xc=300)]
    before = render(minimal_map(units=units))
    moved = [a_unit(xc=100), a_unit(xc=250), a_unit(xc=300)]
    after = render(minimal_map(units=moved))
    assert _changed_line_count(before, after) == 2  # one removed, one added


def test_inserting_a_unit_does_not_cascade() -> None:
    """Units are sorted by content, so an insertion must not renumber the rest.

    This is the whole reason the unit list carries no index. The property under
    test is that the diff size is *constant* in the number of units: inserting
    into a 2-unit map and a 40-unit map must churn the same number of lines. An
    index-per-unit format would make the second case churn every line after the
    insertion point.

    The constant is not 1 -- the ``[units]`` count header and the ``UNIT`` byte
    size in ``[sections]`` both legitimately change too.
    """

    def churn(existing: int) -> int:
        units = [a_unit(xc=200 + i * 10) for i in range(existing)]
        before = render(minimal_map(units=units))
        after = render(minimal_map(units=[a_unit(xc=100)] + units))
        return _changed_line_count(before, after)

    small, large = churn(2), churn(40)
    assert small == large, f"diff grew with map size: {small} vs {large}"
    assert small == 5, (
        f"expected 1 added unit line + the [units] header pair + the UNIT size "
        f"pair, got {small}"
    )


def test_unit_order_in_the_file_does_not_affect_output() -> None:
    """Canonical ordering: the same set of units renders identically."""
    units = [a_unit(xc=100), a_unit(xc=200), a_unit(xc=300)]
    assert render(minimal_map(units=units)) == render(
        minimal_map(units=list(reversed(units)))
    )


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def test_strings_are_resolved_not_shown_as_bare_ids() -> None:
    out = render(minimal_map())
    assert 'name         #1 "Test Map"' in out
    assert 'description  #2 "A description"' in out


def test_non_printable_bytes_are_escaped_deterministically() -> None:
    raw = b"".join([
        sect(b"STR ", _string_table([b"tab\there\xff"])),
        sect(b"SPRP", struct.pack("<HH", 1, 0)),
    ])
    out = render(Chk.from_bytes(raw))
    assert '"tab\\there\\xff"' in out


def test_absent_string_id_renders_as_dash() -> None:
    raw = b"".join([
        sect(b"STR ", _string_table([b"x"])),
        sect(b"SPRP", struct.pack("<HH", 0, 0)),
    ])
    assert "name         -" in render(Chk.from_bytes(raw))


def test_locations_are_reported_one_based() -> None:
    """File record 0 is location id 1."""
    locations = bytearray(20 * 64)
    Location(10, 20, 30, 40, 0, 0).to_bytes()
    locations[0:20] = Location(10, 20, 30, 40, 0, 0).to_bytes()
    raw = sect(b"MRGN", bytes(locations))
    out = render(Chk.from_bytes(raw))
    assert "    1  " in out
    assert "(10,20)-(30,40)" in out


def test_diagnostics_are_surfaced() -> None:
    truncated = b"MTXM" + struct.pack("<i", 9999) + b"ab"
    out = render(Chk.from_bytes(truncated))
    assert "TRUNCATED" in out
    assert "truncated-section" in out


@pytest.mark.parametrize(
    "action_id,in_trig,in_mbrf",
    [
        (5, "PauseGame", "BriefingShowPortrait"),
        (7, "Transmission", "BriefingDisplaySpeakingPortrait"),
        (8, "PlaySound", "BriefingTransmission"),
    ],
)
def test_briefing_and_game_action_ids_are_separate_spaces(
    action_id: int, in_trig: str, in_mbrf: str
) -> None:
    """The same byte means different things in TRIG and MBRF.

    This is why a view carries ``is_briefing``: reading an MBRF action against
    the game action table silently mislabels every one of them.
    """
    trigger = bytearray(Trigger.SIZE)
    trigger[320 + 26] = action_id  # first action's actionType
    assert in_trig in render(Chk.from_bytes(sect(b"TRIG", bytes(trigger))))
    assert in_mbrf in render(Chk.from_bytes(sect(b"MBRF", bytes(trigger))))


def test_iown_ownr_disagreement_is_surfaced() -> None:
    raw = b"".join([
        sect(b"OWNR", bytes([6] * 12)),
        sect(b"IOWN", bytes([5] * 12)),
    ])
    assert "IOWN differs from OWNR" in render(Chk.from_bytes(raw))


def test_empty_input_renders_without_crashing() -> None:
    out = render(Chk.from_bytes(b""))
    assert out.startswith("# chklib inspect")
    assert "[sections] 0" in out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_inspect(tmp_path: pathlib.Path, capsys) -> None:
    path = tmp_path / "scenario.chk"
    path.write_bytes(minimal_map().to_bytes())
    assert main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "# chklib inspect" in out
    assert f"# source {path}" in out


def test_cli_stable_omits_the_path(tmp_path: pathlib.Path, capsys) -> None:
    """git textconv passes a temp filename that would otherwise churn the diff."""
    path = tmp_path / "scenario.chk"
    path.write_bytes(minimal_map().to_bytes())
    assert main(["inspect", "--stable", str(path)]) == 0
    out = capsys.readouterr().out
    assert "# source" not in out
    assert str(path) not in out


def test_cli_reads_a_real_map_archive(capsys) -> None:
    """The CLI takes a .scm/.scx directly, not just a bare scenario.chk."""
    maps = sorted(glob.glob(r"I:\projects\sc64-maps\gamedata\maps\*.scm"))
    if not maps:
        pytest.skip("no map archives available")
    assert main(["inspect", "--stable", maps[0]]) == 0
    out = capsys.readouterr().out
    assert "# chklib inspect" in out
    assert "[map]" in out


def test_cli_reports_an_unreadable_archive_clearly(tmp_path: pathlib.Path) -> None:
    """A truncated archive must name the problem, not raise something opaque."""
    path = tmp_path / "map.scx"
    path.write_bytes(b"MPQ\x1a" + bytes(200))
    with pytest.raises(SystemExit) as excinfo:
        main(["inspect", str(path)])
    assert str(path) in str(excinfo.value)


def test_cli_missing_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        main(["inspect", str(tmp_path / "nope.chk")])


def test_cli_strict_flags_parse_errors(tmp_path: pathlib.Path, capsys) -> None:
    path = tmp_path / "bad.chk"
    path.write_bytes(b"MTXM" + struct.pack("<i", 9999) + b"ab")
    assert main(["inspect", str(path)]) == 0            # tolerant by default
    assert main(["inspect", "--strict", str(path)]) == 1
    assert "truncated-section" in capsys.readouterr().err
