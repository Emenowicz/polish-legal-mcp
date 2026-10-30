"""Locate structural units via ELI ``/struct`` and extract their wording from ELI ``text.html``.

ELI's ``text.html/{tree}`` fragment endpoint is deliberately not used: in consolidated-text
notices it can return an article of the notice itself instead of the annexed act, and it
answers 200 with an empty body for missing units.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import lxml.html
from lxml.etree import ParserError

CONTAINER_TYPES = {"part", "book", "titl", "bran", "chpt", "schp"}
NOTICE_PART_PREFIX = "Treść obwieszczenia"
TYPE_LABELS = {"arti": "art.", "pass": "ust.", "para": "§", "pint": "pkt", "lett": "lit."}
REPEAL_MARKERS = {"(uchylony)", "(uchylona)", "(uchylone)", "(pominięty)", "(pominięta)"}

_PREFIX_RE = re.compile(
    r"^(art\.?|artykuł|§|ust\.?|ustęp|pkt\.?|punkt|lit\.?|litera)\s*", re.IGNORECASE
)
_SPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:)\u201d])")
_SPACE_AFTER_PAREN_RE = re.compile(r"([(\u201e])\s+")


class UnitLookupError(Exception):
    def __init__(self, reason: Literal["not_found", "ambiguous"], message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class UnitMatch:
    node_id: str
    path: list[str]
    paragraph_kind: Literal["ust.", "§"] | None = None


@dataclass
class ExtractedUnit:
    text: str
    footnotes: list[str] = field(default_factory=list)
    marker: str | None = None  # "(uchylony)" etc. when the unit has no normative content


SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_FROM_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUPERSCRIPT_RE = re.compile(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+)$")
_SUFFIX_FORMS_RE = re.compile(r"(?:\^|_)(\d+)$|\((\d+)\)$")


_RANGE_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)\.?$")


def in_range(name: str, wanted: str) -> bool:
    """Grouped units such as 'Art. 164-179. (uchylone)': is article 171 one of them?"""
    match = _RANGE_RE.match(name.strip())
    return bool(
        match and wanted.isdigit() and int(match.group(1)) <= int(wanted) <= int(match.group(2))
    )


def normalize_name(value: str) -> str:
    """Canonical unit number as ELI's struct writes it: '22¹', '22^1', '22(1)' → '22_1'."""
    value = _PREFIX_RE.sub("", value.strip().lower()).replace(" ", "")
    value = value.rstrip(".")
    superscript = _SUPERSCRIPT_RE.search(value)
    if superscript:
        digits = superscript.group(1).translate(_FROM_SUPERSCRIPT)
        return f"{value[: superscript.start()].rstrip(').')}_{digits}"
    suffix = _SUFFIX_FORMS_RE.search(value)
    if suffix:
        return f"{value[: suffix.start()]}_{suffix.group(1) or suffix.group(2)}"
    return value.rstrip(").")


def display_name(value: str) -> str:
    """'22_1' → '22¹' (how Polish legal texts print such numbers)."""
    base, _, sup = normalize_name(value).partition("_")
    return base + sup.translate(SUPERSCRIPT_DIGITS) if sup else base


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children") or []
    return children if isinstance(children, list) else []


def _is_notice_part(node: dict[str, Any]) -> bool:
    return node.get("type") == "part" and str(node.get("title", "")).startswith(NOTICE_PART_PREFIX)


def _label(node: dict[str, Any]) -> str:
    title = _clean(str(node.get("title") or node.get("id") or ""))
    # ELI writes art. 22¹ as "22_1".
    return re.sub(
        r"(\d)_(\d+)", lambda m: m.group(1) + m.group(2).translate(SUPERSCRIPT_DIGITS), title
    )


def _top_units(nodes: list[dict[str, Any]], unit_type: str, path: list[str]):
    """Yield (node, path) for main units reachable only through container nodes."""
    for node in nodes:
        node_type = node.get("type")
        if _is_notice_part(node):
            continue
        if node_type in CONTAINER_TYPES:
            yield from _top_units(_children(node), unit_type, [*path, _label(node)])
        elif node_type == unit_type:
            yield node, path


