"""CJEU case law (Court of Justice and General Court) from the EU Publications Office.

Documents and metadata come from CELLAR, the official source behind EUR-Lex. The Polish version
of a judgment is a translation: only the language of the case is authentic, and every result
says which one it is. Links between judgments and the acts they interpret are CELLAR metadata;
they can be incomplete, so an empty list is never presented as "no case law".
"""

from __future__ import annotations

import re
from datetime import date

from polish_legal_mcp.clients.eurlex import (
    CASE_LAW_RE,
    CASE_NUMBER_RE,
    CELLAR,
    EURLEX_PAGE,
    EurLexClient,
)
from polish_legal_mcp.errors import InvalidInputError, UpstreamNotFoundError
from polish_legal_mcp.models import (
    CjeuInterpretedAct,
    CjeuJudgmentResult,
    CjeuJudgmentSummary,
    CjeuParagraph,
    CjeuSearchResult,
)
from polish_legal_mcp.parsers.cjeu_html import parse_cjeu
from polish_legal_mcp.parsers.legal_html import normalize_name
from polish_legal_mcp.services.eu_law import resolve_eu_act

MAX_PARAGRAPHS = 10
MAX_MATCHES = 10
MATCH_RADIUS = 300
MAX_OPERATIVE_CHARS = 8_000
MONTHS = [
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
]
DOCUMENT_TYPES = {"J": "wyrok", "O": "postanowienie", "C": "opinia rzecznika generalnego"}
TRANSLATION_NOTE = (
    "Wersja polska orzeczenia TSUE jest tłumaczeniem; autentyczna jest wersja w języku "
    "postępowania. Wyrok w trybie prejudycjalnym wiąże co do wykładni prawa UE, ale rozstrzyga "
    "spór sąd krajowy."
)
METADATA_NOTE = (
    "Powiązania orzeczeń z aktami i artykułami pochodzą z metadanych CELLAR (EUR-Lex) i bywają "
    "niepełne, zwłaszcza dla najnowszych orzeczeń – brak wyniku nie dowodzi braku orzecznictwa."
)
_DASHES = str.maketrans({"‑": "-", "‐": "-", "‒": "-", "–": "-"})
_PARAGRAPHS_RE = re.compile(r"^\s*\d+\s*(?:-\s*\d+\s*)?(?:,\s*\d+\s*(?:-\s*\d+\s*)?)*$")
_LOCATION_RE = re.compile(r"^A0*(\d+)(?:P0*(\d+))?(?:PT0*(\d+))?(?:L([A-Z]+))?$")


def describe_location(code: str) -> str:
    """CELLAR location code → Polish designation: 'A17P1LD' → 'art. 17 ust. 1 lit. d'."""
    match = _LOCATION_RE.match(code)
    if not match:
        return code
    article, paragraph, point, letter = match.groups()
    parts = [f"art. {article}"]
    if paragraph:
        parts.append(f"ust. {paragraph}")
    if point:
        parts.append(f"pkt {point}")
    if letter:
        parts.append(f"lit. {letter.lower()}")
    return " ".join(parts)


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _court(celex: str, when: date | None) -> str:
    if celex[5] == "C":
        return "Trybunał Sprawiedliwości"
    # The Court of First Instance became the General Court with the Treaty of Lisbon.
    return "Sąd Pierwszej Instancji" if when and when < date(2009, 12, 1) else "Sąd"


def _title_parts(title: str | None) -> tuple[str | None, str | None, str | None]:
    """(heading, parties, subject) from CELLAR's '#'-separated Polish title."""
    parts = [p.strip() for p in (title or "").split("#") if p.strip()]
    heading = parts[0] if parts else None
    parties = parts[1].rstrip(".") if len(parts) > 1 else None
    subject = next((p for p in parts[2:] if " – " in p or " — " in p), None)
    return heading, parties, subject


