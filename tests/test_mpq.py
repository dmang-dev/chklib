"""Tests for MPQ reading and PKWARE decompression.

Both modules were written from the format description rather than derived from
an existing implementation, so inspection proves nothing. Correctness rests on
two things:

**Known constants.** The Blizzard crypt table and string hash are pinned by the
two canonical table keys, which are published values independent of any archive.
If the crypt table generator or the hash were wrong, these would not match.

**Ground truth.** Every scenario extracted here is compared byte-for-byte
against the same file extracted by StormLib. That runs over the 65-map corpus
and, when a StarCraft installation is present, over every map it ships.
"""

from __future__ import annotations

import glob
import pathlib
import struct

import pytest

from chklib import mpq, pkware
from chklib.mpq import MpqArchive, MpqError, SCENARIO_PATH, looks_like_mpq
from chklib.pkware import PkwareError, explode

# Canonical MPQ table keys: hash("(hash table)", 3) and hash("(block table)", 3).
HASH_TABLE_KEY = 0xC3AF3770
BLOCK_TABLE_KEY = 0xEC83B3A3


# --------------------------------------------------------------------------
# Crypt primitives, pinned by published constants
# --------------------------------------------------------------------------


def test_crypt_table_shape() -> None:
    assert len(mpq._CRYPT) == 0x500
    assert all(0 <= v <= 0xFFFFFFFF for v in mpq._CRYPT)


def test_table_keys_match_the_published_constants() -> None:
    """If either the crypt table or the hash were wrong, these would differ."""
    assert mpq._hash("(hash table)", mpq._HASH_FILE_KEY) == HASH_TABLE_KEY
    assert mpq._hash("(block table)", mpq._HASH_FILE_KEY) == BLOCK_TABLE_KEY


def test_hash_is_case_insensitive() -> None:
    for hash_type in (0, 1, 2, 3):
        assert mpq._hash("staredit\\scenario.chk", hash_type) == mpq._hash(
            "STAREDIT\\SCENARIO.CHK", hash_type
        )


def test_hash_types_differ() -> None:
    """The three lookup hashes must be independent, or collisions become likely."""
    values = {mpq._hash("staredit\\scenario.chk", t) for t in (0, 1, 2, 3)}
    assert len(values) == 4


def test_decrypt_is_deterministic_and_length_preserving() -> None:
    data = bytes(range(64))
    once = mpq._decrypt(data, 0x12345678)
    assert once == mpq._decrypt(data, 0x12345678)
    assert len(once) == len(data)
    assert once != data


def test_decrypt_leaves_a_trailing_partial_word_alone() -> None:
    data = bytes(range(10))  # 2 whole words + 2 bytes
    out = mpq._decrypt(data, 0xDEADBEEF)
    assert out[8:] == data[8:]


# --------------------------------------------------------------------------
# PKWARE tables and error handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,expected",
    [
        (pkware._LITERAL_CODE, 256),
        (pkware._LENGTH_CODE, 16),
        (pkware._DISTANCE_CODE, 64),
    ],
)
def test_pkware_tables_have_the_right_symbol_counts(table, expected: int) -> None:
    """A mistyped run-length byte would change these totals."""
    assert len(table.symbols) == expected
    assert sum(table.counts) == expected


def test_length_base_table_is_not_monotonic() -> None:
    """Symbol 0 means length 3 and symbol 1 means length 2 -- easy to 'fix' wrongly."""
    assert pkware._LENGTH_BASE[0] == 3
    assert pkware._LENGTH_BASE[1] == 2
    assert len(pkware._LENGTH_BASE) == len(pkware._LENGTH_EXTRA) == 16


def test_explode_rejects_a_short_stream() -> None:
    with pytest.raises(PkwareError):
        explode(b"\x00")


def test_explode_rejects_an_invalid_literal_mode() -> None:
    with pytest.raises(PkwareError, match="literal mode"):
        explode(b"\x02\x06\x00")


def test_explode_rejects_an_invalid_dictionary_size() -> None:
    with pytest.raises(PkwareError, match="dictionary size"):
        explode(b"\x00\x09\x00")


def test_explode_rejects_a_backreference_before_the_start() -> None:
    """A corrupt stream must raise rather than silently produce wrong bytes."""
    with pytest.raises(PkwareError):
        explode(b"\x00\x04" + b"\xff" * 64, expected_size=4096)


# --------------------------------------------------------------------------
# Archive structure
# --------------------------------------------------------------------------


def test_looks_like_mpq() -> None:
    assert looks_like_mpq(b"MPQ\x1a" + bytes(40))
    assert looks_like_mpq(b"MPQ\x1b" + bytes(40))
    assert not looks_like_mpq(b"VER \x02\x00\x00\x00")
    assert not looks_like_mpq(b"")


