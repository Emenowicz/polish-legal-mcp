"""Numbered paragraphs and the operative part of CJEU documents served by CELLAR.

Since about 2008 judgments share one layout: every numbered paragraph is a top-level two-cell
table whose first cell holds the number (``p.count`` or, from about 2014, ``p.coj-count``);
quoted legislation and lists are nested inside the second cell. The operative part follows the
sentence "… orzeka, co następuje:". Older translations (e.g. 1964) use a different layout without
numbered paragraphs; they are reported as unsupported rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from polish_legal_mcp.parsers.legal_html import parse_html

_SPACE_RE = re.compile(r"\s+")
_LABEL_RE = re.compile(r"[a-z0-9]{1,3}\)|\(?[ivx]{1,4}\)")
_RULING_RE = re.compile(r"orzeka\s*,?\s*co\s+następuje|postanawia\s*:", re.IGNORECASE)
_LANGUAGE_RE = re.compile(r"Język postępowania:\s*([^.<\n]+)")


@dataclass
class CjeuDocument:
    paragraphs: dict[int, str] = field(default_factory=dict)
    operative_part: str | None = None
    language_of_case: str | None = None

    @property
    def supported(self) -> bool:
        return bool(self.paragraphs)


def _classes(element: Any) -> set[str]:
    return set((element.get("class") or "").split())


def _text(element: Any) -> str:
    lines: list[str] = []
    for block in element.iter("p"):
        text = _SPACE_RE.sub(" ", block.text_content()).strip()
        if not text:
            continue
        # A list item is a row "– | text" or "a) | text": join the label with its text.
        if lines and (lines[-1] in ("–", "—", "-") or _LABEL_RE.fullmatch(lines[-1])):
            lines[-1] = f"{lines[-1]} {text}"
        else:
            lines.append(text)
    return "\n".join(lines)


def _paragraph_number(table: Any) -> int | None:
    rows = table.xpath("./tr | ./tbody/tr")
    if not rows:
        return None
    cells = rows[0].xpath("./td")
    if len(cells) < 2:
        return None
    marks = [p for p in cells[0].iter("p") if _classes(p) & {"count", "coj-count"}]
    if not marks:
        return None
    value = marks[0].text_content().strip()
    return int(value) if value.isdigit() else None


def parse_cjeu(html: str) -> CjeuDocument:
    doc = parse_html(html)
    bodies = doc.xpath("//body")
    body = bodies[0] if bodies else doc
    result = CjeuDocument()
    language = _LANGUAGE_RE.search(_SPACE_RE.sub(" ", body.text_content()))
    if language:
        result.language_of_case = language.group(1).strip()
    ruling_from: int | None = None
    children = [c for c in body if isinstance(c.tag, str)]
    for index, child in enumerate(children):
        if child.tag == "table":
            number = _paragraph_number(child)
            if number is not None and number not in result.paragraphs and ruling_from is None:
                cell = child.xpath("./tr/td[2] | ./tbody/tr/td[2]")[0]
                result.paragraphs[number] = _text(cell)
        if _RULING_RE.search(child.text_content()):
            ruling_from = index + 1
    if ruling_from is not None:
        parts: list[str] = []
        for child in children[ruling_from:]:
            if child.tag == "hr" or _classes(child) & {"note", "coj-note"}:
                break
            if child.xpath('.//*[contains(@class, "signaturecase")]'):
                break
            text = _text(child)
            if text:
                parts.append(text)
        result.operative_part = "\n".join(parts) or None
    return result
