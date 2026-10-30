"""Typed tool outputs. Field descriptions are Polish because MCP clients show them to the model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from polish_legal_mcp.errors import InvalidInputError

ELI_API = "https://api.sejm.gov.pl/eli"
ISAP_DETAILS = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id="
SAOS_PAGE = "https://www.saos.org.pl/judgments/"

_ELI_RE = re.compile(r"^\s*(DU|MP)\s*/\s*(\d{4})\s*/\s*(\d{1,5})\s*$", re.IGNORECASE)
_DISPLAY_RE = re.compile(
    r"^\s*(Dz\.?\s*U\.?|M\.?\s*P\.?)\s*(?:z\s*)?(\d{4})\s*(?:r\.?)?\s*(?:,?\s*nr\s*\d+\s*)?,?\s*"
    r"poz\.?\s*(\d{1,5})\s*\)?\s*$",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(r"^\s*W(DU|MP)(\d{4})(\d{3})(\d{4})\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ActId:
    """Identifier of an act in Dziennik Ustaw (DU) or Monitor Polski (MP)."""

    publisher: str
    year: int
    position: int

    def __post_init__(self) -> None:
        if self.publisher not in ("DU", "MP"):
            raise InvalidInputError(
                "Obsługiwani wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski)."
            )
        if not 1918 <= self.year <= 2100 or not 1 <= self.position <= 99999:
            raise InvalidInputError("Nieprawidłowy rok lub pozycja publikacji.")

    @classmethod
    def parse(cls, value: str) -> ActId:
        """Accept 'DU/1960/168', 'Dz.U. 1960 nr 30 poz. 168', 'Dz. U. z 2025 r. poz. 1691', 'WDU19600300168'."""
        for pattern in (_ELI_RE, _DISPLAY_RE):
            match = pattern.match(value)
            if match:
                pub = match.group(1).upper().replace(".", "").replace(" ", "")
                return cls(
                    "DU" if pub.startswith("DZ") or pub == "DU" else "MP",
                    int(match.group(2)),
                    int(match.group(3)),
                )
        match = _ADDRESS_RE.match(value)
        if match:
            return cls(match.group(1).upper(), int(match.group(2)), int(match.group(4)))
        raise InvalidInputError(
            f"Nie rozpoznano identyfikatora aktu: {value!r}. Użyj formatu ELI, np. 'DU/1960/168', "
            "lub adresu publikacji, np. 'Dz.U. 1960 nr 30 poz. 168'."
        )

    @property
    def eli(self) -> str:
        return f"{self.publisher}/{self.year}/{self.position}"

    @property
    def api_path(self) -> str:
        return f"/acts/{self.eli}"

    @property
    def api_url(self) -> str:
        return f"{ELI_API}{self.api_path}"


class VerificationStatus(StrEnum):
    """Meaning of each status is documented in docs/legal-verification.md."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class CheckResult(BaseModel):
    status: VerificationStatus
    detail: str = Field(description="Uzasadnienie statusu po polsku.")


class VerificationReport(BaseModel):
    """Wyniki cząstkowe. Nie ma jednego globalnego statusu 'zweryfikowano'."""

    source_authenticity: CheckResult = Field(description="Czy treść pochodzi z oficjalnego źródła.")
    act_existence: CheckResult = Field(description="Czy akt istnieje w oficjalnym źródle.")
    unit_existence: CheckResult = Field(
        description="Czy jednostka redakcyjna istnieje w pobranej wersji tekstu."
    )
    exact_wording: CheckResult = Field(description="Czy dosłowne brzmienie zostało odczytane.")
    act_in_force_on_date: CheckResult = Field(
        description="Czy akt (jako całość) obowiązywał w żądanym dniu wg metadanych ELI."
    )
    temporal_applicability: CheckResult = Field(
        description="Czy pobrane brzmienie przepisu jest brzmieniem obowiązującym w żądanym dniu."
    )
    amendment_completeness: CheckResult = Field(
        description="Czy lista zmian jest kompletna (serwer nie jest w stanie tego potwierdzić)."
    )
    substantive_relevance: CheckResult = Field(
        description="Czy przepis jest merytorycznie właściwy dla sprawy (nie jest oceniane)."
    )


class TextFormat(BaseModel):
    kind: str = Field(description="Kod typu tekstu z ELI (H, O, I, T, U).")
    label: str
    url: str
    official: bool = Field(description="False dla tekstu ujednoliconego (informacyjnego).")


