#!/usr/bin/env python3
"""Extract ``staredit\\scenario.chk`` from real maps into the test corpus.

The container's round-trip gate is only meaningful against real files, but map
archives are copyrighted and cannot be committed. This pulls the CHK out of maps
already on disk and writes them to a gitignored fixtures directory, so the gate
runs locally without redistributing anything.

Extraction deliberately goes through **eudplib's** MPQ reader rather than our
own. Generating the corpus with an independent implementation means the gate
cannot pass by agreeing with our own bugs.

    python tools/extract_fixtures.py "path/to/maps/*.scm"

Requires the ``fixtures`` extra:  pip install -e ".[fixtures]"
"""

from __future__ import annotations

import argparse
import glob
import os
import pathlib
import sys

#: Where to look when no pattern is given. Override the root with the
#: CHKLIB_SC64_MAPS environment variable, the same one the tests use.
_SC64_ROOT = os.environ.get("CHKLIB_SC64_MAPS", "I:/projects/sc64-maps/gamedata")
DEFAULT_GLOBS = [
    f"{_SC64_ROOT}/maps/*.scm",
    f"{_SC64_ROOT}/maps-solo/*.scm",
]
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus"

SCENARIO_PATH = "staredit\\scenario.chk"


def load_mpq_reader():
    try:
        from eudplib.bindings._rust import mpqapi
    except ImportError:
        sys.exit(
            "eudplib is required to extract fixtures.\n"
            "  pip install -e \".[fixtures]\"\n"
            "Note eudplib requires Python 3.10-3.13."
        )
    return mpqapi


def extract_one(mpqapi, path: str) -> bytes:
    """Return the scenario.chk bytes from one map archive."""
    archive = mpqapi.MPQ.open(path)
    data = archive.extract_file(SCENARIO_PATH)
    if len(data) <= 1200:
        # Some maps carry a decoy scenario.chk under the neutral locale and the
        # real one under 0x409. eudplib's own loader does the same dance.
        archive.set_file_locale(0x409)
        try:
            data = archive.extract_file(SCENARIO_PATH)
        finally:
            archive.set_file_locale(0)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="*", default=None,
                        help="glob patterns matching .scm/.scx files")
    parser.add_argument("-o", "--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    patterns = args.patterns or DEFAULT_GLOBS
    paths = sorted({p for pattern in patterns for p in glob.glob(pattern)})
    if not paths:
        print(f"no maps matched: {patterns}", file=sys.stderr)
        return 1

    mpqapi = load_mpq_reader()
    args.out.mkdir(parents=True, exist_ok=True)

    written = failed = 0
    for path in paths:
        name = pathlib.Path(path).stem + ".chk"
        try:
            data = extract_one(mpqapi, path)
        except Exception as exc:  # noqa: BLE001 - one bad map must not stop the run
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
            continue
        (args.out / name).write_bytes(data)
        written += 1

    print(f"extracted {written}/{len(paths)} -> {args.out}")
    if failed:
        print(f"{failed} failed", file=sys.stderr)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
