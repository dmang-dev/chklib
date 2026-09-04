"""Tests for the player restriction tables and the last of the typed sections.

The method here is the one ``test_settings.py`` establishes, and for the same
reason: a round-trip proves nothing about field order, because ``pack`` and
``unpack`` walk the same ``LAYOUT`` in the same order, so any permutation of
same-width fields round-trips every byte perfectly and still totals 5700. These
sections make that worse than usual -- every field is ``u8``, so *every* pair is
same-width and *every* permutation round-trips.

So the offsets below are literals, transcribed from Chkdraft's struct
declarations and never computed from the code they check. Where a section could
be checked against something outside this project entirely -- real map bytes --
it is, and those tests say what they measured.
"""

from __future__ import annotations

import pathlib
import struct

import pytest
import tomllib
from conftest import installed_maps

from chklib import Chk
from chklib.records import (
    DOODAD_DISABLED,
    DOODAD_ENABLED,
    MAX_CUWPS,
    Cuwp,
    Doodad,
)
from chklib.restrictions import (
    DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION,
    DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL,
    DEFAULT_RESEARCHED_EXPANSION,
    DEFAULT_RESEARCHED_ORIGINAL,
    PUBLISHED_SIZES,
    RESTRICTION_SECTIONS,
    TOTAL_PLAYERS,
    TechRestrictions,
    TechRestrictionsExpansion,
    UnitRestrictions,
    UpgradeRestrictions,
    UpgradeRestrictionsExpansion,
    restrictions_for,
)
from chklib.views import (
    TYPED_SECTIONS,
    CuwpUsage,
    EditorVersion,
    PlayerColors,
    RemasteredColors,
    ScenarioType,
    ValidationCode,
    view_for,
)

CORPUS = sorted((pathlib.Path(__file__).parent / "fixtures" / "corpus").glob("*.chk"))
INSTALLED = installed_maps()


def sect(name: bytes, payload: bytes) -> bytes:
    return name.ljust(4) + struct.pack("<i", len(payload)) + payload


def one_section(name: str, payload: bytes) -> Chk:
    return Chk.from_bytes(sect(name.encode("ascii"), payload))


# ---------------------------------------------------------------------------
# Byte offsets
#
# Transcribed from Chkdraft src/mapping_core/chk.h, struct by struct, with the
# constants Player::Total=12, Unit::TotalTypes=228, Upgrade::TotalOriginalTypes
# =46/TotalTypes=61, Tech::TotalOriginalTypes=24/TotalTypes=44 (sc.h:119, :167,
# :1687-1688, :1812-1813). Offsets are written out rather than derived: a
# derivation would only ask the layout to agree with itself.
# ---------------------------------------------------------------------------

FIELD_OFFSETS: list[tuple[str, str, int, int]] = [
    # PUNI -- chk.h:1418-1430. 2736 + 228 + 2736 = 5700.
    ("PUNI", "player_unit_buildable", 0, 2736),
    ("PUNI", "default_unit_buildable", 2736, 228),
    ("PUNI", "player_unit_uses_default", 2964, 2736),
    # UPGR -- chk.h:1432-1448. 552 + 552 + 46 + 46 + 552 = 1748.
    ("UPGR", "player_max_upgrade_level", 0, 552),
    ("UPGR", "player_start_upgrade_level", 552, 552),
    ("UPGR", "default_max_level", 1104, 46),
    ("UPGR", "default_start_level", 1150, 46),
    ("UPGR", "player_upgrade_uses_default", 1196, 552),
    # PUPx -- chk.h:1606-1623. 732 + 732 + 61 + 61 + 732 = 2318.
    ("PUPx", "player_max_upgrade_level", 0, 732),
    ("PUPx", "player_start_upgrade_level", 732, 732),
    ("PUPx", "default_max_level", 1464, 61),
    ("PUPx", "default_start_level", 1525, 61),
    ("PUPx", "player_upgrade_uses_default", 1586, 732),
    # PTEC -- chk.h:1450-1470. 288 + 288 + 24 + 24 + 288 = 912.
    ("PTEC", "tech_available_for_player", 0, 288),
    ("PTEC", "tech_researched_for_player", 288, 288),
    ("PTEC", "tech_available_by_default", 576, 24),
    ("PTEC", "tech_researched_by_default", 600, 24),
    ("PTEC", "player_uses_defaults_for_tech", 624, 288),
    # PTEx -- chk.h:1625-1650. 528 + 528 + 44 + 44 + 528 = 1672.
    ("PTEx", "tech_available_for_player", 0, 528),
    ("PTEx", "tech_researched_for_player", 528, 528),
    ("PTEx", "tech_available_by_default", 1056, 44),
    ("PTEx", "tech_researched_by_default", 1100, 44),
    ("PTEx", "player_uses_defaults_for_tech", 1144, 528),
]


