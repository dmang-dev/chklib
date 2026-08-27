# Contribution targets in the existing ecosystem

Triaged 2026-08-26 against Chkdraft (100 open issues) and ChkForge (38). Ratings are
about how well the work suits an AI-assisted loop: a written spec, a self-contained
change, and an objective way to tell whether it worked.

Everything below was checked against the actual source, not inferred from issue titles.

## Test environment

A StarCraft 1.16.1 install is available at **`I:\Blizzard\StarCraft`**, carrying all
three data files Chkdraft's tests look for — `StarDat.mpq`, `BrooDat.mpq` and
`patch_rt.mpq`. Point the test suite at it with:

```powershell
$env:SC_ASSET = "I:\Blizzard\StarCraft"
```

With it set, `MappingCoreTest` is 30/30 and `MiniMapTest` runs its full comparison
across all 25 map dimensions in ~720 ms.

This matters for triage: the original split below was drawn around whether a fix could
be verified *without* game data. That constraint is gone, so the Tier 2 items are now
just as verifiable as Tier 1 — they are separated only by how much UI interaction the
verification needs.

## Tier 1 — verifiable from the test suite alone

These are the good ones. Each has an objective pass/fail signal with no UI in the loop.

### Chkdraft: `MiniMapTest` crashes instead of skipping *(no issue filed — found here)*

`test/mapping_core/test_assets.cpp` — `TestAssets::LoadScData()` returns `false`
immediately when `SC_ASSET` is unset, and `mini_map_test.cpp` uses the still-empty
`Sc::Data` regardless, so it access-violates (SEH `0xc0000005`). Anyone without a
StarCraft install sees a failing suite.

Fix: check the return and `GTEST_SKIP()`.

**Done — [PR #356](https://github.com/TheNitesWhoSay/Chkdraft/pull/356), open against
`development`.** Verified in all three states: `SC_ASSET` unset skips and the suite exits
0; `SC_ASSET` set but wrong still fails loudly, because `LoadScData`'s own `EXPECT_TRUE`
records a failure before returning; `SC_ASSET` set to the real install runs the full
comparison and passes, 30/30. That last case is what proves the guard does not mask the
test where it is meant to run.

### Chkdraft #198 — Open Map dialog defaults to "StarCraft Maps"

`src/mapping_core/map_file.cpp:743` — `getOpenMapFilters()` lists `*.scm` first and
callers default `filterIndex` to 0. Precedent for the fix already exists at
`sound_editor.cpp:355` (`u32 filterIndex = 2; // All StarCraft Compatible Sounds`).

Reorder or set a default index. Small, and the issue states the desired behaviour exactly.

### Chkdraft #141 — Allow forward slashes in paths

`src/mapping_core/system_io.cpp:40,160` already define `altSeparatorRegex` for both the
system and archive path flavours; the work is auditing where it is and isn't applied.
Pure string handling, unit-testable, and it is a real cross-platform prerequisite.

### Chkdraft #139 — Favor deleted constructors over private ones

Mechanical and compiler-verified. Low value on its own, but it is a
clean way to get a first PR merged and learn the review cadence.

### eudplib — a fresh clone does not build *(no issue filed — found here)*

Two independent breakages, both reproduced here:

1. `src/rust/stormlib-rs` and `src/epscript/pybind11` are submodules; without
   `--recursive` cargo cannot resolve the workspace.
2. `src/rust/Cargo.lock` is stale under Rust 1.96, and maturin passes `--locked`, so the
   build aborts rather than refreshing. `cargo update` drops 263 entries.

Fix is a README note plus a lock refresh. Verification: clone clean, build, done.

## Tier 2 — verification needs the running editor

Tractable, and no longer blocked on game data now that `I:\Blizzard\StarCraft` is
available. What separates these from Tier 1 is that confirming the fix means driving
Chkdraft's UI rather than reading a test result.

- **Chkdraft #204** — Windows error beep on number-key player switching. `gui_map.cpp:3019`
  already has `case WM_CHAR: return 0;`, so the beep comes from a *different* window that
  does not swallow `WM_CHAR`. Diagnosis needed before an estimate; classic Win32 bug.
- **Chkdraft #206** — settings do not persist between runs. Bounded, but needs UI testing.
- **Chkdraft #303 / #130** — case- and special-character-insensitive search. Self-contained
  string work; verification is interactive.
- **Chkdraft #84** — Find/Replace in the string editor. Well-understood feature, UI-heavy.
- **Chkdraft #349** — minimap unit draw order. `mini_map_test.cpp` carries a
  `// TODO: Minimap unit draw order needs to be fixed, then the test data can be rebuilt
  accounting for that & multiple player colors can be used`. So the fix and its test data
  move together: `miniMapPixels` in `mini_map_test_data.h` is a golden-image baseline that
  has to be regenerated, and the test currently uses a single player colour to sidestep the
  bug. Verifiable end to end now that `SC_ASSET` works — but changing a golden baseline
  means the maintainer has to trust the new one, so agree the expected order on the issue
  first.

## Tier 3 — the strategic one

### Chkdraft #99 / #105 — cross-platform, Qt

Not a starter task, but the alignment is unusually good. The maintainer, on #105
(2024-02-01):

> "Longer term I want to move it to CMake & QT and have it be a bit more cross-platform,
> but that's still a bit far in the future/getting parity with staredit & scmdraft is
> still my priority."

Half of that plan already shipped — CMake and vcpkg landed 2024-07-27. Qt and
cross-platform did not, and by the maintainer's own statement they will not any time
soon, because feature parity outranks them.

The groundwork is better than the issue age suggests. `src/mapping_core/system_io.cpp`
already branches `_WIN32` vs `<dlfcn.h>`/`<linux/limits.h>`, uses
`portable_file_dialogs`, and its header comment says it exists "to stay cross-platform
safe." The engine was written to port. What blocks it is `src/windows/` — 59 files of
hand-rolled Win32 control wrappers — plus MSVC-only build settings (`cl.exe` pinned in
`CMakePresets.json`, `/permissive-` in `CMakeLists.txt`).

This is the highest-value contribution available in the ecosystem, and it is wanted
upstream rather than speculative. It is also large. Treat it as a campaign, not a task,
and confirm scope with the maintainer on #105 before writing code.

## Not recommended

- **ChkForge** — all 38 issues are "implement \<core feature\>": terrain layer, sprite
  layer, location layer, trigger dialogs, save loading, cut/copy/paste. That is not a
  backlog, it is an unbuilt product. Last commit 2024-05-30. **It has no LICENSE file**,
  so contributions have no defined terms. Its live-OpenBW-simulation idea is worth
  stealing; the repo is not worth reviving without the author.
- **llvm-bw** — a compiler backend at "small progress on lowering." Fascinating, dormant,
  and would require LLVM 9 expertise for uncertain payoff.

## blackvrice/starcraft_map_editor

Not an issue-tracker target — it has none, and it is one person moving fast. But its plan
file is precise about where it stands:

```
M0 baseline            10/10  ####
M1 desktop shell       10/10  ####
M2 CHK lossless core     8/8  ####
M3 map archive I/O       9/9  ####
M4 EUD vertical        10/10  ####
M5 canvas + terrain    13/13  ####
M6 objects/locations     8/8  ####
M6.1 object graphics   10/10  ####
M6.2 placement popup    5/14  #...
M7 general triggers      0/7  ....
M8 stabilise + release   0/9  ....
```

83 of 108. Everything through real tileset and object rendering is done; **triggers have
not been started**, and neither has release engineering. If a collaboration is ever
attractive, triggers are the gap — and triggers are exactly what the `chkdiff` library
must model anyway.
