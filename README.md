# chklib

A read/write library for StarCraft map data, and `chkdiff` — a CHK-aware diff tool
that makes `git diff` say something useful about a `.scx` file.

**Status: working end to end.** Container, typed views, terrain (including ISOM), string-table editing
(`STR` and `STRx`), MPQ reading *and* writing, `chkdiff inspect`, `chkdiff diff`, `pack`/`unpack`, and the git
integration are all implemented and tested. A map can be opened, edited — including its
name, description and any trigger text — and saved back to a playable archive.

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
from chklib import Chk

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
from chklib import Chk
from chklib.views import view_for
from chklib.enums import Tileset, ActionType

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

Covered — 27 sections:

```
DIM VER ERA OWNR IOWN SIDE SPRP FORC          map and players
UNIT THG2 MRGN                                objects and locations
MTXM TILE MASK ISOM                           terrain
STR STRx                                      strings
TRIG MBRF                                     triggers and briefings
WAV SWNM UNIS UNIx UPGS UPGx TECS TECx        settings
```

What remains untyped — `TYPE IVER IVE2 VCOD PUNI UPGR PTEC PUPx PTEx DD2 UPRP UPUS COLR
CRGB` — is reachable as raw bytes through the container and round-trips untouched.

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

## Settings tables

`WAV`, `SWNM`, `UNIS`/`UNIx`, `UPGS`/`UPGx` and `TECS`/`TECx` are all the same shape: a
fixed-size section of parallel arrays, one entry per unit, upgrade, technology, switch or
sound. This is where a map's *custom* unit statistics live.

```python
from chklib.settings import UnitSettings, settings_for

units = settings_for(chk, "UNIS")
units.customised_units()            # [151]
units.custom_name_id(151)           # 30  -> "Cerebrate Zasz"
UnitSettings.displayed_hitpoints(units["hitpoints"][151])   # 1500
```

Four traps, each producing a wrong map rather than an error:

**`useDefault` is inverted from the obvious reading.** `No = 0`, `Yes = 1`, so a *set* flag
means *use the game's built-in stats and ignore the custom data here* — which makes the
custom name id dead. Read as "this entry is customised" and you invert every unit in the
map. `custom_name_id` returns 0 behind a set flag rather than a name the game never shows.

**The unset value is 1, not 0.** These structs are built with
`memset(&useDefault, Yes, ...)`, so an entry a short section never reached uses defaults.
Zero-filling the gap — the obvious way to pad — asserts the exact opposite, and the first
edit writes that inversion to disk.

**`UPGx` has one pad byte after its flag array and `UPGS` does not** — and `TECS`/`TECx`
have none, so the rule doesn't generalise. Its *position* is the whole trap: move it to the
end of the layout and the section still totals 794 bytes while all six cost arrays shift.

**Unit hitpoints are stored at 256× the displayed value.** Both directions are provided;
reading with `displayed_hitpoints` and writing the value straight back would otherwise
store 1/256th of the intended health, silently.

### How the layout is actually checked

A round-trip test cannot verify field order. `_pack` and `_unpack` walk the same layout in
the same order, so **swapping two same-width fields still round-trips every byte
perfectly**, still totals 4048, and still puts `nameStringId` at 3192. Re-deriving an
offset by summing the layout is no better — it only asks the code to agree with itself.

So the field order is transcribed from Chkdraft's `REFLECT` declarations, and the tests pin
each field at a **literal byte offset** that is never computed from the code under test.
A golden test reads a known fixture and asserts the actual values (`Cerebrate Zasz`,
1500 hp, armor 1), which a transposed layout or a two-byte drift cannot satisfy.

Measured across the 423 installed maps, and recorded as tests rather than assumed:

- **166 carry both `UNIS` and `UNIx`.** Which one wins is formally unresolved, but their
  shared arrays are byte-identical in all 166, so the ambiguity has no practical effect
  here. If that ever stops being true, precedence starts to matter.
- Duplicate settings sections are real — one map ships two `WAV` sections. They resolve
  last-wins: only `MTXM` takes the prefix-patch merge the terrain grids use.

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

## `chkdiff diff`

Compares two scenarios by meaning rather than by bytes. Exit status follows `diff(1)`:
0 when identical, 1 when they differ.

```bash
chkdiff diff a.chk b.chk
chkdiff diff --json a.chk b.chk
```

```
~ map  name
    - #1 "Z6) The Dark Templar"
    + #1 "Z8) The Dark Templar"
~ players  p6 race
    - 2 Protoss
    + 0 Zerg
~ forces  force2 members
    - p2,p3
    + p2
```

