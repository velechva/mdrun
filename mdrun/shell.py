from __future__ import annotations

import os
import pty
import re
import select
import termios
import time
from typing import Iterator, Optional, Tuple

SOH = b"\x01"
STX = b"\x02"
ETX = b"\x03"
MARKER_START = SOH + STX
MARKER_END = ETX

BRACKET_PASTE = re.compile(rb"\x1b\[\?2004[hl]")


def _clean(data: bytes) -> bytes:
    data = BRACKET_PASTE.sub(b"", data)
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


_HOLDBACK_PATTERNS = (MARKER_START, b"\x1b[?2004", b"\x1b[")


def _safe_prefix_len(buf: bytes) -> int:
    hold = 0
    for pat in _HOLDBACK_PATTERNS:
        for k in range(min(len(pat) - 1, len(buf)), 0, -1):
            if buf.endswith(pat[:k]):
                hold = max(hold, k)
                break
    if buf.endswith(b"\r"):
        hold = max(hold, 1)
    return len(buf) - hold


def _marker_cmd() -> str:
    return "printf '\\001\\002%03d\\003'" + " $" + "?\n"


class Shell:
    def __init__(
        self,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        no_rcs: bool = False,
        init: Optional[str] = None,
    ) -> None:
        self.dead = False
        self._master: Optional[int] = None
        self._pid: Optional[int] = None

        argv = self._argv(shell, no_rcs)
        cwd = cwd or os.getcwd()

        pid, master = pty.fork()
        if pid == 0:
            try:
                os.chdir(cwd)
            except OSError:
                pass
            env = dict(os.environ)
            env["PS1"] = ""
            env["PROMPT_COMMAND"] = ""
            os.execvpe(argv[0], argv, env)

        self._master = master
        self._pid = pid
        self._settle_attrs()
        self._drain(1.0)

        setup = [
            'export PS1=""',
            'export PS2=""',
            "export PROMPT_COMMAND=''",
        ]
        if init:
            setup.append(". " + init)
        self._write("\n".join(setup) + "\n")
        self._drain(1.0)

    def _argv(self, shell: Optional[str], no_rcs: bool) -> list:
        base = shell or os.environ.get("SHELL") or "bash"
        name = os.path.basename(base)
        argv = [base]
        if no_rcs:
            if name == "bash":
                argv.append("--norc")
            elif name == "zsh":
                argv.append("--no-rcs")
            elif name == "fish":
                argv.append("--no-config")
        if name == "bash":
            argv.append("--noediting")
        return argv

    def _settle_attrs(self) -> None:
        try:
            attrs = termios.tcgetattr(self._master)
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(self._master, termios.TCSANOW, attrs)
        except termios.error:
            pass

    def _write(self, data: str) -> None:
        if self.dead or self._master is None:
            raise RuntimeError("shell not running")
        os.write(self._master, data.encode("utf-8", "replace"))

    def send(self, data: str) -> None:
        self._write(data)

    def interrupt(self) -> None:
        if not self.dead:
            os.write(self._master, b"\x03")

    def _drain(self, seconds: float) -> bytes:
        end = time.time() + seconds
        buf = b""
        while time.time() < end:
            r, _, _ = select.select([self._master], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(self._master, 65536)
                except OSError:
                    self.dead = True
                    break
                if not chunk:
                    self.dead = True
                    break
                buf += chunk
        return buf

    def _pending(self) -> bytes:
        return self._read_available()

    def _read_available(self) -> bytes:
        out = b""
        while True:
            r, _, _ = select.select([self._master], [], [], 0)
            if not r:
                break
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                self.dead = True
                break
            if not chunk:
                self.dead = True
                break
            out += chunk
        return out

    @staticmethod
    def _strip_marker(buf: bytes) -> Optional[Tuple[bytes, int, bytes]]:
        start = buf.find(MARKER_START)
        if start == -1:
            return None
        end = buf.find(MARKER_END, start)
        if end == -1:
            return None
        digits = buf[start + len(MARKER_START):end]
        if not digits.isdigit():
            return None
        return buf[:start], int(digits), buf[end + 1 :]

    def stream(self, code: str, timeout: Optional[float] = None) -> Iterator[Tuple[str, object]]:
        if self.dead:
            raise RuntimeError("shell not running")
        self._read_available()
        self._write(code + "\n" + _marker_cmd())

        buf = b""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            r, _, _ = select.select([self._master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(self._master, 65536)
                except OSError:
                    self.dead = True
                    break
                if not chunk:
                    self.dead = True
                    break
                buf += chunk

            hit = self._strip_marker(buf)
            if hit is not None:
                body, exit_code, _rest = hit
                if body:
                    yield ("out", _clean(body))
                yield ("exit", exit_code)
                return

            safe = _safe_prefix_len(buf)
            if safe > 0:
                out = buf[:safe]
                buf = buf[safe:]
                yield ("out", _clean(out))

            if deadline is not None and time.time() >= deadline:
                self.interrupt()
                raise TimeoutError("command timed out")

        if buf:
            yield ("out", _clean(buf))
        yield ("exit", -1)

    def run(self, code: str, timeout: Optional[float] = None) -> Tuple[int, str]:
        chunks: list = []
        exit_code = None
        for kind, payload in self.stream(code, timeout=timeout):
            if kind == "out":
                chunks.append(payload)
            elif kind == "exit":
                exit_code = payload
        output = b"".join(chunks).decode("utf-8", "replace")
        return (exit_code, output) if exit_code is not None else (0, "")

    def close(self) -> None:
        if self.dead:
            return
        try:
            self._write("exit\n")
        except OSError:
            pass
        try:
            os.close(self._master)
        except OSError:
            pass
        if self._pid:
            try:
                os.kill(self._pid, 9)
            except OSError:
                pass
        self.dead = True