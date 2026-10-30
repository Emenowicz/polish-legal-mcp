"""Read Dziennik Ustaw / Monitor Polski PDFs (all ELI texts since 2025 are PDF-only).

Lines are rebuilt from text fragments with their font size: body text is the dominant size,
footnotes are slightly smaller (separated), superscript footnote markers are dropped and page
headers ("Dziennik Ustaw – 2 – Poz. 817") removed. Units are located by line-start markers
("Art. 5.", "1.", "§ 1.", "1)", "a)") accepted only in numbering sequence, so a wrapped line that
happens to start with "2." is not mistaken for a paragraph.

Automatic PDF extraction is never treated as verified wording on its own (spacing artefacts such
as "rzec z" and line-end hyphenation occur); `same_wording` lets callers confirm it against the
HTML text of an earlier consolidated version.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader

from polish_legal_mcp.parsers.legal_html import (
    REPEAL_MARKERS,
    SUPERSCRIPT_DIGITS,
    UnitLookupError,
    display_name,
    in_range,
    normalize_name,
)

_HEADER_RE = re.compile(
    r"^(?:Dziennik Ustaw|Monitor Polski)\s*[–-]\s*\d+\s*[–-]\s*Poz\.\s*\d+\s*$|^©\s*Kancelaria Sejmu"
)
_ANNEX_RE = re.compile(r"^Załącznik do obwieszczenia")
_ARTICLE_RE = re.compile(r"^Art\.\s*(\d+[a-z]*[⁰¹²³⁴⁵⁶⁷⁸⁹]*)\.\s*(.*)$")
_TOP_PARA_RE = re.compile(r"^§\s*(\d+[a-z]*[⁰¹²³⁴⁵⁶⁷⁸⁹]*)\.\s*(.*)$")
# Grouped units: "Art. 164–179. (uchylone)".
_ARTICLE_RANGE_RE = re.compile(r"^Art\.\s*(\d+\s*[–-]\s*\d+)\.\s*(.*)$")
_TOP_PARA_RANGE_RE = re.compile(r"^§\s*(\d+\s*[–-]\s*\d+)\.\s*(.*)$")
_HEADING_RE = re.compile(
    r"^(?:Rozdział|ROZDZIAŁ|Dział|DZIAŁ|Tytuł|TYTUŁ|Księga|KSIĘGA|Oddział)\s+\S+$"
    r"|^Załącznik(?:i)?\s+(?:nr|do)\b"
)
_PASS_RE = re.compile(r"^(\d+[a-z]*[⁰¹²³⁴⁵⁶⁷⁸⁹]*)\.\s+(.*)$")
_PARA_RE = re.compile(r"^§\s*(\d+[a-z]*[⁰¹²³⁴⁵⁶⁷⁸⁹]*)\.\s+(.*)$")
_POINT_RE = re.compile(r"^(\d+[a-z]*[⁰¹²³⁴⁵⁶⁷⁸⁹]*)\)\s+(.*)$")
_LETTER_RE = re.compile(r"^([a-z]{1,2})\)\s+(.*)$")
_LABEL_ONLY_RE = re.compile(r"^(?:\d+[a-z]*[.)]|[a-z]{1,2}\)|§\s*\d+[a-z]*\.)$")
_SPACE_RE = re.compile(r"\s+")
_REFERENCE_RE = re.compile(r"\((?:Dz\.\s*U\.|M\.\s*P\.)[^()]*\)")
_STRICT_REFERENCE_RE = re.compile(
    r"^\((?:Dz\. U\.|M\. P\.)(?: z \d{4} r\.)? poz\. \d+(?:(?:, | i )\d+)*"
    r"(?:(?:,| oraz) z \d{4} r\. poz\. \d+(?:(?:, | i )\d+)*)*\)$"
)
MIN_TEXT_SIZE = 8.0


@dataclass
class PdfLine:
    page: int
    size: int
    text: str


@dataclass
class PdfText:
    lines: list[PdfLine]
    footnotes: list[PdfLine] = field(default_factory=list)
    annex_start: int = 0


@dataclass
class PdfUnit:
    text: str
    page: int
    path: list[str]


def _page_lines(page: Any, number: int) -> list[PdfLine]:
    fragments: list[tuple[float, float, str]] = []

    def visit(text: str, cm: list[float], tm: list[float], _font: object, size: float) -> None:
        if text:
            scale = (abs(tm[0]) or 1.0) * (abs(cm[0]) or 1.0)
            fragments.append((tm[5], size * scale, text))

    page.extract_text(visitor_text=visit)
    lines: list[PdfLine] = []
    current: list[tuple[float, str]] = []

    def flush(parts: list[tuple[float, str]]) -> None:
        text = _SPACE_RE.sub(" ", _line_text(parts)).strip()
        sizes: Counter[int] = Counter()
        for size, part in parts:
            sizes[round(size)] += len(part.strip())
        if text and sizes:
            lines.append(PdfLine(page=number, size=sizes.most_common(1)[0][0], text=text))

    last_y: float | None = None
    for y, size, text in fragments:
        for index, part in enumerate(text.split("\n")):
            if index > 0 or (last_y is not None and abs(y - last_y) > 2 and size >= MIN_TEXT_SIZE):
                flush(current)
                current = []
            current.append((size, part))
            if size >= MIN_TEXT_SIZE:
                last_y = y
    flush(current)
    return lines


def _line_text(parts: list[tuple[float, str]]) -> str:
    """Body text of one line. Small-font runs: "[1]" or a bare number is a numbering
    superscript (art. 22¹) kept as ¹; "1)" is a footnote marker and is dropped."""
    out: list[str] = []
    run = ""
    for size, text in [*[p for p in parts if p[1]], (MIN_TEXT_SIZE, "")]:
        if size < MIN_TEXT_SIZE:
            run += text
            continue
        if run.strip():
            digits = re.fullmatch(r"\[?(\d+)\]?", run.strip())
            if digits:
                out.append(digits.group(1).translate(SUPERSCRIPT_DIGITS))
            run = ""
        out.append(text)
    return "".join(out)


def read_pdf(data: bytes) -> PdfText:
    """Rebuild body lines (and footnotes) from a PDF using fragment font sizes."""
    reader = PdfReader(io.BytesIO(data))
    raw = [
        line for number, page in enumerate(reader.pages, 1) for line in _page_lines(page, number)
    ]

    sizes = Counter(line.size for line in raw)
    body_size = sizes.most_common(1)[0][0] if sizes else 10
    lines: list[PdfLine] = []
    footnotes: list[PdfLine] = []
    annex_start = 0
    for line in raw:
        if _HEADER_RE.search(line.text):
            continue
        if _ANNEX_RE.match(line.text):
            annex_start = len(lines)
            continue
        if line.size >= body_size:
            lines.append(line)
        elif line.size >= body_size - 1.5:
            footnotes.append(line)
    return PdfText(lines=_merge_labels(lines), footnotes=footnotes, annex_start=annex_start)


def _merge_labels(lines: list[PdfLine]) -> list[PdfLine]:
    """Join a line holding only a unit label ("3)") with the line that follows it."""
    merged: list[PdfLine] = []
    skip = False
    for index, line in enumerate(lines):
        if skip:
            skip = False
            continue
        if _LABEL_ONLY_RE.match(line.text) and index + 1 < len(lines):
            nxt = lines[index + 1]
            merged.append(PdfLine(line.page, line.size, f"{line.text} {nxt.text}"))
            skip = True
        else:
            merged.append(line)
    return merged


def _key(label: str) -> tuple[int, str]:
    match = re.match(r"(\d*)(.*)", normalize_name(label))
    digits, suffix = (match.group(1), match.group(2)) if match else ("", label)
    return (int(digits) if digits else 0, suffix)


def _is_next(previous: str | None, label: str, letters: bool = False) -> bool:
    """Numbering continues exactly: 1→2, 2→2a, 2a→2b, 2a→3; letters a→b, z→za."""
    if letters:
        if previous is None:
            return label == "a"
        if len(label) == len(previous) == 1:
            return ord(label) == ord(previous) + 1
        return len(label) == len(previous) + 1 and label.startswith(previous)
    (num, suf) = _key(label)
    if previous is None:
        return num == 1 and suf == ""
    p_num, p_suf = _key(previous)
    if num == p_num:
        return suf > p_suf
    return num == p_num + 1 and suf == ""


@dataclass
class _Block:
    label: str
    lines: list[PdfLine]


def _split(lines: list[PdfLine], pattern: re.Pattern[str], letters: bool = False) -> list[_Block]:
    """Split lines at line-start markers that continue the numbering sequence."""
    blocks: list[_Block] = []
    previous: str | None = None
    for line in lines:
        match = pattern.match(line.text)
        label = normalize_name(match.group(1)) if match else ""
        if match and _is_next(previous, label, letters):
            previous = label
            blocks.append(_Block(label, [line]))
        elif blocks:
            blocks[-1].lines.append(line)
    return blocks


def _render(lines: list[PdfLine]) -> str:
    out: list[str] = []
    for line in lines:
        text = line.text
        starts_unit = bool(
            _ARTICLE_RE.match(text)
            or _ARTICLE_RANGE_RE.match(text)
            or _PASS_RE.match(text)
            or _PARA_RE.match(text)
            or _POINT_RE.match(text)
            or _LETTER_RE.match(text)
        )
        if out and not starts_unit:
            if out[-1].endswith("-") and text[:1].islower():
                out[-1] = out[-1][:-1] + text  # line-end hyphenation
            else:
                out[-1] = f"{out[-1]} {text}"
        else:
            out.append(text)
    return "\n".join(_SPACE_RE.sub(" ", line).strip() for line in out)


def find_pdf_unit(
    pdf: PdfText,
    article: str,
    paragraph: str | None = None,
    point: str | None = None,
    letter: str | None = None,
) -> PdfUnit:
    lines = pdf.lines[pdf.annex_start :]
    top_para = article.strip().startswith("§")
    pattern = _TOP_PARA_RE if top_para else _ARTICLE_RE
    range_pattern = _TOP_PARA_RANGE_RE if top_para else _ARTICLE_RANGE_RE
    wanted = normalize_name(article)
    starts = [
        i
        for i, line in enumerate(lines)
        if (m := pattern.match(line.text)) and normalize_name(m.group(1)) == wanted
    ]
    grouped = None
    if not starts:
        for i, line in enumerate(lines):
            m = range_pattern.match(line.text)
            if m and in_range(m.group(1).replace(" ", ""), wanted):
                starts.append(i)
                grouped = m.group(1).replace(" ", "")
    label = f"§ {display_name(wanted)}" if top_para else f"art. {display_name(wanted)}"
    if not starts:
        raise UnitLookupError("not_found", f"Nie znaleziono {label} w tekście PDF.")
    if len(starts) > 1:
        raise UnitLookupError("ambiguous", f"{label} występuje w tekście PDF wielokrotnie.")
    begin = starts[0]
    end = begin + 1
    while end < len(lines) and not (
        pattern.match(lines[end].text)
        or range_pattern.match(lines[end].text)
        or _HEADING_RE.match(lines[end].text)
    ):
        end += 1
    unit_lines = list(lines[begin:end])
    head = (range_pattern if grouped else pattern).match(unit_lines[0].text)
    remainder = head.group(2) if head else ""
    path = [f"{'§' if top_para else 'Art.'} {grouped or display_name(wanted)}."]
    scope = unit_lines
    if paragraph:
        body = (
            [PdfLine(unit_lines[0].page, unit_lines[0].size, remainder)] if remainder else []
        ) + unit_lines[1:]
        explicit = paragraph.strip().lower()
        patterns = (
            [_PASS_RE]
            if top_para or explicit.startswith("ust")
            else [_PARA_RE]
            if explicit.startswith("§")
            else [_PASS_RE, _PARA_RE]
        )
        target = normalize_name(paragraph)
        found = None
        for pat in patterns:
            blocks = _split(body, pat)
            found = next((b for b in blocks if b.label == target), None)
            if found:
                path.append(f"{'§ ' if pat is _PARA_RE else ''}{display_name(target)}.")
                break
        if found is None:
            raise UnitLookupError(
                "not_found", f"{label} nie zawiera ust./§ {paragraph} w tekście PDF."
            )
        scope = found.lines
    if point:
        blocks = _split(_tail(scope), _POINT_RE)
        found = next((b for b in blocks if b.label == normalize_name(point)), None)
        if found is None:
            raise UnitLookupError(
                "not_found", f"{' '.join(path)} nie zawiera pkt {point} w tekście PDF."
            )
        scope = found.lines
        path.append(f"{display_name(point)})")
    if letter:
        blocks = _split(_tail(scope), _LETTER_RE, letters=True)
        found = next((b for b in blocks if b.label == normalize_name(letter)), None)
        if found is None:
            raise UnitLookupError(
                "not_found", f"{' '.join(path)} nie zawiera lit. {letter} w tekście PDF."
            )
        scope = found.lines
        path.append(f"{display_name(letter)})")
    return PdfUnit(text=_render(scope), page=scope[0].page, path=path)


def _tail(lines: list[PdfLine]) -> list[PdfLine]:
    """Lines after the unit's own label line, keeping the rest of that first line."""
    if not lines:
        return lines
    first = lines[0]
    for pattern in (_ARTICLE_RE, _TOP_PARA_RE, _PASS_RE, _PARA_RE, _POINT_RE, _LETTER_RE):
        match = pattern.match(first.text)
        if match:
            rest = match.group(2)
            return ([PdfLine(first.page, first.size, rest)] if rest else []) + lines[1:]
    return lines


