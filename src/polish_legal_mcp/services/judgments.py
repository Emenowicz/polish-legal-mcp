"""Case-law services on top of SAOS."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import lxml.html
from lxml.etree import ParserError

from polish_legal_mcp.clients.saos import SaosClient
from polish_legal_mcp.models import (
    SAOS_PAGE,
    JudgmentDetails,
    JudgmentSearchResult,
    JudgmentSummary,
    ReferencedRegulation,
)

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
