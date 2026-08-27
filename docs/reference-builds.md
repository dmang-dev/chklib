# reference/

External projects cloned for study. **Nothing here is vendored** — `reference/` is
gitignored. Each is an upstream clone at the revision noted below.

| Repo | Why it's here | License |
|---|---|---|
| `Chkdraft` | The mature open-source SC1 editor. Its `src/mapping_core` is the reference CHK/MPQ/CASC engine. | MIT |
| `ChkForge` | Qt6 + OpenBW shell over Chkdraft's core. Architecture reference. **No LICENSE file — not reusable.** | none |
| `starcraft_map_editor` | blackvrice — Flutter/Dart SC:R editor, started 2026-07-26. Lossless CHK core + helper-process design. | MIT |
| `eudplib` | armoha — the UMS/EUD trigger-generation library. Python + Rust. | MIT |
| `euddraft` | armoha — plugin/build driver that applies eudplib code to a map. | MIT |
| `llvm-bw` | heinermann — lowers LLVM IR into BW triggers. Incomplete, but the idea is the point. | (unstated) |
| `openbw` | Open-source BW engine; what ChkForge embeds for live simulation. | GPL-3.0 |

## Toolchain hazard on this machine

`git`, `cmake` and `ninja` all resolve to `C:\devkitPro\msys2\usr\bin\` on the default
PATH. Every recipe below strips devkitPro from PATH first. Use:

- git — `C:\Program Files\Git\cmd\git.exe`
- cmake/ninja — from VS 2022 BuildTools `Common7\IDE\CommonExtensions\Microsoft\CMake\`

Also: `flutter.bat` and `bootstrap-vcpkg.bat` must be run from **PowerShell**. Invoking
them as `cmd.exe /c "..."` from the Bash tool drops into an interactive prompt instead
of running, and reports success.

## Chkdraft

Windows/MSVC only. Presets pin `cl.exe` and pass `/permissive-`; `src/windows/` is a
hand-rolled Win32 control library. Needs vcpkg, which is **not** bundled and has no CI
workflow to copy.

```powershell
# vcpkg must be a FULL clone. A --filter=blob:none partial clone fails during
# `vcpkg install`: every port checkout-index triggers a promisor fetch.
git clone https://github.com/microsoft/vcpkg.git I:\vcpkg
I:\vcpkg\bootstrap-vcpkg.bat -disableMetrics
$env:VCPKG_ROOT = "I:\vcpkg"
# then, from a VS dev shell (Enter-VsDevShell -DevCmdArguments '-arch=x64'):
cmake --preset x64-release
cmake --build out/build/x64-release --parallel
```

vcpkg builds icu, freetype, harfbuzz, stormlib, casclib, glm, bzip2 and gtest from
source. ICU dominates; budget a long first configure.

## eudplib (from source)

Requires Python 3.10–3.13 (**not 3.14** — `requires-python = ">=3.10, <3.14"`, and this
machine's default `py -3` is 3.14), plus Rust and MSVC for the static StormLib.

Two non-obvious steps, both of which fail a fresh clone:

1. `git submodule update --init --recursive` — `src/rust/stormlib-rs` and
   `src/epscript/pybind11` are submodules; without them cargo can't resolve the workspace.
2. `cargo update` in `src/rust` — the checked-in `Cargo.lock` is stale under Rust 1.96,
   and maturin passes `--locked`, so the build aborts rather than refreshing it.

```powershell
py -3.13 -m venv .venv-eud
.\.venv-eud\Scripts\python.exe -m pip install .\eudplib .\euddraft
```

Produces `eudplib/bindings/_rust.pyd` and `eudplib/epscript/libepScriptLib.dll`.

Note: `pip install euddraft` alone pulls a prebuilt `eudplib` wheel from PyPI, so the
source build is only needed if you intend to modify eudplib.

## starcraft_map_editor (blackvrice)

Flutter 3.44.8 pinned in `.fvmrc`. Installed here at `I:\projects\flutter` (git clone of
the tag; `PUB_CACHE` is redirected inside it so removal is one folder).

```powershell
$env:PATH = "I:\projects\flutter\bin;" + $env:PATH
$env:PUB_CACHE = "I:\projects\flutter\.pub-cache"
flutter config --enable-windows-desktop
flutter pub get; flutter analyze; flutter test; flutter build windows --debug
```

Its `native/` helpers (`map_archive_helper` over StormLib, `starcraft_data_helper` over
CascLib + GRP/palette/doodad decoders) are separate CMake C++ executables driven as
**child processes**, not FFI.

---

# Build results

Verified on this machine, 2026-08-26. VS 2022 BuildTools 17.x (MSVC 14.44.35207),
Windows SDK 10.0.22000, Rust 1.96.0, Python 3.13.14, Flutter 3.44.8.

| Project | Configure | Build | Tests | Runs |
|---|---|---|---|---|
| Chkdraft | ok | ok, 0 errors | **70/71** | yes |
| eudplib (source) | — | ok | 65/65 maps parsed | yes |
| euddraft | — | ok | CLI ok | yes |
| starcraft_map_editor | ok | ok, 121.7s | **378/378** | yes |

## Chkdraft

`Chkdraft.exe`, 7.2 MB. Launches; with no StarCraft installation present it opens a
data-file browse prompt, which is correct behaviour — it needs `StarDat.mpq` /
`BrooDat.mpq` / `patch_rt.mpq` to render anything.

Test suites: CrossCut 36/36, Chkdraft 4/4, Windows 1/1, MappingCore 29/30.

The single failure is **`MiniMapTest.MiniMapTest`**, and it is an upstream test bug
rather than a defect in the editor. `TestAssets::LoadScData()` reads the `SC_ASSET`
environment variable and returns `false` immediately when it is unset
(`test/mapping_core/test_assets.cpp`). `MiniMapTest` ignores that return value and
uses the still-empty `Sc::Data`, so the test dies on an access violation
(SEH `0xc0000005`) instead of skipping:

```cpp
Sc::Data scData {};
TestAssets::LoadScData(scData);   // returns false when SC_ASSET is unset
// ... proceeds to use scData regardless
```

A `GTEST_SKIP()` on a false return would fix it. Anyone without a StarCraft install
sees this failure; it says nothing about map-editing correctness.

## eudplib — conformance run against the sc64 corpus

Cross-checked the source build against the StarCraft 64 maps from
`I:\projects\sc64-maps`, which are an independent corpus: rebuilt from an N64 BOLT
archive rather than authored by StarEdit.

**65/65 opened, 0 failures.** Section walk of the first map matches expectations for a
hybrid scenario — 37 sections, `DIM` 64x64, `TRIG` 84000 bytes (35 triggers), `MBRF`
2400 bytes (1 briefing trigger).

## starcraft_map_editor

Three binaries: `starcraft_map_editor.exe`, plus `map_archive_helper.exe` and
`starcraft_data_helper.exe`. StormLib and CascLib are pulled by CMake `FetchContent` at
pinned revisions and built from source, so there are no system dependencies.

The helpers speak versioned JSON over stdio and report the library revision they were
built against in every response:

```json
{"error":{"code":"ARCHIVE_PROTOCOL_INVALID_JSON",...},"helperVersion":"0.4.0",
 "protocolVersion":1,"stormLibRevision":"c91595a1a1b7b515567bd62a60af066914a29a6a"}
```

MPQ parsing — the part that handles untrusted input — therefore runs out-of-process and
can crash without taking the editor down. This is the most reusable idea found in the
survey, and it is language-agnostic.
