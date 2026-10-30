"""EU law from the Publications Office (CELLAR): the most current Polish wording of a provision.

Wording comes from the newest EUR-Lex consolidated version valid on the requested date (it
incorporates amendments and corrigenda); the act as published in the Official Journal is the
fallback. Consolidated versions are informational, the OJ text is authentic — every result says
so. Verification rules mirror docs/legal-verification.md (see "EU law").
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.clients.eurlex import (
    CELEX_RE,
    EURLEX_PAGE,
    EurLexClient,
)
from polish_legal_mcp.errors import (
    InvalidInputError,
    LegalMcpError,
    UpstreamNotFoundError,
    UpstreamUnavailableError,
)
from polish_legal_mcp.models import (
    ActId,
    CheckResult,
    CitationVerificationResult,
    EuActSearchResult,
    EuActSummary,
    EuDocumentLink,
    EuProvisionResult,
    ParsedCitation,
    TranspositionMeasure,
    TranspositionResult,
    VerificationReport,
)
from polish_legal_mcp.models import VerificationStatus as VS
from polish_legal_mcp.parsers.eu_html import EuDocument, extract_eu_unit
from polish_legal_mcp.parsers.legal_html import UnitLookupError
from polish_legal_mcp.services.legal import (
    COMPLETENESS_UNVERIFIED,
    NOT_ASSESSED_RELEVANCE,
    STANDARD_WARNINGS,
    gather_limited,
    unit_designation,
    warsaw_today,
)

# Each alias was checked against the Publications Office on 2026-10-29.
EU_ALIASES: dict[str, str] = {
    "rodo": "32016R0679",
    "gdpr": "32016R0679",
    "ogólne rozporządzenie o ochronie danych": "32016R0679",
}
_TYPES = (("rozporządz", "R"), ("regulation", "R"), ("dyrektyw", "L"), ("directive", "L"))
_TYPES += (("decyzj", "D"), ("decision", "D"))
_INSTITUTION = r"\((?:UE|WE|EWG|EU|EC|EEC|Euratom)(?:\s*,\s*Euratom)?\)"
_REGULATION_RE = re.compile(_INSTITUTION + r"\s*(nr\s*)?(\d{1,4})/(\d{1,4})", re.IGNORECASE)
_DIRECTIVE_RE = re.compile(r"\b(\d{2,4})/(\d{1,4})/(?:UE|WE|EWG|EU|EC|EEC)\b", re.IGNORECASE)
MAX_TEXT_CHARS = 12_000
OFFICIAL_NOTE = (
    "Tekst skonsolidowany EUR-Lex ma charakter informacyjny; moc prawną mają akty opublikowane "
    "w Dzienniku Urzędowym UE (wraz ze sprostowaniami)."
)
GENITIVE = {"Rozporządzenie": "rozporządzenia", "Dyrektywa": "dyrektywy", "Decyzja": "decyzji"}


def _year(value: str) -> int:
    number = int(value)
    if number < 100:
        return 1900 + number if number >= 50 else 2000 + number
    return number


def resolve_eu_act(text: str) -> str | None:
    """CELEX number from a CELEX, a known alias or an EU act reference; None if not EU law."""
    value = text.strip()
    if CELEX_RE.match(value.upper()):
        return value.upper()
    lowered = value.lower()
    for alias, celex in EU_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered):
            return celex
    kind = next((code for stem, code in _TYPES if stem in lowered), None)
    directive = _DIRECTIVE_RE.search(value)
    if directive and kind in (None, "L"):
        return f"3{_year(directive.group(1))}L{int(directive.group(2)):04d}"
    regulation = _REGULATION_RE.search(value)
    if regulation:
        first, second = regulation.group(2), regulation.group(3)
        # "(UE) 2016/679" is year/number (since 2015); "(WE) nr 1049/2001" is number/year.
        year, number = (second, first) if regulation.group(1) else (first, second)
        if not regulation.group(1) and len(first) < 4:
            year, number = second, first
        return f"3{_year(year)}{kind or 'R'}{int(number):04d}"
    return None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _consolidation_date(celex: str) -> date | None:
    suffix = celex.rsplit("-", 1)[-1]
    return _date(f"{suffix[:4]}-{suffix[4:6]}-{suffix[6:8]}") if len(suffix) == 8 else None


def _link(celex: str, when: date | None = None) -> EuDocumentLink:
    return EuDocumentLink(celex=celex, version_date=when, eurlex_url=f"{EURLEX_PAGE}{celex}")


def assess_eu_act_in_force(facts: dict[str, list[str]], as_of: date, today: date) -> CheckResult:
    starts = sorted(
        d for d in map(_date, facts.get("resource_legal_date_entry-into-force", [])) if d
    )
    ends = [d for d in map(_date, facts.get("resource_legal_date_end-of-validity", [])) if d]
    end = min((d for d in ends if d.year < 9999), default=None)
    if not starts:
        return CheckResult(status=VS.UNVERIFIED, detail="Metadane nie podają daty wejścia w życie.")
    if as_of < starts[0]:
        return CheckResult(
            status=VS.NOT_APPLICABLE, detail=f"Akt wchodzi w życie {starts[0].isoformat()}."
        )
    if end and as_of > end:
        return CheckResult(
            status=VS.NOT_APPLICABLE, detail=f"Akt obowiązywał do {end.isoformat()}."
        )
    if as_of > today:
        return CheckResult(status=VS.UNVERIFIED, detail="Żądana data jest w przyszłości.")
    if as_of < starts[-1]:
        later = ", ".join(d.isoformat() for d in starts[1:])
        return CheckResult(
            status=VS.UNVERIFIED,
            detail=f"Akt wszedł w życie {starts[0].isoformat()}, ale metadane podają też późniejsze "
            f"daty ({later}) – część przepisów może stosować się dopiero od nich.",
        )
    if "0" in facts.get("resource_legal_in-force", []) and not end:
        return CheckResult(
            status=VS.UNVERIFIED, detail="Akt oznaczony jako nieobowiązujący bez daty końcowej."
        )
    detail = f"Wg metadanych Urzędu Publikacji UE akt obowiązuje od {starts[-1].isoformat()}"
    return CheckResult(
        status=VS.VERIFIED, detail=detail + (f" do {end.isoformat()}." if end else ".")
    )


@dataclass
class EuTemporalInputs:
    as_of: date
    today: date
    act_check: CheckResult
    text_read: bool
    consolidated: bool
    source_date: date | None
    missing_corrigenda: list[str] = field(default_factory=list)
    later_amendments: list[str] = field(default_factory=list)


def assess_eu_temporal(t: EuTemporalInputs) -> CheckResult:
    if t.act_check.status == VS.NOT_APPLICABLE:
        return CheckResult(status=VS.NOT_APPLICABLE, detail=t.act_check.detail)
    if not t.text_read:
        return CheckResult(status=VS.UNVERIFIED, detail="Nie odczytano brzmienia przepisu.")
    if t.as_of > t.today:
        return CheckResult(status=VS.UNVERIFIED, detail="Żądana data jest w przyszłości.")
    reasons = []
    if t.act_check.status != VS.VERIFIED:
        reasons.append(t.act_check.detail)
    if t.missing_corrigenda:
        reasons.append(
            "Tekst nie uwzględnia sprostowań polskiej wersji językowej: "
            + ", ".join(t.missing_corrigenda)
            + "."
        )
    if t.later_amendments:
        reasons.append(
            "Po dacie użytej wersji weszły w życie akty zmieniające, których nie ma jeszcze w "
            "wersji skonsolidowanej: " + ", ".join(t.later_amendments) + "."
        )
    if reasons:
        return CheckResult(status=VS.UNVERIFIED, detail=" ".join(reasons))
    kind = "wersja skonsolidowana EUR-Lex" if t.consolidated else "tekst z Dz. Urz. UE"
    since = t.source_date.isoformat() if t.source_date else "?"
    return CheckResult(
        status=VS.VERIFIED,
        detail=f"Brzmienie z: {kind} (stan od {since}); akt obowiązuje; uwzględnione wszystkie "
        "sprostowania PL znane Urzędowi Publikacji; brak późniejszych aktów zmieniających w "
        "metadanych. Zastrzeżenie: opiera się na kompletności metadanych i konsolidacji EUR-Lex.",
    )


_WORD_RE = re.compile(r"[^\W\d_]+")
_STOPWORDS = {
    "oraz",
    "dla",
    "przez",
    "sprawie",
    "dnia",
    "lub",
    "jest",
    "art",
    "unii",
    "europejskiej",
}
ACT_TYPES = {"rozporządzenie": "R", "dyrektywa": "L", "decyzja": "D"}
MAX_TERMS = 6


_LEGISLATIVE_RE = re.compile(
    r"^(?:Rozporządzenie|Dyrektywa|Decyzja)(?:\s+(?:nr\s+)?\d+/\d+(?:/\w+)?)?\s+"
    r"(?:Parlamentu Europejskiego i Rady|Rady)\b"
)
# First words after the date: "… z dnia 4 października 2023 r. w sprawie zmiany decyzji …".
_AMENDING_RE = re.compile(r"\d{4}\s*r\.\s+(?:zmieniając|w sprawie zmiany|uchylając|korygując)")


def title_terms(query: str) -> list[str]:
    """Virtuoso full-text terms (crude Polish stemming by prefix): words of 7–8 letters keep 6
    ('ochrona' → 'ochron*'), longer ones drop 3 but keep at least 7 ('zamówienia' → 'zamówie*',
    'publiczne' → 'publicz*'); shorter words must match exactly. Shorter prefixes such as
    'public*' expand to thousands of words and take 15+ s on the endpoint."""
    terms: list[str] = []
    for word in _WORD_RE.findall(query.lower()):
        if len(word) < 3 or word in _STOPWORDS:
            continue
        if len(word) <= 6:
            term = word
        elif len(word) <= 8:
            term = f"{word[:6]}*"
        else:
            term = f"{word[: max(7, len(word) - 3)]}*"
        if term not in terms:
            terms.append(term)
    return terms[:MAX_TERMS]


def _rank(summary: EuActSummary) -> tuple[int, int, int, int, int]:
    # ponytail: EUR-Lex SPARQL has no relevance ranking; prefer base legislative acts in force,
    # then newest. A real ranking needs the EUR-Lex search API (registration required).
    return (
        0 if summary.legislative else 1,
        1 if summary.amending else 0,
        1 if summary.celex[5] == "D" else 0,  # regulations and directives before decisions
        0 if summary.in_force else 1,
        -summary.act_date.toordinal() if summary.act_date else 0,
    )


class EuLawService:
    def __init__(
        self,
        client: EurLexClient,
        today: Callable[[], date] = warsaw_today,
        eli: EliClient | None = None,
    ) -> None:
        self._client = client
        self._today = today
        self._eli = eli

    async def transposition(self, act: str, limit: int = 30) -> TranspositionResult:
        """Polish national implementing measures notified to the Commission, with the ELI title
        of each Dziennik Ustaw position for cross-checking (notifications are not verified)."""
        celex = resolve_eu_act(act)
        if celex is None:
            raise InvalidInputError(
                f"Nie rozpoznano aktu UE: {act!r}. Podaj CELEX (np. 32019L1937), numer dyrektywy "
                "(np. dyrektywa 2019/1937/UE) albo skrót RODO."
            )
        measures: dict[str, TranspositionMeasure] = {}
        for row in await self._client.national_measures(celex):
            # The same act is sometimes notified twice (e.g. 2024 whistleblower act).
            title = (row.get("title") or "").strip().lower()
            key = f"{title}|{row.get('number') or ''}" if title else row["m"]
            measure = measures.get(key)
            if measure is None:
                number = row.get("number")
                journal = row.get("journal")
                eli = None
                match = re.fullmatch(r"(\d{4})/(\d{1,5})", (number or "").strip())
                if match and journal == "Dziennik Ustaw":
                    eli = f"DU/{match.group(1)}/{int(match.group(2))}"
                measure = measures[key] = TranspositionMeasure(
                    title=row.get("title"),
                    act_type=row.get("type"),
                    journal=journal,
                    publication=number,
                    eli=eli,
                )
            if row.get("id") and row["id"] not in measure.cellar_ids:
                measure.cellar_ids.append(row["id"])
        ordered = sorted(
            measures.values(),
            key=lambda m: tuple(int(p) for p in m.eli.split("/")[1:]) if m.eli else (0, 0),
            reverse=True,
        )
        shown = ordered[:limit]

        async def check(measure: TranspositionMeasure) -> None:
            if self._eli is None or measure.eli is None:
                return
            try:
                meta = await self._eli.act(ActId.parse(measure.eli))
            except LegalMcpError:
                return
            measure.eli_title = str(meta.get("title") or "") or None
            measure.eli_status = str(meta.get("status") or "") or None

        await gather_limited([lambda m=m: check(m) for m in shown], limit=4)
        warnings = [
            "Lista pochodzi ze zgłoszeń Polski do Komisji Europejskiej (EUR-Lex, krajowe środki "
            "wykonawcze): obejmuje też akty zmieniające i teksty jednolite, nie wskazuje, które "
            "przepisy dyrektywy wdrażają, i nie dowodzi pełnej ani prawidłowej transpozycji. "
            "Porównaj title z eli_title; aktualne brzmienie: get_legal_provision."
        ]
        if not measures:
            warnings.append(
                "EUR-Lex nie wykazuje polskich środków wykonawczych dla tego aktu (rozporządzenia "
                "stosuje się bezpośrednio; dla dyrektyw zgłoszenie mogło nie zostać zarejestrowane)."
            )
        if len(ordered) > limit:
            warnings.append(f"Pokazano {limit} z {len(ordered)} zgłoszonych aktów (najnowsze).")
        return TranspositionResult(
            act_celex=celex,
            measures=shown,
            total_measures=len(ordered),
            eurlex_url=f"https://eur-lex.europa.eu/legal-content/PL/NIM/?uri=CELEX:{celex}",
            warnings=warnings,
        )

    async def search_acts(
        self, query: str, act_type: str | None = None, limit: int = 10
    ) -> EuActSearchResult:
        terms = title_terms(query)
        if not terms:
            raise InvalidInputError(
                "Podaj słowa z tytułu aktu UE (co najmniej 3 litery), np. 'ochrona danych osobowych'."
            )
        rows = await self._client.search_titles(terms, ACT_TYPES.get(act_type or ""))
        items: list[EuActSummary] = []
        for row in rows:
            title = re.sub(r"\s+", " ", row["title"]).strip()
            items.append(
                EuActSummary(
                    celex=row["celex"],
                    title=title[:400],
                    act_date=_date(row.get("date")),
                    in_force=row["force"] in ("1", "true") if "force" in row else None,
                    legislative=bool(_LEGISLATIVE_RE.match(title)),
                    amending=bool(_AMENDING_RE.search(title[:250])),
                    eurlex_url=f"{EURLEX_PAGE}{row['celex']}",
                )
            )
        items.sort(key=_rank)
        warnings = [
            "Wyszukiwanie po słowach z polskiego tytułu (EUR-Lex), bez rankingu trafności: "
            "najpierw akty Parlamentu Europejskiego i Rady/Rady, niezmieniające, obowiązujące. "
            "Brzmienie przepisu: get_eu_provision z numerem CELEX."
        ]
        if not items:
            warnings.append(
                "Brak trafień. Tytuły są odmienione (np. 'ochronie danych osobowych'); krótkie "
                "słowa (do 4 liter) muszą pasować dokładnie – użyj formy z tytułu lub dłuższych słów."
            )
        return EuActSearchResult(
            query_terms=terms, total_matched=len(rows), items=items[:limit], warnings=warnings
        )

    async def get_provision(
        self,
        act: str,
        article: str,
        paragraph: str | None = None,
        point: str | None = None,
        letter: str | None = None,
        as_of: date | None = None,
    ) -> EuProvisionResult:
        today = self._today()
        as_of = as_of or today
        celex = resolve_eu_act(act)
        if celex is None:
            raise InvalidInputError(
                "Nie rozpoznano aktu UE. Podaj numer CELEX (np. 32016R0679), oznaczenie "
                "(np. 'rozporządzenie (UE) 2016/679', 'dyrektywa 2014/24/UE') lub skrót RODO."
            )
        warnings = [*STANDARD_WARNINGS[:1], OFFICIAL_NOTE]
        if celex[5] == "L":
            warnings.append(
                "Dyrektywa wiąże państwa członkowskie co do rezultatu – w sprawach krajowych "
                "stosuje się zwykle przepisy ustawy wdrażającej (sprawdź w ELI)."
            )

        def failed(status: VS, detail: str) -> EuProvisionResult:
            check = CheckResult(status=status, detail=detail)
            other = CheckResult(
                status=VS.SOURCE_UNAVAILABLE if status == VS.SOURCE_UNAVAILABLE else VS.UNVERIFIED,
                detail=detail,
            )
            return EuProvisionResult(
                requested_act=act,
                celex=celex,
                eurlex_url=f"{EURLEX_PAGE}{celex}",
                as_of_date=as_of,
                verification=VerificationReport(
                    source_authenticity=other if status == VS.SOURCE_UNAVAILABLE else _AUTHENTIC,
                    act_existence=check,
                    unit_existence=other,
                    exact_wording=other,
                    act_in_force_on_date=other,
                    temporal_applicability=other,
                    amendment_completeness=COMPLETENESS_UNVERIFIED,
                    substantive_relevance=NOT_ASSESSED_RELEVANCE,
                ),
                warnings=warnings,
            )

        try:
            meta = await self._client.metadata(celex)
        except UpstreamUnavailableError as exc:
            return failed(VS.SOURCE_UNAVAILABLE, str(exc))
        if meta is None:
            return failed(
                VS.NOT_FOUND,
                f"Urząd Publikacji UE nie zna aktu {celex}. Nie dowodzi to, że akt nie istnieje – "
                "sprawdź numer.",
            )

        consolidations = sorted(
            ((c, d) for c in meta["consolidated"] if (d := _consolidation_date(c))),
            key=lambda item: item[1],
        )
        usable = [(c, d) for c, d in consolidations if d <= as_of]
        pending = [_link(c, d) for c, d in consolidations if d > as_of]
        corrigenda = [_link(c, _date(d)) for c, d in meta["corrigenda"]]

        unit = None
        info: EuDocument | None = None
        source_celex, source_date, consolidated = celex, None, False
        lookup_error: UnitLookupError | None = None
        candidates = ([usable[-1]] if usable else []) + [(celex, None)]
        try:
            for candidate, when in candidates:
                try:
                    html = await self._client.document(candidate)
                    unit, info = extract_eu_unit(html, article, paragraph, point, letter)
                    source_celex, source_date, consolidated = candidate, when, when is not None
                    break
                except UnitLookupError as exc:
                    lookup_error = exc
                    if exc.reason == "not_found":
                        break  # the unit is genuinely absent; do not silently switch versions
                except UpstreamNotFoundError as exc:
                    lookup_error = UnitLookupError("ambiguous", str(exc))
        except UpstreamUnavailableError as exc:
            return failed(VS.SOURCE_UNAVAILABLE, str(exc))
        except LegalMcpError as exc:
            lookup_error = UnitLookupError("ambiguous", str(exc))

        if info and info.oj and source_date is None:
            source_date = info.oj.published
        act_check = assess_eu_act_in_force(meta["facts"], as_of, today)
        incorporated = info.incorporated_celex if info and consolidated else set()
        missing = [c.celex for c in corrigenda if c.celex not in incorporated]
        first_effect: dict[str, date] = {}
        for amending, effective in meta["amendments"]:
            when = _date(effective)
            if when and source_date and source_date < when <= as_of:
                first_effect[amending] = min(when, first_effect.get(amending, when))
        later = [f"{c} (od {d.isoformat()})" for c, d in sorted(first_effect.items())]
        if unit is None:
            status = (
                VS.NOT_FOUND
                if lookup_error and lookup_error.reason == "not_found"
                else VS.UNVERIFIED
            )
            message = lookup_error.message if lookup_error else "Nie odczytano przepisu."
            unit_check = CheckResult(status=status, detail=message)
            wording_check = CheckResult(status=status, detail=message)
        else:
            where = (
                f"wersji skonsolidowanej {source_celex}"
                if consolidated
                else f"tekstu {celex} z Dz. Urz. UE"
            )
            unit_check = CheckResult(status=VS.VERIFIED, detail=f"Jednostka odnaleziona w {where}.")
            wording_check = CheckResult(
                status=VS.VERIFIED,
                detail=f"Brzmienie odczytane z {where}; znormalizowano białe znaki i usunięto "
                "znaczniki zmian (►C1, ▼M1)." + (f" {OFFICIAL_NOTE}" if consolidated else ""),
            )
        temporal = assess_eu_temporal(
            EuTemporalInputs(
                as_of=as_of,
                today=today,
                act_check=act_check,
                text_read=unit is not None,
                consolidated=consolidated,
                source_date=source_date,
                missing_corrigenda=missing,
                later_amendments=later,
            )
        )
        if pending:
            warnings.append(
                "Istnieją wersje skonsolidowane z datą późniejszą niż żądana (zmiany oczekujące): "
                + ", ".join(p.celex for p in pending)
                + "."
            )
        if consolidated and source_celex != celex:
            warnings.append(
                f"Brzmienie z wersji skonsolidowanej {source_celex}: {EURLEX_PAGE}{source_celex}"
            )

        text = unit.text if unit else None
        truncated = bool(text and len(text) > MAX_TEXT_CHARS)
        if text and truncated:
            text = text[:MAX_TEXT_CHARS]
            warnings.append("Brzmienie skrócono – pobierz węższą jednostkę (ust./pkt/lit.).")

        title = meta.get("title")
        citation = None
        if unit and title and info and info.oj:
            first, _, rest = title.partition(" ")
            genitive = GENITIVE.get(first)
            if genitive:
                rest = re.sub(r"\s*\(?Tekst mający znaczenie dla EOG\)?\s*$", "", rest).strip()
                suffix = ""
                if corrigenda:
                    suffix += ", ze sprost."
                if len(consolidations) > 1 or later or meta["amendments"]:
                    suffix += ", z późn. zm."
                designation = unit_designation(article, paragraph, "ust.", point, letter)
                citation = f"{designation} {genitive} {rest} ({info.oj.format()}{suffix})"

        eli = (meta["facts"].get("resource_legal_eli") or [None])[0]
        return EuProvisionResult(
            requested_act=act,
            celex=celex,
            title=title,
            eli=eli,
            eurlex_url=f"{EURLEX_PAGE}{celex}",
            official_journal=info.oj.format() if info and info.oj else None,
            as_of_date=as_of,
            structural_path=unit.path if unit else [],
            text=text,
            text_truncated=truncated,
            consolidated_versions=[_link(c, d) for c, d in consolidations],
            polish_corrigenda=corrigenda,
            citation=citation,
            verification=VerificationReport(
                source_authenticity=_AUTHENTIC,
                act_existence=CheckResult(
                    status=VS.VERIFIED,
                    detail=f"Akt {celex} istnieje w zasobach Urzędu Publikacji UE.",
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

    async def verify_citation(
        self, parsed: ParsedCitation, as_of: date
    ) -> CitationVerificationResult | None:
        """Handle citations of EU acts (e.g. 'art. 28 ust. 3 lit. a RODO'); None if not EU law."""
        act_text = parsed.act_text or ""
        if resolve_eu_act(act_text) is None or not parsed.article:
            return None
        result = await self.get_provision(
            act_text, parsed.article, parsed.paragraph, parsed.point, parsed.letter, as_of
        )
        unresolved = [
            "Czy przepis jest merytorycznie właściwą podstawą w tej sprawie – wymaga oceny prawnej."
        ]
        if result.verification.temporal_applicability.status != VS.VERIFIED:
            unresolved.append(
                "Czy brzmienie obowiązuje w żądanym dniu – nie potwierdzono automatycznie."
            )
        sources = [result.eurlex_url] + [c.eurlex_url for c in result.consolidated_versions[-1:]]
        return CitationVerificationResult(
            parsed=parsed,
            resolution_method=f"akt UE {result.celex} (EUR-Lex / Urząd Publikacji UE)",
            eu_provision=result,
            verification=result.verification,
            supporting_sources=[s for s in sources if s],
            warnings=[],
            unresolved_questions=unresolved,
        )


_AUTHENTIC = CheckResult(
    status=VS.VERIFIED,
    detail="Dane pobrane z oficjalnego serwisu Urzędu Publikacji UE (publications.europa.eu, "
    "źródło EUR-Lex) przez HTTPS.",
)