@pytest.mark.parametrize(
    ("section", "field_name", "offset", "count"),
    FIELD_OFFSETS,
    ids=[f"{s}.{f}@{o}" for s, f, o, _ in FIELD_OFFSETS],
)
def test_each_field_sits_at_its_documented_byte_offset(
    section: str, field_name: str, offset: int, count: int
) -> None:
    """The only test here that can catch a transposed layout.

    Two ``u8`` arrays swapped in the layout still pack, still round-trip, and
    still total the published size. Only a literal offset separates them.
    """
    table_cls = RESTRICTION_SECTIONS[section.encode("ascii")]
    assert table_cls.field_offset(field_name) == offset
    assert {f: c for f, _code, c in table_cls.LAYOUT}[field_name] == count


@pytest.mark.parametrize("name", sorted(PUBLISHED_SIZES))
def test_layout_totals_the_published_section_size(name: bytes) -> None:
    assert RESTRICTION_SECTIONS[name].nominal_size() == PUBLISHED_SIZES[name]


def test_a_transposed_layout_would_still_round_trip() -> None:
    """Demonstrates why the offsets above exist rather than asserting them twice.

    Swapping two same-width arrays in PTEC produces a table that packs the same
    number of bytes and reproduces any input exactly -- so a round-trip test
    cannot tell the correct layout from the broken one.
    """
    correct = TechRestrictions.LAYOUT
    swapped = (correct[1], correct[0], *correct[2:])
    assert sum(c for _, _, c in swapped) == sum(c for _, _, c in correct)
    assert [f for f, _, _ in swapped] != [f for f, _, _ in correct]


# ---------------------------------------------------------------------------
# Player-major indexing
# ---------------------------------------------------------------------------


def test_indexing_is_player_major_not_item_major() -> None:
    """``player * per_player + item``, and the transposed reading is different.

    Both readings land inside the array, so getting this backwards reads a real
    value belonging to another player rather than raising.
    """
    assert UnitRestrictions.index_of("player_unit_buildable", 3, 45) == 3 * 228 + 45
    assert UnitRestrictions.index_of("player_unit_buildable", 3, 45) != 45 * 12 + 3
    assert UpgradeRestrictions.index_of("player_max_upgrade_level", 2, 7) == 2 * 46 + 7
    assert (
        UpgradeRestrictionsExpansion.index_of("player_max_upgrade_level", 2, 7)
        == 2 * 61 + 7
    )


def test_there_are_twelve_players_not_eight() -> None:
    """COLR and FORC have 8 slots; these follow OWNR and have 12."""
    assert TOTAL_PLAYERS == 12
    table = UnitRestrictions()
    table.index_of("player_unit_buildable", 11, 0)  # must not raise
    with pytest.raises(IndexError):
        table.index_of("player_unit_buildable", 12, 0)
    assert len(table["player_unit_buildable"]) == 12 * 228


def test_out_of_range_player_or_item_is_rejected() -> None:
    table = TechRestrictions()
    with pytest.raises(IndexError):
        table.at("tech_available_for_player", -1, 0)
    with pytest.raises(IndexError):
        table.at("tech_available_for_player", 0, 24)
    with pytest.raises(KeyError):
        # A per-item array has no player dimension to index.
        table.at("tech_available_by_default", 0, 0)