A byte diff can't do this — inserting one string shifts every offset after it, and one
new trigger moves 2400 bytes of everything downstream. So each section is compared using
whatever notion of identity it actually has:

| Kind | Sections | How |
|---|---|---|
| Identity by index | strings, locations | position by position — ids are referenced from elsewhere in the map |
| No identity | units, sprites | multiset, then pair leftovers by `(owner, type)` so a move reads as a change |
| Content **and** position | triggers | LCS alignment over content hashes |

Triggers are the hard case: no ids, but their order *is* execution order, so they can't
be treated as a set either. A positional comparison reports every later trigger as
modified the moment you insert one at the top.

The fix is an LCS alignment (`difflib.SequenceMatcher`) over per-trigger content hashes,
with survivors inside each replace-block paired greedily by similarity. Inserting a
trigger at position 0 of a 20-trigger map reports **one addition**. Insert one *and* edit
a later one, and you get:

```
+ TRIG  trigger 0
    + owners=[18] | flags=0 | if Bring(...) | do CreateUnit(...)
~ TRIG  trigger 6->7
    - do RemoveUnit(group=17, type=101, flags=0x14)
    + do RemoveUnit(group=17, time=12345, type=101, flags=0x14)
```

The `6->7` records that the trigger both moved and changed. It degrades gracefully: below
the similarity threshold a replacement is reported as an add plus a remove, which is
correct, just less informative.

## Editing

```python
from chklib import Chk, StringTable
from chklib.mpq import MpqArchive, write_scenario, SCENARIO_PATH
from chklib.views import view_for

chk = Chk.from_bytes(MpqArchive(open("map.scm", "rb").read()).read_file(SCENARIO_PATH))

# terrain, players, units, triggers - edit the typed view, write it back
view_for(chk, "ERA").value = 4
chk.replace_section("ERA", view_for(chk, "ERA").to_bytes())

# strings - the map name, description, location names and all trigger text
strings = StringTable.from_view(view_for(chk, "STR"))
strings[view_for(chk, "SPRP").name_string_id] = "Blood Bath (Remix)"
new_id = strings.add("a string that did not exist before")
chk.replace_section("STR", strings.to_bytes())

open("edited.scx", "wb").write(write_scenario(chk.to_bytes(), compress=True))
```

`StringTableView` reads; `StringTable` writes. Two things about it are load-bearing:

**String ids are positional and gaps are preserved.** Id 7 is referenced as 7 from
`SPRP`, `MRGN`, `FORC` and every trigger, so compacting the table would silently repoint
every reference in the map without touching a single trigger.

**Offsets are 16-bit, and the limit is enforced honestly.** Chkdraft's own guard sums
string lengths *without* the terminating NUL its writer then emits, so it accepts
payloads a little over 64 KB and writes offsets that wrap modulo 65536 — a corrupt map
with no error raised. This counts the NULs and raises instead. The id ceiling (32766) is
likewise derived from the offset table filling the addressable space, and named as such
rather than inherited: the sources give four different ceilings across five orders of
magnitude, none of which is a format limit.

Rebuilding the string table of all 65 corpus maps preserves every string at its own id,
and a rename leaves every location name still resolving.

### Terrain

```python
from chklib.views import terrain_for

game = terrain_for(chk, "MTXM")     # what StarCraft reads
editor = terrain_for(chk, "TILE")   # the editor's ISOM-derived layer
fog = terrain_for(chk, "MASK")

game[5, 42] = 0x0864                # (x, y), row-major
chk.replace_section("MTXM", game.to_bytes())
```

Three things here corrupt terrain silently rather than raising, so each is pinned by a
test:

**Indexing is row-major**, `y * width + x`. Chkdraft's own header comments declare these
arrays column-major — and the same wrong comment appears verbatim on MTXM, TILE, ISOM and
MASK — while every accessor in that codebase uses row-major. On a square map the two are
indistinguishable, so the tests use non-square maps.

**MTXM and TILE are distinct layers, never aliases.** They are byte-identical in only
**1 of 65** corpus maps and differ in the other 64 by a mean of 4.8% of tiles, worst 13.2%.

**A set bit in MASK means the tile *is* fogged** for that player, bit 0 being player 1.

Short, long and odd-length sections are read rather than refused — the game itself
tolerates them, and `blackvrice`'s hard error would reject real protected maps. `to_bytes()`
preserves the original length so an unmodified short section stays short; `normalize=True`
emits the full grid, which is what Chkdraft always does.

### `ISOM`

The editor's isometric terrain, on its own grid: `isom_width = tileWidth // 2 + 1`,
`isom_height = tileHeight + 1`, 8 bytes per record.

