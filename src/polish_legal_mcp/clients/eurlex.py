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
_STRING = "^^<http://www.w3.org/2001/XMLSchema#string>"
_POL = "<http://publications.europa.eu/resource/authority/language/POL>"


def validate_celex(celex: str, *, allow_consolidated: bool = False) -> str:
    value = celex.strip().upper()
    if allow_consolidated and CONSOLIDATED_RE.match(value):
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
        """Polish XHTML: the act as published in the OJ, or a consolidated version (0…-date)."""
        celex = validate_celex(celex, allow_consolidated=True)
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