def _summary(celex: str, row: dict[str, object], locations: list[str]) -> CjeuJudgmentSummary:
    when = _date(str(row.get("date") or "") or None)
    heading, parties, _ = _title_parts(str(row.get("title") or "") or None)
    case = re.sub(r"^Spraw[ay] (połączone )?", "", str(row.get("case") or "")).rstrip(".") or None
    return CjeuJudgmentSummary(
        celex=celex,
        document_type=DOCUMENT_TYPES.get(celex[6], "dokument"),
        court=_court(celex, when),
        case_number=case,
        decision_date=when,
        ecli=str(row.get("ecli") or "") or None,
        title=heading,
        parties=parties,
        provisions=[describe_location(code) for code in locations],
        eurlex_url=f"{EURLEX_PAGE}{celex}",
    )


def format_cjeu_citation(summary: CjeuJudgmentSummary) -> str | None:
    """'wyrok Trybunału Sprawiedliwości z dnia 16 lipca 2020 r., <strony>, C-311/18,
    EU:C:2020:559' — only when every element comes from official metadata."""
    s = summary
    if s.document_type not in ("wyrok", "postanowienie"):
        return None  # an AG opinion is cited with the Advocate General's name, not in metadata
    if not (s.decision_date and s.case_number and s.parties and s.ecli):
        return None
    court = {
        "Trybunał Sprawiedliwości": "Trybunału Sprawiedliwości",
        "Sąd": "Sądu",
        "Sąd Pierwszej Instancji": "Sądu Pierwszej Instancji",
    }[s.court]
    d = s.decision_date
    return (
        f"{s.document_type} {court} z dnia {d.day} {MONTHS[d.month - 1]} {d.year} r., "
        f"{s.parties}, {s.case_number}, {s.ecli.removeprefix('ECLI:')}"
    )


def _wanted_paragraphs(spec: str) -> list[int]:
    if not _PARAGRAPHS_RE.match(spec):
        raise InvalidInputError("Punkty podaj jako numer, zakres lub listę, np. '134', '134-136'.")
    numbers: list[int] = []
    for part in spec.split(","):
        first, _, last = part.partition("-")
        lo, hi = int(first), int(last or first)
        numbers.extend(range(min(lo, hi), max(lo, hi) + 1))
        if len(numbers) > MAX_PARAGRAPHS:
            raise InvalidInputError(f"Jednorazowo można pobrać do {MAX_PARAGRAPHS} punktów.")
    return numbers