def has_unit_type(struct: list[dict[str, Any]], unit_type: str) -> bool:
    return any(True for _ in _top_units(struct, unit_type, []))


def _pick_child(
    parent: dict[str, Any], types: tuple[str, ...], name: str, what: str, parent_label: str
) -> dict[str, Any]:
    wanted = normalize_name(name)
    kids = [c for c in _children(parent) if c.get("type") in types]
    matches = [c for c in kids if normalize_name(str(c.get("name", ""))) == wanted]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        kinds = ", ".join(TYPE_LABELS.get(str(m.get("type")), "?") for m in matches)
        raise UnitLookupError(
            "ambiguous", f"{parent_label}: niejednoznaczne oznaczenie {what} {name} ({kinds})."
        )
    available = ", ".join(str(c.get("name")) for c in kids) or "brak"
    raise UnitLookupError(
        "not_found",
        f"{parent_label} nie zawiera jednostki {what} {name} w pobranej wersji tekstu "
        f"(dostępne: {available}).",
    )


def find_unit(
    struct: list[dict[str, Any]],
    article: str,
    paragraph: str | None = None,
    point: str | None = None,
    letter: str | None = None,
) -> UnitMatch:
    """Resolve a citation to a struct node id. Never guesses: raises UnitLookupError instead."""
    top_type = "para" if article.strip().startswith("§") else "arti"
    wanted = normalize_name(article)
    candidates = [
        (node, path)
        for node, path in _top_units(struct, top_type, [])
        if normalize_name(str(node.get("name", ""))) == wanted
    ]
    top_label = "§" if top_type == "para" else "art."
    if not candidates:
        candidates = [
            (node, path)
            for node, path in _top_units(struct, top_type, [])
            if in_range(str(node.get("name", "")), wanted)
        ]
    if not candidates:
        hint = ""
        if (
            top_type == "arti"
            and has_unit_type(struct, "para")
            and not has_unit_type(struct, "arti")
        ):
            hint = " Akt dzieli się na paragrafy – podaj jednostkę jako '§ N'."
        raise UnitLookupError(
            "not_found", f"Nie znaleziono {top_label} {article} w pobranej wersji tekstu.{hint}"
        )
    if len(candidates) > 1:
        raise UnitLookupError(
            "ambiguous",
            f"{top_label} {article} występuje w kilku miejscach aktu: "
            + "; ".join(" > ".join(p) or "(poziom główny)" for _, p in candidates),
        )
    node, path = candidates[0]
    path = [*path, _label(node)]
    paragraph_kind: Literal["ust.", "§"] | None = None
    if paragraph:
        explicit = paragraph.strip().lower()
        if top_type == "para" or explicit.startswith("ust"):
            types: tuple[str, ...] = ("pass",)
        elif explicit.startswith("§"):
            types = ("para",)
        else:
            types = ("pass", "para")
        node = _pick_child(node, types, paragraph, "ust./§", path[-1])
        paragraph_kind = "§" if node.get("type") == "para" else "ust."
        path.append(_label(node))
    if point:
        node = _pick_child(node, ("pint",), point, "pkt", " ".join(path[-2:]))
        path.append(_label(node))
    if letter:
        node = _pick_child(node, ("lett",), letter, "lit.", " ".join(path[-2:]))
        path.append(_label(node))
    return UnitMatch(node_id=str(node["id"]), path=path, paragraph_kind=paragraph_kind)


def iter_toc(struct: list[dict[str, Any]], max_depth: int = 6):
    """Flatten struct into (id, type, label, depth), skipping notice text and sub-article units."""

    def walk(nodes: list[dict[str, Any]], depth: int):
        for node in nodes:
            if _is_notice_part(node) or depth > max_depth:
                continue
            node_type = str(node.get("type", ""))
            yield str(node.get("id", "")), node_type, _label(node), depth
            if node_type in CONTAINER_TYPES:
                yield from walk(_children(node), depth + 1)

    yield from walk(struct, 0)


def _classes(el: Any) -> set[str]:
    return set((el.get("class") or "").split())


