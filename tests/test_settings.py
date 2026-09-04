"""Tests for the settings tables.

A note on what these can and cannot check, because the obvious test here is
worthless. ``_pack`` and ``_unpack`` walk the same ``LAYOUT`` in the same order,
so a round-trip through both is symmetric: **swap two same-width fields and
every byte still round-trips perfectly.** A round-trip test therefore proves
nothing about field order, and neither does re-deriving an offset by summing
``LAYOUT`` -- that just asks the code to agree with itself.

So the layout is pinned by **literal byte offsets** transcribed from Chkdraft's
``REFLECT`` declarations (``chk.h``), never computed from the module under test.
Those offsets are the real test; the round-trips only guard the write path.

Four format traps get their own tests, each producing a wrong map rather than an
error: the inverted ``useDefault`` polarity, its unset value being 1 rather than
0, the ``UPGx`` pad byte's *position*, and the 256x hitpoint scale.
"""

from __future__ import annotations

import pathlib
import struct

import pytest
from conftest import installed_maps

from chklib import Chk
from chklib.mpq import SCENARIO_PATH, MpqArchive
from chklib.settings import (
    USE_DEFAULT_NO,
    USE_DEFAULT_YES,
    LayoutError,
    SoundPaths,
    SwitchNames,
    TechSettings,
    TechSettingsExpansion,
    UnitSettings,
    UnitSettingsExpansion,
    UpgradeSettings,
    UpgradeSettingsExpansion,
    settings_for,
)
from chklib.views import string_table_for

ALL_TABLES = (
    SoundPaths, SwitchNames,
    UnitSettings, UnitSettingsExpansion,
    UpgradeSettings, UpgradeSettingsExpansion,
    TechSettings, TechSettingsExpansion,
)


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def chk_with(name: bytes, payload: bytes) -> Chk:
    return Chk.from_bytes(sect(name, payload))


def table_of(table_cls, payload: bytes):
    name = table_cls.SECTION.encode().ljust(4, b" ")
    return settings_for(chk_with(name, payload), name)


# --------------------------------------------------------------------------
# Layout: literal offsets, transcribed from the reference implementation
# --------------------------------------------------------------------------

# (section, field, byte offset, struct code). Every number here is a literal
# from Chkdraft's declarations -- none is computed from chklib's own LAYOUT, or
# the test would only be asking the code whether it agrees with itself.
FIELD_OFFSETS = [
    ("WAV",  "sound_string_ids",     0,    "I"),
    ("SWNM", "switch_string_ids",    0,    "I"),

    ("UNIS", "use_default",          0,    "B"),
    ("UNIS", "hitpoints",            228,  "I"),
    ("UNIS", "shield_points",        1140, "H"),
    ("UNIS", "armor_level",          1596, "B"),
    ("UNIS", "build_time",           1824, "H"),
    ("UNIS", "mineral_cost",         2280, "H"),
    ("UNIS", "gas_cost",             2736, "H"),
    ("UNIS", "name_string_ids",      3192, "H"),
    ("UNIS", "base_damage",          3648, "H"),
    ("UNIS", "upgrade_damage",       3848, "H"),

    ("UNIx", "name_string_ids",      3192, "H"),
    ("UNIx", "base_damage",          3648, "H"),
    ("UNIx", "upgrade_damage",       3908, "H"),

    ("UPGS", "use_default",          0,    "B"),
    ("UPGS", "base_mineral_cost",    46,   "H"),
    ("UPGS", "mineral_cost_factor",  138,  "H"),
    ("UPGS", "base_gas_cost",        230,  "H"),
    ("UPGS", "gas_cost_factor",      322,  "H"),
    ("UPGS", "base_research_time",   414,  "H"),
    ("UPGS", "research_time_factor", 506,  "H"),

    # The pad byte sits at 61, so the costs start at 62 rather than 61.
    ("UPGx", "use_default",          0,    "B"),
    ("UPGx", "unused",               61,   "B"),
    ("UPGx", "base_mineral_cost",    62,   "H"),
    ("UPGx", "mineral_cost_factor",  184,  "H"),
    ("UPGx", "base_gas_cost",        306,  "H"),
    ("UPGx", "gas_cost_factor",      428,  "H"),
    ("UPGx", "base_research_time",   550,  "H"),
    ("UPGx", "research_time_factor", 672,  "H"),

    ("TECS", "use_default",          0,    "B"),
    ("TECS", "mineral_cost",         24,   "H"),
    ("TECS", "gas_cost",             72,   "H"),
    ("TECS", "research_time",        120,  "H"),
    ("TECS", "energy_cost",          168,  "H"),

    ("TECx", "use_default",          0,    "B"),
    ("TECx", "mineral_cost",         44,   "H"),
    ("TECx", "gas_cost",             132,  "H"),
    ("TECx", "research_time",        220,  "H"),
    ("TECx", "energy_cost",          308,  "H"),
]

