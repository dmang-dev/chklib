"""Where the test map corpora live.

Most of this suite runs on fixtures it builds itself. A meaningful minority runs
against **real maps**, because that is what caught the defects inspection did
not: duplicate ``MTXM`` in ladder maps, short and odd terrain sections, ``STRx``,
PKWARE-imploded and encrypted archives. Those tests need maps on disk.

Two corpora, both optional, each resolved from an environment variable so the
suite is not tied to one machine:

``CHKLIB_SC_MAPS``
    A StarCraft installation's ``Maps`` directory. Searched recursively for
    ``.scm``/``.scx``. This is the interesting corpus -- it contains protected
    maps, Remastered maps and current ladder maps.

``CHKLIB_SC64_MAPS``
    A directory of ``.scm`` files extracted from a StarCraft 64 cartridge by
    https://github.com/dmang-dev/sc64-maps. Used together with the extracted
    fixtures under ``tests/fixtures/corpus`` as an independent conformance set.

Anything not found makes the tests that need it **skip**, never fail, so a clean
clone with no StarCraft installed still has a green suite.
"""

from __future__ import annotations

import glob
import os
import pathlib

__all__ = [
    "SC_MAPS_DIR", "SC64_MAPS_DIRS", "FIXTURE_CORPUS",
    "installed_maps", "sc64_maps", "fixture_chks",
]

#: Defaults are this machine's layout; override with the environment variables.
_DEFAULT_SC_MAPS = r"I:/Blizzard/StarCraft/Maps"
_DEFAULT_SC64_MAPS = r"I:/projects/sc64-maps/gamedata"

SC_MAPS_DIR = pathlib.Path(os.environ.get("CHKLIB_SC_MAPS", _DEFAULT_SC_MAPS))
_SC64_ROOT = pathlib.Path(os.environ.get("CHKLIB_SC64_MAPS", _DEFAULT_SC64_MAPS))

#: sc64-maps writes multiplayer and single-player scenarios to sibling folders.
SC64_MAPS_DIRS = (_SC64_ROOT / "maps", _SC64_ROOT / "maps-solo", _SC64_ROOT)

#: Scenarios extracted by ``tools/extract_fixtures.py``. Gitignored: map archives
#: are copyrighted and are not redistributed with this project.
FIXTURE_CORPUS = pathlib.Path(__file__).parent / "fixtures" / "corpus"


def installed_maps() -> list[str]:
    """Every ``.scm``/``.scx`` under a StarCraft installation, or ``[]``."""
    if not SC_MAPS_DIR.is_dir():
        return []
    pattern = str(SC_MAPS_DIR / "**" / "*.sc[mx]")
    return sorted(set(glob.glob(pattern, recursive=True)))


def sc64_maps() -> list[str]:
    """Every StarCraft 64 ``.scm`` produced by sc64-maps, or ``[]``."""
    found: set[str] = set()
    for directory in SC64_MAPS_DIRS:
        if directory.is_dir():
            found.update(glob.glob(str(directory / "*.scm")))
    return sorted(found)


def fixture_chks() -> list[pathlib.Path]:
    """Extracted ``scenario.chk`` fixtures, or ``[]`` if not yet generated."""
    return sorted(FIXTURE_CORPUS.glob("*.chk")) if FIXTURE_CORPUS.is_dir() else []
