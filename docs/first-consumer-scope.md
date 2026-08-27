# First consumer: `chkdiff`

A library with no consumer is a guess. This scopes the one program that gets built
*with* the CHK library, defines the library by what that program needs, and stops there.

> **Status: complete.** All five milestones are done and the acceptance gate below is
> green. 790 tests pass, covering a corpus of 488 real maps.
>
> | Milestone | State |
> |---|---|
> | 1. Container | done — byte-exact round-trip on 65 maps |
> | 2. Typed views | done — 14 sections, typed round-trip green |
> | 3. `chkdiff inspect` | done — deterministic across all 65 |
> | 4. `chkdiff diff` | done — LCS trigger alignment, no cascade |
> | 5. git `textconv` | done — tested against a real repository |
>
> One thing this plan got wrong: it listed the **archive layer as library requirement 1
> but never made it a milestone**, so MPQ reading was discovered as a blocker only when
> milestone 5 needed it. It was then written fresh (MPQ v1 + a PKWARE exploder) to keep
> the project MIT-licensed, and verified byte-for-byte against StormLib on 488 maps.
>
> Consumer #2 now decides what comes next, as intended.

## The pick

**`chkdiff` — a CHK-aware diff and inspect CLI, plus a git integration.**

```
chkdiff inspect  <map>              # structured dump: sections, forces, triggers, terrain stats
chkdiff diff     <a> <b>            # semantic diff, not a byte diff
chkdiff diff     <a> <b> --json     # machine-readable, for tooling
git config diff.scx.textconv 'chkdiff inspect --stable'
```

The git hook is the hook. Register `chkdiff` as a `textconv` driver and `git diff` starts
showing **"trigger 12: action 2 changed PlaySound → Transmission"** instead of
`Binary files differ`. Map authors currently version binary blobs blind. Nothing that
exists today can do this.

## Why this consumer and not the others

| Candidate | Why not first |
|---|---|
| A cross-platform editor | Needs the whole library plus a UI plus renderers. Ships in a year, or never. |
| A web viewer | Rendering dominates the work; the CHK model would be a small corner of it. |
| A map linter | Needs opinions about what's "wrong" before the format layer is even trustworthy. |
| A batch transformer | This is `sc64-le` again. Real, but the audience is one person. |

`chkdiff` is the smallest program that is **useless without a typed model**. A byte diff
of two `.scx` files is worthless — MPQ compression and section padding make almost every
byte move. To say anything useful you must parse sections into meaning. That constraint
is what forces the library to be a library rather than a parser.

It also has an audience beyond this project: map authors reviewing contributions, the
preservation community comparing releases, and anyone trying to answer "what actually
changed in this map."

## What it forces the library to have

Scope is defined by this list. Anything not on it is out.

1. **Archive layer** — open `.scm`/`.scx` (MPQ) and bare `.chk`. Read-only is enough for
   `chkdiff`; write lands in the same layer because the round-trip gate below needs it.
2. **Section model preserving order, duplicates, and raw bytes.** Duplicate and
   out-of-order sections are how protected maps work; a `dict[name] → bytes` silently
   destroys them. This is the single most important design decision and the one every
   existing option gets wrong.
3. **Typed views over the raw bytes, not instead of them.** `DIM`, `ERA`, `OWNR`, `FORC`,
   `MTXM`, `UNIT`, `MRGN`, `STR`/`STRx`, and — the differentiator — **`TRIG`/`MBRF` as
   structured conditions and actions**, not 2400-byte blobs.
4. **Stable identity for diffing.** Triggers have no IDs, so the differ needs a content
   hash and a matching heuristic, or every insertion reports as "everything changed."
5. **Diagnostics as data.** Malformed input must produce a described problem, not an
   exception. Half the interesting maps in the wild are malformed on purpose.

Explicitly **not** in scope: rendering, GRP/tileset decoding, CASC, an ISOM brush engine,
EUD, a GUI.

## Acceptance gate

The library is done when, across the corpus:

- **Byte-exact round-trip.** Read then write an untouched map, get the original bytes
  back — sections in original order, duplicates intact, unknown sections preserved. This
  is what exercises the write path even though the CLI never exposes it.
- **Typed round-trip.** `TRIG` → structured → `TRIG` is byte-identical.
- **`chkdiff a a`** reports no differences for every map.
- **Diff sensitivity.** Mutate one trigger action; the diff names it and nothing else.

Corpus: the 65 StarCraft 64 maps in `sc64-maps` (independent — rebuilt from an N64 BOLT
archive, never touched by StarEdit), plus stock Blizzard maps, plus deliberately
malformed fixtures.

## What already exists

Most of the bottom half is written, spread across ~20 ad-hoc scripts in two repos. The
work is consolidating it behind an API, not inventing it.

| Layer | Where it lives now |
|---|---|
| PKWARE implode/explode | `sc64-maps/pkware_implode.py`, `pkware_explode.py` |
| MPQ read, incl. encrypted + imploded blocks | `sc64-maps/compare_with_stock.py` |
| MPQ write | `sc64-maps/sc64.py` |
| CHK section walk | `sc64-maps/extract_sc64_maps.py` (`chk_sections`) |
| CHK section write / patch | `sc64-le/patch_scenario.py`, `sc64-maps/merge_players.py` |
| `MBRF` briefing construction | `sc64-maps/briefing_to_mbrf.py` |
| CASC read | `sc64-maps/casc_read.py` |
| Verifier | `sc64-maps/verify_maps.py` |
| **A section-by-section differ** | **`sc64-maps/compare_with_stock.py`, 342 lines** |

`compare_with_stock.py` is `chkdiff` already — welded to a ROM extractor, comparing only
raw section bytes, with no typed model. Extracting and generalising it is the project.

Genuinely missing: the trigger condition/action model, trigger matching for diffs, the
order/duplicate-preserving section container, and a public API.

## Milestones

1. **Container** — section model with order, duplicates, raw bytes. Round-trip gate green
   on the full corpus. No typed views yet.
2. **Typed views** — the metadata sections, then `TRIG`/`MBRF`. Typed round-trip gate green.
3. **`chkdiff inspect`** — stable, deterministic textual dump.
4. **`chkdiff diff`** — matching heuristic, human and `--json` output.
5. **git `textconv`** — docs and a one-line install.

Stop at 5. Consumer #2 decides what comes next.

## Risks

- **Trigger matching is a real algorithm**, not a formatting exercise. If it proves hard,
  degrade to positional diffing with a warning rather than blocking the milestone.
- **Protected maps** will produce structures the typed layer cannot interpret. The raw
  bytes must always remain reachable so `inspect` degrades instead of failing.
- **A second Python CHK stack** is only justified if it stays the *same* stack — the
  sc64 projects should eventually depend on this library, not fork from it.