BY_SECTION = {cls.SECTION: cls for cls in ALL_TABLES}
SENTINEL = {"B": 0xA5, "H": 0xA55A, "I": 0xA55A3C3C}


@pytest.mark.parametrize(
    "section,fieldname,offset,code",
    FIELD_OFFSETS,
    ids=[f"{s}.{f}@{o}" for s, f, o, _ in FIELD_OFFSETS],
)
def test_each_field_packs_at_its_documented_byte_offset(
    section: str, fieldname: str, offset: int, code: str
) -> None:
    """Write one sentinel into entry 0 of a field and find it at the offset the
    reference implementation declares.

    This is what catches a transposed layout. Two same-width fields swapped
    still round-trip byte-for-byte, still total 4048, and still put
    ``name_string_ids`` at 3192 -- but they land at each other's offsets here.
    """
    table = BY_SECTION[section]()
    value = SENTINEL[code]
    table.set(fieldname, 0, value)
    packed = table.to_bytes()

    width = {"B": 1, "H": 2, "I": 4}[code]
    assert struct.unpack_from(f"<{code}", packed, offset)[0] == value, (
        f"{section}.{fieldname} did not land at byte {offset}"
    )
    # And nothing else moved: every other byte is still its default.
    blank = BY_SECTION[section]().to_bytes()
    assert packed[:offset] == blank[:offset]
    assert packed[offset + width :] == blank[offset + width :]


def test_upgx_pad_byte_sits_between_the_flags_and_the_costs() -> None:
    """The pad's *position* is the whole trap. Moving it to the end of the
    layout keeps the total at 794 and keeps a field named ``unused`` present,
    while shifting all six cost arrays one byte."""
    table = UpgradeSettingsExpansion()
    table.set("base_mineral_cost", 0, 0x1234)
    packed = table.to_bytes()
    assert len(packed) == 794
    assert packed[61] == 0, "byte 61 is the pad, not a cost"
    assert struct.unpack_from("<H", packed, 62)[0] == 0x1234
    assert UpgradeSettings.nominal_size() == 598
    assert "unused" not in {n for n, _, _ in UpgradeSettings.LAYOUT}


def test_tech_tables_have_no_pad_byte() -> None:
    """The UPGx rule deliberately does not generalise."""
    for table_cls in (TechSettings, TechSettingsExpansion):
        assert "unused" not in {n for n, _, _ in table_cls.LAYOUT}
    table = TechSettings()
    table.set("mineral_cost", 0, 0x1234)
    assert struct.unpack_from("<H", table.to_bytes(), 24)[0] == 0x1234


@pytest.mark.parametrize(
    "table_cls,size",
    [
        (SoundPaths, 2048), (SwitchNames, 1024),
        (UnitSettings, 4048), (UnitSettingsExpansion, 4168),
        (UpgradeSettings, 598), (UpgradeSettingsExpansion, 794),
        (TechSettings, 216), (TechSettingsExpansion, 396),
    ],
    ids=lambda v: getattr(v, "SECTION", v),
)
def test_layout_matches_the_published_size(table_cls, size: int) -> None:
    assert table_cls.nominal_size() == size
    assert len(table_cls().to_bytes()) == size