```python
from chklib.views import isom_for

grid = isom_for(chk)                 # 33x65 records for a 64x64 map
grid[1, 2].values()                  # the four sides, editor flags masked off
```

Its **bit layout is the least corroborated thing in the format** — Chkdraft is the only
witness — so each side stays a raw `u16` and the accessors are an interpretation over it.
Measuring 488 real maps supports the layout well:

| Claim | Evidence |
|---|---|
| framing `(w/2+1)*(h+1)*8` | 455 exact, 7 short, 0 long |
| value is 11 bits at 14–4 | observed range 0–2014, ceiling 2047 |
| edge flags are 3 bits at 3–1 | every observed value even, all 8 seen |
| editor flags reach the file | **21 `Visited` and 50 `Modified` sides found** |

That last row matters: the layout says Chkdraft merely *clears* those bits by convention
and nothing guarantees it. Real maps carry them, so masking on read is necessary rather
than defensive.

Two traps avoided. Chkdraft's `scenario.cpp` pads with `expected - actual` computed in
`size_t` after testing `!=`, so an *oversized* ISOM underflows into an astronomical
insert; padding here is short-only. And eudplib writes a decoy ISOM with a length past
`0x80000000` as a protection marker — the container already stops at a negative section
length, so such a map simply has no ISOM rather than a fabricated one.

### `STRx`

Remastered maps use `STRx` — exactly `STR` with the count and every offset widened from
`u16` to `u32`, nothing else changed. `string_table_for(chk)` returns whichever table a
map's references actually resolve against, because **`STRx` supersedes `STR` in either
file order** and that rule is not expressible as a section lookup. Chkdraft, bw-chk and
eudplib agree on it; blackvrice abstains when both are present and so fails to open
ordinary Remastered maps that kept a legacy `STR`.

Verified against the 24 `STRx` maps in a real installation: every scenario name and
description resolves, and rebuilding each table preserves every string at its id. Reading
one of those sections at the wrong width yields empty strings rather than an error — which
is why the width is decided by the section name and never sniffed.

An empirical note the format sources don't carry: of 423 installed maps, 24 use `STRx` and
**none uses both**, so the precedence rule is real but unexercised by those maps.

## Reading map files

`.scm`/`.scx` maps are MPQ archives, so the library reads those directly — that is what
makes `chkdiff` usable on files as they actually exist in a repository.

```python
from chklib.mpq import MpqArchive, SCENARIO_PATH

chk_bytes = MpqArchive(open("(4)Blood Bath.scm", "rb").read()).read_file(SCENARIO_PATH)
```

Reading is deliberately partial: MPQ v1 with encrypted hash/block tables, multi-sector
and single-unit files, `FIX_KEY`, and the compressions maps actually use. MPQ v2 is
**refused rather than guessed at**, because a v2 archive parsed as v1 produces
plausible-looking wrong bytes.

### Saving

```python
from chklib.mpq import write_scenario

open("edited.scx", "wb").write(write_scenario(chk_bytes, compress=True))
```

```bash
chkdiff unpack "(4)Blood Bath.scm" scenario.chk
chkdiff pack --compress scenario.chk rebuilt.scx
```

Writing produces a plainly laid out v1 archive and encrypts nothing — encryption exists
to make files hard to extract, which buys a map editor nothing.

On compression, two tools whose maps demonstrably load in StarCraft disagree, and both
are fine: **euddraft** writes zlib (`MPQ_COMPRESSION_ZLIB`), while **sc64-maps** stores
everything plainly. So the default is *stored* — the option nothing can refuse — and
`--compress` opts into zlib on the strength of euddraft's production use. Writing PKWARE
implode, what Blizzard's own maps use, would need a compressor; only the decompressor is
implemented here.

Round-tripping every installed map through the writer and back out via StormLib:

| Check | Result |
|---|---|
| 423 maps rewritten, reopened by StormLib | **423/423 identical** |
| 65 sc64 scenarios, stored and zlib | **65/65 identical** in both modes |
| Size of our zlib archives vs Blizzard's | 44% |

`chkdiff pack` refuses a scenario that fails to parse, since it would fail in StarCraft
too; `--force` overrides.

A permissive off-the-shelf reader wasn't an option: `mpyq` (BSD) cannot open a single
genuine Blizzard map — it has no decryption, and no PKWARE implode, which is compression
method 0x08 and what Blizzard maps overwhelmingly use. So both the archive layer and a
PKWARE exploder are implemented here from the format description.

**Verification is by ground truth, not inspection.** Every scenario is compared
byte-for-byte against the same file extracted by StormLib:

| Corpus | Result |
|---|---|
| 423 maps in a StarCraft 1.16.1 install | **423 byte-identical**, 0 mismatched, 0 errors |
| 65 StarCraft 64 scenarios | **65 byte-identical** |
| PKWARE sectors decompressed | 22,308, all exact, all three dictionary sizes |
| Archives with encrypted blocks | 312 |

Eight of those maps are deliberately protected — seven declare a `hashTableSize` of
`0x10000400` instead of `0x400`, and one hides its hash entry away from its home slot.
Both are handled (the declared size is clamped to what the file can hold; a failed probe
falls back to a full scan), and both are *recorded* on the archive object rather than
silently absorbed, so a caller can tell a protected map from a clean one.

One honest gap: the literal-mode byte was 0 in all 488 maps, so the 256-symbol Huffman
table for coded literals has never been exercised against ground truth. It is marked
unverified in the source.

## Git integration

`git diff` on a map file says this today:

```
Binary files a/map.scx and b/map.scx differ
```

Two lines of setup replace that with the actual change:

```bash
chkdiff install-textconv          # prints the commands; --write applies them
```

```bash
git config --global diff.starcraft.textconv "chkdiff textconv"
git config --global diff.starcraft.binary false
```

plus, in `.gitattributes`:

```
*.scm diff=starcraft
*.scx diff=starcraft
*.chk diff=starcraft
```

After which the same commit reads:

```diff
 [map]
 version      205  Brood War
 tileset      5  Desert
-dimensions   128x96 tiles  (4096x3072 px)
-name         #1 "Dust Bowl"
+dimensions   128x128 tiles  (4096x4096 px)
+name         #1 "Hot Zone"
```

`chkdiff textconv` exists as its own subcommand rather than being an alias for
`inspect --stable`, because a textconv driver has a requirement ordinary commands don't:
**it must never fail.** Git runs it over every blob on both sides of a diff, including
historical ones that may be truncated, protected, or not maps at all — and a driver that
exits non-zero makes `git diff` fail outright, which is worse than no driver. So it always
exits 0, always prints something, and degrades unreadable input to a short deterministic
note that never contains the randomised temp path git passes in.

The tests drive a real git repository rather than mocking it, and assert the status quo
(`Binary files ... differ`) as well as the improvement.

## Development

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

That runs everything except the tests that need **real maps** — and those are the ones
that found most of the bugs here, so they are worth enabling. Point two environment
variables at map corpora you already have:

```bash
export CHKLIB_SC_MAPS="/path/to/StarCraft/Maps"        # a StarCraft installation
export CHKLIB_SC64_MAPS="/path/to/sc64-maps/gamedata"  # optional, see below
```

`CHKLIB_SC_MAPS` is the valuable one. A StarCraft install ships hundreds of maps including
protected ones, Remastered `STRx` maps and current ladder maps — the corpus that turned up
duplicate `MTXM`, short and odd terrain sections, and every MPQ edge case the reader
handles.

`CHKLIB_SC64_MAPS` points at output from [sc64-maps](https://github.com/dmang-dev/sc64-maps),
scenarios extracted from a StarCraft 64 cartridge. They are useful precisely because
StarEdit never touched them, so they catch assumptions a PC-only corpus cannot.

Some tests additionally need extracted `scenario.chk` fixtures, which are gitignored
because map archives are copyrighted and are not redistributed here:

```bash
pip install -e ".[fixtures]"          # adds eudplib, Python 3.10-3.13 only
python tools/extract_fixtures.py "path/to/maps/*.scm"
```

Fixtures are produced with eudplib's MPQ reader rather than our own, deliberately: a gate
generated by an independent implementation cannot pass by agreeing with our own bugs.

**Everything is optional.** Anything not found makes the tests that need it skip, never
fail, so a clean clone with no StarCraft installed still has a green suite.

## The name

Named after the **CHK file format**, following what the rest of this ecosystem does:
Chkdraft, ChkForge and bw-chk all name the format, and StormLib and CascLib name a
technology. None of them names a Blizzard product.

This project was briefly called `openstaredit`, which was a mistake worth not repeating:
`StarEdit.exe` is a Blizzard product that ships with the game, and prefixing a product
name with `open` reads as "the open replacement for it" — exactly the association to
avoid. A file format's four-character code carries no such baggage.

## License

MIT — see [LICENSE](LICENSE).

StarCraft is a trademark of Blizzard Entertainment, Inc. This project is not
affiliated with or endorsed by Blizzard Entertainment, and redistributes none of
its data: the test corpora are read from your own installation and are gitignored.