def test_not_an_archive() -> None:
    with pytest.raises(MpqError):
        MpqArchive(b"not an mpq at all" * 4)


def test_v2_archives_are_refused_rather_than_misread() -> None:
    header = struct.pack("<4sIIHHIIII", b"MPQ\x1a", 44, 1000, 1, 3, 100, 200, 16, 4)
    with pytest.raises(MpqError, match="format version"):
        MpqArchive(header + bytes(1000))


def test_unsupported_compression_raises() -> None:
    """Better a clear error than plausible-looking wrong bytes."""
    with pytest.raises(MpqError, match="unsupported compression"):
        mpq._decompress(b"\x40rest-of-sector", 4096, mpq.FLAG_COMPRESS)


def test_zlib_sector_is_inflated() -> None:
    import zlib

    original = b"hello world " * 40
    payload = bytes((mpq.COMP_ZLIB,)) + zlib.compress(original)
    assert len(payload) < len(original), "the fixture must actually shrink"
    assert mpq._decompress(payload, len(original), mpq.FLAG_COMPRESS) == original


def test_sector_that_did_not_shrink_is_stored_verbatim() -> None:
    data = b"abcdefgh"
    assert mpq._decompress(data, 8, mpq.FLAG_COMPRESS) == data


# --------------------------------------------------------------------------
# Ground truth: the sc64 corpus
# --------------------------------------------------------------------------

SC64_MAPS = sorted(
    glob.glob(r"I:\projects\sc64-maps\gamedata\maps\*.scm")
    + glob.glob(r"I:\projects\sc64-maps\gamedata\maps-solo\*.scm")
)
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "corpus"

_PAIRS = [
    (path, FIXTURES / f"{pathlib.Path(path).stem}.chk")
    for path in SC64_MAPS
    if (FIXTURES / f"{pathlib.Path(path).stem}.chk").exists()
]


@pytest.mark.skipif(not _PAIRS, reason="sc64 maps or extracted fixtures unavailable")
@pytest.mark.parametrize(
    "archive,expected", _PAIRS, ids=[pathlib.Path(p).stem for p, _ in _PAIRS]
)
def test_matches_stormlib_on_the_sc64_corpus(archive: str, expected: pathlib.Path) -> None:
    """The fixtures were extracted by StormLib via eudplib, independently."""
    mine = MpqArchive(pathlib.Path(archive).read_bytes()).read_file(SCENARIO_PATH)
    assert mine == expected.read_bytes()


# --------------------------------------------------------------------------
# Ground truth: a real StarCraft installation
# --------------------------------------------------------------------------

SC_MAPS = sorted(
    set(glob.glob(r"I:/Blizzard/StarCraft/Maps/**/*.scm", recursive=True))
    | set(glob.glob(r"I:/Blizzard/StarCraft/Maps/**/*.scx", recursive=True))
)


def _stormlib(path: str) -> bytes:
    from eudplib.bindings._rust import mpqapi

    archive = mpqapi.MPQ.open(path)
    data = archive.extract_file(SCENARIO_PATH)
    if len(data) <= 1200:
        archive.set_file_locale(0x409)
        data = archive.extract_file(SCENARIO_PATH)
        archive.set_file_locale(0)
    return data


@pytest.mark.skipif(not SC_MAPS, reason="no StarCraft installation found")
def test_matches_stormlib_on_every_installed_map() -> None:
    """The real test: encrypted blocks, PKWARE implode, and protected archives.

    Run as one test rather than parametrized because it is the aggregate result
    that matters, and because a few hundred ids would drown the report.
    """
    pytest.importorskip("eudplib", reason="StormLib ground truth needs eudplib")

    identical = mismatched = failed = 0
    problems: list[str] = []
    for path in SC_MAPS:
        try:
            mine = MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failed += 1
            problems.append(f"{pathlib.Path(path).name}: {type(exc).__name__}: {exc}")
            continue
        try:
            truth = _stormlib(path)
        except Exception:  # noqa: BLE001 - no ground truth for this one
            continue
        if mine == truth:
            identical += 1
        else:
            mismatched += 1
            problems.append(
                f"{pathlib.Path(path).name}: {len(mine)} bytes vs {len(truth)}"
            )

    assert identical >= 100, f"only {identical} maps compared; is the install populated?"
    assert not problems, "\n".join(problems[:20])
    assert mismatched == 0 and failed == 0