def test_layout_check_raises_rather_than_asserts() -> None:
    """``python -O`` strips ``assert``, so a bare assertion here would let a
    layout typo through silently in exactly the deployment that can least
    afford it."""
    import chklib.settings as settings_mod

    source = pathlib.Path(settings_mod.__file__).read_text(encoding="utf-8")
    body = source[source.index("def _check_layouts"):]
    assert "assert " not in body, "layout guard must not rely on assert"
    assert issubclass(LayoutError, Exception)


# --------------------------------------------------------------------------
# The polarity trap, and its unset value
# --------------------------------------------------------------------------


def test_use_default_set_means_the_custom_data_is_ignored() -> None:
    payload = bytearray(UnitSettings.nominal_size())
    payload[7] = USE_DEFAULT_YES
    payload[9] = USE_DEFAULT_NO
    struct.pack_into("<H", payload, 3192 + 7 * 2, 111)
    struct.pack_into("<H", payload, 3192 + 9 * 2, 222)
    table = table_of(UnitSettings, bytes(payload))

    assert (USE_DEFAULT_NO, USE_DEFAULT_YES) == (0, 1), "polarity constants"
    assert table.uses_defaults(7)
    assert not table.uses_defaults(9)
    assert table.name_string_ids[7] == 111
    assert table.custom_name_id(7) == 0, "a name behind a set flag is dead data"
    assert table.custom_name_id(9) == 222


def test_a_short_section_reads_as_using_defaults_not_as_customised() -> None:
    """The unset value is 1. Zero-filling the gap -- the obvious way to pad --
    claims every entry the section did not reach is explicitly customised with
    all-zero statistics, and the first edit writes that to disk."""
    table = table_of(UnitSettings, b"\x00")          # one explicit custom entry
    assert table.is_short
    assert table.customised_units() == [0], "only the byte actually present"
    assert table.uses_defaults(227)
    assert table["use_default"][227] == USE_DEFAULT_YES

    # And it survives a write.
    table.set("mineral_cost", 5, 42)
    reread = table_of(UnitSettings, table.to_bytes())
    assert reread.customised_units() == [0]


@pytest.mark.parametrize(
    "table_cls", [UnitSettings, UpgradeSettings, UpgradeSettingsExpansion,
                  TechSettings, TechSettingsExpansion], ids=lambda c: c.SECTION)
def test_a_blank_table_uses_defaults_everywhere(table_cls) -> None:
    table = table_cls()
    assert set(table["use_default"]) == {USE_DEFAULT_YES}


def test_customised_units_lists_the_clear_flags() -> None:
    payload = bytearray(b"\x01" * 228 + bytes(UnitSettings.nominal_size() - 228))
    payload[3] = payload[200] = 0
    assert table_of(UnitSettings, bytes(payload)).customised_units() == [3, 200]


def test_hitpoints_convert_both_ways() -> None:
    assert UnitSettings.displayed_hitpoints(1500 * 256) == 1500
    assert UnitSettings.stored_hitpoints(1500) == 1500 * 256
    # The round-trip a caller actually performs must not lose the factor.
    table = UnitSettings()
    table.set("hitpoints", 5, UnitSettings.stored_hitpoints(80))
    assert UnitSettings.displayed_hitpoints(table["hitpoints"][5]) == 80


# --------------------------------------------------------------------------
# String-referencing tables
# --------------------------------------------------------------------------


def test_wav_holds_512_u32_string_ids() -> None:
    payload = bytearray(2048)
    struct.pack_into("<I", payload, 0, 29)
    struct.pack_into("<I", payload, 6 * 4, 24)
    table = table_of(SoundPaths, bytes(payload))
    assert len(table.sound_string_ids) == 512
    assert table.used_slots() == [0, 6]
    assert table.sound_string_ids[6] == 24


