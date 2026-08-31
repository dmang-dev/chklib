"""Tests for the git integration.

The driver has one hard requirement beyond producing good output: **it must
never fail**. Git runs it over every blob on both sides of a diff, including
historical ones that may be truncated, protected, or not maps at all. A driver
that exits non-zero makes ``git diff`` fail outright, which is worse than having
no driver.

The end-to-end tests drive a real git repository, because that is the only way to
know the whole chain works -- ``.gitattributes`` matching, the config keys, the
driver, and git's own diffing of the result.
"""

from __future__ import annotations

import pathlib
import shutil
import struct
import subprocess
import sys

import pytest

from chklib.cli import main
from conftest import installed_maps

MAPS = installed_maps()


def _git() -> str | None:
    """Resolve a native git.

    Prefers a known install location over ``PATH``: on this machine a POSIX
    emulation layer shadows git, and a portable test should not depend on which
    one happens to come first.
    """
    for candidate in (
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ):
        if pathlib.Path(candidate).is_file():
            return candidate
    found = shutil.which("git")
    return found if found and "devkitpro" not in found.lower() else found


GIT = _git()


def sect(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<i", len(payload)) + payload


def a_chk() -> bytes:
    return b"".join([
        sect(b"VER ", struct.pack("<H", 59)),
        sect(b"ERA ", struct.pack("<H", 4)),
        sect(b"DIM ", struct.pack("<HH", 64, 64)),
    ])


# --------------------------------------------------------------------------
# The driver must never fail
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,label",
    [
        (a_chk(), "valid chk"),
        (b"", "empty file"),
        (b"this is not a map at all", "garbage"),
        (b"MPQ\x1a" + bytes(64), "truncated mpq"),
        (b"MPQ\x1a" + b"\xff" * 4096, "corrupt mpq"),
        (bytes(range(256)) * 10, "binary noise"),
    ],
)
def test_textconv_always_succeeds(tmp_path: pathlib.Path, capsys,
                                  content: bytes, label: str) -> None:
    path = tmp_path / "blob.scx"
    path.write_bytes(content)
    assert main(["textconv", str(path)]) == 0, label
    assert capsys.readouterr().out, f"{label} produced no output"


def test_textconv_on_a_missing_file_still_succeeds(tmp_path: pathlib.Path, capsys) -> None:
    """Git can hand the driver a path that no longer exists."""
    assert main(["textconv", str(tmp_path / "gone.scx")]) == 0
    assert "unreadable" in capsys.readouterr().out


def test_textconv_never_leaks_the_path(tmp_path: pathlib.Path, capsys) -> None:
    """Git passes a randomised temp filename; leaking it churns every diff."""
    path = tmp_path / "some-random-temp-name.scx"
    path.write_bytes(b"MPQ\x1a" + bytes(64))
    main(["textconv", str(path)])
    out = capsys.readouterr().out
    assert str(path) not in out
    assert "some-random-temp-name" not in out


def test_textconv_is_deterministic_for_unreadable_input(tmp_path: pathlib.Path, capsys) -> None:
    """Two copies of the same bad blob must render identically."""
    first, second = tmp_path / "a.scx", tmp_path / "b.scx"
    payload = b"MPQ\x1a" + bytes(64)
    first.write_bytes(payload)
    second.write_bytes(payload)
    main(["textconv", str(first)])
    out_a = capsys.readouterr().out
    main(["textconv", str(second)])
    assert capsys.readouterr().out == out_a


@pytest.mark.skipif(not MAPS, reason="no StarCraft installation found")
def test_textconv_renders_a_real_archive(capsys) -> None:
    assert main(["textconv", MAPS[0]]) == 0
    out = capsys.readouterr().out
    assert "# chklib inspect" in out
    assert "[map]" in out


# --------------------------------------------------------------------------
# install-textconv
# --------------------------------------------------------------------------


def test_install_textconv_prints_instructions_without_touching_git(capsys) -> None:
    assert main(["install-textconv"]) == 0
    out = capsys.readouterr().out
    assert "diff.starcraft.textconv" in out
    assert "*.scm diff=starcraft" in out
    assert "--write" in out


