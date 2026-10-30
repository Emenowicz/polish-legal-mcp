"""Legislation services on top of the ELI API: search, act details, provisions, amendments,
citation verification and lexical legal-basis discovery.

Verification semantics are documented in docs/legal-verification.md. The guiding rule: a
status is VERIFIED only when the specific question was positively answered from official
metadata or text; anything else is UNVERIFIED (or a more specific negative status).
"""

from __future__ import annotations

import asyncio
import difflib
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.errors import (
    InvalidInputError,
    LegalMcpError,
    UpstreamNotFoundError,
    UpstreamUnavailableError,
)
from polish_legal_mcp.models import (
    ELI_API,
    ISAP_DETAILS,
    ActId,
    ActReference,
    ActSummary,
    AmendmentsResult,
    CheckResult,
    Citation,
    CitationVerificationResult,
    LegalActDetails,
    LegalBasisCandidate,
    LegalBasisResult,
    ProvisionResult,
    SearchActsResult,
    SourceDocument,
    TextFormat,
    TocEntry,
    VerificationReport,
)
from polish_legal_mcp.models import VerificationStatus as VS
from polish_legal_mcp.parsers import citations as cit
from polish_legal_mcp.parsers.entry_into_force import StagedEntry, find_staged_entry, parse_dates
from polish_legal_mcp.parsers.legal_html import (
    UnitLookupError,
    act_body_text,
    display_name,
    extract_unit,
    find_unit,
    iter_toc,
    parse_html,
)
from polish_legal_mcp.parsers.pdf_text import find_pdf_unit, repeal_marker, same_wording

if TYPE_CHECKING:
    from polish_legal_mcp.services.eu_law import EuLawService

WARSAW = ZoneInfo("Europe/Warsaw")
SEARCH_WINDOW = 100
MAX_PROVISION_CHARS = 12_000
MAX_CONSOLIDATED_TEXTS_SCANNED = 15
MAX_PENDING_CHECKED = 5
MAX_CITED_ACTS = 6
# Cited in most civil judgments whatever the subject (procedure, court and attorney fees):
# noise for "which act governs this issue". Original Dz.U. entries, as SAOS indexes them.
PROCEDURAL_ACTS = {
    "DU/1964/296",  # Kodeks postępowania cywilnego
    "DU/2005/1398",  # ustawa o kosztach sądowych w sprawach cywilnych
    "DU/2002/1348",  # rozporządzenie w sprawie opłat za czynności adwokackie (2002)
    "DU/2002/1349",  # rozporządzenie w sprawie opłat za czynności radców prawnych (2002)
    "DU/2015/1800",  # rozporządzenie w sprawie opłat za czynności adwokackie (2015)
    "DU/2015/1804",  # rozporządzenie w sprawie opłat za czynności radców prawnych (2015)
    "DU/1997/555",  # Kodeks postępowania karnego
}
AMENDING = "Akty zmieniające"
TK_RULINGS = "Orzeczenie TK"
CONSOLIDATED_TEXTS = "Inf. o tekście jednolitym"
CONSOLIDATED_TEXT_OF = "Tekst jednolity dla aktu"

STATUS_NOTES = {
    "obowiązujący": "Akt oznaczony jako obowiązujący (sprawdź też datę wejścia w życie).",
    "akt posiada tekst jednolity": "Akt obowiązuje; ogłoszono jego tekst jednolity.",
    "akt objęty tekstem jednolitym": "Akt (zwykle zmieniający) został uwzględniony w tekście "
    "jednolitym innego aktu – nie oznacza to uchylenia.",
    "tekst jednolity dla aktu jednorazowego": "Tekst jednolity aktu jednorazowego.",
    "wygaśnięcie aktu": "Dla obwieszczenia o tekście jednolitym zwykle oznacza zastąpienie "
    "nowszym tekstem jednolitym, a nie utratę mocy samej ustawy.",
    "uchylony": "Akt uchylony.",
    "uchylony wykazem": "Akt uchylony wykazem.",
    "uznany za uchylony": "Akt uznany za uchylony.",
    "nieobowiązujący - przyczyna nieustalona": "Akt nieobowiązujący – przyczyna nieustalona.",
    "nieobowiązujący - uchylona podstawa prawna": "Akt nieobowiązujący – uchylono podstawę "
    "prawną jego wydania.",
    "akt indywidualny": "Akt indywidualny.",
    "akt jednorazowy": "Akt jednorazowy (np. zmieniający) – wyczerpał się przez wykonanie.",
    "bez statusu": "ELI nie podaje statusu.",
    "brak mocy prawnej": "Akt bez mocy prawnej.",
    "wydane z naruszeniem prawa": "Akt wydany z naruszeniem prawa.",
}
TEXT_LABELS = {
    "H": ("Tekst HTML (ISAP)", True),
    "O": ("Tekst ogłoszony (PDF)", True),
    "I": ("Tekst ogłoszony (PDF)", True),
    "T": ("Tekst aktu (PDF)", True),
    "U": ("Tekst ujednolicony (PDF) – opracowanie informacyjne, nie tekst urzędowy", False),
    "W": ("Tekst (DOCX)", True),
}
GENITIVE = {
    "Ustawa": "ustawy",
    "Rozporządzenie": "rozporządzenia",
    "Obwieszczenie": "obwieszczenia",
    "Uchwała": "uchwały",
    "Zarządzenie": "zarządzenia",
    "Konstytucja": "Konstytucji",
}
ONE_OFF_STATUSES = {"akt jednorazowy", "akt indywidualny", "tekst jednolity dla aktu jednorazowego"}
AMENDING_TITLE_RE = re.compile(r"\bo zmianie\b|\bzmieniając", re.IGNORECASE)
STOPWORDS = {
    "który", "która", "które", "których", "którym", "oraz", "przez", "jest", "będzie", "była",
    "jakie", "jaki", "jaka", "dotyczy", "dotyczący", "dotycząca", "dotyczące", "dotyczącej",
    "sprawie", "sprawy", "zakresie", "przygotowuję", "przygotować", "wniosek", "wniosku",
    "znajdź", "podaj", "przepisy", "przepisów", "artykuły", "ustawa", "ustawy", "prawo",
    "prawa", "należy", "można", "czy", "takie", "także", "również", "tego", "jego", "jej",
    "potrzebuję", "chcę", "proszę", "pytanie", "kwestia", "temat", "sytuacji", "podstawa",
    "podstawy", "prawnej", "prawna", "uzasadnienie", "uzasadnienia", "obowiązujące",
}  # fmt: skip