def test_swnm_holds_256_u32_string_ids() -> None:
    payload = bytearray(1024)
    struct.pack_into("<I", payload, 3 * 4, 77)
    table = table_of(SwitchNames, bytes(payload))
    assert len(table.switch_string_ids) == 256
    assert table.named_switches() == [3]


def test_settings_for_ignores_unknown_names() -> None:
    chk = chk_with(b"WAV ", bytes(2048))
    assert settings_for(chk, "MTXM") is None
    assert settings_for(chk, "ISOM") is None
    assert settings_for(chk, "SWNM") is None, "absent section, not a bad name"


def test_settings_for_accepts_padded_and_bytes_names() -> None:
    chk = chk_with(b"WAV ", bytes(2048))
    for name in ("WAV", "WAV ", b"WAV", b"WAV "):
        assert settings_for(chk, name) is not None, name


def test_settings_lookup_is_case_sensitive() -> None:
    """``UNIS`` and ``UNIx`` differ only in the case of their last character, so
    a case-insensitive lookup would silently read one as the other -- and they
    are different sizes."""
    chk = chk_with(b"UNIx", bytes(4168))
    assert settings_for(chk, "UNIx") is not None
    assert settings_for(chk, "UNIS") is None


def test_duplicate_sections_resolve_last_wins() -> None:
    """SPEC 1.5 lists only ``MTXM`` under the Override policy, so these sections
    keep the Standard rule. A real map carries two WAV sections, so this is not
    hypothetical."""
    first = bytearray(2048)
    struct.pack_into("<I", first, 0, 111)
    second = bytearray(2048)
    struct.pack_into("<I", second, 0, 222)
    chk = Chk.from_bytes(sect(b"WAV ", bytes(first)) + sect(b"WAV ", bytes(second)))
    assert settings_for(chk, "WAV").sound_string_ids[0] == 222


