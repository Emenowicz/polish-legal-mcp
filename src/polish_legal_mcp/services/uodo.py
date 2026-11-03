"""Decisions of the President of UODO (data protection authority) from the official Portal
Orzeczeń UODO API.

The portal's search matches substrings of the decision text without inflection, so the tool
asks for word stems ('kamer' finds 'kamera', 'kamerami'); every word must occur. Portal texts
are informational (manual of the portal: art. 57 ust. 1 lit. b RODO), and reuse requires naming
the source, the time of retrieval and whether the data were processed (source_note).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from polish_legal_mcp.clients.uodo import UODO_API, UODO_DOCUMENT_PAGE, UodoClient
from polish_legal_mcp.errors import InvalidInputError, LegalMcpError, UpstreamNotFoundError
from polish_legal_mcp.models import (
    UodoDecision,
    UodoDecisionSummary,
    UodoReference,
    UodoSearchResult,
    UodoStatusEvent,
)
from polish_legal_mcp.services.judgments import html_to_text, phrase_excerpts
from polish_legal_mcp.services.legal import polish_date

MAX_WORDS = 5
_WORD_RE = re.compile(r"[^\W_]{3,40}(?:-[^\W_]{1,40})*")
# All 586 signatures in the portal (2026-11-03), e.g. ZWAD.405.31.331.2019, ZSOŚS.440.1.2019.II.
_SIGNATURE_RE = re.compile(
    r"^[A-ZĄĆĘŁŃÓŚŹŻ]{2,6}\.\d{3,4}(?:\.\d{1,5}){1,2}\.\d{4}(?:\.[IVX]{1,4})?$"
)
_DURP_RE = re.compile(r"^urn:ndoc:pro:pl:durp:(\d{4}):(\d{1,5})$")
EVENTS = {
    "announcement": "wydanie decyzji",
    "publication": "publikacja w portalu",
    "validation": "uprawomocnienie",
    "repealed": "uchylenie",
}
STATUSES = {"nonfinal": "nieprawomocna", "final": "prawomocna", "repealed": "uchylona"}
# Prefixes seen in the decisions' references (sample of 15 decisions, 2026-11-03).
REFERENCE_KINDS = (
    ("urn:ndoc:pro:pl:durp:", "akt polski (Dz.U.)"),
    ("urn:ndoc:pro:eu:", "akt UE"),
    ("urn:ndoc:court:pl:sa:", "sąd administracyjny"),
    ("urn:ndoc:court:pl:sp:", "sąd powszechny"),
    ("urn:ndoc:court:pl:tk:", "Trybunał Konstytucyjny"),
    ("urn:ndoc:court:eu:tsue:", "TSUE"),
    ("urn:ndoc:gov:eu:edpb:", "EROD"),
    ("urn:ndoc:gov:pl:uodo:", "decyzja UODO"),
)
MAX_REFERENCES = 60
MAX_KEYWORDS = 20
DEFAULT_TEXT_LIMIT = 8_000
NOT_LAW = (
    "Treści w Portalu Orzeczeń UODO mają charakter informacyjny. Decyzja Prezesa UODO dotyczy "
    "konkretnej sprawy i nie jest przepisem prawa; przed powołaniem sprawdź jej status."
)


def query_words(query: str) -> list[str]:
    words = query.split()
    if not words or len(words) > MAX_WORDS or not all(_WORD_RE.fullmatch(w) for w in words):
        raise InvalidInputError(
            f"Podaj od 1 do {MAX_WORDS} słów (litery i cyfry, co najmniej 3 znaki każde), np. "
            "'monitoring kamer'. Portal szuka fragmentów słów bez odmiany."
        )
    return [w.lower() for w in words]


def normalize_signature(signature: str) -> str:
    value = re.sub(r"\s+", "", signature).upper()
    if not _SIGNATURE_RE.match(value):
        raise InvalidInputError(
            "Sygnatura decyzji UODO ma postać np. 'DKE.561.5.2026' albo 'ZSOŚS.440.25.2019'."
        )
    return value


def signature_condition(signature: str) -> str:
    # Substring, because one record may carry two signatures ("ZSPR.421.2.2019 ZSPR.405.67.2019");
    # has_signature then keeps exact matches only.
    return f"refname:glob:*{signature}*"


def has_signature(item: dict[str, Any], signature: str) -> bool:
    return signature in str(item.get("refname") or "").upper().split()


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _dated(meta: dict[str, Any], use: str) -> date | None:
    return next(
        (_date(d.get("date")) for d in meta.get("dates") or [] if d.get("use") == use), None
    )


def _events(meta: dict[str, Any], names: dict[str, str]) -> list[UodoStatusEvent]:
    events = []
    for item in meta.get("dates") or []:
        when = _date(item.get("date"))
        if when is None:
            continue
        status = item.get("status")
        refid = item.get("refid")
        events.append(
            UodoStatusEvent(
                date=when,
                event=EVENTS.get(str(item.get("use")), str(item.get("use"))),
                status=STATUSES.get(str(status), str(status)) if status else None,
                court_case=names.get(str(refid), str(refid)) if refid else None,
            )
        )
    return sorted(events, key=lambda e: e.date)  # stable: same-day events keep portal order


def _citation(name: str, decided: date | None, signature: str) -> str | None:
    if not (name.startswith("Decyzja Prezesa UODO") and decided and signature):
        return None
    return (
        f"decyzja Prezesa Urzędu Ochrony Danych Osobowych z dnia {polish_date(decided)} r., "
        f"{', '.join(signature.split())}"  # a record may carry two signatures
    )


def _summary(meta: dict[str, Any], names: dict[str, str] | None = None) -> UodoDecisionSummary:
    signature = str(meta.get("refname") or "")
    name = str((meta.get("name") or {}).get("pl") or "")
    decided = _dated(meta, "announcement")
    events = _events(meta, names or {})
    last = events[-1] if events else None
    return UodoDecisionSummary(
        signature=signature,
        refid=str(meta["refid"]),
        name=name,
        subject=(meta.get("title") or {}).get("pl") or None,
        decision_date=decided,
        publication_date=_dated(meta, "publication"),
        status=last.status if last else None,
        status_date=last.date if last else None,
        citation=_citation(name, decided, signature),
        source_url=f"{UODO_DOCUMENT_PAGE}{meta['refid']}/content",
    )


def _references(meta: dict[str, Any]) -> list[UodoReference]:
    seen: set[str] = set()
    references = []
    for ref in meta.get("refs") or []:
        refid, name = str(ref.get("refid") or ""), str(ref.get("name") or "")
        if not refid or not name or refid in seen:
            continue
        seen.add(refid)
        kind = next((k for prefix, k in REFERENCE_KINDS if refid.startswith(prefix)), "inne")
        durp = _DURP_RE.match(refid)
        references.append(
            UodoReference(
                kind=kind,
                name=name,
                eli_candidate=f"DU/{durp.group(1)}/{int(durp.group(2))}" if durp else None,
            )
        )
    return references[:MAX_REFERENCES]


def _status_warnings(summary: UodoDecisionSummary, events: list[UodoStatusEvent]) -> list[str]:
    if summary.status == "uchylona":
        repeal = next((e for e in reversed(events) if e.status == "uchylona"), None)
        by = f", {repeal.court_case}" if repeal and repeal.court_case else ""
        return [
            f"Decyzja uchylona wg portalu UODO ({summary.status_date}{by}). Nie przedstawiaj jej "
            "jako aktualnego stanowiska bez sprawdzenia orzeczenia sądu."
        ]
    if summary.status == "nieprawomocna":
        return [
            "Decyzja nieprawomocna wg portalu UODO: może zostać zaskarżona do sądu "
            "administracyjnego i uchylona."
        ]
    return []


class UodoService:
    def __init__(self, uodo: UodoClient) -> None:
        self._uodo = uodo

    async def search(
        self,
        query: str | None = None,
        signature: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 10,
        page: int = 0,
    ) -> UodoSearchResult:
        words = query_words(query) if query else []
        conditions = [f"content_pl:glob:*{w}*" for w in words]
        wanted = normalize_signature(signature) if signature else None
        if wanted:
            conditions.append(signature_condition(wanted))
        if not conditions:
            raise InvalidInputError("Podaj słowa z treści decyzji (query) albo sygnaturę.")
        if date_from:
            conditions.append(f"date_announcement:ge:{date_from.isoformat()}")
        if date_to:
            conditions.append(f"date_announcement:le:{date_to.isoformat()}")
        found = await self._uodo.search_decisions(conditions, page * limit, limit + 1)
        items = [
            _summary(i)
            for i in found[:limit]
            if i.get("refid") and (not wanted or has_signature(i, wanted))
        ]
        warnings = []
        if words:
            warnings.append(
                "Portal szuka fragmentów słów bez odmiany, a wszystkie słowa muszą wystąpić w "
                "treści: używaj rdzeni ('kamer' znajdzie 'kamery', 'kamerami')."
            )
        if not items:
            warnings.append(
                "Brak wyników w Portalu Orzeczeń UODO nie dowodzi, że takiej decyzji nie ma. "
                "Spróbuj krótszych rdzeni słów albo mniejszej liczby słów."
            )
        return UodoSearchResult(
            query_words=words,
            signature=wanted,
            page=page,
            page_size=limit,
            has_more=len(found) > limit,
            items=items,
            warnings=warnings,
            source_note="Źródło: Portal Orzeczeń UODO (orzeczenia.uodo.gov.pl); wyniki od "
            "najnowszych decyzji.",
        )

    async def _refid(self, decision: str) -> str:
        value = decision.strip()
        if value.startswith("urn:"):
            return value  # checked by the client before any request
        signature = normalize_signature(value)
        found = await self._uodo.search_decisions([signature_condition(signature)], 0, 5)
        found = [i for i in found if i.get("refid") and has_signature(i, signature)]
        if not found:
            raise UpstreamNotFoundError(
                f"Portal Orzeczeń UODO nie zawiera decyzji o sygnaturze {signature}. Nie dowodzi "
                "to, że decyzja nie istnieje (może nie być opublikowana w portalu)."
            )
        return str(found[0]["refid"])

    async def get(
        self,
        decision: str,
        text_offset: int = 0,
        text_limit: int = DEFAULT_TEXT_LIMIT,
        search_text: str | None = None,
    ) -> UodoDecision:
        refid = await self._refid(decision)
        meta, body, fetched = await self._uodo.decision(refid)
        court_refids = [str(d["refid"]) for d in meta.get("dates") or [] if d.get("refid")]
        try:
            names = await self._uodo.names(court_refids)
        except LegalMcpError:
            names = {}  # the status history then shows the portal identifiers
        summary = _summary(meta, names)
        events = _events(meta, names)
        full_text = html_to_text(body)
        entities = meta.get("entities") or []
        department = next(
            (e["name"]["pl"] for e in entities if e.get("function") == "department"), None
        )
        creator = next((e for e in entities if e.get("function") == "creator"), None)
        signed_by = (
            " ".join(
                p for p in ((creator.get("title") or {}).get("pl"), creator["name"]["pl"]) if p
            )
            if creator and (creator.get("name") or {}).get("pl")
            else None
        )
        warnings = [*_status_warnings(summary, events), NOT_LAW]
        if not full_text:
            warnings.append("Portal UODO nie udostępnia treści tej decyzji w HTML.")
        parts = meta.get("parts")
        if isinstance(parts, int) and parts > 1:
            warnings.append(f"Decyzja ma w portalu {parts} części; pokazano pierwszą.")
        published = summary.publication_date.isoformat() if summary.publication_date else "?"
        return UodoDecision(
            **summary.model_dump(),
            status_history=events,
            department=department,
            signed_by=signed_by,
            keywords=[
                str(t["name"]["pl"])
                for t in meta.get("terms") or []
                if (t.get("name") or {}).get("pl")
            ][:MAX_KEYWORDS],
            references=_references(meta),
            text=full_text[text_offset : text_offset + text_limit],
            text_offset=text_offset,
            text_length_total=len(full_text),
            has_more=text_offset + text_limit < len(full_text),
            excerpts=phrase_excerpts(full_text, search_text),
            api_url=f"{UODO_API}/documents/public/items/{refid}/meta.json",
            retrieved_at=fetched,
            source_note=(
                f"Źródło: Portal Orzeczeń UODO (orzeczenia.uodo.gov.pl), decyzja opublikowana "
                f"w portalu {published}, dane pobrane {fetched:%Y-%m-%d %H:%M} UTC. Dane "
                "przetworzone: treść HTML zamieniona na tekst i podzielona na fragmenty, "
                "metadane przeformatowane."
            ),
            warnings=warnings,
        )