NOT_ASSESSED_RELEVANCE = CheckResult(
    status=VS.UNVERIFIED,
    detail="Serwer nie ocenia, czy przepis ma zastosowanie do konkretnej sprawy – wymaga to "
    "oceny prawnej osoby merytorycznej.",
)
COMPLETENESS_UNVERIFIED = CheckResult(
    status=VS.UNVERIFIED,
    detail="Lista zmian pochodzi z metadanych ELI ('Akty zmieniające', 'Orzeczenie TK'). Serwer "
    "nie potwierdza jej kompletności ani nie analizuje przepisów przejściowych i etapowego "
    "wchodzenia w życie aktów zmieniających.",
)
OFFICIAL_SOURCE = CheckResult(
    status=VS.VERIFIED,
    detail="Dane pobrane bezpośrednio z oficjalnego API ELI Kancelarii Sejmu "
    "(https://api.sejm.gov.pl/eli) przez HTTPS.",
)
STANDARD_WARNINGS = [
    "Wynik ma charakter pomocniczy i nie stanowi opinii prawnej.",
    "Etapowe wchodzenie w życie poszczególnych przepisów i przepisy przejściowe są analizowane "
    "jedynie heurystycznie.",
]


def warsaw_today() -> date:
    return datetime.now(WARSAW).date()


def _date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _refs(meta: dict[str, Any], relation: str) -> list[dict[str, Any]]:
    refs = meta.get("references") or {}
    items = refs.get(relation) if isinstance(refs, dict) else None
    return [r for r in items or [] if isinstance(r, dict) and r.get("id")]


def _act_id_or_none(value: str) -> ActId | None:
    try:
        return ActId.parse(value)
    except InvalidInputError:
        return None


def summarize_act(meta: dict[str, Any]) -> ActSummary:
    act_id = ActId.parse(
        str(meta.get("ELI") or f"{meta['publisher']}/{meta['year']}/{meta['pos']}")
    )
    title = str(meta.get("title", "")).strip()
    act_type = meta.get("type")
    status = meta.get("status")
    address = meta.get("address")
    return ActSummary(
        eli=act_id.eli,
        title=title,
        act_type=act_type,
        display_address=meta.get("displayAddress"),
        publisher=act_id.publisher,
        year=act_id.year,
        position=act_id.position,
        volume=meta.get("volume") or None,
        status=status,
        status_note=STATUS_NOTES.get(str(status)) if status else None,
        in_force=meta.get("inForce"),
        announcement_date=_date(meta.get("announcementDate")),
        promulgation_date=_date(meta.get("promulgation")),
        entry_into_force=_date(meta.get("entryIntoForce")),
        valid_from=_date(meta.get("validFrom")),
        repeal_date=_date(meta.get("repealDate")),
        expiration_date=_date(meta.get("expirationDate")),
        legal_status_date=_date(meta.get("legalStatusDate")),
        has_html=bool(meta.get("textHTML")),
        has_pdf=bool(meta.get("textPDF")),
        is_amending_act=bool(AMENDING_TITLE_RE.search(title)),
        is_consolidated_text_notice=act_type == "Obwieszczenie" and "jednolitego tekstu" in title,
        keywords=[str(k) for k in (meta.get("keywords") or []) + (meta.get("keywordsNames") or [])],
        isap_url=f"{ISAP_DETAILS}{address}" if address else None,
        api_url=act_id.api_url,
    )


def text_formats(meta: dict[str, Any], act_id: ActId) -> list[TextFormat]:
    formats: list[TextFormat] = []
    for text in meta.get("texts") or []:
        kind, file_name = str(text.get("type", "")), str(text.get("fileName", ""))
        label, official = TEXT_LABELS.get(kind, (f"Tekst ({kind})", True))
        url = (
            f"{act_id.api_url}/text.html"
            if kind == "H"
            else f"{act_id.api_url}/text/{kind}/{file_name}"
        )
        formats.append(TextFormat(kind=kind, label=label, url=url, official=official))
    return formats


def publication_address(meta: dict[str, Any]) -> str | None:
    """Format 'Dz. U. z 2025 r. poz. 1691' (or with 'Nr' for pre-2012 volumes)."""
    publisher, year, pos = meta.get("publisher"), meta.get("year"), meta.get("pos")
    if publisher not in ("DU", "MP") or not year or not pos:
        return None
    journal = "Dz. U." if publisher == "DU" else "M. P."
    volume = meta.get("volume") or 0
    if volume:
        return f"{journal} z {year} r. Nr {volume}, poz. {pos}"
    return f"{journal} z {year} r. poz. {pos}"


def unit_designation(
    article: str, paragraph: str | None, kind: str | None, point: str | None, letter: str | None
) -> str:
    article = article.strip()
    if article.startswith("§"):
        parts = [f"§ {display_name(article)}"]
    else:
        parts = [f"art. {display_name(article)}"]
    if paragraph:
        parts.append(f"{kind or 'ust.'} {display_name(paragraph)}")
    if point:
        parts.append(f"pkt {display_name(point)}")
    if letter:
        parts.append(f"lit. {display_name(letter)}")
    return " ".join(parts)


def cit_normalize(value: str) -> str:
    return re.sub(r"^(art\.?|ust\.?|pkt|lit\.?|§)\s*", "", value.strip(), flags=re.IGNORECASE)


def format_citation(
    designation: str, base_meta: dict[str, Any], pub_meta: dict[str, Any], amended: bool
) -> str | None:
    title = str(base_meta.get("title", "")).strip().rstrip(".")
    first, _, rest = title.partition(" ")
    genitive = GENITIVE.get(first)
    address = publication_address(pub_meta)
    if not genitive or not rest or not address:
        return None  # do not fabricate a normalized citation without metadata
    suffix = ", z późn. zm." if amended else ""
    return f"{designation} {genitive} {rest} ({address}{suffix})"


def assess_act_in_force(meta: dict[str, Any], as_of: date, today: date) -> CheckResult:
    start = _date(meta.get("entryIntoForce")) or _date(meta.get("validFrom"))
    ends = [d for d in (_date(meta.get("repealDate")), _date(meta.get("expirationDate"))) if d]
    end = min(ends) if ends else None
    if start is None:
        return CheckResult(
            status=VS.UNVERIFIED, detail="Metadane ELI nie podają daty wejścia w życie aktu."
        )
    if as_of < start:
        return CheckResult(
            status=VS.NOT_APPLICABLE,
            detail=f"Akt wchodzi w życie {start.isoformat()} – po żądanej dacie {as_of.isoformat()}.",
        )
    if end and as_of >= end:
        return CheckResult(
            status=VS.NOT_APPLICABLE,
            detail=f"Akt utracił moc (uchylenie/wygaśnięcie) z dniem {end.isoformat()}.",
        )
    if as_of > today:
        return CheckResult(
            status=VS.UNVERIFIED,
            detail="Żądana data jest w przyszłości – stan prawny może się jeszcze zmienić.",
        )
    if end is None and meta.get("inForce") == "NOT_IN_FORCE":
        return CheckResult(
            status=VS.UNVERIFIED,
            detail="ELI oznacza akt jako nieobowiązujący, ale nie podaje daty utraty mocy.",
        )
    detail = f"Wg metadanych ELI akt wszedł w życie {start.isoformat()}"
    detail += (
        f" i utracił moc {end.isoformat()}." if end else "; brak daty uchylenia lub wygaśnięcia."
    )
    return CheckResult(status=VS.VERIFIED, detail=detail)


@dataclass
class TemporalInputs:
    as_of: date
    today: date
    act_check: CheckResult
    text_read: bool
    unit_found: bool
    source_label: str
    state_date: date | None
    marker: str | None
    changes: list[ActReference]
    undated_changes: list[ActReference]
    staged: StagedEntry | None
    footnote_dates: list[date]
    wording_verified: bool = True


