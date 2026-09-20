from __future__ import annotations

import argparse
import curses
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .parser import CodeBlock, parse
from .render import ansi_paint, code_lines, render_element
from .runner import display_lang, run_block
from .shell import Shell
from .tui import App


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mdrun",
        description="Preview a markdown file and run its code blocks in a persistent shell.",
        epilog=f"mdrun v{__version__}",
    )
    p.add_argument("file", nargs="?", default="-", help="markdown file, or '-' for stdin")
    p.add_argument("--shell", default=None, help="shell to run code blocks in (default: $SHELL)")
    p.add_argument("--cwd", default=None, help="working directory for the shell (default: cwd)")
    p.add_argument("--no-rcs", action="store_true", help="start the shell without rc files")
    p.add_argument("--init", default=None, help="extra file to source inside the shell at startup")
    p.add_argument("--timeout", type=float, default=None, metavar="SEC", help="per-block run timeout")
    p.add_argument("--list", action="store_true", help="list code blocks and exit")
    p.add_argument("--preview", action="store_true", help="render the document and exit")
    p.add_argument("--width", type=int, default=None, metavar="COLS", help="render width for --preview")
    p.add_argument("--run", default=None, metavar="N[,N...]", help="run given block index(es) and exit")
    p.add_argument("--keys", default=None, metavar="FILE", help="scripted keystrokes from FILE for testing")
    return p


def _make_session(args) -> Shell:
    return Shell(shell=args.shell or os.environ.get("SHELL"), cwd=args.cwd, no_rcs=args.no_rcs, init=args.init)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    text = _read(args.file)
    elements, blocks = parse(text)

    if args.list:
        for i, b in enumerate(blocks):
            label = b.label or ""
            print(f"{i:2d}  {display_lang(b):<8} {label}  (lines {b.start + 1}-{b.end + 1})")
        return 0

    if args.preview:
        width = args.width or int(os.environ.get("COLUMNS", "80"))
        for el in elements:
            if isinstance(el, CodeBlock):
                rows = code_lines(el, width, el.index == 0)
            else:
                rows = render_element(el, width)
            for ln in rows:
                print(ansi_paint(ln))
        return 0

    if args.run:
        sel = [int(x) for x in args.run.split(",") if x.strip()]
        for n in sel:
            if n < 0 or n >= len(blocks):
                print(f"mdrun: block {n} out of range (0..{len(blocks) - 1})", file=sys.stderr)
                return 2
        session = _make_session(args)
        try:
            rc = 0
            for n in sel:
                b = blocks[n]
                header = f"==> block {n} ({display_lang(b)})"
                if b.label:
                    header += f": {b.label}"
                print(header, flush=True)
                code, out = run_block(session, b, args.timeout)
                sys.stdout.write(out)
                if out and not out.endswith("\n"):
                    sys.stdout.write("\n")
                print(f"<== exit {code}", flush=True)
                rc = rc or code
            return 0 if rc == 0 else 1
        finally:
            session.close()

    if not args.file or args.file == "-":
        print("mdrun: the interactive view needs a file (try --run, --preview or --list for stdin)", file=sys.stderr)
        return 2

    keys = Path(args.keys).read_bytes() if args.keys else None

    session = _make_session(args)
    try:
        return curses.wrapper(
            lambda s: App(s, args.file, elements, blocks, session, keys=keys, timeout=args.timeout).main()
        )
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())