class ActSummary(BaseModel):
    eli: str = Field(description="Identyfikator ELI, np. DU/1960/168.")
    title: str
    act_type: str | None = None
    display_address: str | None = Field(
        default=None, description="Np. 'Dz.U. 1960 nr 30 poz. 168'."
    )
    publisher: str
    year: int
    position: int
    volume: int | None = Field(default=None, description="Numer dziennika (dla aktów sprzed 2012).")
    status: str | None = Field(default=None, description="Status aktu wg ELI.")
    status_note: str | None = Field(default=None, description="Wyjaśnienie statusu.")
    in_force: str | None = Field(default=None, description="Pole inForce z ELI (stan na dziś).")
    announcement_date: date | None = None
    promulgation_date: date | None = None
    entry_into_force: date | None = None
    valid_from: date | None = None
    repeal_date: date | None = None
    expiration_date: date | None = None
    legal_status_date: date | None = Field(
        default=None, description="Dla tekstu jednolitego: data stanu prawnego."
    )
    has_html: bool = False
    has_pdf: bool = False
    is_amending_act: bool = Field(default=False, description="Ustawa/rozporządzenie zmieniające.")
    is_consolidated_text_notice: bool = Field(
        default=False, description="Obwieszczenie o tekście jednolitym."
    )
    keywords: list[str] = Field(default_factory=list)
    isap_url: str | None = Field(default=None, description="Strona aktu w ISAP (oficjalna).")
    api_url: str


class ActReference(BaseModel):
    eli: str
    relation: str = Field(description="Typ powiązania wg ELI, np. 'Akty zmieniające'.")
    effective_date: date | None = Field(
        default=None, description="Data z metadanych ELI (dla zmian: data wejścia w życie)."
    )
    article: str | None = None
    title: str | None = None
    api_url: str


class SearchActsResult(BaseModel):
    total_count: int = Field(description="Liczba trafień w ELI.")
    returned: int
    items: list[ActSummary]
    coverage_note: str
    warnings: list[str] = Field(default_factory=list)


class TocEntry(BaseModel):
    unit_id: str
    unit_type: str
    label: str
    depth: int


class LegalActDetails(BaseModel):
    act: ActSummary
    texts: list[TextFormat]
    consolidated_text_of: str | None = Field(
        default=None,
        description="Jeśli to obwieszczenie: ELI aktu, którego tekst jednolity dotyczy.",
    )
    consolidated_texts: list[str] = Field(
        default_factory=list, description="ELI obwieszczeń o tekstach jednolitych (od najnowszych)."
    )
    reference_counts: dict[str, int] = Field(default_factory=dict)
    toc: list[TocEntry] = Field(default_factory=list, description="Spis jednostek (stronicowany).")
    toc_total: int = 0
    toc_offset: int = 0
    toc_has_more: bool = False
    warnings: list[str] = Field(default_factory=list)


class SourceDocument(BaseModel):
    eli: str
    title: str
    kind: Literal["tekst_jednolity", "tekst_pierwotny"]
    state_date: date | None = Field(
        default=None,
        description="Data stanu prawnego tekstu (tekst jednolity) lub data wejścia w życie (tekst pierwotny).",
    )
    html_available: bool
    html_url: str | None = None
    pdf_url: str | None = None
    isap_url: str | None = None


class Citation(BaseModel):
    act_title: str
    act_type: str | None
    publisher: str
    year: int
    position: int
    publication_eli: str = Field(
        description="Publikacja użyta w cytacie (najnowszy t.j. lub tekst pierwotny)."
    )
    article: str
    paragraph: str | None = None
    paragraph_kind: Literal["ust.", "§"] | None = None
    point: str | None = None
    letter: str | None = None
    official_url: str
    source_version: str = Field(description="Wersja tekstu, z której pochodzi brzmienie.")
    as_of_date: date
    formatted: str | None = Field(
        default=None, description="Sformatowany cytat; brak, gdy brakuje metadanych do jego budowy."
    )