def test_set_at_writes_the_entry_it_names() -> None:
    table = UnitRestrictions()
    table.set_at("player_unit_buildable", 4, 60, 0)
    assert table.at("player_unit_buildable", 4, 60) == 0
    assert table.at("player_unit_buildable", 4, 61) == 1
    assert table.at("player_unit_buildable", 5, 60) == 1
    assert table.modified


# ---------------------------------------------------------------------------
# Polarity and fill values
# ---------------------------------------------------------------------------


def test_a_blank_table_says_everything_is_available() -> None:
    """The unset value is 1 for these arrays, so zero-filling would invert them.

    A synthesised PUNI that said "no player may build anything" is the failure
    this pins: it is a legal section that silently breaks every map it lands in.
    """
    puni = UnitRestrictions()
    assert set(puni["player_unit_buildable"]) == {1}
    assert set(puni["default_unit_buildable"]) == {1}
    assert set(puni["player_unit_uses_default"]) == {1}
    assert puni.buildable(0, 0) is True

    ptec = TechRestrictions()
    assert set(ptec["tech_available_by_default"]) == {1}
    assert set(ptec["player_uses_defaults_for_tech"]) == {1}
    # The per-player arrays are genuinely zero; only the defaults are set.
    assert set(ptec["tech_available_for_player"]) == {0}


def test_a_short_section_fills_the_gap_with_the_unset_value() -> None:
    """Not zero. A truncated PUNI must read as "available", not "forbidden"."""
    table = UnitRestrictions.from_section(one_section("PUNI", b"\x00" * 10).last(b"PUNI"))
    assert table.is_short
    assert table["player_unit_buildable"][:10] == [0] * 10
    assert table["player_unit_buildable"][10] == 1
    assert set(table["player_unit_uses_default"]) == {1}


def test_default_tables_are_the_chkdraft_values_not_zeros() -> None:
    """The from-scratch defaults, pinned as literals.

    These are the one part of the module with a single source, so they are
    checked against transcribed values rather than against themselves.
    """
    assert len(DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL) == 46
    assert len(DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION) == 61
    assert len(DEFAULT_RESEARCHED_ORIGINAL) == 24
    assert len(DEFAULT_RESEARCHED_EXPANSION) == 44

    # chk.h:1435-1439 -- the first sixteen upgrades go to three.
    assert DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL[:16] == (3,) * 16
    assert DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL[18] == 0
    assert DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL[45] == 0

    # chk.h:1454-1461 -- exactly six technologies start researched.
    assert [i for i, v in enumerate(DEFAULT_RESEARCHED_ORIGINAL) if v] == [
        4, 6, 12, 14, 18, 23
    ]
    # chk.h:1629-1641 -- four expansion technologies join them.
    assert [i for i, v in enumerate(DEFAULT_RESEARCHED_EXPANSION) if v] == [
        4, 6, 12, 14, 18, 23, 28, 29, 33, 34
    ]

    # The expansion table is NOT the original padded with zeros.
    assert (
        DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION[:46] == DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL
    )
    assert set(DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION[46:]) != {0}
    assert set(DEFAULT_RESEARCHED_EXPANSION[24:]) != {0}


def test_a_blank_upgrade_table_carries_the_default_levels() -> None:
    assert UpgradeRestrictions()["default_max_level"] == list(
        DEFAULT_MAX_UPGRADE_LEVEL_ORIGINAL
    )
    assert UpgradeRestrictionsExpansion()["default_max_level"] == list(
        DEFAULT_MAX_UPGRADE_LEVEL_EXPANSION
    )
    assert TechRestrictions()["tech_researched_by_default"] == list(
        DEFAULT_RESEARCHED_ORIGINAL
    )
    assert TechRestrictionsExpansion()["tech_researched_by_default"] == list(
        DEFAULT_RESEARCHED_EXPANSION
    )


