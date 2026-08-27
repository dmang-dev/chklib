"""``chkdiff`` -- command line entry point.

``inspect`` and ``diff`` are the tools; ``pack`` and ``unpack`` move a scenario
in and out of a map archive; ``textconv`` and ``install-textconv`` wire the whole
thing into ``git diff``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from . import __version__
from .chk import Chk
from .diff import diff
from .inspect import FORMAT_VERSION, render
from .mpq import (
    MpqArchive,
    MpqError,
    SCENARIO_PATH,
    looks_like_mpq,
    write_scenario,
)

__all__ = ["main"]


def _load(path: pathlib.Path) -> Chk:
    """Read a bare ``scenario.chk``, or pull one out of a ``.scm``/``.scx``.

    Map files in a repository are MPQ archives, so accepting them directly is
    what makes the git integration possible at all.
    """
    raw = path.read_bytes()
    if looks_like_mpq(raw):
        try:
            raw = MpqArchive(raw).read_file(SCENARIO_PATH)
        except MpqError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
    return Chk.from_bytes(raw)


def _cmd_inspect(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path)
    if not path.is_file():
        raise SystemExit(f"not a file: {path}")
    chk = _load(path)
    # --stable omits the source line: git's textconv passes a temporary filename
    # that changes on every invocation and would appear as a spurious diff.
    source = None if args.stable else str(path)
    sys.stdout.write(render(chk, source=source))
    if args.strict and chk.has_errors:
        for diagnostic in chk.diagnostics:
            if diagnostic.severity == "error":
                print(f"error: {diagnostic}", file=sys.stderr)
        return 1
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    left, right = pathlib.Path(args.a), pathlib.Path(args.b)
    for path in (left, right):
        if not path.is_file():
            raise SystemExit(f"not a file: {path}")
    report = diff(_load(left), _load(right))
    sys.stdout.write(report.to_json() if args.json else report.to_text())
    # Exit codes follow diff(1): 0 identical, 1 differences found.
    return 0 if report.is_empty else 1


def _cmd_unpack(args: argparse.Namespace) -> int:
    """Extract ``staredit\\scenario.chk`` from a map archive."""
    source = pathlib.Path(args.map)
    if not source.is_file():
        raise SystemExit(f"not a file: {source}")
    raw = source.read_bytes()
    if not looks_like_mpq(raw):
        raise SystemExit(f"{source} is not an MPQ archive")
    try:
        chk = MpqArchive(raw).read_file(SCENARIO_PATH)
    except MpqError as exc:
        raise SystemExit(f"{source}: {exc}") from exc
    destination = pathlib.Path(args.out)
    destination.write_bytes(chk)
    print(f"wrote {destination} ({len(chk)} bytes)")
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    """Wrap a ``scenario.chk`` into a playable map archive."""
    source = pathlib.Path(args.chk)
    if not source.is_file():
        raise SystemExit(f"not a file: {source}")
    raw = source.read_bytes()
    if looks_like_mpq(raw):
        raise SystemExit(f"{source} is already an archive; did you mean unpack?")

    # Refuse to emit something the reader cannot make sense of. A map that fails
    # to parse here will fail in StarCraft too, and silently writing it wastes
    # the user's time later rather than now.
    chk = Chk.from_bytes(raw)
    if chk.has_errors and not args.force:
        for diagnostic in chk.diagnostics:
            if diagnostic.severity == "error":
                print(f"error: {diagnostic}", file=sys.stderr)
        raise SystemExit(
            f"{source} has parse errors; pass --force to pack it anyway"
        )

    destination = pathlib.Path(args.out)
    archive = write_scenario(raw, compress=args.compress)
    destination.write_bytes(archive)
    ratio = f", {len(archive) / len(raw):.0%} of the scenario" if raw else ""
    print(f"wrote {destination} ({len(archive)} bytes{ratio})")
    return 0


def _cmd_textconv(args: argparse.Namespace) -> int:
    """The git textconv driver. Must never fail.

    Git runs this over every blob on both sides of a diff, including historical
    ones that may be truncated, protected, or not maps at all. A driver that
    exits non-zero or raises makes ``git diff`` fail outright, so anything
    unreadable degrades to a short deterministic note instead.

    The note is deliberately content-derived rather than error-derived: two
    unreadable blobs with the same bytes must produce the same text, or the diff
    churns.
    """
    path = pathlib.Path(args.path)
    try:
        chk = _load(path)
    except SystemExit as exc:
        # _load raises SystemExit with a human message; keep the reason but not
        # the path, which git randomises per invocation.
        reason = str(exc).replace(str(path), "").lstrip(": ").strip()
        sys.stdout.write(f"# openstaredit: unreadable ({reason})\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - a driver must not propagate
        sys.stdout.write(f"# openstaredit: unreadable ({type(exc).__name__})\n")
        return 0
    sys.stdout.write(render(chk, source=None))
    return 0


_GIT_SETUP = """\
# 1. Tell git how to render a map as text (once per machine, or --local):
git config --global diff.starcraft.textconv "chkdiff textconv"
git config --global diff.starcraft.binary false

