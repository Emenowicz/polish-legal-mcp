"""Client for the UODO decisions portal API (https://orzeczenia.uodo.gov.pl/api-doc/).

Search filters are path segments (`index:operator:value`, joined with '/'), so every condition
is built by the service from validated words, dates or a signature and checked again here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import InvalidInputError, UpstreamBadResponseError

UODO_API = "https://orzeczenia.uodo.gov.pl/api"
UODO_DOCUMENT_PAGE = "https://orzeczenia.uodo.gov.pl/document/"
SEARCH = "/documents/search/PublicDocument/,"  # "," = any publication date
SEARCH_FIELDS = "refid,refname,name,title,dates"
DECISION_REFID_RE = re.compile(r"^urn:ndoc:gov:pl:uodo:\d{4}:[a-z0-9_]{3,40}$")
COURT_REFID_RE = re.compile(r"^urn:ndoc:court:[a-z]{2}:[a-z]{2,6}:\d{4}:[a-z0-9_-]{2,40}$")
# Field, operator, value without path, query or fragment characters.
_CONDITION_RE = re.compile(r"^[a-z_]{2,30}:(eq|ge|le|glob|in):[^/?#\\%]{1,400}$")


def _check(conditions: list[str]) -> None:
    for condition in conditions:
        if not _CONDITION_RE.match(condition) or ".." in condition:
            raise InvalidInputError("Niedozwolony warunek wyszukiwania w portalu UODO.")


class UodoClient:
    def __init__(self, settings: Settings, *, backoff_base: float = 0.5) -> None:
        self._http = UpstreamClient(
            "Portal Orzeczeń UODO", UODO_API, settings, backoff_base=backoff_base
        )
        self._documents = TTLCache(
            settings.document_cache_ttl_seconds, settings.cache_max_documents
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _search(
        self, conditions: list[str], offset: int, count: int, fields: str
    ) -> list[dict[str, Any]]:
        _check(conditions)
        data = await self._http.get_json(
            f"{SEARCH}/{'/'.join(conditions)}",
            {"from": offset, "count": count, "order": "-date_announcement", "fields": fields},
            log_path=SEARCH,  # the conditions carry the user's search words
        )
        if not isinstance(data, list) or not all(isinstance(i, dict) for i in data):
            raise UpstreamBadResponseError("Portal UODO: nieoczekiwany format wyników.")
        return data

    async def search_decisions(
        self, conditions: list[str], offset: int, count: int
    ) -> list[dict[str, Any]]:
        """Decisions of the President of UODO (the index also holds acts and court rulings)."""
        return await self._search(
            ["publicator_subtype:eq:uodo", *conditions], offset, count, SEARCH_FIELDS
        )

    async def names(self, refids: list[str]) -> dict[str, str]:
        """Signatures of documents in the portal index (e.g. court rulings named in a decision's
        status history) by refid; unknown ids are left out."""
        wanted = [r for r in refids if COURT_REFID_RE.match(r) or DECISION_REFID_RE.match(r)]
        if not wanted:
            return {}
        items = await self._search(
            [f"refid:in:{','.join(wanted)}"], 0, len(wanted), "refid,refname,name"
        )
        return {
            str(i["refid"]): str(i["refname"])
            for i in items
            if i.get("refid") in wanted and i.get("refname")
        }

    async def decision(self, refid: str) -> tuple[dict[str, Any], str, datetime]:
        """(metadata, body HTML, when they were fetched) of one decision."""
        if not DECISION_REFID_RE.match(refid):
            raise InvalidInputError(
                "Identyfikator decyzji UODO musi mieć postać "
                "'urn:ndoc:gov:pl:uodo:2026:dke_561_5' (albo podaj sygnaturę)."
            )

        async def load() -> tuple[dict[str, Any], str, datetime]:
            meta = await self._http.get_json(f"/documents/public/items/{refid}/meta.json")
            if not isinstance(meta, dict) or meta.get("refid") != refid:
                raise UpstreamBadResponseError("Portal UODO: nieoczekiwany format metadanych.")
            body = await self._http.get_text(f"/documents/public/items/{refid}:0/body.html")
            return meta, body, datetime.now(UTC)

        return await self._documents.get_or_load(refid, load)
