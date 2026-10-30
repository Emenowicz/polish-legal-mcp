"""Client for the official ELI API of the Sejm (https://api.sejm.gov.pl/eli)."""

from __future__ import annotations

import asyncio
from typing import Any

from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import UpstreamBadResponseError
from polish_legal_mcp.models import ELI_API, ActId
from polish_legal_mcp.parsers.pdf_text import PdfText, read_pdf

KEYWORDS_TTL_SECONDS = 24 * 3600


class EliClient:
    def __init__(self, settings: Settings, *, backoff_base: float = 0.5) -> None:
        self._http = UpstreamClient(
            "ELI (api.sejm.gov.pl)", ELI_API, settings, backoff_base=backoff_base
        )
        documents = settings.document_cache_ttl_seconds
        self._meta = TTLCache(settings.cache_ttl_seconds, 512)
        self._struct = TTLCache(documents, settings.cache_max_documents)
        # Act HTML is up to ~3 MB (Civil Code), hence the small bound (Raspberry Pi sized).
        self._html = TTLCache(documents, settings.cache_max_documents)
        self._keywords = TTLCache(KEYWORDS_TTL_SECONDS, 1)
        # Parsed PDF texts (Civil Code: ~2 MB in memory); parsing takes seconds on a Pi.
        self._pdf = TTLCache(documents, 8)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, params: dict[str, str | int]) -> dict[str, Any]:
        data = await self._http.get_json("/acts/search", params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamBadResponseError("ELI: nieoczekiwany format wyników wyszukiwania.")
        return data

    async def act(self, act_id: ActId) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            data = await self._http.get_json(act_id.api_path)
            if not isinstance(data, dict) or "title" not in data:
                raise UpstreamBadResponseError("ELI: nieoczekiwany format metadanych aktu.")
            return data

        return await self._meta.get_or_load(act_id.eli, load)

    async def struct(self, act_id: ActId) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self._http.get_json(f"{act_id.api_path}/struct")
            if not isinstance(data, list):
                raise UpstreamBadResponseError("ELI: nieoczekiwany format struktury aktu.")
            return data

        return await self._struct.get_or_load(act_id.eli, load)

    async def html(self, act_id: ActId) -> str:
        return await self._html.get_or_load(
            act_id.eli, lambda: self._http.get_text(f"{act_id.api_path}/text.html")
        )

    async def pdf_text(self, act_id: ActId) -> PdfText:
        """Official PDF of the act, read into lines (in a worker thread: CPU-bound)."""

        async def load() -> PdfText:
            data = await self._http.get_bytes(
                f"{act_id.api_path}/text.pdf", accept="application/pdf"
            )
            try:
                return await asyncio.to_thread(read_pdf, data)
            except Exception as exc:  # pypdf raises many types on damaged files
                raise UpstreamBadResponseError(
                    f"ELI: nie udało się odczytać PDF {act_id.eli}."
                ) from exc

        return await self._pdf.get_or_load(act_id.eli, load)

    async def keywords(self) -> list[str]:
        async def load() -> list[str]:
            data = await self._http.get_json("/keywords")
            if not isinstance(data, list):
                raise UpstreamBadResponseError("ELI: nieoczekiwany format słownika haseł.")
            return [str(w) for w in data]

        return await self._keywords.get_or_load("all", load)