def test_accessors_follow_the_uses_default_flag() -> None:
    """Reading the per-player array alone reports a value the game ignores."""
    puni = UnitRestrictions()
    puni.set_at("player_unit_buildable", 2, 7, 0)
    # The default flag is still set, so the global default wins over that 0.
    assert puni.buildable(2, 7) is True
    puni.set_at("player_unit_uses_default", 2, 7, 0)
    assert puni.buildable(2, 7) is False

    upgr = UpgradeRestrictions()
    upgr.set_at("player_max_upgrade_level", 1, 0, 1)
    assert upgr.max_level(1, 0) == 3  # still on the default table
    upgr.set_at("player_upgrade_uses_default", 1, 0, 0)
    assert upgr.max_level(1, 0) == 1

    ptec = TechRestrictions()
    assert ptec.researched(0, 4) is True   # a default-researched technology
    assert ptec.researched(0, 5) is False
    ptec.set_at("player_uses_defaults_for_tech", 0, 4, 0)
    assert ptec.researched(0, 4) is False  # now its own row, which is zero


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def test_a_player_major_index_reports_as_player_and_item() -> None:
    """``player 3 unit 45`` is actionable; ``index 729`` is not."""
    assert (
        UnitRestrictions.index_label("player_unit_buildable", 3 * 228 + 45)
        == "player 3 unit 45"
    )
    assert (
        UnitRestrictions.index_label("default_unit_buildable", 45) == "default unit 45"
    )
    assert (
        TechRestrictionsExpansion.index_label(
            "tech_researched_for_player", 5 * 44 + 9
        )
        == "player 5 tech 9"
    )


# ---------------------------------------------------------------------------
# Records: Cuwp and Doodad
# ---------------------------------------------------------------------------

CUWP_OFFSETS = [
    ("valid_state_flags", 0, "H"),
    ("valid_field_flags", 2, "H"),
    ("owner", 4, "B"),
    ("hitpoint_percent", 5, "B"),
    ("shield_percent", 6, "B"),
    ("energy_percent", 7, "B"),
    ("resource_amount", 8, "I"),
    ("hangar_amount", 12, "H"),
    ("state_flags", 14, "H"),
    ("unknown", 16, "I"),
]

DOODAD_OFFSETS = [
    ("type", 0, "H"),
    ("xc", 2, "H"),
    ("yc", 4, "H"),
    ("owner", 6, "B"),
    ("enabled", 7, "B"),
]


@pytest.mark.parametrize(("field_name", "offset", "code"), CUWP_OFFSETS)
def test_cuwp_field_sits_at_its_offset(field_name: str, offset: int, code: str) -> None:
    """Set one field to a distinctive value and find it at the stated offset."""
    width = struct.calcsize(code)
    marker = {1: 0xA5, 2: 0xA5C3, 4: 0xA5C3F00D}[width]
    record = Cuwp(*[0] * len(CUWP_OFFSETS))
    setattr(record, field_name, marker)
    raw = record.to_bytes()
    assert len(raw) == Cuwp.SIZE == 20
    assert struct.unpack_from(f"<{code}", raw, offset)[0] == marker
    assert raw[:offset] == bytes(offset)


@pytest.mark.parametrize(("field_name", "offset", "code"), DOODAD_OFFSETS)
def test_doodad_field_sits_at_its_offset(
    field_name: str, offset: int, code: str
) -> None:
    width = struct.calcsize(code)
    marker = {1: 0xA5, 2: 0xA5C3}[width]
    record = Doodad(*[0] * len(DOODAD_OFFSETS))
    setattr(record, field_name, marker)
    raw = record.to_bytes()
    assert len(raw) == Doodad.SIZE == 8
    assert struct.unpack_from(f"<{code}", raw, offset)[0] == marker
    assert raw[:offset] == bytes(offset)


def test_uprp_is_exactly_sixty_four_slots() -> None:
    assert MAX_CUWPS * Cuwp.SIZE == 1280


def test_doodad_enabled_is_inverted() -> None:
    """0 means enabled. ``bool(enabled)`` is exactly backwards.

    A synthesised doodad written with 1 -- the obvious value for a flag named
    "enabled" -- is switched off, and nothing raises.
    """
    assert DOODAD_ENABLED == 0
    assert DOODAD_DISABLED == 1
    assert Doodad(type=1, xc=0, yc=0, owner=0, enabled=DOODAD_ENABLED).is_enabled
    assert not Doodad(type=1, xc=0, yc=0, owner=0, enabled=DOODAD_DISABLED).is_enabled
    # The all-zero record -- what a from-scratch doodad looks like -- is enabled.
    assert Doodad.from_bytes(bytes(8)).is_enabled


