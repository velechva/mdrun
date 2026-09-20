from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

from .parser import (
    Blank,
    CodeBlock,
    Heading,
    ListItem,
    Paragraph,
    Quote,
    Rule,
    Element,
)

COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}

INLINE = re.compile(
    r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|`[^`]+`|~~[^~]+~~|\[[^]]*\]\([^)]*\))"
)
LINK = re.compile(r"\[([^]]*)\]\([^)]*\)")


@dataclass
class Style:
    fg: str | None = None
    bold: bool = False
    dim: bool = False
    underline: bool = False
    reverse: bool = False
    italic: bool = False


@dataclass
class Span:
    text: str
    style: Style = field(default_factory=Style)


DEFAULT = Style()
Line = List[Span]


def _merge(style: Style, **kw) -> Style:
    base = {
        "fg": style.fg,
        "bold": style.bold,
        "dim": style.dim,
        "underline": style.underline,
        "reverse": style.reverse,
        "italic": style.italic,
    }
    base.update(kw)
    return Style(**base)


def _dwidth(ch: str) -> int:
    o = ord(ch)
    if o < 32 or o == 127:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def span_width(spans: Line) -> int:
    return sum(_dwidth(c) for sp in spans for c in sp.text)


def inline(text: str, style: Style = DEFAULT) -> Line:
    spans: Line = []
    pos = 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos : m.start()], style))
        tok = m.group(1)
        if tok.startswith("**") and tok.endswith("**"):
            spans.append(Span(tok[2:-2], _merge(style, bold=True)))
        elif tok.startswith("__") and tok.endswith("__") and len(tok) > 2:
            spans.append(Span(tok[2:-2], _merge(style, bold=True)))
        elif tok.startswith("`"):
            spans.append(Span(tok[1:-1], _merge(style, fg="cyan")))
        elif tok.startswith("~~") and tok.endswith("~~"):
            spans.append(Span(tok[2:-2], _merge(style, dim=True)))
        elif tok.startswith("[") and "]" in tok:
            lm = LINK.match(tok)
            label = lm.group(1) if lm else tok
            spans.append(Span(label, _merge(style, underline=True)))
        elif tok.startswith("*") and tok.endswith("*"):
            spans.append(Span(tok[1:-1], _merge(style, italic=True)))
        elif tok.startswith("_") and tok.endswith("_") and len(tok) > 2:
            spans.append(Span(tok[1:-1], _merge(style, italic=True)))
        else:
            spans.append(Span(tok, style))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:], style))
    return spans


def wrap(spans: Line, width: int) -> List[Line]:
    if width <= 0:
        return [spans]
    tokens: List[tuple[str, str, Style]] = []
    for sp in spans:
        buf = ""
        for ch in sp.text:
            if ch == " ":
                if buf:
                    tokens.append(("word", buf, sp.style))
                    buf = ""
                if tokens and tokens[-1][0] == "space":
                    continue
                tokens.append(("space", " ", sp.style))
            else:
                buf += ch
        if buf:
            tokens.append(("word", buf, sp.style))
    if not any(t[0] == "word" for t in tokens):
        return [spans]
    lines: List[Line] = []
    cur: Line = []
    curw = 0
    pending_space = False
    for kind, text, st in tokens:
        if kind == "space":
            pending_space = True
            continue
        w = text
        ww = sum(_dwidth(c) for c in w)
        sep = 1 if (pending_space and curw > 0) else 0
        if curw + sep + ww <= width:
            if sep:
                cur.append(Span(" ", st))
            cur.append(Span(w, st))
            curw += sep + ww
            pending_space = False
            continue
        if cur:
            lines.append(cur)
            cur = []
            curw = 0
        pending_space = False
        if ww > width:
            piece = ""
            pw = 0
            for ch in w:
                cw = _dwidth(ch)
                if pw + cw > width and piece:
                    lines.append([Span(piece, st)])
                    piece = ""
                    pw = 0
                piece += ch
                pw += cw
            if piece:
                cur.append(Span(piece, st))
                curw = pw
        else:
            cur.append(Span(w, st))
            curw = ww
    if cur:
        lines.append(cur)
    return lines


def _rule(width: int) -> Line:
    return [Span("\u2500" * width, Style(dim=True))]


def render_element(element: Element or None, width: int) -> List[Line]:
    if element is None or isinstance(element, Blank):
        return [[Span("")]]
    if isinstance(element, Heading):
        prefix = Span(" " * ((element.level - 1) * 2), Style(dim=True))
        if element.level == 1:
            body = inline(element.text, Style(fg="cyan", bold=True, underline=True))
        elif element.level == 2:
            body = inline(element.text, Style(fg="cyan", bold=True))
        else:
            body = inline(element.text, Style(bold=True))
        return wrap([prefix] + body, width)
    if isinstance(element, Paragraph):
        return wrap(inline(element.text), width)
    if isinstance(element, ListItem):
        prefix = "  " * element.indent + element.marker + " "
        pl = span_width([Span(prefix)])
        inner = wrap(inline(element.text), max(width - pl, 1))
        out: List[Line] = [[Span(prefix)] + inner[0]] if inner else [[Span(prefix)]]
        for ln in inner[1:]:
            out.append([Span(" " * pl)] + ln)
        return out
    if isinstance(element, Quote):
        inner = wrap(inline(element.text, Style(dim=True)), width - 2)
        out = [[Span("\u2502 ", Style(dim=True))] + ln for ln in inner]
        return out
    if isinstance(element, Rule):
        return [_rule(width)]
    return [[Span("", DEFAULT)]]


def code_lines(block: CodeBlock, width: int, selected: bool) -> List[Line]:
    selected = bool(selected)
    lines: List[Line] = []
    gutter = 2 if selected else 2
    for raw in block.lines:
        text = raw if raw else " "
        width_used = width - gutter - (1 if selected else 0)
        if width_used > 0 and len(text) > width_used:
            text = text[: max(width_used - 1, 0)] + "\u2026"
        style = Style(
            fg="white" if selected else "cyan",
            reverse=selected,
            dim=not selected,
        )
        spans: Line = [Span(" " * gutter, style)]
        if selected:
            spans.append(Span("\u276f", Style(fg="yellow", bold=True)))
        spans.append(Span(text, style))
        lines.append(spans)
    return lines


def ansi_paint(spans: Line) -> str:
    parts: List[str] = []
    for sp in spans:
        codes: List[int] = []
        if sp.style.fg:
            codes.append(COLORS.get(sp.style.fg, 39))
        if sp.style.bold:
            codes.append(1)
        if sp.style.dim:
            codes.append(2)
        if sp.style.italic:
            codes.append(3)
        if sp.style.underline:
            codes.append(4)
        if sp.style.reverse:
            codes.append(7)
        if codes:
            parts.append("\x1b[" + ";".join(str(c) for c in codes) + "m")
        parts.append(sp.text)
        if codes:
            parts.append("\x1b[0m")
    return "".join(parts)


def render_dump(elements: List[Element], width: int, selected_index: int) -> List[str]:
    out: List[str] = []
    for el in elements:
        if isinstance(el, CodeBlock):
            for ln in code_lines(el, width, el.index == selected_index):
                line = "".join(s.text for s in ln)
                out.append(f"  {line}" if el.index != selected_index else f"> {line}")
        else:
            for ln in render_element(el, width):
                line = "".join(s.text for s in ln)
                out.append(line if line != "\u2500" * width else "-" * width)
    return out