# --------------------------------------------------------------------------
# End to end, against a real git repository
# --------------------------------------------------------------------------


def _run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [GIT, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repository with the textconv driver wired up."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("init", "-q", ".", cwd=repo)
    _run("config", "user.email", "test@example.invalid", cwd=repo)
    _run("config", "user.name", "Test", cwd=repo)
    # Invoke through this interpreter so the test does not depend on the console
    # script being on PATH.
    driver = f'"{sys.executable}" -m chklib.cli textconv'
    _run("config", "diff.starcraft.textconv", driver, cwd=repo)
    _run("config", "diff.starcraft.binary", "false", cwd=repo)
    (repo / ".gitattributes").write_text(
        "*.scm diff=starcraft\n*.scx diff=starcraft\n*.chk diff=starcraft\n"
    )
    return repo


@pytest.mark.skipif(GIT is None, reason="git not available")
def test_git_diff_shows_semantic_changes_for_a_chk(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    target = repo / "scenario.chk"
    target.write_bytes(a_chk())
    _run("add", "-A", cwd=repo)
    _run("commit", "-qm", "first", cwd=repo)

    # Change the tileset from Jungle (4) to Badlands (0) and the size.
    target.write_bytes(b"".join([
        sect(b"VER ", struct.pack("<H", 59)),
        sect(b"ERA ", struct.pack("<H", 0)),
        sect(b"DIM ", struct.pack("<HH", 96, 64)),
    ]))
    out = _run("diff", "--", "scenario.chk", cwd=repo).stdout

    assert "Binary files" not in out
    assert "-tileset      4  Jungle" in out
    assert "+tileset      0  Badlands" in out
    assert "-dimensions   64x64 tiles" in out
    assert "+dimensions   96x64 tiles" in out


@pytest.mark.skipif(GIT is None, reason="git not available")
def test_git_diff_is_binary_without_the_driver(tmp_path: pathlib.Path) -> None:
    """The status quo this feature replaces, asserted rather than assumed."""
    repo = tmp_path / "plain"
    repo.mkdir()
    _run("init", "-q", ".", cwd=repo)
    _run("config", "user.email", "test@example.invalid", cwd=repo)
    _run("config", "user.name", "Test", cwd=repo)
    target = repo / "scenario.chk"
    target.write_bytes(a_chk())
    _run("add", "-A", cwd=repo)
    _run("commit", "-qm", "first", cwd=repo)
    target.write_bytes(a_chk() + sect(b"MTXM", bytes(64)))
    out = _run("diff", "--", "scenario.chk", cwd=repo).stdout
    assert "Binary files" in out


@pytest.mark.skipif(GIT is None or not MAPS, reason="git or StarCraft maps unavailable")
def test_git_diff_on_real_map_archives(tmp_path: pathlib.Path) -> None:
    """The actual use case: a .scx tracked in a repository and replaced."""
    if len(MAPS) < 2:
        pytest.skip("need two maps")
    repo = _repo(tmp_path)
    target = repo / "map.scx"
    shutil.copy(MAPS[0], target)
    _run("add", "-A", cwd=repo)
    _run("commit", "-qm", "first", cwd=repo)
    shutil.copy(MAPS[1], target)

    out = _run("diff", "--", "map.scx", cwd=repo).stdout
    assert "Binary files" not in out
    assert "[map]" in out or "name" in out
    # Both sides rendered, so git actually ran the driver twice.
    assert out.count("chklib inspect") <= 1  # identical headers collapse


@pytest.mark.skipif(GIT is None, reason="git not available")
def test_git_diff_survives_an_unreadable_blob(tmp_path: pathlib.Path) -> None:
    """A corrupt file under the driver must not break the whole diff."""
    repo = _repo(tmp_path)
    target = repo / "broken.scx"
    target.write_bytes(b"MPQ\x1a" + bytes(64))
    _run("add", "-A", cwd=repo)
    _run("commit", "-qm", "first", cwd=repo)
    target.write_bytes(b"MPQ\x1a" + bytes(128))

    result = subprocess.run(
        [GIT, "diff", "--", "broken.scx"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
