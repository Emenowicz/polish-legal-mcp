"""Client for the EU Publications Office (CELLAR), the official source behind EUR-Lex.

Documents: https://publications.europa.eu/resource/celex/{CELEX} (content negotiation, Polish).
Metadata: the public SPARQL endpoint https://publications.europa.eu/webapi/rdf/sparql.
"""

from __future__ import annotations

import re
from typing import Any

from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import InvalidInputError, UpstreamBadResponseError

CELLAR = "https://publications.europa.eu"
EURLEX_PAGE = "https://eur-lex.europa.eu/legal-content/PL/TXT/?uri=CELEX:"
# Sector 3 (secondary legislation): year + type (R regulation, L directive, D decision) + number;
# sector 0 = EUR-Lex consolidated version of such an act, suffixed with its date (-YYYYMMDD).
CELEX_RE = re.compile(r"^3\d{4}[RLD]\d{4}$")
CONSOLIDATED_RE = re.compile(r"^0\d{4}[RLD]\d{4}-\d{8}$")
# Sector 6 (case law): C = Court of Justice, T = General Court; J judgment, O order, C opinion
# of the Advocate General. Pseudo-CELEX numbers such as 62018CJ0311_RES are excluded.
CASE_LAW_RE = re.compile(r"^6\d{4}[CT][JOC]\d{4}$")
# Case number as the Court prints it: "C-311/18", "T-604/18", old cases "6/64".
CASE_NUMBER_RE = re.compile(r"^(?:([CT])\s*-\s*)?(\d{1,4})/(\d{2})$", re.IGNORECASE)
ECLI_RE = re.compile(r"^ECLI:EU:[CT]:\d{4}:\d{1,6}$")
_STRING = "^^<http://www.w3.org/2001/XMLSchema#string>"
_POL = "<http://publications.europa.eu/resource/authority/language/POL>"
_PREFIX = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX ann: <http://publications.europa.eu/ontology/annotation#>\n"
)
_INTERPRETS = "cdm:case-law_interpretes_resource_legal"


def validate_celex(
    celex: str, *, allow_consolidated: bool = False, allow_case_law: bool = False
) -> str:
    value = celex.strip().upper()
    if allow_consolidated and CONSOLIDATED_RE.match(value):
        return value
    if allow_case_law and CASE_LAW_RE.match(value):
        return value
    if not CELEX_RE.match(value):
        raise InvalidInputError(
            f"Nieprawidłowy numer CELEX: {celex!r} (oczekiwano np. 32016R0679)."
        )
    return value