@pytest.mark.skipif(not SC_MAPS, reason="no StarCraft installation found")
def test_protected_archives_are_recovered_and_flagged() -> None:
    """Protectors inflate the declared table size and displace hash entries.

    Both are handled, and both are recorded rather than silently absorbed, so a
    caller can tell a protected map from a clean one.
    """
    clamped = scanned = 0
    for path in SC_MAPS:
        archive = MpqArchive(pathlib.Path(path).read_bytes())
        try:
            archive.read_file(SCENARIO_PATH)
        except MpqError:
            continue
        clamped += bool(archive.clamped_tables)
        scanned += bool(archive.recovered_by_scan)
    # This installation contains a handful of protected maps; if it did not,
    # the recovery paths would be untested and worth knowing about.
    assert clamped or scanned, "no protected maps present to exercise recovery"


@pytest.mark.skipif(not SC_MAPS, reason="no StarCraft installation found")
def test_pkware_path_is_actually_exercised() -> None:
    """Guard against the exploder being dead code that the gate never reaches."""
    calls = 0
    real = mpq.explode

    def counting(data, expected=None):
        nonlocal calls
        calls += 1
        return real(data, expected)

    mpq.explode = counting
    try:
        for path in SC_MAPS[:20]:
            MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
    finally:
        mpq.explode = real
    assert calls > 0, "no PKWARE sector decompressed - the gate proves nothing about it"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_encrypt_inverts_decrypt() -> None:
    """The two directions differ in one place; a property test is the cheapest
    way to be sure that place is right."""
    import os

    for trial in range(200):
        data = os.urandom((trial % 30) * 4)
        key = mpq._hash(f"file{trial}.dat", mpq._HASH_FILE_KEY)
        assert mpq._decrypt(mpq._encrypt(data, key), key) == data


def test_encrypt_actually_changes_the_bytes() -> None:
    data = bytes(range(64))
    key = mpq._hash("(hash table)", mpq._HASH_FILE_KEY)
    assert mpq._encrypt(data, key) != data


@pytest.mark.parametrize("compress", [False, True], ids=["stored", "zlib"])
def test_write_then_read_round_trip(compress: bool) -> None:
    payload = b"scenario bytes " * 500
    archive = mpq.write_scenario(payload, compress=compress)
    assert looks_like_mpq(archive)
    assert MpqArchive(archive).read_file(SCENARIO_PATH) == payload


def test_written_archive_reports_v1() -> None:
    archive = MpqArchive(mpq.write_scenario(b"x" * 100))
    assert archive.format_version == 0
    assert archive.block_count >= 1


def test_writer_rejects_a_duplicate_name() -> None:
    writer = mpq.MpqWriter()
    writer.add("a\b.txt", b"one")
    with pytest.raises(MpqError, match="already added"):
        writer.add("a\b.txt", b"two")


def test_writer_rejects_an_empty_archive() -> None:
    with pytest.raises(MpqError, match="at least one file"):
        mpq.MpqWriter().to_bytes()


def test_writer_rejects_a_non_power_of_two_hash_table() -> None:
    """Real MPQ readers mask with size-1, so a non-power-of-two breaks them."""
    writer = mpq.MpqWriter(hash_table_size=100)
    writer.add("a.txt", b"x")
    with pytest.raises(MpqError, match="power of two"):
        writer.to_bytes()


def test_writer_rejects_a_hash_table_that_cannot_hold_the_files() -> None:
    writer = mpq.MpqWriter(hash_table_size=1, listfile=False)
    for index in range(4):
        writer.add(f"f{index}.txt", b"x")
    with pytest.raises(MpqError, match="cannot hold"):
        writer.to_bytes()


def test_multiple_files_round_trip() -> None:
    writer = mpq.MpqWriter()
    # Real MPQ paths use backslashes; a raw string keeps this a separator
    # rather than an escape.
    contents = {rf"dir\file{i}.dat": bytes([i]) * (i * 100 + 1) for i in range(8)}
    for index, (name, data) in enumerate(contents.items()):
        # Mix stored and compressed files inside one archive.
        writer.add(name, data, compress=bool(index % 2))
    archive = MpqArchive(writer.to_bytes())
    for name, data in contents.items():
        assert archive.read_file(name) == data, name


def test_listfile_is_written_and_lists_every_file() -> None:
    writer = mpq.MpqWriter()
    writer.add("one.txt", b"1")
    writer.add("two.txt", b"2")
    archive = MpqArchive(writer.to_bytes())
    listing = archive.read_file("(listfile)").decode("ascii").split()
    assert set(listing) == {"one.txt", "two.txt"}


def test_listfile_can_be_suppressed() -> None:
    writer = mpq.MpqWriter(listfile=False)
    writer.add("one.txt", b"1")
    archive = MpqArchive(writer.to_bytes())
    assert "(listfile)" not in archive


def test_empty_file_round_trips() -> None:
    writer = mpq.MpqWriter()
    writer.add("empty.dat", b"")
    assert MpqArchive(writer.to_bytes()).read_file("empty.dat") == b""