# 2. Tell git which files that applies to. In .gitattributes:
*.scm diff=starcraft
*.scx diff=starcraft
*.chk diff=starcraft
"""


def _cmd_install_textconv(args: argparse.Namespace) -> int:
    scope = "--local" if args.local else "--global"
    if not args.write:
        sys.stdout.write(_GIT_SETUP)
        sys.stdout.write(
            "\nRe-run with --write to apply the git config, and --local to scope"
            " it to this repository.\n"
        )
        return 0

    import subprocess

    for key, value in (
        ("diff.starcraft.textconv", "chkdiff textconv"),
        ("diff.starcraft.binary", "false"),
    ):
        result = subprocess.run(
            ["git", "config", scope, key, value],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"git config failed: {result.stderr.strip()}")
        print(f"set {key} = {value!r} ({scope})")
    print("\nNow add these lines to .gitattributes:")
    print("  *.scm diff=starcraft")
    print("  *.scx diff=starcraft")
    print("  *.chk diff=starcraft")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chkdiff",
        description="Inspect and compare StarCraft scenario data.",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"chkdiff {__version__} (inspect format v{FORMAT_VERSION})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser(
        "inspect",
        help="print a stable, deterministic rendering of a scenario",
        description=(
            "Print a deterministic textual rendering of a scenario.chk. "
            "Suitable as a git textconv driver, in which case pass --stable."
        ),
    )
    inspect.add_argument("path", help="path to a scenario.chk")
    inspect.add_argument(
        "--stable", action="store_true",
        help="omit the source filename, for reproducible output (use for git textconv)",
    )
    inspect.add_argument(
        "--strict", action="store_true",
        help="exit non-zero if the file has parse errors",
    )
    inspect.set_defaults(func=_cmd_inspect)

    compare = sub.add_parser(
        "diff",
        help="compare two scenarios semantically",
        description=(
            "Compare two scenario.chk files by meaning rather than by bytes. "
            "Exit status follows diff(1): 0 when identical, 1 when they differ."
        ),
    )
    compare.add_argument("a", help="path to the first scenario.chk")
    compare.add_argument("b", help="path to the second scenario.chk")
    compare.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of text",
    )
    compare.set_defaults(func=_cmd_diff)

    unpack = sub.add_parser(
        "unpack", help="extract scenario.chk from a .scm/.scx map archive"
    )
    unpack.add_argument("map", help="path to a .scm or .scx")
    unpack.add_argument("out", help="path to write scenario.chk to")
    unpack.set_defaults(func=_cmd_unpack)

    pack = sub.add_parser(
        "pack",
        help="wrap a scenario.chk into a .scm/.scx map archive",
        description=(
            "Wrap a scenario.chk into a map archive. Stores the scenario "
            "uncompressed by default, which every reader accepts; --compress "
            "uses zlib, which is what euddraft writes for production maps."
        ),
    )
    pack.add_argument("chk", help="path to a scenario.chk")
    pack.add_argument("out", help="path to write the map archive to")
    pack.add_argument(
        "--compress", action="store_true", help="zlib-compress the scenario"
    )
    pack.add_argument(
        "--force", action="store_true", help="pack even if the scenario has parse errors"
    )
    pack.set_defaults(func=_cmd_pack)

    textconv = sub.add_parser(
        "textconv",
        help="render a map for git's diff.<driver>.textconv",
        description=(
            "Render a map as deterministic text for git. Always exits 0 and "
            "always prints something, because a textconv driver that fails "
            "makes git diff fail."
        ),
    )
    textconv.add_argument("path", help="path to a map or scenario.chk")
    textconv.set_defaults(func=_cmd_textconv)

    install = sub.add_parser(
        "install-textconv",
        help="print (or apply) the git configuration for map diffs",
    )
    install.add_argument(
        "--write", action="store_true", help="run the git config commands"
    )
    install.add_argument(
        "--local", action="store_true",
        help="scope the config to this repository instead of the whole machine",
    )
    install.set_defaults(func=_cmd_install_textconv)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # A downstream consumer closed the pipe -- `chkdiff diff x y | head` is
        # completely normal usage. Python would otherwise flush stdout again at
        # shutdown and print a second traceback to stderr, so point the fd at
        # devnull first. 128 + SIGPIPE(13) is the conventional status.
        devnull = None
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, AttributeError, ValueError):
            # stdout may not be a real file (a test double, or already closed).
            # There is nothing useful left to do, and raising here would defeat
            # the purpose of catching BrokenPipeError in the first place.
            if devnull is not None:
                os.close(devnull)
        return 141


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
