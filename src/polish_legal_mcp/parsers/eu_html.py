"""Extract articles from EU documents served by the Publications Office (CELLAR).

Two formats, both with ``div#art_N`` per article:
- Official Journal ("oj-" classes): paragraphs ``div#028.003``, points/letters in two-cell tables.
- EUR-Lex consolidated versions: paragraphs ``div.norm`` with ``span.no-parag`` ("3."),
  points/letters in ``div.grid-container`` (label column + body column), and ▼C1/►C1 markers
  showing where corrigenda (C) or amendments (M) were applied; markers are removed from wording.
Older documents without article ids are reported as unsupported, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from polish_legal_mcp.parsers.legal_html import UnitLookupError, normalize_name, parse_html

_SPACE_RE = re.compile(r"\s+")
_MARKER_RE = re.compile(r"[►▼]\s*[A-Z]\d+\s*|◄")
_OJ_REF_RE = re.compile(
    r"\b([LC])\s*0*(\d+)\s*[,/]?\s*(?:z\s*)?(\d{1,2}\.\d{1,2}\.\d{4})?,?\s*(?:s\.|str\.)?\s*(\d+)?"
)
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


@dataclass
class EuUnit:
    text: str
    path: list[str]


@dataclass
class OjReference:
    series_and_number: str  # e.g. "L 119"
    page: str  # e.g. "1"
    date: str  # e.g. "4.5.2016"

    def format(self) -> str:
        # The Official Journal of the European Communities became "of the European Union"
        # on 1 February 2003 (Treaty of Nice).
        published = self.published
        journal = "Dz. Urz. WE" if published and published < date(2003, 2, 1) else "Dz. Urz. UE"
        return f"{journal} {self.series_and_number} z {self.date}, str. {self.page}"

    @property
    def published(self) -> date | None:
        match = _DATE_RE.search(self.date)
        if not match:
            return None
        day, month, year = (int(g) for g in match.groups())
        return date(year, month, day)


@dataclass
class EuDocument:
    oj: OjReference | None
    consolidated: bool
    # (marker, CELEX, date) of corrigenda (C1…) and amendments (M1…) incorporated in a
    # consolidation, as listed in its header.
    modifiers: list[tuple[str, str | None, date | None]] = field(default_factory=list)

    @property
    def incorporated_celex(self) -> set[str]:
        return {celex for _, celex, _ in self.modifiers if celex}


def _clean(text: str) -> str:
    text = _MARKER_RE.sub(" ", text.replace("\xa0", " "))
    return _SPACE_RE.sub(" ", text).strip()


def _classes(element: Any) -> set[str]:
    return set((element.get("class") or "").split())


def _by_class(root: Any, css_class: str) -> list[Any]:
    return root.xpath(f'.//*[contains(concat(" ", normalize-space(@class), " "), " {css_class} ")]')


def _parse_date(text: str) -> date | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def describe_document(doc: Any) -> EuDocument:
    """OJ reference of the original act and, for consolidations, the incorporated modifiers."""
    consolidated = bool(_by_class(doc, "hd-modifiers")) or bool(_by_class(doc, "norm"))
    oj: OjReference | None = None
    header_oj, header_date = _by_class(doc, "oj-hd-oj"), _by_class(doc, "oj-hd-date")
    if header_oj and header_date:
        number, _, page = _clean(header_oj[0].text_content()).rpartition("/")
        oj = OjReference(number.strip(), page.strip(), _clean(header_date[0].text_content()))
    else:
        for ref in _by_class(doc, "title-doc-oj-reference"):
            match = _OJ_REF_RE.search(_clean(ref.text_content()))
            if match and match.group(3) and match.group(4):
                oj = OjReference(
                    f"{match.group(1)} {match.group(2)}", match.group(4), match.group(3)
                )
                break
    modifiers: list[tuple[str, str | None, date | None]] = []
    for row in doc.xpath('//tr[.//p[contains(@class, "arrow")]]'):
        link = row.xpath('.//p[contains(@class, "arrow")]//a')
        if not link:
            continue
        marker = _SPACE_RE.sub("", link[0].text_content()).lstrip("►")
        celex = (link[0].get("title") or "").split(":")[0].strip() or None
        modifiers.append((marker, celex, _parse_date(row.text_content())))
    return EuDocument(oj=oj, consolidated=consolidated, modifiers=modifiers)


def _within(element: Any, scope: Any) -> bool:
    return element is scope or any(a is scope for a in element.iterancestors())


def _is_list(element: Any) -> bool:
    return element.tag == "table" or "grid-container" in _classes(element)


def _list_rows(container: Any) -> list[tuple[str, Any]]:
    if container.tag == "table":
        rows = []
        for tr in container.xpath("./tbody/tr | ./tr"):
            cells = tr.xpath("./td")
            if len(cells) >= 2:
                rows.append((_clean(cells[0].text_content()), cells[1]))
        return rows
    label = container.xpath('./*[contains(@class, "grid-list-column-1")]')
    body = container.xpath('./*[contains(@class, "grid-list-column-2")]')
    if label and body:
        return [(_clean(label[0].text_content()), body[0])]
    return []


def _rows(scope: Any) -> list[tuple[str, Any]]:
    """(label, body) of point/letter lists directly in scope (not nested in another list)."""
    rows: list[tuple[str, Any]] = []
    for element in scope.iter():
        if not isinstance(element.tag, str) or element is scope or not _is_list(element):
            continue
        if any(
            _is_list(a) for a in element.iterancestors() if a is not scope and _within(a, scope)
        ):
            continue
        rows.extend(_list_rows(element))
    return rows


def _render(element: Any, lines: list[str], indent: str = "") -> None:
    pending_label = ""
    for child in element:
        if not isinstance(child.tag, str) or "modref" in _classes(child):
            continue
        if _is_list(child):
            for label, body in _list_rows(child):
                sub: list[str] = []
                _render(body, sub, indent + "  ")
                first = sub[0].strip() if sub else ""
                lines.append(f"{indent}{label} {first}".rstrip())
                lines.extend(sub[1:])
        elif "no-parag" in _classes(child):
            pending_label = _clean(child.text_content())
        elif child.tag == "p" or (child.tag == "div" and not len(child) and child.text):
            text = _clean(child.text_content())
            if text:
                lines.append(
                    f"{indent}{pending_label} {text}".strip() if pending_label else indent + text
                )
                pending_label = ""
        else:
            start = len(lines)
            _render(child, lines, indent)
            if pending_label and len(lines) > start:
                lines[start] = f"{indent}{pending_label} {lines[start].strip()}"
                pending_label = ""


def _paragraph(article_div: Any, number: str, paragraph: str) -> Any:
    par = normalize_name(paragraph)
    if number.isdigit() and par.isdigit():
        hits = article_div.xpath(".//div[@id=$id]", id=f"{int(number):03d}.{int(par):03d}")
        if hits:
            return hits[0]
    available = []
    for div in article_div.xpath(
        './div[contains(concat(" ", normalize-space(@class), " "), " norm ")]'
    ):
        label = div.xpath('./span[contains(@class, "no-parag")]')
        if label:
            value = _clean(label[0].text_content()).rstrip(".")
            available.append(value)
            if value == par:
                return div
    if not available:
        ids = article_div.xpath('.//div[contains(@id, ".")]/@id')
        available = [str(int(i.split(".")[1])) for i in ids if i.split(".")[-1].isdigit()]
    raise UnitLookupError(
        "not_found",
        f"Art. {number} nie zawiera ust. {paragraph} (dostępne: {', '.join(available) or 'brak'}).",
    )


def _pick_row(scope: Any, label: str, what: str, where: str) -> Any:
    wanted = f"{normalize_name(label)})"
    rows = _rows(scope)
    matches = [body for lbl, body in rows if lbl.lower() == wanted]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise UnitLookupError("ambiguous", f"{where}: niejednoznaczne oznaczenie {what} {label}.")
    available = ", ".join(lbl for lbl, _ in rows) or "brak"
    raise UnitLookupError(
        "not_found", f"{where} nie zawiera {what} {label} (dostępne: {available})."
    )


def extract_eu_unit(
    html: str,
    article: str,
    paragraph: str | None = None,
    point: str | None = None,
    letter: str | None = None,
) -> tuple[EuUnit, EuDocument]:
    doc = parse_html(html)
    info = describe_document(doc)
    if not doc.xpath('//div[starts-with(@id, "art_")]'):
        raise UnitLookupError(
            "ambiguous",
            "Dokument nie ma znaczników artykułów (starszy format) – automatyczne wyodrębnienie "
            "przepisu nie jest obsługiwane; sprawdź tekst w EUR-Lex.",
        )
    number = normalize_name(article)
    found = doc.xpath("//div[@id=$id]", id=f"art_{number}")
    if not found:
        raise UnitLookupError("not_found", f"Nie znaleziono art. {article} w tekście aktu.")
    scope = found[0]
    title = scope.xpath('./p[contains(@class, "ti-art") or contains(@class, "title-article")]')
    heading = scope.xpath('.//p[contains(@class, "sti-art") or contains(@class, "stitle-article")]')
    label = _clean(title[0].text_content()) if title else f"Artykuł {number}"
    path = [f"{label} – {_clean(heading[0].text_content())}" if heading else label]
    if paragraph:
        scope = _paragraph(scope, number, paragraph)
        path.append(f"ust. {normalize_name(paragraph)}")
    if point:
        scope = _pick_row(scope, point, "pkt", " ".join(path))
        path.append(f"pkt {normalize_name(point)}")
    if letter:
        scope = _pick_row(scope, letter, "lit.", " ".join(path))
        path.append(f"lit. {normalize_name(letter)}")
    lines: list[str] = []
    if point or letter:
        _render(scope, lines)
        if lines:
            lines[0] = f"{normalize_name(letter or point or '')}) {lines[0].strip()}"
    elif paragraph:
        label_el = scope.xpath('./span[contains(@class, "no-parag")]')
        _render(scope, lines)
        if label_el and lines and not lines[0].startswith(_clean(label_el[0].text_content())):
            lines[0] = f"{_clean(label_el[0].text_content())} {lines[0].strip()}"
    else:
        _render(scope, lines)
    text = "\n".join(line.rstrip() for line in lines if line.strip())
    return EuUnit(text=text, path=path), info