def test_incompressible_data_falls_back_to_storing() -> None:
    """Compression that makes a file bigger must not be used."""
    import os

    noise = os.urandom(20000)
    archive = mpq.write_scenario(noise, compress=True)
    assert MpqArchive(archive).read_file(SCENARIO_PATH) == noise


def test_compression_actually_shrinks_compressible_data() -> None:
    payload = b"aaaabbbb" * 8000
    stored = mpq.write_scenario(payload, compress=False)
    packed = mpq.write_scenario(payload, compress=True)
    assert len(packed) < len(stored) // 4


@pytest.mark.skipif(not _PAIRS, reason="no extracted fixtures available")
@pytest.mark.parametrize("compress", [False, True], ids=["stored", "zlib"])
def test_stormlib_can_open_what_we_write(tmp_path: pathlib.Path, compress: bool) -> None:
    """The check that matters: an independent implementation reopens our output.

    Our own reader agreeing with our own writer would prove nothing.
    """
    pytest.importorskip("eudplib")
    from eudplib.bindings._rust import mpqapi

    for index, (_, fixture) in enumerate(_PAIRS[:12]):
        chk = fixture.read_bytes()
        target = tmp_path / f"map{index}.scx"
        target.write_bytes(mpq.write_scenario(chk, compress=compress))
        assert mpqapi.MPQ.open(str(target)).extract_file(SCENARIO_PATH) == chk


@pytest.mark.skipif(not SC_MAPS, reason="no StarCraft installation found")
def test_every_installed_map_survives_a_rewrite(tmp_path: pathlib.Path) -> None:
    """Extract every real map, rewrite it, and have StormLib reopen the result."""
    pytest.importorskip("eudplib")
    from eudplib.bindings._rust import mpqapi

    checked = 0
    problems: list[str] = []
    for index, path in enumerate(SC_MAPS):
        try:
            chk = MpqArchive(pathlib.Path(path).read_bytes()).read_file(SCENARIO_PATH)
        except MpqError:
            continue
        target = tmp_path / f"m{index}.scx"
        target.write_bytes(mpq.write_scenario(chk, compress=True))
        if mpqapi.MPQ.open(str(target)).extract_file(SCENARIO_PATH) != chk:
            problems.append(pathlib.Path(path).name)
        checked += 1
    assert checked >= 100, f"only {checked} maps rewritten"
    assert not problems, f"{len(problems)} rewrites differ: {problems[:10]}"


def test_pack_unpack_cli_round_trip(tmp_path: pathlib.Path, capsys) -> None:
    """The user-facing path: unpack a map, pack it back, read it again."""
    from chklib.cli import main

    if not _PAIRS:
        pytest.skip("no fixtures available")
    source = _PAIRS[0][0]
    chk_out = tmp_path / "scenario.chk"
    map_out = tmp_path / "rebuilt.scx"

    assert main(["unpack", source, str(chk_out)]) == 0
    assert main(["pack", str(chk_out), str(map_out)]) == 0
    capsys.readouterr()

    original = MpqArchive(pathlib.Path(source).read_bytes()).read_file(SCENARIO_PATH)
    assert MpqArchive(map_out.read_bytes()).read_file(SCENARIO_PATH) == original


def test_pack_refuses_a_broken_scenario(tmp_path: pathlib.Path, capsys) -> None:
    """A scenario that cannot be parsed would fail in StarCraft too."""
    from chklib.cli import main

    bad = tmp_path / "bad.chk"
    bad.write_bytes(b"MTXM" + struct.pack("<i", 0x7FFFFFFF) + b"ab")
    with pytest.raises(SystemExit, match="parse errors"):
        main(["pack", str(bad), str(tmp_path / "out.scx")])
    # ...but --force is an explicit escape hatch.
    assert main(["pack", "--force", str(bad), str(tmp_path / "out.scx")]) == 0


def test_pack_rejects_an_archive_as_input(tmp_path: pathlib.Path) -> None:
    from chklib.cli import main

    already = tmp_path / "map.scx"
    already.write_bytes(mpq.write_scenario(b"x" * 64))
    with pytest.raises(SystemExit, match="already an archive"):
        main(["pack", str(already), str(tmp_path / "out.scx")])


def test_unpack_rejects_a_bare_chk(tmp_path: pathlib.Path) -> None:
    from chklib.cli import main

    plain = tmp_path / "scenario.chk"
    plain.write_bytes(b"VER " + struct.pack("<i", 2) + b"\x3b\x00")
    with pytest.raises(SystemExit, match="not an MPQ archive"):
        main(["unpack", str(plain), str(tmp_path / "out.chk")])