def _clean(text: str) -> str:
    """Normalize whitespace only; wording is otherwise left untouched."""
    text = _SPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()
    return _SPACE_AFTER_PAREN_RE.sub(r"\1", _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text))


def _strip_footnotes(unit: Any) -> list[str]:
    """Remove footnote tooltips (returned separately).

    Elements with class ``xHidden`` (publication addresses of cited acts) are kept: they are
    part of the published text and are only collapsed by the ISAP stylesheet.
    """
    footnotes: list[str] = []
    for link in unit.xpath(
        './/a[contains(concat(" ", normalize-space(@class), " "), " gloss-link ")]'
    ):
        marker = _clean("".join(link.xpath(".//sup//text()")))
        body = _clean(" ".join(link.xpath('.//*[contains(@class, "tooltip-text")]//text()')))
        if body:
            footnotes.append(f"{marker} {body}".strip())
        link.drop_tree()
    # Remaining <sup> are numbering superscripts (art. 22<sup>1</sup>), not footnote markers.
    for sup in unit.iter("sup"):
        digits = (sup.text or "").strip()
        if digits.isdigit():
            sup.text = digits.translate(SUPERSCRIPT_DIGITS)
    return footnotes


def _render(unit: Any, depth: int, lines: list[str]) -> None:
    heading_el = next((c for c in unit if c.tag == "h3"), None)
    heading = _clean(heading_el.text_content()) if heading_el is not None else ""
    indent = "  " * depth
    lines.append(indent + heading)
    heading_index = len(lines) - 1
    inner = next((c for c in unit if c.tag == "div" and "unit-inner" in _classes(c)), None)
    if inner is None:
        return
    for child in inner:
        if not isinstance(child.tag, str):
            continue
        if "unit" in _classes(child):
            _render(child, depth + 1, lines)
            continue
        text = _clean(child.text_content())
        if not text:
            continue
        if len(lines) - 1 == heading_index:
            lines[heading_index] = f"{lines[heading_index]} {text}".rstrip()
        else:
            lines.append(indent + text)


_XML_DECLARATION_RE = re.compile(r"^\s*<\?xml[^>]*\?>")


def parse_html(html: str) -> Any:
    # lxml rejects str input that still carries an XML encoding declaration (EU XHTML does).
    html = _XML_DECLARATION_RE.sub("", html, count=1)
    try:
        return lxml.html.document_fromstring(html)
    except (ParserError, ValueError) as exc:
        raise UnitLookupError("not_found", "Nie udało się przetworzyć HTML tekstu aktu.") from exc


def extract_unit(doc: Any, node_id: str) -> ExtractedUnit | None:
    """Return the unit's wording (footnotes separated), or None if the id is absent in HTML."""
    elements = [e for e in doc.xpath("//*[@id=$id]", id=node_id) if "unit" in _classes(e)]
    if not elements:
        return None
    if len(elements) > 1:
        raise UnitLookupError(
            "ambiguous", f"Identyfikator {node_id} występuje w HTML wielokrotnie."
        )
    unit = elements[0]
    footnotes = _strip_footnotes(unit)
    lines: list[str] = []
    _render(unit, 0, lines)
    text = "\n".join(line.rstrip() for line in lines if line.strip())
    heading_el = next((c for c in unit if c.tag == "h3"), None)
    heading = _clean(heading_el.text_content()) if heading_el is not None else ""
    body = _clean(text.replace(heading, "", 1)) if heading else _clean(text)
    marker = body if body in REPEAL_MARKERS else None
    return ExtractedUnit(text=text, footnotes=footnotes, marker=marker)


def act_body_text(doc: Any, struct: list[dict[str, Any]]) -> str:
    """Text of the act itself, excluding the body of a consolidated-text notice."""
    part_ids = [
        str(n.get("id"))
        for n in struct
        if n.get("type") == "part" and not _is_notice_part(n) and n.get("id")
    ]
    has_notice = any(_is_notice_part(n) for n in struct)
    if has_notice and part_ids:
        texts = []
        for part_id in part_ids:
            for el in doc.xpath("//*[@id=$id]", id=part_id):
                texts.append(el.text_content())
        return _clean(" ".join(texts))
    return _clean(doc.text_content())
