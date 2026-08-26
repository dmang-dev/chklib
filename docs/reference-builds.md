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