def _comparable(text: str) -> str:
    text = text.replace("­", "")
    text = re.sub(r"[„”“\"«»]", '"', text)
    return re.sub(r"[\s\-–—‑]", "", text)


def references(text: str) -> list[str]:
    return [_SPACE_RE.sub(" ", m.group(0)) for m in _REFERENCE_RE.finditer(text)]


def same_wording(html_text: str, pdf_text: str) -> tuple[bool, str | None]:
    """Compare HTML and PDF wording ignoring whitespace/hyphenation artefacts.

    Returns (True, None) for identical wording; (True, updated_html) when only the publication
    addresses of cited acts differ and the PDF ones are well-formed (the HTML text with those
    addresses replaced); (False, None) otherwise.
    """
    if _comparable(html_text) == _comparable(pdf_text):
        return True, None
    html_refs, pdf_refs = references(html_text), references(pdf_text)
    if len(html_refs) != len(pdf_refs) or not html_refs:
        return False, None
    strip = lambda t: _comparable(_REFERENCE_RE.sub("", t))  # noqa: E731
    if strip(html_text) != strip(pdf_text):
        return False, None
    if not all(_STRICT_REFERENCE_RE.match(ref) for ref in pdf_refs):
        return False, None
    updated = html_text
    for old, new in zip(_REFERENCE_RE.findall(html_text), pdf_refs, strict=True):
        updated = updated.replace(old, new, 1)
    return True, updated


def repeal_marker(text: str) -> str | None:
    """'(uchylony)' etc. when the unit has no normative content left."""
    if "\n" in text.strip():
        return None
    body = text.strip()
    for pattern in (
        _ARTICLE_RE,
        _ARTICLE_RANGE_RE,
        _TOP_PARA_RE,
        _TOP_PARA_RANGE_RE,
        _PASS_RE,
        _PARA_RE,
        _POINT_RE,
        _LETTER_RE,
    ):
        match = pattern.match(body)
        if match:
            body = match.group(2)
            break
    body = _SPACE_RE.sub(" ", body).strip()
    return body if body in REPEAL_MARKERS else None