class ProvisionResult(BaseModel):
    requested_act_id: str
    base_act: ActSummary | None = None
    as_of_date: date
    unit_id: str | None = None
    structural_path: list[str] = Field(default_factory=list)
    text: str | None = Field(
        default=None, description="Brzmienie przepisu (bez przypisów); pochodzenie: text_origin."
    )
    text_truncated: bool = False
    text_origin: Literal["html", "html_confirmed_by_pdf", "pdf_automatic"] | None = Field(
        default=None,
        description="Skąd brzmienie: html – oficjalny HTML; html_confirmed_by_pdf – HTML zgodny z "
        "najnowszym tekstem jednolitym w PDF; pdf_automatic – odczyt automatyczny z PDF (sprawdź).",
    )
    pdf_page: int | None = Field(default=None, description="Strona PDF, na której jest jednostka.")
    earlier_wording: str | None = Field(
        default=None,
        description="Wcześniejsze brzmienie (HTML starszego tekstu jednolitego) – do porównania, "
        "gdy najnowszy tekst w PDF się od niego różni.",
    )
    earlier_wording_source: SourceDocument | None = None
    footnotes: list[str] = Field(
        default_factory=list, description="Przypisy redakcyjne do jednostki."
    )
    wording_source: SourceDocument | None = None
    latest_official_consolidated_text: SourceDocument | None = None
    transitional_provisions: list[str] = Field(
        default_factory=list,
        description="Rozdziały z przepisami przejściowymi aktu z zakresem artykułów – do sprawdzenia.",
    )
    changes_after_source: list[ActReference] = Field(
        default_factory=list,
        description="Zmiany/orzeczenia TK z datą między stanem tekstu a żądaną datą.",
    )
    citation: Citation | None = None
    verification: VerificationReport
    warnings: list[str] = Field(default_factory=list)