def assess_temporal(t: TemporalInputs) -> CheckResult:
    """Decide whether the retrieved wording is the wording applicable on ``as_of``."""
    if t.act_check.status == VS.NOT_APPLICABLE:
        return CheckResult(status=VS.NOT_APPLICABLE, detail=t.act_check.detail)
    if not t.text_read:
        reason = (
            "Nie odnaleziono jednostki w pobranej wersji tekstu – mogła zostać dodana lub "
            "zmieniona później albo nie istnieje."
            if not t.unit_found
            else "Nie odczytano brzmienia przepisu z tekstu strukturalnego (HTML)."
        )
        return CheckResult(status=VS.UNVERIFIED, detail=reason)
    if not t.wording_verified:
        return CheckResult(
            status=VS.UNVERIFIED,
            detail=f"{t.source_label}: brzmienie odczytane automatycznie z PDF – niezweryfikowane; "
            "porównaj z PDF przed cytowaniem.",
        )
    if t.as_of > t.today:
        return CheckResult(
            status=VS.UNVERIFIED,
            detail="Żądana data jest w przyszłości – stan prawny może się zmienić.",
        )
    if t.state_date is None:
        return CheckResult(
            status=VS.UNVERIFIED,
            detail=f"{t.source_label}: metadane nie podają daty stanu prawnego.",
        )
    reasons: list[str] = []
    if t.act_check.status != VS.VERIFIED:
        reasons.append(t.act_check.detail)
    if t.changes:
        listed = ", ".join(
            f"{c.eli} ({c.effective_date.isoformat() if c.effective_date else '?'})"
            for c in t.changes
        )
        reasons.append(
            f"Między stanem tekstu ({t.state_date.isoformat()}) a żądaną datą "
            f"({t.as_of.isoformat()}) w metadanych ELI występują zmiany lub orzeczenia TK: {listed}. "
            "Serwer nie odtwarza brzmienia po zmianach."
        )
    if t.undated_changes:
        reasons.append(
            "Część zmian/orzeczeń nie ma daty w metadanych: "
            + ", ".join(c.eli for c in t.undated_changes)
            + "."
        )
    if t.staged and (
        t.staged.unresolved or (t.staged.latest_date and t.staged.latest_date > t.as_of)
    ):
        reasons.append(
            "Akt przewiduje etapowe wejście w życie, którego nie dało się jednoznacznie rozstrzygnąć "
            f"dla żądanej daty: „{t.staged.clause[:400]}”."
        )
    late = sorted(d for d in t.footnote_dates if d > t.as_of)
    if late:
        reasons.append(
            "Przypisy do jednostki wskazują daty późniejsze niż żądana: "
            + ", ".join(d.isoformat() for d in late)
            + "."
        )
    if reasons:
        return CheckResult(status=VS.UNVERIFIED, detail=" ".join(reasons))
    base = (
        f"{t.source_label} (stan na {t.state_date.isoformat()}); akt obowiązuje w żądanym dniu; "
        f"brak zmian i orzeczeń TK w metadanych ELI między {t.state_date.isoformat()} a "
        f"{t.as_of.isoformat()}."
    )
    if t.marker:
        return CheckResult(
            status=VS.NOT_APPLICABLE,
            detail=f"Jednostka ma w tekście oznaczenie {t.marker}. {base}",
        )
    return CheckResult(
        status=VS.VERIFIED,
        detail=base + " Zastrzeżenie: weryfikacja opiera się na kompletności metadanych ELI.",
    )


