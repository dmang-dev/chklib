"""``chkdiff`` -- command line entry point.

Currently implements ``inspect``. ``diff`` is the next milestone.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from . import __version__
from .chk import Chk
from .inspect import FORMAT_VERSION, render

__all__ = ["main"]

MPQ_MAGICS = (b"MPQ\x1a", b"MPQ\x1b")

_MPQ_HELP = """\
{path} is an MPQ archive ({ext}), not a raw scenario.chk.

This library does not read MPQ archives yet, so the scenario has to be extracted
first. Any of these work:

  python tools/extract_fixtures.py "{path}"     # needs the 'fixtures' extra
  # or open the map in an editor and export staredit\\scenario.chk

MPQ support is the next thing needed before the git integration is useful, since
map files in a repository are archives, not bare CHKs.\
"""


def _looks_like_mpq(raw: bytes) -> bool:
    return raw[:4] in MPQ_MAGICS


def _load(path: pathlib.Path) -> Chk:
    raw = path.read_bytes()
    if _looks_like_mpq(raw):
        raise SystemExit(_MPQ_HELP.format(path=path, ext=path.suffix or "no suffix"))
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
