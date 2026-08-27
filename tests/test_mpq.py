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

from openstaredit import mpq, pkware
from openstaredit.mpq import MpqArchive, MpqError, SCENARIO_PATH, looks_like_mpq
from openstaredit.pkware import PkwareError, explode

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
