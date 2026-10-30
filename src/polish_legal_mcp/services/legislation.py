"""Bills pending in the Sejm (API Sejmu): which drafts may amend a given act.

A bill names the acts it amends only in its title, so matching is by the act's name in the
title ("o zmianie ustawy – Kodeks cywilny", "…o zmianie Kodeksu cywilnego"). Drafts amending
"niektórych innych ustaw" without naming the act are missed; results are candidates.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from polish_legal_mcp.clients.sejm import SejmClient
from polish_legal_mcp.errors import InvalidInputError, LegalMcpError
from polish_legal_mcp.models import LegislativeProcess, PendingBillsResult
from polish_legal_mcp.services.legal import gather_limited

_DASHES = str.maketrans({"–": "-", "—": "-", "‒": "-", "\xa0": " "})
_NAME_RE = re.compile(r"^\s*\S+\s+z\s+dnia\s+\d{1,2}\s+\S+\s+\d{4}\s*r\.\s*-?\s*(.+)$", re.I)
_GENITIVE_RE = re.compile(r"\b(\w+?)(ny|czy|wy|ry|ski|cki)\b")
MAX_DETAILS = 10


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_DASHES)).strip().lower()


def act_name_phrases(act_title: str) -> list[str]:
    """Phrases naming an act inside bill titles: 'Ustawa z dnia … r. - Kodeks cywilny' →
    ['kodeks cywilny', 'kodeksu cywilnego']; '… r. o gospodarce nieruchomościami' →
    ['o gospodarce nieruchomościami']."""
    match = _NAME_RE.match(_normalize(act_title))
    if not match:
        return []
    name = match.group(1).strip(" .")
    phrases = [name]
    if name.startswith("kodeks "):
        # ponytail: genitive of code names by adjective endings (cywilny → cywilnego); enough
        # for the codes in force, a morphological dictionary would cover any name.
        rest = _GENITIVE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)[:-1]}ego", name[7:])
        phrases.append(f"kodeksu {rest}")
    return phrases


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _current_stage(detail: dict[str, Any]) -> str | None:
    stages = detail.get("stages") or []
    if not stages:
        return None
    last = stages[-1]
    parts = [str(last.get("stageName") or "")]
    if last.get("date"):
        parts.append(f"({last['date']})")
    children = last.get("children") or []
    if children and children[-1].get("stageName"):
        child = children[-1]
        parts.append(
            f"– {child['stageName']}" + (f" ({child['date']})" if child.get("date") else "")
        )
    if last.get("decision"):
        parts.append(f": {last['decision']}")
    return " ".join(p for p in parts if p).replace(" :", ":")


class LegislationService:
    def __init__(self, sejm: SejmClient) -> None:
        self._sejm = sejm

    async def pending_bills(
        self,
        act_eli: str | None = None,
        act_title: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> PendingBillsResult:
        phrases = act_name_phrases(act_title) if act_title else []
        if act_title and not phrases:
            raise InvalidInputError(
                "Nie udało się ustalić nazwy aktu z jego tytułu – użyj parametru query."
            )
        needle = _normalize(query) if query else None
        if not phrases and not needle:
            raise InvalidInputError("Podaj akt (act_id) albo frazę z tytułu projektu (query).")
        term = await self._sejm.current_term()
        processes = await self._sejm.processes(term)
        pending = [p for p in processes if not p.get("passed") and not p.get("closureDate")]
        matches = []
        for process in pending:
            title = _normalize(str(process.get("title") or ""))
            if phrases and not any(phrase in title for phrase in phrases):
                continue
            if needle and needle not in title:
                continue
            matches.append(process)
        matches.sort(key=lambda p: str(p.get("processStartDate") or ""), reverse=True)

        async def describe(process: dict[str, Any]) -> LegislativeProcess:
            number = str(process["number"])
            item = LegislativeProcess(
                number=number,
                title=str(process.get("title") or ""),
                document_type=process.get("documentType"),
                start_date=_date(process.get("processStartDate")),
                sejm_url=f"https://www.sejm.gov.pl/Sejm{term}.nsf/PrzebiegProc.xsp?nr={number}",
                api_url=f"https://api.sejm.gov.pl/sejm/term{term}/processes/{number}",
            )
            try:
                detail = await self._sejm.process(term, number)
            except LegalMcpError:
                return item
            item.current_stage = _current_stage(detail)
            description = detail.get("description")
            item.description = str(description)[:600] if description else None
            item.government_draft_url = detail.get("rclLink")
            return item

        shown = matches[: min(limit, MAX_DETAILS)]
        items = await gather_limited([lambda p=p: describe(p) for p in shown], limit=4)
        warnings = [
            "Dopasowanie po nazwie aktu w tytule projektu: projekty zmieniające „niektóre inne "
            "ustawy” bez wymienienia tego aktu nie zostaną znalezione. To projekty w toku – "
            "nie są prawem obowiązującym; ich treść może się zmienić lub projekt może upaść."
        ]
        if len(matches) > len(shown):
            warnings.append(f"Pokazano {len(shown)} z {len(matches)} pasujących projektów.")
        if not matches:
            warnings.append(
                "API Sejmu nie wykazuje projektów w toku w bieżącej kadencji z tą nazwą w "
                "tytule. Nie dowodzi to braku prac (np. projekty rządowe przed wniesieniem do "
                "Sejmu są w RCL)."
            )
        return PendingBillsResult(
            term=term,
            act_eli=act_eli,
            act_title=act_title,
            matched_phrases=phrases,
            query=query,
            pending_scanned=len(pending),
            items=items,
            warnings=warnings,
        )
