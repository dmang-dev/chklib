# openstaredit

A read/write library for StarCraft map data, and `chkdiff` — a CHK-aware diff tool
that makes `git diff` say something useful about a `.scx` file.

**Status: early but usable.** The section container, typed views and `chkdiff inspect`
are implemented, with round-trip and determinism gates green across a 65-map corpus.
`chkdiff diff` and MPQ reading are next.

## Why

Everything that can read a StarCraft map today is one of three things:

- **read-only** — [`bw-chk`](https://github.com/ShieldBattery/bw-chk) parses `scenario.chk`
  in JavaScript, triggers included, but has no writer at all and does not parse locations.
- **a section bag** — [`eudplib`](https://github.com/armoha/eudplib) models a CHK as
  `dict[bytes, bytes]`. It collapses duplicate sections and its writer injects a junk
  `ISOM` section, so it does not round-trip.
- **welded into a Windows GUI** — [Chkdraft](https://github.com/TheNitesWhoSay/Chkdraft)
  has the only complete read/write CHK engine that exists, including a trigger text
  compiler, but it is C++ inside a Win32 application with no bindings.

There is no library anywhere that reads *and* writes a CHK losslessly with a typed
trigger model. That is the gap this fills.

See [docs/first-consumer-scope.md](docs/first-consumer-scope.md) for the scope and the
acceptance gate, and [docs/contribution-targets.md](docs/contribution-targets.md) for a
survey of the surrounding ecosystem.

## The container

A `scenario.chk` is a flat sequence of sections: a 4-byte name, a signed 32-bit
little-endian length, then that many payload bytes. `Chk` preserves three things other
readers discard:

- **Order** — StarCraft applies sections in file order, later overriding earlier. A
  mapping has already lost the information needed to know which wins.
- **Duplicates** — deliberately duplicated sections are a standard protection technique.
- **Raw bytes** — unknown sections, and sections whose declared length disagrees with
  reality, survive untouched.

```python
from openstaredit import Chk

chk = Chk.from_bytes(open("scenario.chk", "rb").read())

len(chk)                    # 37
chk.last("DIM").data        # b'@\x00@\x00'  - the section StarCraft would use
chk.find("VER")             # every section with that name, in file order
chk.duplicated_names        # [b'VER '] if the map is protected that way
chk.diagnostics             # problems, reported rather than raised

chk.to_bytes() == raw       # True, always
```

Malformed input is never raised on. Half the interesting maps in the wild are malformed
on purpose, and a parser that refuses them is useless for exactly the maps people care
about.

## Typed views

Views are layered *over* the raw bytes, never in place of them, so a read/write cycle
reproduces the input exactly — including short sections, undocumented flag bits, and the
fields the format sources only call `unused`.

```python
from openstaredit import Chk
from openstaredit.views import view_for
from openstaredit.enums import Tileset, ActionType

chk = Chk.from_bytes(raw)

view_for(chk, "DIM")            # Dimensions(64x64), .pixel_width, .tile_count
view_for(chk, "VER").name       # 'Hybrid (Brood War compatible)'
Tileset(view_for(chk, "ERA").value).name          # 'Badlands'

strings = view_for(chk, "STR")
sprp    = view_for(chk, "SPRP")
strings.text(sprp.name_string_id)                 # 'Tutorial 1'

for unit in view_for(chk, "UNIT"):                # 36-byte records
    unit.owner, unit.xc, unit.yc, unit.type

for trigger in view_for(chk, "TRIG"):             # 2400-byte records
    for action in trigger.used_actions():
        ActionType(action.action_type)            # CreateUnitWithProperties, ...
```

Covered: `DIM VER ERA OWNR IOWN SIDE SPRP UNIT THG2 MRGN TRIG MBRF STR`. Everything else
is reachable as raw bytes through the container.

Three behaviours worth knowing, each of which a naive implementation gets wrong:

- **`TRIG` and `MBRF` share a byte layout but not an action id space.** Ids 0–9 mean
  entirely different things in each, so a view carries `is_briefing`.
- **String ids are 1-based, and strings end at the next NUL** — never at the following
  slot's offset. In 30 of the 65 corpus maps the string data is not in ascending id
  order, so offset-differencing yields negative lengths.
- **Location ids are 1-based too**: `Anywhere` is id 64, which is file record 63.

The format work behind this is in `.research/SPEC.md` (gitignored, regenerable): six
independent implementations cross-checked against each other and validated against the
corpus, with every claim carrying a confidence tier and every unresolved disagreement
listed rather than guessed at.

## `chkdiff inspect`

A deterministic textual rendering of a scenario, designed so that `git diff` can use it
as a `textconv` driver.

```bash
chkdiff inspect scenario.chk
chkdiff inspect --stable scenario.chk    # omit the filename, for git
```

The point is that a small change to a map becomes a small change in the text. Moving one
unit and dropping its hitpoints — 3 changed bytes in the binary — reads as:

```diff
-p12   type=188   at=(224,448)  hp=100%  resources=5000
+p12   type=188   at=(288,448)  hp=55%   resources=5000
```

Three rules make that work, and they are what the tests pin down:

- **Determinism.** The same bytes always produce the same characters — no paths, no
  timestamps, no iteration-order accidents. Verified over all 65 corpus maps.
- **One fact per line.** A moved unit is one changed line, not a reflowed block.
- **Canonical ordering where order carries no meaning.** Units and sprites are sorted by
  content and carry no index, so inserting one produces a constant-size diff regardless
  of how many units the map has. Triggers, locations and strings keep file order, because
  for them the index *is* the identity.

Nothing is invented: unit and sprite types print as numbers, because naming them needs
`units.dat` from a StarCraft installation, which this library does not read.

**MPQ archives are not supported yet.** `chkdiff` reads a bare `scenario.chk`; pointing it
at a `.scm`/`.scx` gives a message explaining how to extract one. That gap has to close
before the git integration is actually useful, since maps in a repository are archives.

## Development

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

The corpus tests need fixtures, which are extracted from maps on your own disk and are
gitignored — map archives are copyrighted and are not redistributed here:

```bash
pip install -e ".[fixtures]"          # adds eudplib, Python 3.10-3.13 only
python tools/extract_fixtures.py "path/to/maps/*.scm"
```

Fixtures are produced with eudplib's MPQ reader rather than our own, deliberately: a gate
generated by an independent implementation cannot pass by agreeing with our own bugs.

Without fixtures the corpus tests skip and the rest of the suite still runs.

## License

MIT. Not affiliated with or endorsed by Blizzard Entertainment.