class EurLexClient:
    def __init__(self, settings: Settings, *, backoff_base: float = 0.5) -> None:
        self._http = UpstreamClient(
            "EUR-Lex (Urząd Publikacji UE)",
            CELLAR,
            settings,
            backoff_base=backoff_base,
            follow_redirects=True,
        )
        self._documents = TTLCache(settings.cache_ttl_seconds, settings.cache_max_documents)
        self._metadata = TTLCache(settings.cache_ttl_seconds, 256)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def document(self, celex: str) -> str:
        """Polish XHTML: the act as published in the OJ, a consolidated version (0…-date) or a
        CJEU document (6…)."""
        celex = validate_celex(celex, allow_consolidated=True, allow_case_law=True)
        return await self._documents.get_or_load(
            celex,
            lambda: self._http.get_text(
                f"/resource/celex/{celex}",
                accept="application/xhtml+xml;q=1, text/html;q=0.9",
                headers={"Accept-Language": "pol"},
            ),
        )

    async def _select(self, query: str) -> list[dict[str, str]]:
        data = await self._http.get_json(
            "/webapi/rdf/sparql",
            {"query": query, "format": "application/sparql-results+json"},
            accept="application/sparql-results+json",
        )
        try:
            rows = data["results"]["bindings"]
            return [{k: v["value"] for k, v in row.items()} for row in rows]
        except (KeyError, TypeError) as exc:
            raise UpstreamBadResponseError(
                "EUR-Lex: nieoczekiwany format odpowiedzi SPARQL."
            ) from exc

    async def metadata(self, celex: str) -> dict[str, Any] | None:
        """Polish title, dates, in-force flag, ELI, consolidated versions and Polish corrigenda.

        Returns None when the Publications Office knows no such CELEX number.
        """
        celex = validate_celex(celex)
        return await self._metadata.get_or_load(celex, lambda: self._load_metadata(celex))

    async def _load_metadata(self, celex: str) -> dict[str, Any] | None:
        work = f'?w cdm:resource_legal_id_celex "{celex}"{_STRING} .'
        prefix = "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        facts = await self._select(
            prefix
            + "# plmcp:metadata\nSELECT ?p ?o WHERE { "
            + work
            + " ?w ?p ?o . FILTER(isLiteral(?o)) FILTER(STRSTARTS(STR(?p), "
            '"http://publications.europa.eu/ontology/cdm#resource_legal_") || '
            'STR(?p) = "http://publications.europa.eu/ontology/cdm#work_date_document") }'
        )
        if not facts:
            return None
        titles = await self._select(
            prefix
            + "# plmcp:title\nSELECT ?t WHERE { "
            + work
            + f" ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {_POL} ;"
            " cdm:expression_title ?t . } LIMIT 1"
        )
        consolidated = await self._select(
            prefix
            + "# plmcp:consolidated\nSELECT ?celex WHERE { ?c cdm:resource_legal_id_celex ?celex ."
            f' FILTER(STRSTARTS(STR(?celex), "0{celex[1:]}-")) }} ORDER BY ?celex'
        )
        corrigenda = await self._select(
            prefix
            + "# plmcp:corrigenda\nSELECT ?celex ?date WHERE { ?c cdm:resource_legal_id_celex ?celex ."
            f' FILTER(STRSTARTS(STR(?celex), "{celex}R")) ?e cdm:expression_belongs_to_work ?c ;'
            f" cdm:expression_uses_language {_POL} . OPTIONAL {{ ?c cdm:work_date_document ?date }} }}"
            " ORDER BY ?date"
        )
        amendments = await self._select(
            prefix
            + "# plmcp:amendments\nSELECT DISTINCT ?celex ?force WHERE { "
            + work
            + " ?a cdm:resource_legal_amends_resource_legal ?w . ?a cdm:resource_legal_id_celex"
            " ?celex . OPTIONAL { ?a cdm:resource_legal_date_entry-into-force ?force } }"
        )
        values: dict[str, list[str]] = {}
        for row in facts:
            key = row["p"].rsplit("#", 1)[-1]
            values.setdefault(key, []).append(row["o"])
        result = {
            "celex": celex,
            "facts": values,
            "title": titles[0]["t"] if titles else None,
            "consolidated": [row["celex"] for row in consolidated],
            "corrigenda": [(row["celex"], row.get("date")) for row in corrigenda],
            "amendments": [(row["celex"], row.get("force")) for row in amendments],
        }
        return result

    async def search_titles(
        self, terms: list[str], act_type: str | None = None
    ) -> list[dict[str, str]]:
        """Acts (sector 3) whose Polish title contains all terms (Virtuoso full text, no ranking).

        terms: letters only (validated here); a trailing '*' is a prefix match of ≥4 letters.
        """
        if not terms or any(
            not re.fullmatch(r"[^\W\d_]{4,30}\*?|[^\W\d_]{2,30}", t) for t in terms
        ):
            raise InvalidInputError("Nieprawidłowe słowa wyszukiwania.")
        if act_type not in (None, "R", "L", "D"):
            raise InvalidInputError("Nieprawidłowy rodzaj aktu UE.")
        expression = " AND ".join(f"'{t}'" for t in terms)
        kind = act_type or "RLD"
        return await self._select(
            _PREFIX + "# plmcp:title_search " + "-".join(t.rstrip("*") for t in terms) + "\n"
            "SELECT DISTINCT ?celex ?title ?date ?force WHERE {"
            f" ?e cdm:expression_title ?title ; cdm:expression_uses_language {_POL} ;"
            " cdm:expression_belongs_to_work ?w ."
            f' ?title bif:contains "{expression}" .'
            " ?w cdm:resource_legal_id_celex ?celex ."
            " OPTIONAL { ?w cdm:work_date_document ?date }"
            " OPTIONAL { ?w cdm:resource_legal_in-force ?force }"
            f' FILTER(REGEX(STR(?celex), "^3[0-9]{{4}}[{kind}][0-9]{{4}}$")) }}'
            " ORDER BY DESC(?date) LIMIT 300"
        )

    # ------------------------------------------------------------------ case law (CJEU)

    async def case_law_celex(
        self, case_number: str | None = None, ecli: str | None = None
    ) -> list[str]:
        """CELEX numbers of the documents of a case ("C-311/18") or of an ECLI.

        Both inputs are validated by regex before they reach the query.
        """
        if ecli:
            if not ECLI_RE.match(ecli):
                raise InvalidInputError(f"Nieprawidłowy ECLI: {ecli!r} (np. ECLI:EU:C:2020:559).")
            rows = await self._select(
                _PREFIX
                + f"# plmcp:case_ecli {ecli.replace(':', '-')}\nSELECT DISTINCT ?celex WHERE {{"
                f' ?w cdm:case-law_ecli "{ecli}"{_STRING} ; cdm:resource_legal_id_celex ?celex . }}'
            )
        else:
            match = CASE_NUMBER_RE.match((case_number or "").strip())
            if not match:
                raise InvalidInputError(
                    f"Nieprawidłowa sygnatura sprawy: {case_number!r} (np. C-311/18, T-604/18)."
                )
            court, number, year = match.groups()
            label = f"{court.upper()}-{int(number)}/{year}" if court else f"{int(number)}/{year}"
            rows = await self._select(
                _PREFIX + f"# plmcp:case_number {label.replace('/', '-')}\n"
                "SELECT DISTINCT ?celex WHERE { ?e cdm:expression_case-law_identifier_case"
                f' "Sprawa {label}"{_STRING} ; cdm:expression_belongs_to_work ?w .'
                " ?w cdm:resource_legal_id_celex ?celex . }"
            )
            if not rows:
                # Joined cases ("Sprawy połączone C-313/23 i C-316/23") and appeals ("… P"):
                # full-text match on the Polish identifier (~0.8 s instead of ~0.3 s).
                rows = await self._select(
                    _PREFIX + f"# plmcp:case_text {label.replace('/', '-')}\n"
                    "SELECT DISTINCT ?celex WHERE { ?e cdm:expression_case-law_identifier_case ?c ;"
                    f" cdm:expression_uses_language {_POL} ; cdm:expression_belongs_to_work ?w ."
                    f" ?c bif:contains '\"{label}\"' . ?w cdm:resource_legal_id_celex ?celex . }}"
                )
        return sorted({r["celex"] for r in rows if CASE_LAW_RE.match(r["celex"])})

    async def case_law_metadata(self, celex: str) -> dict[str, Any] | None:
        """Date, ECLI, Polish title and case identifier, acts interpreted (with locations)."""
        celex = validate_celex(celex, allow_case_law=True)
        return await self._metadata.get_or_load(f"case:{celex}", lambda: self._load_case(celex))

    async def _load_case(self, celex: str) -> dict[str, Any] | None:
        work = f'?w cdm:resource_legal_id_celex "{celex}"{_STRING} .'
        facts = await self._select(
            _PREFIX + f"# plmcp:case_meta {celex}\nSELECT ?date ?ecli ?case ?title WHERE {{ {work}"
            " OPTIONAL { ?w cdm:work_date_document ?date } OPTIONAL { ?w cdm:case-law_ecli ?ecli }"
            f" OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {_POL} ."
            " OPTIONAL { ?e cdm:expression_case-law_identifier_case ?case }"
            " OPTIONAL { ?e cdm:expression_title ?title } } } LIMIT 5"
        )
        if not facts:
            return None
        interpreted = await self._select(
            _PREFIX + f"# plmcp:case_acts {celex}\nSELECT ?act (GROUP_CONCAT(DISTINCT ?loc;"
            f' separator=" ") AS ?locs) WHERE {{ {work} ?w {_INTERPRETS} ?a .'
            " ?a cdm:resource_legal_id_celex ?act . OPTIONAL { ?ax owl:annotatedSource ?w ;"
            f" owl:annotatedProperty {_INTERPRETS} ; owl:annotatedTarget ?a ;"
            " ann:reference_to_modified_location ?loc . } } GROUP BY ?act ORDER BY ?act"
        )
        first = facts[0]
        return {
            "celex": celex,
            "date": first.get("date"),
            "ecli": first.get("ecli"),
            "case": next((f["case"] for f in facts if f.get("case")), None),
            "title": next((f["title"] for f in facts if f.get("title")), None),
            "interpreted": [(r["act"], r.get("locs", "").split()) for r in interpreted],
        }

    async def case_law_for_act(
        self, celex: str, location: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """CJEU/General Court decisions that interpret an act, newest first (CELLAR metadata).

        location: annotation code of an article, e.g. "A17" (article 17) or "A04" (article 4).
        """
        celex = validate_celex(celex)
        if location is not None and not re.match(r"^A\d{2,3}$", location):
            raise InvalidInputError("Nieprawidłowe oznaczenie artykułu.")
        loc_filter = (
            f' FILTER(?loc = "{location}" || STRSTARTS(?loc, "{location}P") ||'
            f' STRSTARTS(?loc, "{location}L"))'
            if location
            else ""
        )
        annotation = (
            "?ax owl:annotatedSource ?j ; owl:annotatedProperty "
            f"{_INTERPRETS} ; owl:annotatedTarget ?act ; ann:reference_to_modified_location ?loc ."
        )
        located = f"{annotation}{loc_filter}" if location else f"OPTIONAL {{ {annotation} }}"
        rows = await self._select(
            _PREFIX
            + f"# plmcp:case_for_act {celex}-{location or 'all'}\nSELECT ?celex ?date ?ecli ?case ?title"
            ' (GROUP_CONCAT(DISTINCT ?loc; separator=" ") AS ?locs) WHERE {'
            f' ?act cdm:resource_legal_id_celex "{celex}"{_STRING} .'
            f" ?j {_INTERPRETS} ?act ; cdm:resource_legal_id_celex ?celex ."
            ' FILTER(REGEX(STR(?celex), "^6[0-9]{4}[CT][JO][0-9]{4}$"))'
            f" {located}"
            " OPTIONAL { ?j cdm:work_date_document ?date } OPTIONAL { ?j cdm:case-law_ecli ?ecli }"
            f" OPTIONAL {{ ?e cdm:expression_belongs_to_work ?j ; cdm:expression_uses_language {_POL} ."
            " OPTIONAL { ?e cdm:expression_case-law_identifier_case ?case }"
            " OPTIONAL { ?e cdm:expression_title ?title } } }"
            f" GROUP BY ?celex ?date ?ecli ?case ?title ORDER BY DESC(?date) LIMIT {int(limit) * 2}"
        )
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:  # one row per Polish title variant; keep the first per CELEX
            seen.setdefault(row["celex"], {**row, "locs": row.get("locs", "").split()})
        return list(seen.values())[:limit]