async def gather_limited[T](
    factories: Iterable[Callable[[], Awaitable[T]]], limit: int = 5
) -> list[T]:
    semaphore = asyncio.Semaphore(limit)

    async def run(factory: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await factory()

    return await asyncio.gather(*(run(f) for f in factories))


@dataclass
class Version:
    act_id: ActId
    meta: dict[str, Any]
    kind: str  # "tekst_jednolity" | "tekst_pierwotny"

    @property
    def state_date(self) -> date | None:
        if self.kind == "tekst_jednolity":
            return _date(self.meta.get("legalStatusDate"))
        return _date(self.meta.get("entryIntoForce")) or _date(self.meta.get("validFrom"))

    @property
    def ordering_date(self) -> date | None:
        """Upper bound of the legal state: state date, else promulgation (for old notices)."""
        return self.state_date or _date(self.meta.get("promulgation"))

    @property
    def has_html(self) -> bool:
        return bool(self.meta.get("textHTML"))

    def source_document(self) -> SourceDocument:
        summary = summarize_act(self.meta)
        return SourceDocument(
            eli=self.act_id.eli,
            title=summary.title,
            kind="tekst_jednolity" if self.kind == "tekst_jednolity" else "tekst_pierwotny",
            state_date=self.state_date,
            html_available=self.has_html,
            html_url=f"{self.act_id.api_url}/text.html" if self.has_html else None,
            pdf_url=f"{self.act_id.api_url}/text.pdf" if self.meta.get("textPDF") else None,
            isap_url=summary.isap_url,
        )

    @property
    def label(self) -> str:
        kind = "Tekst jednolity" if self.kind == "tekst_jednolity" else "Tekst pierwotny"
        return f"{kind} {self.act_id.eli}"


class LegalService:
    def __init__(
        self,
        eli: EliClient,
        today: Callable[[], date] = warsaw_today,
        eu: EuLawService | None = None,
    ) -> None:
        self._eli = eli
        self._today = today
        self._eu = eu

    # ----------------------------------------------------------------- search & details

    async def search_acts(
        self,
        query: str,
        publisher: str | None = None,
        year: int | None = None,
        limit: int = 10,
        act_type: str | None = None,
        in_force_only: bool = False,
        keyword: str | None = None,
    ) -> SearchActsResult:
        warnings: list[str] = []
        params: dict[str, str | int] = {"title": query.strip(), "limit": SEARCH_WINDOW, "offset": 0}
        if publisher:
            params["publisher"] = ActId(publisher.upper(), 2000, 1).publisher
        if year:
            params["year"] = year
        if act_type:
            params["type"] = act_type
        if in_force_only:
            params["inForce"] = "1"
        if keyword:
            params["keyword"] = await self._validated_keyword(keyword)
        data = await self._eli.search(params)
        items = [summarize_act(i) for i in data["items"]]
        # ponytail: ELI has no relevance ranking; within the fetched window we only move base
        # acts ahead of amending acts and consolidated-text notices. Upgrade: real ranking.
        items.sort(key=lambda a: a.is_amending_act or a.is_consolidated_text_notice)
        total = int(data.get("totalCount") or len(items))
        if total > SEARCH_WINDOW:
            warnings.append(
                f"ELI zwróciło {total} trafień; uporządkowano tylko pierwsze {SEARCH_WINDOW}. "
                "Zawęź zapytanie (rok, typ aktu, tylko obowiązujące)."
            )
        return SearchActsResult(
            total_count=total,
            returned=min(limit, len(items)),
            items=items[:limit],
            coverage_note=(
                "Wyszukiwanie po fragmentach słów w tytułach aktów (ELI), bez wyszukiwania "
                "pełnotekstowego i semantycznego. Tytuły są w przypadkach zależnych – używaj "
                "rdzeni słów, np. 'zakwaterowa', 'gospodarce nieruchomo'."
            ),
            warnings=warnings,
        )

    async def _validated_keyword(self, keyword: str) -> str:
        vocabulary = await self._eli.keywords()
        lookup = {k.lower(): k for k in vocabulary}
        exact = lookup.get(keyword.strip().lower())
        if exact:
            return exact
        close = difflib.get_close_matches(keyword.strip().lower(), list(lookup), n=5, cutoff=0.6)
        suggestions = ", ".join(lookup[c] for c in close) or "brak"
        raise InvalidInputError(
            f"Hasło {keyword!r} nie występuje w słowniku haseł ELI. Podobne: {suggestions}."
        )

    async def get_act(
        self, act_id: ActId, toc_offset: int = 0, toc_limit: int = 200
    ) -> LegalActDetails:
        meta = await self._eli.act(act_id)
        summary = summarize_act(meta)
        warnings: list[str] = []
        toc: list[TocEntry] = []
        total = 0
        if summary.has_html:
            try:
                entries = list(iter_toc(await self._eli.struct(act_id)))
                total = len(entries)
                toc = [
                    TocEntry(unit_id=i, unit_type=t, label=label, depth=d)
                    for i, t, label, d in entries[toc_offset : toc_offset + toc_limit]
                ]
            except LegalMcpError as exc:
                warnings.append(f"Nie pobrano struktury aktu: {exc}")
        else:
            warnings.append(
                "Akt nie ma tekstu HTML w ELI (dotyczy m.in. wszystkich aktów ogłoszonych od 2025 r.) "
                "– automatyczne odczytanie struktury i brzmienia nie jest możliwe; użyj PDF."
            )
        consolidated_of = _refs(meta, CONSOLIDATED_TEXT_OF)
        refs = meta.get("references") or {}
        return LegalActDetails(
            act=summary,
            texts=text_formats(meta, act_id),
            consolidated_text_of=consolidated_of[0]["id"] if consolidated_of else None,
            consolidated_texts=[r["id"] for r in _refs(meta, CONSOLIDATED_TEXTS)],
            reference_counts={k: len(v) for k, v in refs.items() if isinstance(v, list)},
            toc=toc,
            toc_total=total,
            toc_offset=toc_offset,
            toc_has_more=toc_offset + len(toc) < total,
            warnings=warnings,
        )

    # -------------------------------------------------------------------- versions

    async def base_act(self, act_id: ActId) -> tuple[ActId, str | None]:
        """Base act and its title; a consolidated text (tekst jednolity) resolves to its act."""
        base_id, base_meta, _ = await self._resolve_base(act_id)
        return base_id, summarize_act(base_meta).title

    async def _resolve_base(self, act_id: ActId) -> tuple[ActId, dict[str, Any], Version | None]:
        """Return (base act id, base metadata, explicitly requested consolidated text)."""
        meta = await self._eli.act(act_id)
        target = _refs(meta, CONSOLIDATED_TEXT_OF)
        if target:
            base_id = ActId.parse(target[0]["id"])
            return base_id, await self._eli.act(base_id), Version(act_id, meta, "tekst_jednolity")
        return act_id, meta, None

    async def _consolidated_versions(self, base_meta: dict[str, Any]) -> list[Version]:
        ids = [
            i for i in (_act_id_or_none(r["id"]) for r in _refs(base_meta, CONSOLIDATED_TEXTS)) if i
        ]
        ids.sort(key=lambda a: (a.year, a.position), reverse=True)
        versions: list[Version] = []
        # ponytail: fetches up to 15 notices up front (cached 1 h); lazy newest-first iteration
        # would save cold calls for heavily consolidated acts such as the Civil Code.
        for act_id in ids[:MAX_CONSOLIDATED_TEXTS_SCANNED]:
            try:
                versions.append(Version(act_id, await self._eli.act(act_id), "tekst_jednolity"))
            except UpstreamNotFoundError:
                continue
        return versions

    async def _select_versions(
        self, base_id: ActId, base_meta: dict[str, Any], as_of: date
    ) -> tuple[Version, Version | None]:
        """(wording source, latest official consolidated text as of the date)."""
        original = Version(base_id, base_meta, "tekst_pierwotny")
        latest_official: Version | None = None
        for version in await self._consolidated_versions(base_meta):
            ordering = version.ordering_date
            if ordering is None or ordering > as_of:
                continue
            if latest_official is None:
                latest_official = version
            if version.has_html:
                return version, latest_official
        return original, latest_official

    async def _changes(
        self, base_meta: dict[str, Any], lo: date, hi: date, include_lo: bool = True
    ) -> tuple[list[ActReference], list[ActReference]]:
        """Amendments and TK rulings dated within [lo, hi] (or (lo, hi]); plus undated ones."""

        def inside(when: date) -> bool:
            return (lo <= when if include_lo else lo < when) and when <= hi

        dated: list[ActReference] = []
        undated: list[ActReference] = []
        for ref in _refs(base_meta, AMENDING):
            ref_date = _date(ref.get("date"))
            item = _reference(ref, AMENDING, ref_date)
            if ref_date is None:
                undated.append(item)
            elif inside(ref_date):
                dated.append(item)
        candidates = [
            (ref, act_id)
            for ref in _refs(base_meta, TK_RULINGS)
            if (act_id := _act_id_or_none(ref["id"])) and lo.year - 2 <= act_id.year <= hi.year
        ]

        async def tk_date(act_id: ActId) -> date | None:
            try:
                meta = await self._eli.act(act_id)
            except LegalMcpError:
                return None
            dates = [
                d for d in (_date(meta.get("entryIntoForce")), _date(meta.get("promulgation"))) if d
            ]
            return max(dates) if dates else None

        dates = await gather_limited([lambda a=a: tk_date(a) for _, a in candidates])
        for (ref, _), ref_date in zip(candidates, dates, strict=True):
            item = _reference(ref, TK_RULINGS, ref_date)
            if ref_date is None:
                undated.append(item)
            elif inside(ref_date):
                dated.append(item)
        dated.sort(key=lambda r: r.effective_date or date.min)
        return dated, undated

    async def _pending_note(self, base_meta: dict[str, Any], as_of: date) -> str | None:
        """Amendments of this act dated after ``as_of``, flagging staged amending acts.

        ELI gives one date per amended act. When the amending act itself is already in force on
        ``as_of`` but its changes to this act are dated later, it enters into force in stages;
        ELI does not show whether some changes to this act took effect earlier, so the user is
        told to check its final provisions. Informational only: it never widens VERIFIED.
        """
        pending = sorted(
            (
                (d, act_id)
                for r in _refs(base_meta, AMENDING)
                if (d := _date(r.get("date")))
                and d > as_of
                and (act_id := _act_id_or_none(r["id"]))
            ),
            key=lambda pair: pair[0],
        )[:MAX_PENDING_CHECKED]
        if not pending:
            return None

        async def entry(act_id: ActId) -> date | None:
            try:
                meta = await self._eli.act(act_id)
            except LegalMcpError:
                return None
            return _date(meta.get("entryIntoForce"))

        entries = await gather_limited([lambda a=a: entry(a) for _, a in pending])
        parts = []
        for (when, act_id), started in zip(pending, entries, strict=True):
            part = f"{act_id.eli} (zmiany tego aktu od {when.isoformat()})"
            if started and started <= as_of:
                part += (
                    f" – ustawa zmieniająca obowiązuje już od {started.isoformat()} (etapowe wejście "
                    "w życie): sprawdź w jej przepisach końcowych, czy nie zmieniła tego przepisu "
                    "wcześniej"
                )
            parts.append(part)
        return (
            f"Zmiany oczekujące na wejście w życie po {as_of.isoformat()}: "
            + "; ".join(parts)
            + "."
        )

    # ------------------------------------------------------------------- provisions

    async def get_provision(
        self,
        act_id: ActId,
        article: str,
        paragraph: str | None = None,
        point: str | None = None,
        letter: str | None = None,
        as_of: date | None = None,
    ) -> ProvisionResult:
        today = self._today()
        as_of = as_of or today
        warnings = list(STANDARD_WARNINGS)

        def failed(status: VS, detail: str) -> ProvisionResult:
            check = CheckResult(status=status, detail=detail)
            unavailable = CheckResult(
                status=VS.SOURCE_UNAVAILABLE if status == VS.SOURCE_UNAVAILABLE else VS.UNVERIFIED,
                detail=detail,
            )
            return ProvisionResult(
                requested_act_id=act_id.eli,
                as_of_date=as_of,
                verification=VerificationReport(
                    source_authenticity=OFFICIAL_SOURCE
                    if status != VS.SOURCE_UNAVAILABLE
                    else check,
                    act_existence=check,
                    unit_existence=unavailable,
                    exact_wording=unavailable,
                    act_in_force_on_date=unavailable,
                    temporal_applicability=unavailable,
                    amendment_completeness=COMPLETENESS_UNVERIFIED,
                    substantive_relevance=NOT_ASSESSED_RELEVANCE,
                ),
                warnings=warnings,
            )

        try:
            base_id, base_meta, requested_version = await self._resolve_base(act_id)
            source, latest_official = await self._select_versions(base_id, base_meta, as_of)
        except UpstreamNotFoundError as exc:
            return failed(VS.NOT_FOUND, str(exc))
        except UpstreamUnavailableError as exc:
            return failed(VS.SOURCE_UNAVAILABLE, str(exc))
        if requested_version is not None:
            source = requested_version
            warnings.append(
                f"Użyto wskazanego tekstu jednolitego {act_id.eli} (zamiast automatycznego wyboru wersji)."
            )

        act_check = assess_act_in_force(base_meta, as_of, today)
        unit_check: CheckResult
        wording_check: CheckResult
        unit_id: str | None = None
        path: list[str] = []
        text: str | None = None
        footnotes: list[str] = []
        marker: str | None = None
        paragraph_kind: str | None = None
        staged: StagedEntry | None = None
        unit_found = False

        if not source.has_html:
            reason = (
                f"{source.label} jest dostępny tylko jako PDF – serwer nie wyodrębnia automatycznie "
                "jednostek redakcyjnych z PDF. Sprawdź brzmienie w PDF."
            )
            unit_check = CheckResult(status=VS.UNVERIFIED, detail=reason)
            wording_check = CheckResult(status=VS.UNVERIFIED, detail=reason)
        else:
            try:
                struct = await self._eli.struct(source.act_id)
                match = find_unit(struct, article, paragraph, point, letter)
                doc = parse_html(await self._eli.html(source.act_id))
                extracted = extract_unit(doc, match.node_id)
                unit_id, path, paragraph_kind = match.node_id, match.path, match.paragraph_kind
                unit_found = True
                if extracted is None:
                    unit_check = CheckResult(
                        status=VS.SOURCE_UNAVAILABLE,
                        detail="Jednostka występuje w strukturze aktu, ale jej tekstu brak w HTML.",
                    )
                    wording_check = unit_check
                else:
                    text, footnotes, marker = extracted.text, extracted.footnotes, extracted.marker
                    unit_check = CheckResult(
                        status=VS.VERIFIED,
                        detail=f"Jednostka odnaleziona w strukturze i tekście HTML: {source.label}.",
                    )
                    wording_check = CheckResult(
                        status=VS.VERIFIED,
                        detail=f"Brzmienie odczytane z oficjalnego HTML ({source.label}); "
                        "znormalizowano jedynie białe znaki, przypisy podano osobno.",
                    )
                    promulgation = _date(base_meta.get("promulgation"))
                    staged = find_staged_entry(act_body_text(doc, struct), promulgation)
            except UnitLookupError as exc:
                status = VS.NOT_FOUND if exc.reason == "not_found" else VS.UNVERIFIED
                unit_check = CheckResult(status=status, detail=exc.message)
                wording_check = CheckResult(status=status, detail=exc.message)
            except UpstreamUnavailableError as exc:
                unit_check = CheckResult(status=VS.SOURCE_UNAVAILABLE, detail=str(exc))
                wording_check = unit_check
            except LegalMcpError as exc:
                unit_check = CheckResult(status=VS.UNVERIFIED, detail=str(exc))
                wording_check = unit_check

        # Since 2025 ELI publishes consolidated texts only as PDF. Confirm the HTML wording
        # against the newest one, or read the current wording from it when it differs.
        current = source
        text_origin: str | None = "html" if text is not None else None
        pdf_page: int | None = None
        earlier_wording: str | None = None
        earlier_source: SourceDocument | None = None
        pdf_version: Version | None = None
        if not source.has_html:
            pdf_version = source
        elif (
            requested_version is None
            and latest_official is not None
            and not latest_official.has_html
            and latest_official.act_id != source.act_id
        ):
            pdf_version = latest_official
        if pdf_version is not None:
            pdf_unit = None
            try:
                pdf = await self._eli.pdf_text(pdf_version.act_id)
                pdf_unit = find_pdf_unit(pdf, article, paragraph, point, letter)
            except UnitLookupError as exc:
                if text is None and not source.has_html:
                    status = VS.NOT_FOUND if exc.reason == "not_found" else VS.UNVERIFIED
                    detail = f"{exc.message} (odczyt automatyczny PDF {pdf_version.act_id.eli})"
                    unit_check = CheckResult(status=status, detail=detail)
                    wording_check = CheckResult(status=status, detail=detail)
                else:
                    warnings.append(
                        f"Nie odnaleziono jednostki w najnowszym tekście jednolitym "
                        f"{pdf_version.act_id.eli} (PDF): {exc.message} Możliwa zmiana numeracji, "
                        "uchylenie lub błąd automatycznego odczytu."
                    )
            except LegalMcpError as exc:
                warnings.append(f"Nie odczytano PDF {pdf_version.act_id.eli}: {exc}")
            if pdf_unit is not None:
                pdf_page = pdf_unit.page
                current = pdf_version
                same, updated = same_wording(text, pdf_unit.text) if text else (False, None)
                where = f"{pdf_version.label} (PDF, str. {pdf_unit.page})"
                if same and text is not None:
                    text = updated or text
                    text_origin = "html_confirmed_by_pdf"
                    wording_check = CheckResult(
                        status=VS.VERIFIED,
                        detail=f"Brzmienie z oficjalnego HTML ({source.label}) jest zgodne z "
                        f"najnowszym tekstem jednolitym {where}"
                        + (
                            "; adresy publikacji przywołanych aktów zaktualizowano według PDF."
                            if updated
                            else "."
                        ),
                    )
                else:
                    if text is not None:
                        earlier_wording, earlier_source = text, source.source_document()
                    text = pdf_unit.text
                    footnotes = []
                    marker = repeal_marker(pdf_unit.text)
                    text_origin = "pdf_automatic"
                    unit_found = True
                    path = path or pdf_unit.path
                    detail = (
                        f"Brzmienie odczytane automatycznie z {where}"
                        + (
                            f"; różni się od wersji HTML ({source.label}) – przepis zmieniony lub "
                            "błąd odczytu."
                            if earlier_wording
                            else "."
                        )
                        + " Przed cytowaniem porównaj z PDF."
                    )
                    unit_check = CheckResult(
                        status=VS.UNVERIFIED,
                        detail=f"Jednostka odnaleziona w {where} (odczyt automatyczny).",
                    )
                    wording_check = CheckResult(status=VS.UNVERIFIED, detail=detail)

        state = current.state_date
        changes: list[ActReference] = []
        undated: list[ActReference] = []
        if state is not None:
            lo, hi = min(state, as_of), max(state, as_of)
            # A consolidated text "według stanu prawnego na dzień X" already includes changes in
            # force on X; for an original text stay conservative and include its start date.
            include_lo = not (current.kind == "tekst_jednolity" and lo == state)
            changes, undated = await self._changes(base_meta, lo, hi, include_lo)

        if text and len(text) > MAX_PROVISION_CHARS:
            text = text[:MAX_PROVISION_CHARS]
            truncated = True
            warnings.append("Brzmienie skrócono – pobierz węższą jednostkę (ust./pkt/lit.).")
        else:
            truncated = False

        temporal = assess_temporal(
            TemporalInputs(
                as_of=as_of,
                today=today,
                act_check=act_check,
                text_read=text is not None,
                unit_found=unit_check.status != VS.NOT_FOUND,
                source_label=current.label,
                state_date=state,
                marker=marker,
                changes=changes,
                undated_changes=undated,
                staged=staged,
                footnote_dates=[d for f in footnotes for d in parse_dates(f)],
                wording_verified=wording_check.status == VS.VERIFIED,
            )
        )

        pending_note = await self._pending_note(base_meta, as_of)
        if pending_note:
            warnings.append(pending_note)

        latest_doc = latest_official.source_document() if latest_official else None
        if latest_official and latest_official.act_id != current.act_id:
            warnings.append(
                f"Najnowszy urzędowy tekst jednolity na żądaną datę to {latest_official.act_id.eli} "
                f"(stan na {latest_official.state_date or '?'}), dostępny tylko w PDF lub nieużyty; "
                f"brzmienie pochodzi z {source.label}. Porównaj z PDF: {latest_doc.pdf_url if latest_doc else ''}"
            )
        if current.kind == "tekst_pierwotny" and _refs(base_meta, AMENDING):
            warnings.append(
                "Brzmienie pochodzi z tekstu pierwotnego aktu, który był później zmieniany."
            )

        citation = None
        if unit_found:
            designation = unit_designation(article, paragraph, paragraph_kind, point, letter)
            pub = latest_official or Version(base_id, base_meta, "tekst_pierwotny")
            pub_state = pub.state_date or pub.ordering_date
            amended_after_pub = any(
                (d := _date(r.get("date"))) is not None
                and pub_state is not None
                and pub_state < d <= as_of
                for r in _refs(base_meta, AMENDING)
            )
            citation = Citation(
                act_title=str(base_meta.get("title", "")).strip(),
                act_type=base_meta.get("type"),
                publisher=base_id.publisher,
                year=base_id.year,
                position=base_id.position,
                publication_eli=pub.act_id.eli,
                article=cit_normalize(article)
                if not article.strip().startswith("§")
                else article.strip(),
                paragraph=paragraph,
                paragraph_kind=paragraph_kind,  # type: ignore[arg-type]
                point=point,
                letter=letter,
                official_url=summarize_act(base_meta).isap_url or base_id.api_url,
                source_version=f"{current.label} (stan na {state.isoformat() if state else '?'})",
                as_of_date=as_of,
                formatted=format_citation(designation, base_meta, pub.meta, amended_after_pub),
            )

        return ProvisionResult(
            requested_act_id=act_id.eli,
            base_act=summarize_act(base_meta),
            as_of_date=as_of,
            unit_id=unit_id,
            structural_path=path,
            text=text,
            text_truncated=truncated,
            text_origin=text_origin,  # type: ignore[arg-type]
            pdf_page=pdf_page,
            earlier_wording=earlier_wording[:MAX_PROVISION_CHARS] if earlier_wording else None,
            earlier_wording_source=earlier_source,
            footnotes=footnotes,
            wording_source=current.source_document(),
            latest_official_consolidated_text=latest_doc,
            changes_after_source=changes + undated,
            citation=citation,
            verification=VerificationReport(
                source_authenticity=OFFICIAL_SOURCE,
                act_existence=CheckResult(
                    status=VS.VERIFIED,
                    detail=f"Akt {base_id.eli} istnieje w ELI: {base_meta.get('title')}",
                ),
                unit_existence=unit_check,
                exact_wording=wording_check,
                act_in_force_on_date=act_check,
                temporal_applicability=temporal,
                amendment_completeness=COMPLETENESS_UNVERIFIED,
                substantive_relevance=NOT_ASSESSED_RELEVANCE,
            ),
            warnings=warnings,
        )

    # ------------------------------------------------------------------- amendments

    async def get_amendments(
        self, act_id: ActId, as_of: date | None = None, limit: int = 20
    ) -> AmendmentsResult:
        today = self._today()
        as_of = as_of or today
        base_id, base_meta, _ = await self._resolve_base(act_id)
        _, latest = await self._select_versions(base_id, base_meta, as_of)
        refs = sorted(
            _refs(base_meta, AMENDING),
            key=lambda r: _date(r.get("date")) or date.min,
            reverse=True,
        )

        async def describe(ref: dict[str, Any]) -> ActReference:
            item = _reference(ref, AMENDING, _date(ref.get("date")))
            ref_id = _act_id_or_none(ref["id"])
            if ref_id:
                try:
                    item.title = str((await self._eli.act(ref_id)).get("title", "")) or None
                except LegalMcpError:
                    item.title = None
            return item

        amendments = await gather_limited([lambda r=r: describe(r) for r in refs[:limit]])

        async def describe_tk(ref: dict[str, Any]) -> ActReference:
            # ELI lists TK rulings without date or title; take both from the ruling's own record.
            item = _reference(ref, TK_RULINGS, _date(ref.get("date")))
            ref_id = _act_id_or_none(ref["id"])
            if ref_id:
                try:
                    meta = await self._eli.act(ref_id)
                    item.title = str(meta.get("title", "")) or None
                    item.effective_date = item.effective_date or _date(
                        meta.get("entryIntoForce") or meta.get("promulgation")
                    )
                except LegalMcpError:
                    pass
            return item

        tk_refs = sorted(
            _refs(base_meta, TK_RULINGS),
            key=lambda r: (a.year, a.position) if (a := _act_id_or_none(r["id"])) else (0, 0),
            reverse=True,
        )
        tk_rulings = await gather_limited([lambda r=r: describe_tk(r) for r in tk_refs[:limit]])
        latest_state = latest.state_date if latest else None
        warnings = list(STANDARD_WARNINGS[:1])
        warnings.append(COMPLETENESS_UNVERIFIED.detail)
        warnings.append(
            "Data przy zmianie to data z metadanych ELI; akt zmieniający może wchodzić w życie "
            "etapami – sprawdź jego przepis o wejściu w życie."
        )
        if len(refs) > limit:
            warnings.append(f"Pokazano {limit} najnowszych z {len(refs)} zmian.")
        if base_id != act_id:
            warnings.append(
                f"{act_id.eli} to obwieszczenie o tekście jednolitym; pokazano zmiany aktu {base_id.eli}."
            )
        return AmendmentsResult(
            base_act=summarize_act(base_meta),
            as_of_date=as_of,
            latest_consolidated_text=latest.source_document() if latest else None,
            amendments=amendments,
            amendments_total=len(refs),
            pending_on_date=[r["id"] for r in refs if (d := _date(r.get("date"))) and d > as_of],
            after_latest_consolidated_text=[
                r["id"]
                for r in refs
                if latest_state and (d := _date(r.get("date"))) and d > latest_state
            ],
            constitutional_tribunal_rulings=tk_rulings,
            warnings=warnings,
        )

    # ------------------------------------------------------------------- citations

    async def verify_citation(
        self, citation: str, as_of: date | None = None
    ) -> CitationVerificationResult:
        parsed = cit.parse_citation(citation)
        as_of = as_of or self._today()
        unresolved: list[str] = []
        warnings: list[str] = []
        if self._eu is not None and not parsed.publication and parsed.article:
            eu_result = await self._eu.verify_citation(parsed, as_of)
            if eu_result is not None:
                return eu_result
        act_id: ActId | None = None
        method: str | None = None

        if parsed.publication:
            act_id, method = ActId.parse(parsed.publication), "adres publikacji / ELI w cytacie"
        elif alias := cit.alias_act_id(parsed):
            act_id, method = alias, f"rozpoznany skrót {parsed.known_alias}"
        elif parsed.act_text:
            act_id, method, candidates = await self._resolve_by_title(parsed.act_text)
            if act_id is None:
                unresolved.append(
                    "Nie udało się jednoznacznie ustalić aktu na podstawie tytułu"
                    + (f"; kandydaci: {', '.join(candidates)}" if candidates else "")
                    + ". Podaj adres publikacji (Dz. U. rok, poz.)."
                )

        def report(act_existence: CheckResult) -> VerificationReport:
            unknown = CheckResult(
                status=VS.UNVERIFIED,
                detail="Nie sprawdzono – brak rozpoznanego aktu lub jednostki.",
            )
            return VerificationReport(
                source_authenticity=OFFICIAL_SOURCE,
                act_existence=act_existence,
                unit_existence=unknown,
                exact_wording=unknown,
                act_in_force_on_date=unknown,
                temporal_applicability=unknown,
                amendment_completeness=COMPLETENESS_UNVERIFIED,
                substantive_relevance=NOT_ASSESSED_RELEVANCE,
            )

        if act_id is None:
            return CitationVerificationResult(
                parsed=parsed,
                verification=report(
                    CheckResult(status=VS.UNVERIFIED, detail="Nie zidentyfikowano aktu.")
                ),
                warnings=warnings,
                unresolved_questions=unresolved,
            )
        if not parsed.article:
            try:
                meta = await self._eli.act(act_id)
                existence = CheckResult(
                    status=VS.VERIFIED,
                    detail=f"Akt {act_id.eli} istnieje w ELI: {meta.get('title')}",
                )
            except UpstreamNotFoundError as exc:
                existence = CheckResult(status=VS.NOT_FOUND, detail=str(exc))
            except UpstreamUnavailableError as exc:
                existence = CheckResult(status=VS.SOURCE_UNAVAILABLE, detail=str(exc))
            unresolved.append("Cytat nie wskazuje jednostki redakcyjnej (art./§).")
            return CitationVerificationResult(
                parsed=parsed,
                resolution_method=method,
                verification=report(existence),
                supporting_sources=[act_id.api_url],
                warnings=warnings,
                unresolved_questions=unresolved,
            )

        provision = await self.get_provision(
            act_id, parsed.article, parsed.paragraph, parsed.point, parsed.letter, as_of
        )
        if parsed.publication and provision.latest_official_consolidated_text:
            latest = provision.latest_official_consolidated_text.eli
            if parsed.publication not in (
                latest,
                provision.base_act.eli if provision.base_act else None,
            ):
                warnings.append(
                    f"Cytat wskazuje publikację {parsed.publication}; najnowszy tekst jednolity na "
                    f"żądaną datę to {latest}."
                )
        sources = [
            url
            for url in (
                provision.base_act.isap_url if provision.base_act else None,
                provision.wording_source.html_url if provision.wording_source else None,
                provision.latest_official_consolidated_text.pdf_url
                if provision.latest_official_consolidated_text
                else None,
            )
            if url
        ]
        unresolved.append(
            "Czy przepis jest merytorycznie właściwą podstawą w tej sprawie – wymaga oceny prawnej."
        )
        if provision.verification.temporal_applicability.status != VS.VERIFIED:
            unresolved.append(
                "Czy brzmienie obowiązuje w żądanym dniu – nie potwierdzono automatycznie."
            )
        return CitationVerificationResult(
            parsed=parsed,
            resolution_method=method,
            provision=provision,
            verification=provision.verification,
            supporting_sources=sources,
            warnings=warnings,
            unresolved_questions=unresolved,
        )

    async def _resolve_by_title(self, act_text: str) -> tuple[ActId | None, str | None, list[str]]:
        act_type, fragment, year = cit.title_search_terms(act_text)
        if not fragment or len(fragment) < 4:
            return None, None, []
        params: dict[str, str | int] = {"title": fragment, "limit": 50}
        if act_type:
            params["type"] = act_type
        data = await self._eli.search(params)
        base_acts = [
            summarize_act(i)
            for i in data["items"]
            if not AMENDING_TITLE_RE.search(str(i.get("title", "")))
            and i.get("type") != "Obwieszczenie"
        ]
        if act_type:
            base_acts = [a for a in base_acts if a.act_type == act_type]
        if year:
            base_acts = [a for a in base_acts if f" {year} r" in a.title]
        if len(base_acts) == 1:
            return ActId.parse(base_acts[0].eli), "wyszukanie po tytule (ELI)", []
        return None, None, [a.eli for a in base_acts[:5]]

    # ------------------------------------------------------------------- legal basis

    async def find_legal_basis(
        self,
        issue: str,
        as_of: date | None = None,
        limit: int = 10,
        cited: tuple[list[tuple[ActId, int, str]], int] | None = None,
    ) -> LegalBasisResult:
        """Lexical candidates; ``cited`` adds acts cited in SAOS judgments found for the issue
        (from JudgmentService.cited_acts) as a second, case-law signal."""
        today = self._today()
        terms = issue_terms(issue)
        if not terms:
            raise InvalidInputError("Opis nie zawiera słów, na podstawie których można szukać.")
        vocabulary = await self._eli.keywords()
        matched = _match_keywords(terms, vocabulary)[:5]
        in_force_filter = as_of is None or as_of == today
        # eli -> (act, matched labels, methods, issue terms covered)
        found: dict[str, tuple[ActSummary, set[str], set[str], set[str]]] = {}

        async def collect(
            params: dict[str, str | int], label: str, method: str, covered: set[str]
        ) -> None:
            params = {**params, "limit": 50}
            if in_force_filter:
                params["inForce"] = "1"
            data = await self._eli.search(params)
            for item in data["items"]:
                act = summarize_act(item)
                if (
                    act.is_amending_act
                    or act.is_consolidated_text_notice
                    or act.act_type not in ("Ustawa", "Rozporządzenie")
                    or act.status in ONE_OFF_STATUSES
                ):
                    continue
                if as_of and act.entry_into_force and act.entry_into_force > as_of:
                    continue
                entry = found.setdefault(act.eli, (act, set(), set(), set()))
                entry[1].add(label)
                entry[2].add(method)
                entry[3].update(covered)
                title = act.title.lower()
                entry[3].update(t for t in terms if _stem(t) in title)

        case_law: dict[str, int] = {}

        async def add_cited(act_id: ActId, count: int, checked: int) -> None:
            try:
                meta = await self._eli.act(act_id)
            except LegalMcpError:
                return
            act = summarize_act(meta)
            if (
                act.is_amending_act
                or act.act_type not in ("Ustawa", "Rozporządzenie", "Konstytucja")
                or act.status in ONE_OFF_STATUSES
                or (in_force_filter and act.in_force == "NOT_IN_FORCE")
            ):
                return
            entry = found.setdefault(act.eli, (act, set(), set(), set()))
            entry[1].add(f"powołany w {count} z {checked} orzeczeń SAOS")
            entry[2].add("orzecznictwo SAOS")
            case_law[act.eli] = count

        searches = [
            lambda k=k: collect({"keyword": k}, k, "hasło ELI", _covered_terms(k, terms))
            for k in matched
        ]
        if cited:
            acts, checked = cited
            relevant = [(a, n) for a, n, _ in acts if a.eli not in PROCEDURAL_ACTS and n >= 2][
                :MAX_CITED_ACTS
            ]
            searches += [lambda a=a, n=n: add_cited(a, n, checked) for a, n in relevant]
        title_terms = sorted(terms, key=len, reverse=True)[:3]
        searches += [
            lambda t=t: collect({"title": _stem(t)}, t, "fragment tytułu", {t}) for t in title_terms
        ]
        await gather_limited(searches, limit=4)

        ranked = sorted(
            found.values(),
            # ponytail: rank by description words covered plus 3–4 points for acts cited in
            # case law (cited in ≥2 judgments); no semantic relevance.
            key=lambda e: (
                -(len(e[3]) + (min(case_law[e[0].eli], 3) + 1 if e[0].eli in case_law else 0)),
                -len(e[1]),
                e[0].act_type != "Ustawa",
                -(e[0].year),
            ),
        )
        warnings = [
            "Dopasowanie leksykalne (słowa opisu ↔ hasła ELI i tytuły aktów), nie semantyczne. "
            "Wyniki to kandydaci do dalszej analizy, a nie wskazanie podstawy prawnej.",
            *STANDARD_WARNINGS[:1],
        ]
        if cited:
            warnings.append(
                "Sygnał z orzecznictwa: akty powoływane w orzeczeniach SAOS znalezionych dla słów "
                "opisu (bez aktów proceduralnych, np. k.p.c., opłat sądowych). Częstość powołań "
                "nie oznacza, że akt jest podstawą prawną w tej sprawie; SAOS nie obejmuje "
                "WSA/NSA, a metadane dotyczą głównie sądów powszechnych."
            )
        if not in_force_filter:
            warnings.append(
                "Dla daty innej niż dzisiejsza nie filtrowano po statusie obowiązywania – "
                "zweryfikuj każdy akt narzędziem get_legal_provision lub get_legal_act."
            )
        return LegalBasisResult(
            issue_terms=terms,
            matched_keywords=matched,
            candidates=[
                LegalBasisCandidate(
                    act=a, matched_terms=sorted(labels), match_method=", ".join(sorted(m))
                )
                for a, labels, m, _ in ranked[:limit]
            ],
            method_note="Hasła ze słownika ELI (/keywords) dopasowane do rdzeni słów opisu oraz "
            "wyszukiwanie fragmentów najdłuższych słów w tytułach aktów obowiązujących.",
            warnings=warnings,
        )


def _reference(ref: dict[str, Any], relation: str, ref_date: date | None) -> ActReference:
    act_id = _act_id_or_none(str(ref["id"]))
    return ActReference(
        eli=str(ref["id"]),
        relation=relation,
        effective_date=ref_date,
        article=ref.get("art"),
        api_url=act_id.api_url if act_id else f"{ELI_API}/acts/{ref['id']}",
    )


_WORD_RE = re.compile(r"[a-ząćęłńóśźż]+", re.IGNORECASE)


def issue_terms(text: str) -> list[str]:
    seen: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        if len(word) >= 4 and word not in STOPWORDS and word not in seen:
            seen.append(word)
    return seen[:30]


def _stem(word: str) -> str:
    # ponytail: crude stemming (drop up to 3 trailing letters, keep 5, or 6 for words of 8+ so
    # 'zachowek' does not become 'zacho' and match 'Zachodni'); a Polish stemmer would also
    # handle alternations such as spadek → spadku.
    return word[: max(5 if len(word) < 8 else 6, len(word) - 3)]


def _stem_match(a: str, b: str) -> bool:
    shared = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        shared += 1
    return shared >= max(5, min(len(a), len(b)) - 3)


def _covered_terms(keyword: str, terms: list[str]) -> set[str]:
    words = [w for w in _WORD_RE.findall(keyword.lower()) if len(w) >= 4]
    return {t for t in terms if any(_stem_match(w, t) for w in words)}


def _match_keywords(terms: list[str], vocabulary: list[str]) -> list[str]:
    matched: list[tuple[int, str]] = []
    for keyword in vocabulary:
        words = [w for w in _WORD_RE.findall(keyword.lower()) if len(w) >= 4]
        if words and all(any(_stem_match(w, t) for t in terms) for w in words):
            matched.append((len(words), keyword))
    matched.sort(key=lambda m: -m[0])
    return [k for _, k in matched]