class CjeuService:
    def __init__(self, eurlex: EurLexClient) -> None:
        self._eurlex = eurlex

    async def _case_documents(self, reference: str) -> list[str]:
        value = reference.strip().translate(_DASHES)
        value = re.sub(r"(?i)^(sprawa|sprawy połączone|wyrok)\s+", "", value)
        if CASE_LAW_RE.match(value.upper()):
            return [value.upper()]
        if value.upper().startswith("ECLI:"):
            documents = await self._eurlex.case_law_celex(ecli=value.upper())
        else:
            documents = await self._eurlex.case_law_celex(case_number=value)
            match = CASE_NUMBER_RE.match(value)
            if not documents and match:
                # Joined cases are filed under the first case number only.
                court, number, year = match.groups()
                yy = int(year)
                full_year = 1900 + yy if yy >= 54 else 2000 + yy
                guess = f"6{full_year}{(court or 'C').upper()}J{int(number):04d}"
                if await self._eurlex.case_law_metadata(guess):
                    documents = [guess]
        if not documents:
            raise UpstreamNotFoundError(
                f"Nie znaleziono w EUR-Lex sprawy {reference!r}. W sprawach połączonych podaj "
                "pierwszą sygnaturę albo ECLI. Nie dowodzi to, że orzeczenie nie istnieje."
            )
        return documents

    async def get_judgment(
        self,
        reference: str,
        paragraphs: str | None = None,
        search_text: str | None = None,
        include_operative_part: bool = True,
    ) -> CjeuJudgmentResult:
        documents = await self._case_documents(reference)
        # Prefer the judgment, then an order, then the Advocate General's opinion.
        order = {"J": 0, "O": 1, "C": 2}
        celex = sorted(documents, key=lambda c: order.get(c[6], 3))[0]
        meta = await self._eurlex.case_law_metadata(celex)
        if meta is None:
            raise UpstreamNotFoundError(f"EUR-Lex nie zna dokumentu {celex}.")
        warnings = [TRANSLATION_NOTE]
        try:
            document = parse_cjeu(await self._eurlex.document(celex))
        except UpstreamNotFoundError:
            document = None
            warnings.append(
                "Brak polskiej wersji dokumentu w EUR-Lex (częste dla starszych orzeczeń i "
                "orzeczeń Sądu) – zwracam tylko metadane."
            )
        summary = _summary(celex, meta, [])
        result = CjeuJudgmentResult(
            **summary.model_dump(),
            source_url=f"{CELLAR}/resource/celex/{celex}",
            paragraph_count=len(document.paragraphs) if document else 0,
            language_of_case=document.language_of_case if document else None,
            subject=_title_parts(meta.get("title"))[2],
            interpreted_acts=[
                CjeuInterpretedAct(
                    celex=act,
                    provisions=[describe_location(code) for code in locations],
                    eurlex_url=f"{EURLEX_PAGE}{act}",
                )
                for act, locations in meta.get("interpreted") or []
            ],
            other_documents=[d for d in documents if d != celex],
            citation=format_cjeu_citation(summary),
            warnings=warnings,
        )
        if document is None:
            return result
        if not document.supported and (paragraphs or search_text):
            result.warnings.append(
                "Ten dokument ma starszy układ bez numerowanych punktów – wyodrębnienie punktów "
                "nie jest obsługiwane; sprawdź tekst w EUR-Lex."
            )
        if paragraphs:
            wanted = _wanted_paragraphs(paragraphs)
            result.paragraphs = [
                CjeuParagraph(number=n, text=document.paragraphs[n])
                for n in wanted
                if n in document.paragraphs
            ]
            if result.citation and result.paragraphs:
                result.citation += f", pkt {paragraphs.replace(' ', '').replace('-', '–')}"
            missing = [n for n in wanted if n not in document.paragraphs]
            if missing and document.supported:
                result.warnings.append(
                    f"Brak punktów {', '.join(map(str, missing))} – dokument ma punkty 1–"
                    f"{max(document.paragraphs)}."
                )
        if search_text:
            needle = search_text.strip().lower()
            for number, text in document.paragraphs.items():
                start = text.lower().find(needle)
                if start == -1:
                    continue
                lo, hi = max(0, start - MATCH_RADIUS), start + len(needle) + MATCH_RADIUS
                snippet = ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")
                result.matches.append(CjeuParagraph(number=number, text=snippet))
                if len(result.matches) == MAX_MATCHES:
                    result.warnings.append(f"Pokazano pierwsze {MAX_MATCHES} trafień.")
                    break
        if include_operative_part and document.operative_part:
            result.operative_part = document.operative_part[:MAX_OPERATIVE_CHARS]
        return result

    async def search_for_act(
        self, act: str, article: str | None = None, limit: int = 10
    ) -> CjeuSearchResult:
        celex = resolve_eu_act(act)
        if celex is None:
            raise InvalidInputError(
                f"Nie rozpoznano aktu UE: {act!r}. Podaj CELEX (np. 32016R0679), numer aktu "
                "(np. rozporządzenie (UE) 2016/679, dyrektywa 2014/24/UE) albo skrót RODO."
            )
        location = None
        if article:
            number = normalize_name(article)
            if not number.isdigit():
                raise InvalidInputError("Filtr artykułu przyjmuje sam numer, np. '17'.")
            location = f"A{int(number):02d}"
        rows = await self._eurlex.case_law_for_act(celex, location, limit)
        items = [_summary(row["celex"], row, row["locs"]) for row in rows]
        warnings = [METADATA_NOTE, TRANSLATION_NOTE]
        if article:
            warnings.append(
                "Filtr artykułu korzysta z adnotacji CELLAR, których często brak dla nowszych "
                "orzeczeń – sprawdź też wyszukiwanie bez filtra."
            )
        return CjeuSearchResult(act_celex=celex, article=article, items=items, warnings=warnings)
