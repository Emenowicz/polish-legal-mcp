"""Case-law services on top of SAOS."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import lxml.html
from lxml.etree import ParserError

from polish_legal_mcp.clients.saos import SaosClient
from polish_legal_mcp.errors import InvalidInputError, LegalMcpError
from polish_legal_mcp.models import (
    SAOS_PAGE,
    ActId,
    JudgmentDetails,
    JudgmentSearchResult,
    JudgmentSummary,
    ProvisionJudgment,
    ProvisionJudgmentsResult,
    ReferencedRegulation,
)
from polish_legal_mcp.parsers.legal_html import display_name, normalize_name
from polish_legal_mcp.services.legal import gather_limited

DEFAULT_TEXT_LIMIT = 8_000
MAX_EXCERPTS = 10
EXCERPT_RADIUS = 300
COURT_NAMES = {
    "SUPREME": "Sąd Najwyższy",
    "CONSTITUTIONAL_TRIBUNAL": "Trybunał Konstytucyjny",
    "NATIONAL_APPEAL_CHAMBER": "Krajowa Izba Odwoławcza",
}
COVERAGE_NOTE = (
    "SAOS nie zawiera wszystkich orzeczeń w Polsce. W praktyce brak w nim orzeczeń sądów "
    "administracyjnych (WSA/NSA) – szukaj ich w CBOSA (orzeczenia.nsa.gov.pl). Orzeczenie "
    "nie jest przepisem prawa i co do zasady wiąże tylko w danej sprawie."
)
# Upper bound of judgments fetched per call to confirm the provision in their metadata (SAOS
# search results do not carry referencedRegulations, so each candidate costs one request).
MAX_PROVISION_CANDIDATES = 20
CITED_NOT_INTERPRETED = (
    "Powołanie przepisu w metadanych SAOS oznacza, że sąd wskazał go w orzeczeniu – nie musi "
    "oznaczać jego wykładni. Przed cytowaniem sprawdź treść: get_judgment z search_text, "
    "np. 'art. 5'."
)
# One entry of SAOS's "… (Dz. U. z 1964 r. Nr 16, poz. 93 - art. 5; art. 98 § 1(1); art. 75 c)".
_SAOS_UNIT_RE = re.compile(
    r"art\.\s*(\d+(?:\s?[a-z](?![a-z]))?(?:\(\d+\))?)"
    r"(?:\s*(?:§|ust\.?)\s*(\d+[a-z]?(?:\(\d+\))?))?",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def html_to_text(html: str) -> str:
    """Strip SAOS HTML (search highlights, anonymisation spans) into plain paragraphs."""
    if not html.strip():
        return ""
    try:
        doc = lxml.html.fragment_fromstring(html, create_parent="div")  # pyright: ignore[reportArgumentType]
    except (ParserError, ValueError):
        return _SPACE_RE.sub(" ", re.sub(r"<[^>]+>", " ", html)).strip()
    for block in doc.iter("p", "div", "h1", "h2", "h3", "h4", "li", "tr", "br"):
        block.tail = "\n" + (block.tail or "")
    text = doc.text_content().replace("\xa0", " ")
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _court(item: dict[str, Any]) -> tuple[str | None, str | None]:
    court_type = item.get("courtType")
    division = item.get("division") or {}
    court = division.get("court") or {}
    name = court.get("name") or COURT_NAMES.get(str(court_type))
    if court_type == "SUPREME" and not division:
        chambers = item.get("chambers") or []
        if chambers:
            return name, ", ".join(str(c.get("name")) for c in chambers if c.get("name")) or None
    return name, division.get("name")


def _summary(item: dict[str, Any]) -> JudgmentSummary:
    judgment_id = int(item["id"])
    court_name, division = _court(item)
    excerpt = html_to_text(str(item.get("textContent") or "")) or None
    return JudgmentSummary(
        judgment_id=judgment_id,
        case_numbers=[c["caseNumber"] for c in item.get("courtCases") or [] if c.get("caseNumber")],
        court_type=item.get("courtType"),
        court_name=court_name,
        division=division,
        judgment_date=_date(item.get("judgmentDate")),
        judgment_type=item.get("judgmentType"),
        excerpt=excerpt[:1000] if excerpt else None,
        keywords=[str(k) for k in item.get("keywords") or []],
        source_url=f"{SAOS_PAGE}{judgment_id}",
        api_url=f"https://www.saos.org.pl/api/judgments/{judgment_id}",
    )


def cited_units(regulation_text: str) -> list[tuple[str, str | None, str]]:
    """(article, § or ust., original entry) cited from one act, canonical numbers ('22_1')."""
    _, separator, tail = regulation_text.rpartition(" - ")
    units: list[tuple[str, str | None, str]] = []
    for entry in tail.rstrip(") ").split(";") if separator else []:
        match = _SAOS_UNIT_RE.match(entry.strip())
        if match:
            paragraph = normalize_name(match.group(2)) if match.group(2) else None
            units.append((normalize_name(match.group(1)), paragraph, entry.strip()))
    return units


def _provision_label(article: str, paragraph: str | None) -> str:
    label = f"art. {display_name(article)}"
    if paragraph:
        kind = (
            "§" if paragraph.strip().startswith("§") else "ust." if "ust" in paragraph else "§/ust."
        )
        label += f" {kind} {display_name(paragraph)}"
    return label


class JudgmentService:
    def __init__(self, saos: SaosClient) -> None:
        self._saos = saos

    async def search(
        self,
        query: str,
        court_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 10,
        page: int = 0,
        referenced_regulation: str | None = None,
        legal_base: str | None = None,
    ) -> JudgmentSearchResult:
        params: dict[str, str | int] = {
            "all": query.strip(),
            "pageSize": limit,
            "pageNumber": page,
            "sortingField": "JUDGMENT_DATE",
            "sortingDirection": "DESC",
        }
        if court_type:
            params["courtType"] = court_type
        if date_from:
            params["judgmentDateFrom"] = date_from.isoformat()
        if date_to:
            params["judgmentDateTo"] = date_to.isoformat()
        if referenced_regulation:
            params["referencedRegulation"] = referenced_regulation.strip()
        if legal_base:
            params["legalBase"] = legal_base.strip()
        data = await self._saos.search(params)
        total = int((data.get("info") or {}).get("totalResults") or 0)
        links = data.get("links") or []
        return JudgmentSearchResult(
            total_results=total,
            page=page,
            page_size=limit,
            has_more=any(link.get("rel") == "next" for link in links),
            items=[_summary(i) for i in data["items"] if i.get("id")],
            coverage_note=COVERAGE_NOTE,
        )

    async def for_provision(
        self,
        act: ActId,
        act_title: str | None,
        article: str,
        paragraph: str | None = None,
        court_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        candidates_per_page: int = 10,
        page: int = 0,
    ) -> ProvisionJudgmentsResult:
        """Judgments whose SAOS metadata list the provision among the cited ones.

        SAOS indexes cited acts by their original Dz.U. entry (lawJournalEntryCode). The search
        narrows by act + "art. N" phrase; each candidate's metadata then confirm the exact unit.
        """
        if act.publisher != "DU":
            raise InvalidInputError(
                "SAOS wiąże orzeczenia tylko z aktami z Dziennika Ustaw (nie z Monitora Polskiego)."
            )
        wanted = normalize_name(article)
        wanted_paragraph = normalize_name(paragraph) if paragraph else None
        number, _, superscript = wanted.partition("_")
        phrase = f"art. {number}({superscript})" if superscript else f"art. {number}"
        params: dict[str, str | int] = {
            "lawJournalEntryCode": f"{act.year}/{act.position}",
            "referencedRegulation": f'"{phrase}"',
            "pageSize": min(candidates_per_page, MAX_PROVISION_CANDIDATES),
            "pageNumber": page,
            "sortingField": "JUDGMENT_DATE",
            "sortingDirection": "DESC",
        }
        if court_type:
            params["courtType"] = court_type
        if date_from:
            params["judgmentDateFrom"] = date_from.isoformat()
        if date_to:
            params["judgmentDateTo"] = date_to.isoformat()
        data = await self._saos.search(params)
        candidates = [i for i in data["items"] if i.get("id")]

        async def details(item: dict[str, Any]) -> dict[str, Any] | None:
            try:
                return await self._saos.judgment(int(item["id"]))
            except LegalMcpError:
                return None

        fetched = await gather_limited([lambda i=i: details(i) for i in candidates], limit=4)
        confirmed: list[ProvisionJudgment] = []
        unchecked = 0
        for item, full in zip(candidates, fetched, strict=True):
            if full is None:
                unchecked += 1
                continue
            matches = [
                entry
                for reg in full.get("referencedRegulations") or []
                if reg.get("journalYear") == act.year and reg.get("journalEntry") == act.position
                for art, par, entry in cited_units(str(reg.get("text") or ""))
                if art == wanted and (wanted_paragraph is None or par == wanted_paragraph)
            ]
            if matches:
                confirmed.append(
                    ProvisionJudgment(**_summary(item).model_dump(), matched_references=matches)
                )
        total = int((data.get("info") or {}).get("totalResults") or 0)
        provision = _provision_label(article, paragraph)
        warnings = [CITED_NOT_INTERPRETED]
        if not confirmed:
            warnings.append(
                f"SAOS nie wykazuje orzeczeń, w których metadanych powołano {provision} aktu "
                f"Dz.U. {act.year} poz. {act.position} (sprawdzono {len(candidates)} kandydatów "
                f"na stronie {page}). Nie dowodzi to braku orzecznictwa: metadane powołanych "
                "przepisów SAOS dotyczą głównie sądów powszechnych i dla nowszych ustaw bywają "
                "niepełne. Spróbuj search_judgments z frazą."
            )
        if unchecked:
            warnings.append(
                f"Nie udało się pobrać {unchecked} orzeczeń do sprawdzenia (SAOS niedostępny)."
            )
        return ProvisionJudgmentsResult(
            act_eli=act.eli,
            act_title=act_title,
            provision=provision,
            total_candidates=total,
            candidates_checked=len(candidates) - unchecked,
            page=page,
            has_more=any(link.get("rel") == "next" for link in data.get("links") or []),
            items=confirmed,
            warnings=warnings,
            coverage_note=COVERAGE_NOTE,
        )

    async def get(
        self,
        judgment_id: int,
        text_offset: int = 0,
        text_limit: int = DEFAULT_TEXT_LIMIT,
        search_text: str | None = None,
    ) -> JudgmentDetails:
        item = await self._saos.judgment(judgment_id)
        court_name, division = _court(item)
        full_text = html_to_text(str(item.get("textContent") or ""))
        excerpts: list[str] = []
        if search_text:
            needle = search_text.strip().lower()
            lowered = full_text.lower()
            start = lowered.find(needle)
            while start != -1 and len(excerpts) < MAX_EXCERPTS:
                lo, hi = max(0, start - EXCERPT_RADIUS), start + len(needle) + EXCERPT_RADIUS
                excerpts.append(
                    ("…" if lo else "") + full_text[lo:hi] + ("…" if hi < len(full_text) else "")
                )
                start = lowered.find(needle, hi)
        source = item.get("source") or {}
        warnings = [COVERAGE_NOTE]
        if not full_text:
            warnings.append("SAOS nie udostępnia treści tego orzeczenia.")
        regulations = []
        for reg in item.get("referencedRegulations") or []:
            year, entry = reg.get("journalYear"), reg.get("journalEntry")
            regulations.append(
                ReferencedRegulation(
                    text=str(reg.get("text") or reg.get("journalTitle") or ""),
                    journal_year=year,
                    journal_no=reg.get("journalNo") or None,
                    journal_entry=entry,
                    eli_candidate=f"DU/{year}/{entry}" if year and entry else None,
                )
            )
        return JudgmentDetails(
            judgment_id=int(item["id"]),
            case_numbers=[
                c["caseNumber"] for c in item.get("courtCases") or [] if c.get("caseNumber")
            ],
            court_type=item.get("courtType"),
            court_name=court_name,
            division=division,
            judgment_date=_date(item.get("judgmentDate")),
            judgment_type=item.get("judgmentType"),
            judges=[str(j.get("name")) for j in item.get("judges") or [] if j.get("name")],
            decision=item.get("decision"),
            summary=item.get("summary"),
            keywords=[str(k) for k in item.get("keywords") or []],
            legal_bases=[str(b) for b in item.get("legalBases") or []],
            referenced_regulations=regulations,
            text=full_text[text_offset : text_offset + text_limit],
            text_offset=text_offset,
            text_length_total=len(full_text),
            has_more=text_offset + text_limit < len(full_text),
            excerpts=excerpts,
            source_url=f"{SAOS_PAGE}{item['id']}",
            original_source_url=source.get("judgmentUrl"),
            warnings=warnings,
        )
