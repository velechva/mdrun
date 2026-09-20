from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
LIST = re.compile(r"^(\s*)((?:[-*+]|\d+[.)])\s+)(.*)$")
QUOTE = re.compile(r"^>\s?(.*)$")
RULE = re.compile(
    r"^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$"
)
INDENT = re.compile(r"^(?: {4}|\t)(.*)$")


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class ListItem:
    marker: str
    indent: int
    text: str


@dataclass
class Quote:
    text: str


@dataclass
class Rule:
    pass


@dataclass
class Blank:
    pass


@dataclass
class CodeBlock:
    index: int
    lang: str
    lines: List[str]
    start: int
    end: int
    label: str = field(default="")


Element = (
    Heading
    | Paragraph
    | ListItem
    | Quote
    | Rule
    | Blank
    | CodeBlock
)


def parse(text: str) -> Tuple[List[Element], List[CodeBlock]]:
    raw = text.splitlines()
    elements: List[Element] = []
    blocks: List[CodeBlock] = []
    para: List[str] = []

    def flush_para() -> None:
        if para:
            elements.append(Paragraph(" ".join(para).strip()))
            para.clear()

    i = 0
    n = len(raw)
    while i < n:
        line = raw[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            elements.append(Blank())
            i += 1
            continue

        m = FENCE_OPEN.match(line)
        if m:
            flush_para()
            fence_len = len(m.group(1))
            fence_ch = m.group(1)[0]
            lang = m.group(2)
            start = i
            code: List[str] = []
            i += 1
            while i < n:
                cl = raw[i]
                cm = FENCE_CLOSE.match(cl)
                if cm and cm.group(1)[0] == fence_ch and len(cm.group(1)) >= fence_len:
                    i += 1
                    break
                code.append(cl)
                i += 1
            block = CodeBlock(index=len(blocks), lang=lang, lines=code, start=start, end=i - 1)
            blocks.append(block)
            elements.append(block)
            continue

        if INDENT.match(line):
            flush_para()
            start = i
            code = []
            while i < n:
                l = raw[i]
                if not l.strip():
                    code.append("")
                    i += 1
                    continue
                mi = INDENT.match(l)
                if not mi:
                    break
                code.append(mi.group(1))
                i += 1
            while code and code[-1] == "":
                code.pop()
            block = CodeBlock(index=len(blocks), lang="", lines=code, start=start, end=i - 1)
            blocks.append(block)
            elements.append(block)
            continue

        m = HEADING.match(line)
        if m:
            flush_para()
            elements.append(Heading(level=len(m.group(1)), text=m.group(2).strip()))
            i += 1
            continue

        m = LIST.match(line)
        if m:
            flush_para()
            elements.append(
                ListItem(marker=m.group(2).strip(), indent=len(m.group(1)) // 2, text=m.group(3))
            )
            i += 1
            continue

        m = QUOTE.match(line)
        if m:
            flush_para()
            elements.append(Quote(m.group(1)))
            i += 1
            continue

        if RULE.match(line):
            flush_para()
            elements.append(Rule())
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para()

    current_label = ""
    for el in elements:
        if isinstance(el, Heading):
            current_label = el.text
        elif isinstance(el, CodeBlock):
            el.label = current_label

    return elements, blocks