class AmendmentsResult(BaseModel):
    base_act: ActSummary
    as_of_date: date
    latest_consolidated_text: SourceDocument | None = None
    amendments: list[ActReference]
    amendments_total: int
    pending_on_date: list[str] = Field(
        default_factory=list, description="ELI zmian z datą późniejszą niż żądana data."
    )
    after_latest_consolidated_text: list[str] = Field(
        default_factory=list, description="ELI zmian wchodzących w życie po stanie najnowszego t.j."
    )
    constitutional_tribunal_rulings: list[ActReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ParsedCitation(BaseModel):
    raw: str
    article: str | None = None
    paragraph: str | None = None
    paragraph_kind: Literal["ust.", "§"] | None = None
    point: str | None = None
    letter: str | None = None
    act_text: str | None = Field(default=None, description="Fragment opisujący akt.")
    publication: str | None = Field(default=None, description="ELI wskazany w cytacie, jeśli był.")
    known_alias: str | None = Field(
        default=None, description="Rozpoznany skrót kodeksu, np. 'k.c.'."
    )


class EuDocumentLink(BaseModel):
    celex: str
    version_date: date | None = Field(
        default=None, description="Data wersji skonsolidowanej lub sprostowania."
    )
    eurlex_url: str


class EuActSummary(BaseModel):
    celex: str
    title: str
    act_date: date | None = None
    in_force: bool | None = Field(default=None, description="Według metadanych EUR-Lex.")
    legislative: bool = Field(
        description="Akt Parlamentu Europejskiego i Rady albo Rady (nie akt wykonawczy/delegowany "
        "Komisji)."
    )
    amending: bool = Field(description="Tytuł wskazuje akt zmieniający inny akt.")
    eurlex_url: str


class EuActSearchResult(BaseModel):
    query_terms: list[str]
    total_matched: int = Field(description="Trafienia w tytułach (pobrano maks. 300).")
    items: list[EuActSummary]
    warnings: list[str] = Field(default_factory=list)


class LegislativeProcess(BaseModel):
    number: str = Field(description="Numer procesu (druku) w Sejmie danej kadencji.")
    title: str
    document_type: str | None = None
    start_date: date | None = None
    current_stage: str | None = Field(
        default=None, description="Ostatni etap według API Sejmu, z datą i decyzją."
    )
    description: str | None = Field(default=None, description="Opis projektu z API Sejmu.")
    government_draft_url: str | None = Field(
        default=None, description="Projekt rządowy w RCL (legislacja.gov.pl), jeśli podany."
    )
    sejm_url: str
    api_url: str


class PendingBillsResult(BaseModel):
    term: int
    act_eli: str | None = None
    act_title: str | None = None
    matched_phrases: list[str] = Field(
        default_factory=list, description="Nazwy aktu szukane w tytułach projektów."
    )
    query: str | None = None
    pending_scanned: int = Field(description="Liczba projektów w toku przejrzanych w kadencji.")
    items: list[LegislativeProcess]
    warnings: list[str] = Field(default_factory=list)


class TranspositionMeasure(BaseModel):
    title: str | None = Field(default=None, description="Tytuł podany w zgłoszeniu do Komisji.")
    act_type: str | None = None
    journal: str | None = None
    publication: str | None = Field(default=None, description="Numer publikacji, np. '2016/1125'.")
    eli: str | None = Field(default=None, description="Identyfikator ELI (Dziennik Ustaw).")
    eli_title: str | None = Field(
        default=None, description="Oficjalny tytuł tej pozycji w ELI – do porównania z title."
    )
    eli_status: str | None = None
    cellar_ids: list[str] = Field(default_factory=list, description="CELEX zgłoszenia (sektor 7).")


class TranspositionResult(BaseModel):
    act_celex: str
    country: str = "PL"
    measures: list[TranspositionMeasure]
    total_measures: int
    eurlex_url: str = Field(description="Strona aktu UE w EUR-Lex (zakładka transpozycji).")
    warnings: list[str] = Field(default_factory=list)


class EuProvisionResult(BaseModel):
    requested_act: str
    celex: str | None = None
    title: str | None = Field(default=None, description="Tytuł aktu UE (wersja polska).")
    eli: str | None = Field(default=None, description="Identyfikator ELI aktu UE.")
    eurlex_url: str | None = Field(default=None, description="Strona aktu w EUR-Lex.")
    official_journal: str | None = Field(
        default=None,
        description="Publikacja w Dz. Urz. UE, np. 'Dz. Urz. UE L 119 z 4.5.2016, str. 1'.",
    )
    as_of_date: date
    structural_path: list[str] = Field(default_factory=list)
    text: str | None = Field(
        default=None,
        description="Brzmienie z tekstu opublikowanego w Dz. Urz. UE (wersja pierwotna, PL).",
    )
    text_truncated: bool = False
    consolidated_versions: list[EuDocumentLink] = Field(
        default_factory=list,
        description="Wersje skonsolidowane (EUR-Lex, charakter dokumentacyjny) – ślad zmian aktu.",
    )
    polish_corrigenda: list[EuDocumentLink] = Field(
        default_factory=list, description="Sprostowania dotyczące polskiej wersji językowej."
    )
    citation: str | None = Field(
        default=None, description="Sformatowany cytat; brak bez metadanych."
    )
    verification: VerificationReport
    warnings: list[str] = Field(default_factory=list)


class CitationVerificationResult(BaseModel):
    parsed: ParsedCitation
    resolution_method: str | None = None
    provision: ProvisionResult | None = None
    eu_provision: EuProvisionResult | None = Field(
        default=None, description="Wynik dla aktu prawa UE (zamiast provision)."
    )
    verification: VerificationReport
    supporting_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class LegalBasisCandidate(BaseModel):
    act: ActSummary
    matched_terms: list[str]
    match_method: str


class LegalBasisResult(BaseModel):
    issue_terms: list[str]
    matched_keywords: list[str] = Field(description="Hasła ze słownika ELI dopasowane leksykalnie.")
    candidates: list[LegalBasisCandidate]
    method_note: str
    warnings: list[str] = Field(default_factory=list)


class JudgmentSummary(BaseModel):
    judgment_id: int
    case_numbers: list[str]
    court_type: str | None = None
    court_name: str | None = None
    division: str | None = None
    judgment_date: date | None = None
    judgment_type: str | None = None
    excerpt: str | None = Field(default=None, description="Fragment z wyszukiwarki SAOS.")
    keywords: list[str] = Field(default_factory=list)
    citation: str | None = Field(
        default=None,
        description="Cytat z metadanych SAOS, np. 'wyrok Sądu Najwyższego z dnia 12 marca 2020 "
        "r., I CSK 123/19'; brak, gdy metadanych nie wystarcza.",
    )
    source_url: str
    api_url: str


class JudgmentSearchResult(BaseModel):
    total_results: int
    page: int
    page_size: int
    has_more: bool
    items: list[JudgmentSummary]
    coverage_note: str


class CjeuParagraph(BaseModel):
    number: int = Field(description="Numer punktu uzasadnienia (cytuj jako 'pkt N').")
    text: str


class CjeuInterpretedAct(BaseModel):
    celex: str
    provisions: list[str] = Field(
        default_factory=list,
        description="Przepisy wskazane w metadanych CELLAR, np. 'art. 17 ust. 1 lit. d'; "
        "lista bywa niepełna.",
    )
    eurlex_url: str


class CjeuJudgmentSummary(BaseModel):
    celex: str
    document_type: str = Field(
        description="wyrok, postanowienie albo opinia rzecznika generalnego."
    )
    court: str
    case_number: str | None = None
    decision_date: date | None = None
    ecli: str | None = None
    title: str | None = None
    parties: str | None = None
    provisions: list[str] = Field(
        default_factory=list, description="Interpretowane przepisy aktu (metadane CELLAR)."
    )
    eurlex_url: str


class CjeuSearchResult(BaseModel):
    act_celex: str
    article: str | None = None
    items: list[CjeuJudgmentSummary]
    warnings: list[str] = Field(default_factory=list)


class CjeuJudgmentResult(CjeuJudgmentSummary):
    language_of_case: str | None = Field(
        default=None,
        description="Język postępowania – jedyna autentyczna wersja; polska jest tłumaczeniem.",
    )
    subject: str | None = Field(default=None, description="Hasła/przedmiot sprawy z tytułu.")
    interpreted_acts: list[CjeuInterpretedAct] = Field(default_factory=list)
    paragraph_count: int = Field(description="Liczba numerowanych punktów uzasadnienia.")
    paragraphs: list[CjeuParagraph] = Field(default_factory=list)
    matches: list[CjeuParagraph] = Field(
        default_factory=list, description="Punkty zawierające szukaną frazę (fragmenty)."
    )
    operative_part: str | None = Field(default=None, description="Sentencja (rozstrzygnięcie).")
    other_documents: list[str] = Field(
        default_factory=list,
        description="Inne dokumenty tej sprawy (CELEX), np. opinia rzecznika generalnego (…CC…).",
    )
    citation: str | None = Field(
        default=None, description="Sformatowany cytat; brak bez metadanych."
    )
    source_url: str
    warnings: list[str] = Field(default_factory=list)


class ProvisionJudgment(JudgmentSummary):
    matched_references: list[str] = Field(
        description="Pozycje z listy przepisów powołanych w orzeczeniu (metadane SAOS), które "
        "odpowiadają szukanemu przepisowi, np. 'art. 5' albo 'art. 98 § 1'."
    )


class ProvisionJudgmentsResult(BaseModel):
    act_eli: str = Field(
        description="Akt bazowy (teksty jednolite są sprowadzane do aktu bazowego)."
    )
    act_title: str | None = None
    provision: str
    total_candidates: int = Field(
        description="Orzeczenia w SAOS powołujące ten akt i pasujące do numeru artykułu "
        "(przed sprawdzeniem metadanych każdego z nich)."
    )
    candidates_checked: int
    page: int
    has_more: bool = Field(description="Czy są kolejni kandydaci do sprawdzenia (page+1).")
    items: list[ProvisionJudgment]
    warnings: list[str] = Field(default_factory=list)
    coverage_note: str


class ReferencedRegulation(BaseModel):
    text: str
    journal_year: int | None = None
    journal_no: int | None = None
    journal_entry: int | None = None
    eli_candidate: str | None = Field(
        default=None, description="Przypuszczalny ELI (DU/rok/poz.) – niezweryfikowany."
    )


class JudgmentDetails(BaseModel):
    judgment_id: int
    case_numbers: list[str]
    court_type: str | None = None
    court_name: str | None = None
    division: str | None = None
    judgment_date: date | None = None
    judgment_type: str | None = None
    judges: list[str] = Field(default_factory=list)
    decision: str | None = None
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    legal_bases: list[str] = Field(default_factory=list)
    referenced_regulations: list[ReferencedRegulation] = Field(default_factory=list)
    text: str = Field(description="Fragment treści orzeczenia (sentencja i/lub uzasadnienie).")
    text_offset: int
    text_length_total: int
    has_more: bool
    excerpts: list[str] = Field(
        default_factory=list, description="Fragmenty zawierające szukaną frazę."
    )
    citation: str | None = Field(
        default=None, description="Cytat z metadanych SAOS; brak, gdy metadanych nie wystarcza."
    )
    source_url: str = Field(description="Strona orzeczenia w SAOS.")
    original_source_url: str | None = Field(
        default=None, description="Źródło pierwotne (portal sądu)."
    )
    warnings: list[str] = Field(default_factory=list)
