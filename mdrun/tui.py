from __future__ import annotations

import curses
import re
import time
from collections import deque
from typing import List, Optional

from .parser import CodeBlock, Element, parse
from .render import Style, Span, code_lines, render_element
from .runner import display_lang, stream_block
from .shell import Shell

ANSI_RE = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b[@A-Z\\\]^_`|]|\x1b\([0-9A-Za-z]"
)

CURSES_COLORS = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class App:
    def __init__(
        self,
        stdscr,
        title: str,
        elements: List[Element],
        blocks: List[CodeBlock],
        session: Shell,
        keys: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.stdscr = stdscr
        self.title = title
        self.elements = elements
        self.blocks = blocks
        self.session = session
        self.timeout = timeout

        self.selected = 0
        self.scroll = 0
        self.quit = False

        self._pair: dict = {}

        self._keys: Optional[deque] = deque(keys) if keys is not None else None
        self._keys_exhausted = self._keys is not None and not bool(self._keys)

        self._state = "preview"

        self._out_lines: List[str] = []
        self._gen = None
        self._exit_code: Optional[int] = None
        self._run_message: str = ""
        self._idle_since: Optional[float] = None

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        idx = 1
        for name, cnum in CURSES_COLORS.items():
            try:
                curses.init_pair(idx, cnum, -1)
            except curses.error:
                continue
            self._pair[name] = idx
            idx += 1

    def _getch(self) -> int:
        if self._keys is not None:
            if self._keys:
                self._keys_exhausted = False
                return self._keys.popleft()
            self._keys_exhausted = True
            return -1
        return self.stdscr.getch()

    def _span_attrs(self, style: Style) -> int:
        a = 0
        if style.bold:
            a |= curses.A_BOLD
        if style.dim:
            a |= curses.A_DIM
        if style.underline:
            a |= curses.A_UNDERLINE
        if style.reverse:
            a |= curses.A_REVERSE
        italic = getattr(curses, "A_ITALIC", 0)
        if style.italic:
            a |= italic
        if style.fg and style.fg in self._pair:
            a |= curses.color_pair(self._pair[style.fg])
        return a

    def _add_spans(self, y: int, x: int, spans, width: int) -> None:
        try:
            for sp in spans:
                if x >= width:
                    break
                text = sp.text.replace("\t", "    ")
                take = max(width - x, 0)
                if take <= 0:
                    break
                shown = text[:take]
                self.stdscr.addstr(y, x, shown, self._span_attrs(sp.style))
                x += len(shown)
        except curses.error:
            pass

    def _fill(self, y: int, x: int, ch: str, attrs: int, width: int) -> None:
        try:
            self.stdscr.hline(y, x, ch, max(width - x, 0), attrs)
        except curses.error:
            pass

    def _bar(self, y: int, text: str, attrs: int = curses.A_REVERSE) -> None:
        h, w = self.stdscr.getmaxyx()
        limit = max(w - 1, 0)
        shown = text[:limit]
        try:
            self.stdscr.addstr(y, 0, shown, attrs)
        except curses.error:
            pass
        self._fill(y, len(shown), " ", attrs, limit)

    def _make_layout(self, width: int) -> List[tuple]:
        rows: List[tuple] = []
        for el in self.elements:
            if isinstance(el, CodeBlock):
                sel = el.index == self.selected
                for ln in code_lines(el, width, sel):
                    rows.append((el.index, ln))
            else:
                for ln in render_element(el, width):
                    rows.append((None, ln))
        return rows

    def _pane_widths(self, width: int) -> tuple:
        if width < 44:
            return width, 0
        left = width * 55 // 100
        left = max(24, min(left, width - 24))
        return left, width - left - 1

    def _ensure_visible(self, rows: List[tuple], view_h: int) -> None:
        sel_rows = [i for i, (bi, _ln) in enumerate(rows) if bi == self.selected]
        if sel_rows:
            first, last = sel_rows[0], sel_rows[-1]
            if first < self.scroll:
                self.scroll = first
            elif last >= self.scroll + view_h:
                self.scroll = last - view_h + 1
        self.scroll = max(0, min(self.scroll, max(0, len(rows) - view_h)))

    def _paint_preview_content(self, top: int, height: int, width: int) -> None:
        rows = self._make_layout(width)
        self._ensure_visible(rows, height)
        y = top
        for i in range(self.scroll, min(len(rows), self.scroll + height)):
            if y >= top + height:
                break
            self._add_spans(y, 0, rows[i][1], width)
            y += 1

    def _paint_divider(self, top: int, height: int, x: int) -> None:
        for y in range(top, top + height):
            self._add_spans(y, x, [Span("\u2502", Style(dim=True))], x + 1)

    @staticmethod
    def _wrap_plain(text: str, width: int) -> List[str]:
        if width <= 0:
            return [text]
        if not text:
            return [""]
        out: List[str] = []
        while len(text) > width:
            out.append(text[:width])
            text = text[width:]
        out.append(text)
        return out

    def _paint_output(self, top: int, height: int, x: int, width: int) -> None:
        if not self._out_lines:
            if self._state == "run" or self._exit_code is not None:
                hint = "(no output)"
            else:
                hint = "(select a block and press Enter to run it)"
            self._add_spans(top, x, [Span(hint, Style(dim=True))], x + width)
            return
        visual: List[str] = []
        for ln in self._out_lines:
            visual.extend(self._wrap_plain(ln, width))
        tail = visual[-height:]
        y = top
        for ln in tail:
            if y >= top + height:
                break
            self._add_spans(y, x, [Span(ln, Style())], x + width)
            y += 1

    def _paint(self) -> None:
        scr = self.stdscr
        h, w = scr.getmaxyx()
        left_w, right_w = self._pane_widths(w)

        block = self.blocks[self.selected] if self.blocks else None
        title = f" mdrun \u00b7 {self.title} \u00b7 block {self.selected + 1}/{len(self.blocks)} "
        if block is not None:
            title += f"\u00b7 {display_lang(block)} "
        self._bar(0, title)

        body_h = max(h - 2, 1)
        self._paint_preview_content(1, body_h, left_w)
        if right_w > 0:
            self._paint_divider(1, body_h, left_w)
            self._paint_output(1, body_h, left_w + 1, right_w)
            self._add_spans(0, left_w + 1, [Span(" output ", Style(bold=True, reverse=True))], max(w - 1, left_w + 1))

        footer = self._status_line()
        self._bar(h - 1, footer)
        scr.move(0, 0)

    def _push_output(self, payload: bytes) -> None:
        text = strip_ansi(payload.decode("utf-8", "replace"))
        text = text.replace("\r", "").replace("\t", "    ")
        for ln in text.split("\n"):
            self._out_lines.append(ln)

    def _status_line(self) -> str:
        base = " j/k \u2191/\u2193 select   Enter run   r re-run   q quit   "
        if self._state == "run":
            return base + "\u2502 running\u2026 Esc/Ctrl-C to interrupt "
        if self._run_message:
            return base + f"\u2502 {self._run_message} "
        if self._exit_code is not None:
            return base + f"\u2502 finished \u00b7 exit {self._exit_code} "
        return base

    def _attempt_run(self) -> None:
        self._out_lines = []
        self._exit_code = None
        self._run_message = ""
        self._gen = stream_block(self.session, self.blocks[self.selected], self.timeout)
        self._state = "run"

    def _poll_run(self) -> None:
        if self._gen is None:
            self._state = "done"
            return
        try:
            kind, payload = next(self._gen)
            if kind == "out":
                self._push_output(payload)
            elif kind == "exit":
                self._exit_code = payload
        except StopIteration:
            self._gen = None
            self._state = "done"
        except TimeoutError:
            self._run_message = "timed out (interrupted)"
            self._gen = None
            self._state = "done"
        except RuntimeError as e:
            self._run_message = str(e)
            self._gen = None
            self._state = "done"

    def main(self) -> int:
        self._init_colors()
        self.stdscr.keypad(True)
        while not self.quit:
            h, w = self.stdscr.getmaxyx()
            if h < 3 or w < 10:
                self.stdscr.addstr(0, 0, "terminal too small", curses.A_BOLD)
                self.stdscr.refresh()
                if self._getch() in (ord("q"), 27, ord("Q")):
                    self.quit = True
                continue

            if self._keys_exhausted:
                if self._idle_since is None:
                    self._idle_since = time.time()
                elif time.time() - self._idle_since > 1.5:
                    self.quit = True
            else:
                self._idle_since = None

            if self._state == "run":
                self._poll_run()

            self.stdscr.nodelay(False)
            self.stdscr.timeout(80 if self._state == "run" else 300)
            self._paint()
            k = self._getch()
            if k == -1:
                self.stdscr.refresh()
                continue

            if k in (ord("q"), ord("Q")):
                if self._state == "run":
                    self.session.interrupt()
                self.quit = True
            elif k in (ord("j"), ord("J"), curses.KEY_DOWN, curses.KEY_NPAGE, ord(" ")):
                if self.blocks:
                    self.selected = min(len(self.blocks) - 1, self.selected + 1)
            elif k in (ord("k"), ord("K"), curses.KEY_UP, curses.KEY_PPAGE):
                self.selected = max(0, self.selected - 1)
            elif k in (ord("g"),):
                self.selected = 0
            elif k in (ord("G"),):
                self.selected = len(self.blocks) - 1 if self.blocks else 0
            elif k in (10, 13, curses.KEY_ENTER, ord("e"), ord("r"), ord("R")):
                if self._state != "run" and self.blocks:
                    self._attempt_run()
            elif k in (27, 3):
                if self._state == "run":
                    self.session.interrupt()
                else:
                    self.quit = True

            self.stdscr.refresh()
        return 0