def test_a_partial_trailing_doodad_survives() -> None:
    """Seven corpus maps ship a 60-byte DD2: seven doodads and half an eighth.

    Dropping the remainder, or padding it out to a whole record, both change the
    file. The trailing bytes are carried instead.
    """
    payload = bytes(range(60))
    view = view_for(one_section("DD2", payload), "DD2")
    assert len(view.records) == 7
    assert view.trailing == payload[56:]
    assert view.to_bytes() == payload


# ---------------------------------------------------------------------------
# Scalar sections
# ---------------------------------------------------------------------------


def test_scenario_type_is_a_tag_not_a_number() -> None:
    view = view_for(one_section("TYPE", b"RAWB"), "TYPE")
    assert isinstance(view, ScenarioType)
    assert view.tag == b"RAWB"
    assert view.is_brood_war
    assert str(view) == "RAWB"
    assert not view_for(one_section("TYPE", b"RAWS"), "TYPE").is_brood_war


def test_editor_versions_read_as_u16() -> None:
    for name, value in (("IVER", 10), ("IVE2", 11)):
        view = view_for(one_section(name, struct.pack("<H", value)), name)
        assert isinstance(view, EditorVersion)
        assert view.version == value


def test_validation_code_splits_seeds_from_opcodes() -> None:
    seeds = list(range(256))
    opcodes = list(range(16))
    payload = struct.pack("<256I", *seeds) + struct.pack("<16B", *opcodes)
    assert len(payload) == 1040
    view = view_for(one_section("VCOD", payload), "VCOD")
    assert isinstance(view, ValidationCode)
    assert view.seeds == seeds
    assert view.opcodes == opcodes
    assert view.to_bytes() == payload
    assert not view.is_standard


def test_player_colors_accept_values_no_enum_names() -> None:
    """The corpus carries 14 and 15, and Remastered can go higher.

    Validating against Chkdraft's named colours would refuse real maps, so this
    pins that the raw byte is kept.
    """
    payload = bytes([0, 1, 2, 3, 14, 15, 200, 255])
    view = view_for(one_section("COLR", payload), "COLR")
    assert isinstance(view, PlayerColors)
    assert view.colors == payload
    assert view.to_bytes() == payload


def test_remastered_colors_split_twenty_four_and_eight() -> None:
    rgb = bytes(range(24))
    settings = bytes([3] * 8)
    view = view_for(one_section("CRGB", rgb + settings), "CRGB")
    assert isinstance(view, RemasteredColors)
    assert view.rgb == rgb
    assert view.settings == settings
    assert view.color(0) == (0, 1, 2)
    assert view.color(7) == (21, 22, 23)
    with pytest.raises(IndexError):
        view.color(8)


def test_cuwp_usage_tolerates_an_empty_section() -> None:
    """Seven corpus maps declare a zero-length UPUS."""
    view = view_for(one_section("UPUS", b""), "UPUS")
    assert isinstance(view, CuwpUsage)
    assert view.used_slots() == []
    assert view.is_short
    assert view.to_bytes() == b""

    payload = bytes([0, 1, 0, 1] + [0] * 60)
    used = view_for(one_section("UPUS", payload), "UPUS")
    assert used.used_slots() == [1, 3]


# ---------------------------------------------------------------------------
# Whole-format coverage
# ---------------------------------------------------------------------------


def test_every_documented_section_is_typed() -> None:
    """The full CHK section set, listed here rather than taken from the code."""
    documented = {
        "TYPE", "VER", "IVER", "IVE2", "VCOD", "IOWN", "OWNR", "ERA", "DIM",
        "SIDE", "MTXM", "PUNI", "UPGR", "PTEC", "UNIT", "ISOM", "TILE", "DD2",
        "THG2", "MASK", "STR", "UPRP", "UPUS", "MRGN", "TRIG", "MBRF", "SPRP",
        "FORC", "WAV", "UNIS", "UPGS", "TECS", "SWNM", "COLR", "PUPx", "PTEx",
        "UNIx", "UPGx", "TECx", "CRGB", "STRx",
    }
    assert documented - set(TYPED_SECTIONS) == set()
    assert len(TYPED_SECTIONS) == len(documented) == 41


