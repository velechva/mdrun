from __future__ import annotations

import os
import shlex
import tempfile
from typing import Iterator, Optional, Tuple

from .parser import CodeBlock
from .shell import Shell

SHELL_LANGS = {"", "sh", "bash", "zsh", "ksh", "fish", "shell", "console"}

INTERPS = {
    "python": "python3",
    "py": "python3",
    "python3": "python3",
}


def block_command(block: CodeBlock) -> Tuple[str, Optional[str]]:
    lang = (block.lang or "").strip().lower()
    if lang in SHELL_LANGS:
        return "\n".join(block.lines), None
    interp = INTERPS.get(lang, lang)
    code = "\n".join(block.lines)
    fd, path = tempfile.mkstemp(prefix="mdrun_", suffix="." + lang)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(code)
    except BaseException:
        os.unlink(path)
        raise
    return f"{interp} {shlex.quote(path)}", path


def run_block(shell: Shell, block: CodeBlock, timeout: Optional[float] = None) -> Tuple[int, str]:
    cmd, cleanup = block_command(block)
    try:
        return shell.run(cmd, timeout)
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass


def stream_block(shell: Shell, block: CodeBlock, timeout: Optional[float] = None) -> Iterator[Tuple[str, object]]:
    cmd, cleanup = block_command(block)
    try:
        yield from shell.stream(cmd, timeout)
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass


def display_lang(block: CodeBlock) -> str:
    return (block.lang or "shell").strip()