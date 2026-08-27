"""``chkdiff`` -- command line entry point.

Implements ``inspect`` and ``diff``.
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


def _cmd_diff(args: argparse.Namespace) -> int:
    left, right = pathlib.Path(args.a), pathlib.Path(args.b)
    for path in (left, right):
        if not path.is_file():
            raise SystemExit(f"not a file: {path}")
    report = diff(_load(left), _load(right))
    sys.stdout.write(report.to_json() if args.json else report.to_text())
    # Exit codes follow diff(1): 0 identical, 1 differences found.
    return 0 if report.is_empty else 1


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
