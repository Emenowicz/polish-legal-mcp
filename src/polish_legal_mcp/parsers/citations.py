"""Parse Polish statutory citations such as
'art. 6 pkt 1 lit. a ustawy z dnia 21 sierpnia 1997 r. o gospodarce nieruchomościami (Dz. U. …)'.
"""

from __future__ import annotations

import re

from polish_legal_mcp.errors import InvalidInputError
from polish_legal_mcp.models import ActId, ParsedCitation

# Abbreviations mapped to base acts. Each ELI id was checked against api.sejm.gov.pl (2026-10-29).
KNOWN_ALIASES: dict[str, tuple[str, str]] = {
    "kc": ("k.c.", "DU/1964/93"),
    "kpc": ("k.p.c.", "DU/1964/296"),
    "kpa": ("k.p.a.", "DU/1960/168"),
    "kp": ("k.p.", "DU/1974/141"),
    "kk": ("k.k.", "DU/1997/553"),
    "ugn": ("u.g.n.", "DU/1997/741"),
    "konstytucji": ("Konstytucja RP", "DU/1997/483"),
    "konstytucja": ("Konstytucja RP", "DU/1997/483"),
}

_NO = r"\d+[a-z]{0,3}"
UNIT_RE = re.compile(
    rf"""
    (?:art\.?\s*(?P<art>{_NO})|§\s*(?P<top_par>{_NO}))
    (?:\s*,?\s*(?:ust\.?\s*(?P<ust>{_NO})|§\s*(?P<par>{_NO})))?
    (?:\s*,?\s*pkt\.?\s*(?P<pkt>{_NO}))?
    (?:\s*,?\s*lit\.?\s*(?P<lit>[a-z]{{1,2}})\b)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
PUBLICATION_RE = re.compile(
    r"(Dz\.?\s*U\.?|M\.?\s*P\.?)\s*(?:z\s*)?(\d{4})\s*(?:r\.?)?\s*(?:,?\s*nr\s*\d+\s*)?,?\s*poz\.?\s*(\d{1,5})",
    re.IGNORECASE,
)
ELI_IN_TEXT_RE = re.compile(r"\b(DU|MP)/(\d{4})/(\d{1,5})\b", re.IGNORECASE)
ACT_DATE_RE = re.compile(r"z\s+dnia\s+\d{1,2}\s+\w+\s+(\d{4})\s*r?\.?", re.IGNORECASE)

MAX_CITATION_LENGTH = 1000


def parse_citation(raw: str) -> ParsedCitation:
    text = raw.strip()
    if not text:
        raise InvalidInputError("Pusty cytat.")
    if len(text) > MAX_CITATION_LENGTH:
        raise InvalidInputError("Cytat jest zbyt długi (limit 1000 znaków).")
    parsed = ParsedCitation(raw=text)
    unit = UNIT_RE.search(text)
    if unit:
        if unit.group("art"):
            parsed.article = unit.group("art")
            if unit.group("par"):
                parsed.paragraph, parsed.paragraph_kind = unit.group("par"), "§"
        else:
            parsed.article = f"§ {unit.group('top_par')}"
        if unit.group("ust"):
            parsed.paragraph, parsed.paragraph_kind = unit.group("ust"), "ust."
        parsed.point = unit.group("pkt")
        parsed.letter = unit.group("lit")
        rest = text[unit.end() :]
    else:
        rest = text

    eli = ELI_IN_TEXT_RE.search(text)
    publication = PUBLICATION_RE.search(text)
    if eli:
        parsed.publication = ActId(eli.group(1).upper(), int(eli.group(2)), int(eli.group(3))).eli
    elif publication:
        publisher = "DU" if publication.group(1).upper().startswith("DZ") else "MP"
        parsed.publication = ActId(
            publisher, int(publication.group(2)), int(publication.group(3))
        ).eli

    act_text = rest
    if publication:
        act_text = act_text.split(publication.group(0))[0]
    act_text = re.sub(r"[()\s,]+$", "", act_text).strip()
    parsed.act_text = act_text or None

    alias_key = re.sub(r"[^a-ząćęłńóśźż]", "", (act_text or "").lower())
    if alias_key in KNOWN_ALIASES:
        parsed.known_alias = KNOWN_ALIASES[alias_key][0]
    return parsed


def alias_act_id(parsed: ParsedCitation) -> ActId | None:
    if not parsed.known_alias:
        return None
    for label, eli in KNOWN_ALIASES.values():
        if label == parsed.known_alias:
            return ActId.parse(eli)
    return None


def title_search_terms(act_text: str) -> tuple[str | None, str | None, int | None]:
    """Split 'ustawy z dnia 21 sierpnia 1997 r. o gospodarce nieruchomościami' into
    (act type stem for ELI 'type', title fragment, act-date year)."""
    lowered = act_text.lower()
    act_type = None
    for stem, eli_type in (
        ("ustaw", "Ustawa"),
        ("rozporządz", "Rozporządzenie"),
        ("obwieszcz", "Obwieszczenie"),
        ("uchwał", "Uchwała"),
        ("zarządz", "Zarządzenie"),
    ):
        if lowered.startswith(stem):
            act_type = eli_type
            break
    year_match = ACT_DATE_RE.search(act_text)
    year = int(year_match.group(1)) if year_match else None
    fragment = act_text[year_match.end() :] if year_match else act_text
    if act_type and not year_match:
        fragment = re.sub(r"^\w+\s*", "", fragment)
    fragment = re.sub(r"\s+", " ", fragment).strip(" ,.-–")
    return act_type, (fragment or None), year
