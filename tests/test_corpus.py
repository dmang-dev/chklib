"""The milestone-1 gate, run against real maps.

Fixtures are gitignored and produced by ``tools/extract_fixtures.py``. When they
are absent these tests skip, so a clean clone still has a green suite.

The corpus is the StarCraft 64 scenarios from ``sc64-maps``: rebuilt from an N64
BOLT archive rather than authored by StarEdit, so they exercise assumptions a
PC-only corpus would never reach.
"""

from __future__ import annotations

import pathlib

import pytest

from chklib import Chk

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "corpus"
MAPS = sorted(CORPUS.glob("*.chk")) if CORPUS.is_dir() else []

pytestmark = pytest.mark.skipif(
    not MAPS,
    reason=f"no fixtures in {CORPUS}; run tools/extract_fixtures.py",
)


def _ids(paths):
    return [p.stem for p in paths]


@pytest.mark.parametrize("path", MAPS, ids=_ids(MAPS))
def test_round_trip_is_byte_exact(path: pathlib.Path) -> None:
    raw = path.read_bytes()
    assert Chk.from_bytes(raw).to_bytes() == raw


@pytest.mark.parametrize("path", MAPS, ids=_ids(MAPS))
def test_required_sections_present(path: pathlib.Path) -> None:
    """Every real scenario carries at least these."""
    chk = Chk.from_bytes(path.read_bytes())
    for name in ("VER", "DIM", "ERA", "OWNR", "MTXM"):
        assert name in chk, f"{path.stem} is missing {name}"


@pytest.mark.parametrize("path", MAPS, ids=_ids(MAPS))
def test_dim_section_is_sane(path: pathlib.Path) -> None:
    """DIM is 4 bytes of width/height, and dimensions are within engine limits."""
    chk = Chk.from_bytes(path.read_bytes())
    dim = chk.last("DIM")
    assert len(dim) == 4
    width = int.from_bytes(dim.data[0:2], "little")
    height = int.from_bytes(dim.data[2:4], "little")
    assert 0 < width <= 256 and 0 < height <= 256, f"{path.stem}: {width}x{height}"


@pytest.mark.parametrize("path", MAPS, ids=_ids(MAPS))
def test_no_parse_errors(path: pathlib.Path) -> None:
    """These maps are unprotected, so nothing should trip an error diagnostic."""
    chk = Chk.from_bytes(path.read_bytes())
    errors = [d for d in chk.diagnostics if d.severity == "error"]
    assert not errors, f"{path.stem}: {[str(e) for e in errors]}"


def test_corpus_is_not_trivially_small() -> None:
    """Guard against a gate that passes because it ran on nothing."""
    assert len(MAPS) >= 10, f"only {len(MAPS)} fixtures found"


def test_mtxm_matches_declared_dimensions() -> None:
    """MTXM is one 16-bit tile per map cell -- a real cross-section consistency check."""
    checked = 0
    for path in MAPS:
        chk = Chk.from_bytes(path.read_bytes())
        dim, mtxm = chk.last("DIM"), chk.last("MTXM")
        width = int.from_bytes(dim.data[0:2], "little")
        height = int.from_bytes(dim.data[2:4], "little")
        # MTXM may be short; StarCraft treats missing trailing tiles as 0.
        assert len(mtxm) <= width * height * 2, (
            f"{path.stem}: MTXM {len(mtxm)} bytes exceeds {width}x{height} map"
        )
        checked += 1
    assert checked == len(MAPS)