# --------------------------------------------------------------------------
# The write path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table_cls", ALL_TABLES, ids=lambda c: c.SECTION)
def test_an_untouched_table_re_emits_its_bytes_verbatim(table_cls) -> None:
    payload = (bytes(range(256)) * (table_cls.nominal_size() // 256 + 1))[
        : table_cls.nominal_size()
    ]
    assert table_of(table_cls, payload).to_bytes() == payload


@pytest.mark.parametrize("table_cls", ALL_TABLES, ids=lambda c: c.SECTION)
def test_repacking_a_full_section_reproduces_it(table_cls) -> None:
    """``normalize=True`` forces the packer to run, which the untouched path
    never does. This checks the write path is self-consistent -- it cannot check
    field order, which is what the byte-offset tests are for."""
    payload = (bytes(range(256)) * (table_cls.nominal_size() // 256 + 1))[
        : table_cls.nominal_size()
    ]
    assert table_of(table_cls, payload).to_bytes(normalize=True) == payload


@pytest.mark.parametrize("table_cls", ALL_TABLES, ids=lambda c: c.SECTION)
def test_short_sections_are_tolerated(table_cls) -> None:
    for payload in (b"", b"\x01", b"\xff" * 9):
        table = table_of(table_cls, payload)
        assert table.is_short
        assert table.to_bytes() == payload, "untouched keeps its original length"
        assert len(table.to_bytes(normalize=True)) == table_cls.nominal_size()


def test_an_edit_past_a_short_section_is_not_discarded() -> None:
    """The same failure mode the terrain grids had: splicing back into a short
    section would silently drop the edit."""
    table = table_of(UnitSettings, b"\x00\x00")
    table.set("mineral_cost", 200, 1234)
    written = table.to_bytes()
    assert len(written) == UnitSettings.nominal_size()
    assert table_of(UnitSettings, written)["mineral_cost"][200] == 1234


def test_a_truncated_final_element_keeps_the_bytes_it_had() -> None:
    """These are little-endian, so the low bytes a section does contain are
    meaningful; dropping the whole element loses them."""
    payload = bytes(228) + b"\x11\x22"          # two bytes into hitpoints[0]
    table = table_of(UnitSettings, payload)
    assert table["hitpoints"][0] == 0x2211
    table.set("gas_cost", 5, 7)                 # edit something unrelated
    assert table.to_bytes()[228:232] == b"\x11\x22\x00\x00"


def test_bytes_past_the_layout_survive_an_edit() -> None:
    """An oversized section's tail is not modelled, but discarding it on the
    first edit would lose data the file carried."""
    payload = bytes(UnitSettings.nominal_size()) + b"\xde\xad\xbe\xef"
    table = table_of(UnitSettings, payload)
    assert table.is_oversized and not table.is_short
    assert table.trailing_bytes == b"\xde\xad\xbe\xef"
    table.set("mineral_cost", 0, 1)
    written = table.to_bytes()
    assert len(written) == len(payload)
    assert written[-4:] == b"\xde\xad\xbe\xef"


def test_a_blank_table_normalizes_into_a_valid_section() -> None:
    """Constructing a table to synthesise a new section is the natural use of
    the dataclass defaults."""
    assert len(SoundPaths().to_bytes(normalize=True)) == 2048
    assert len(UnitSettings().to_bytes(normalize=True)) == 4048


def test_set_rejects_an_out_of_range_index() -> None:
    """A negative index would otherwise wrap to the far end of the array and
    corrupt a different entry while appearing to succeed."""
    table = TechSettings()
    with pytest.raises(IndexError, match="outside"):
        table.set("mineral_cost", -1, 5)
    with pytest.raises(IndexError, match="outside"):
        table.set("mineral_cost", 24, 5)
    assert set(table["mineral_cost"]) == {0}, "nothing was written"
    assert not table.modified


def test_set_rejects_a_value_too_wide_for_its_field() -> None:
    """Otherwise the table is marked modified, cannot be packed, and can no
    longer fall back to its verbatim bytes -- and the error names a struct
    format code rather than the field."""
    table = UnitSettings()
    with pytest.raises(ValueError, match="mineral_cost"):
        table.set("mineral_cost", 0, 70000)
    with pytest.raises(ValueError, match="hitpoints"):
        table.set("hitpoints", 0, -1)
    assert not table.modified
    assert len(table.to_bytes(normalize=True)) == 4048


def test_reads_reject_an_out_of_range_unit() -> None:
    table = UnitSettings()
    with pytest.raises(IndexError, match="outside"):
        table.uses_defaults(-1)
    with pytest.raises(IndexError, match="outside"):
        table.custom_name_id(228)


def test_writing_a_wrong_length_array_raises() -> None:
    table = TechSettings()
    table.arrays["mineral_cost"] = [1, 2, 3]
    table.modified = True
    with pytest.raises(ValueError, match="expected exactly"):
        table.to_bytes()


# --------------------------------------------------------------------------
# Real maps
# --------------------------------------------------------------------------

CORPUS = sorted((pathlib.Path(__file__).parent / "fixtures" / "corpus").glob("*.chk"))
INSTALLED = installed_maps()
SECTION_NAMES = ("WAV", "SWNM", "UNIS", "UNIx", "UPGS", "UPGx", "TECS", "TECx")


def _chk_of(path) -> Chk:
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
def test_every_settings_section_repacks_to_its_original_bytes() -> None:
    """The gate that matters: ``normalize=True`` so the packer actually runs.

    Comparing ``to_bytes()`` without it compares a section's bytes to
    themselves, because an unmodified table short-circuits to ``raw``.
    """
    seen = dict.fromkeys(SECTION_NAMES, 0)
    problems: list[str] = []
    for map_name, chk in _real_chks(list(CORPUS) + list(INSTALLED)):
        for name in SECTION_NAMES:
            table = settings_for(chk, name)
            if table is None:
                continue
            seen[name] += 1
            original = chk.last(name.encode().ljust(4, b" ")).data
            if table.is_short:
                continue  # a short section legitimately grows when repacked
            if table.to_bytes(normalize=True) != original:
                problems.append(f"{map_name} {name}")
    assert not problems, problems[:6]
    for name, count in seen.items():
        assert count > 0, f"{name} never appeared, so it was never exercised"


@pytest.mark.skipif(not CORPUS, reason="fixture corpus not available")
def test_a_known_map_reads_its_known_custom_unit() -> None:
    """A golden value, which is what actually distinguishes a right offset from
    a wrong one. A transposed layout or a two-byte drift yields a different
    number or a different string, not merely a printable one.
    """
    # Named exactly, not matched by substring. The campaign ships two maps
    # called "The Dark Templar" -- Z6 and Z8 -- with different UNIS data, so a
    # substring match pinned these values against whichever sorted first. That
    # held, but it made the golden values depend on filename order rather than
    # on the map they were read from, which is the opposite of the point.
    wanted = "008-019 Z6) The Dark Templar.chk"
    match = [p for p in CORPUS if p.name == wanted]
    if not match:
        pytest.skip(f"{wanted}, the fixture this pins, is not present")
    chk = _chk_of(match[0])
    table = settings_for(chk, "UNIS")
    strings = string_table_for(chk)

    assert table.customised_units() == [151]
    assert UnitSettings.displayed_hitpoints(table["hitpoints"][151]) == 1500
    assert table["armor_level"][151] == 1
    assert table["build_time"][151] == 15
    name_id = table.custom_name_id(151)
    assert name_id == 30
    assert strings.get(name_id) == b"Cerebrate Zasz"


@pytest.mark.skipif(not CORPUS and not INSTALLED, reason="no maps available")
def test_custom_unit_names_are_unit_names_not_file_paths() -> None:
    """A wrong offset lands in a neighbouring array whose ids resolve to the
    ``staredit\\wav\\...`` sound paths, which are printable and would satisfy a
    weaker check.
    """
    found: list[str] = []
    for _, chk in _real_chks(list(CORPUS) + list(INSTALLED)):
        table = settings_for(chk, "UNIS") or settings_for(chk, "UNIx")
        strings = string_table_for(chk)
        if table is None or strings is None:
            continue
        for unit_type in table.customised_units():
            string_id = table.custom_name_id(unit_type)
            raw = strings.get(string_id) if string_id else None
            if raw:
                found.append(raw.decode("cp1252", "replace"))
        if len(found) >= 20:
            break
    assert found, "no custom unit name resolved on any map"
    paths = [n for n in found if "\\" in n or n.lower().endswith(".wav")]
    assert not paths, f"these are sound paths, not unit names: {paths[:3]}"


@pytest.mark.skipif(not INSTALLED, reason="no StarCraft installation found")
def test_unis_and_unix_coexist_and_agree_on_installed_maps() -> None:
    """SPEC 7.6 leaves the precedence unresolved. It is heavily exercised, but
    the shared arrays are identical everywhere here, so the ambiguity has no
    practical effect. Recorded rather than assumed: if this ever fails,
    precedence starts to matter and has to be settled.

    Measured over the installed maps only. The fixture corpus all carries both
    sections, so counting it would satisfy the threshold before a single
    installed map was read.
    """
    both = disagreed = 0
    for _, chk in _real_chks(INSTALLED):
        if "UNIS" not in chk or "UNIx" not in chk:
            continue
        both += 1
        a, b = settings_for(chk, "UNIS"), settings_for(chk, "UNIx")
        if any(a[key] != b[key] for key in ("use_default", "hitpoints", "name_string_ids")):
            disagreed += 1
    assert both > 50, f"only {both} installed maps carry both; expected it to be common"
    assert disagreed == 0, f"{disagreed} maps disagree between UNIS and UNIx"