def test_the_package_version_matches_its_metadata() -> None:
    """``__version__`` and pyproject drift silently otherwise, and a release
    picks up whichever the build backend happens to read."""
    import chklib

    root = pathlib.Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]
    assert chklib.__version__ == declared


# ---------------------------------------------------------------------------
# Real maps
# ---------------------------------------------------------------------------


def _chk_of(path) -> Chk:
    from chklib.mpq import SCENARIO_PATH, MpqArchive

    data = pathlib.Path(path).read_bytes()
    if str(path).lower().endswith(".chk"):
        return Chk.from_bytes(data)
    return Chk.from_bytes(MpqArchive(data).read_file(SCENARIO_PATH))


def _real_chks(paths):
    for path in paths:
        try:
            yield pathlib.Path(path).name, _chk_of(path)
        except Exception:  # noqa: BLE001
            continue


@pytest.mark.skipif(not CORPUS and not INSTALLED, reason="no maps available")
@pytest.mark.parametrize("name", sorted(PUBLISHED_SIZES))
def test_every_restriction_section_repacks_to_its_original_bytes(name: bytes) -> None:
    """``normalize=True`` so the packer actually runs.

    Without it ``to_bytes`` short-circuits to the stored bytes and the
    comparison is a tautology -- the mistake this suite's settings counterpart
    was written to correct.
    """
    compared = 0
    problems: list[str] = []
    for map_name, chk in _real_chks(list(CORPUS) + list(INSTALLED)):
        table = restrictions_for(chk, name)
        if table is None or table.is_short:
            continue
        compared += 1
        if table.to_bytes(normalize=True) != bytes(table.raw):
            problems.append(map_name)
    assert not problems, f"{name.decode()} repack differs in {problems[:5]}"
    assert compared > 0, f"{name.decode()} never appeared, so it was never exercised"


@pytest.mark.skipif(not CORPUS and not INSTALLED, reason="no maps available")
def test_restriction_sections_are_always_exactly_nominal_in_real_maps() -> None:
    """Measured, not assumed: none of the five is ever short or oversized.

    That is unusual -- ``WAV`` and the terrain grids both have short cases in
    this corpus -- and it is why the fill values above are exercised only when
    synthesising a section rather than when reading one.
    """
    seen = 0
    odd: list[str] = []
    for map_name, chk in _real_chks(list(CORPUS) + list(INSTALLED)):
        for name in PUBLISHED_SIZES:
            table = restrictions_for(chk, name)
            if table is None:
                continue
            seen += 1
            if table.is_short or table.is_oversized:
                odd.append(f"{map_name} {name.decode()} {len(table.raw)}")
    assert seen > 0
    assert not odd, f"expected every section exactly nominal, got {odd[:5]}"

@pytest.mark.skipif(not INSTALLED, reason="no installed maps available")
@pytest.mark.parametrize(
    "name", ["TYPE", "IVER", "IVE2", "VCOD", "COLR", "CRGB", "UPRP", "UPUS", "DD2"]
)
def test_the_remaining_typed_sections_round_trip_on_real_maps(name: str) -> None:
    """The installed corpus is what reaches COLR and CRGB at all.

    ``test_corpus_typed.py``'s gate runs on the sc64 fixtures, which are
    pre-Remastered and carry neither, so it excludes them by name. This is the
    test that makes that exclusion honest rather than a hole.
    """
    seen = 0
    problems: list[str] = []
    for map_name, chk in _real_chks(list(INSTALLED) + list(CORPUS)):
        section = chk.last(name)
        if section is None:
            continue
        seen += 1
        view = view_for(chk, name)
        if view is None:
            problems.append(f"{map_name}: no view")
        elif view.to_bytes() != bytes(section.data):
            problems.append(f"{map_name}: {name} did not round-trip")
    assert not problems, problems[:5]
    assert seen > 0, f"{name} never appeared, so this proved nothing"
