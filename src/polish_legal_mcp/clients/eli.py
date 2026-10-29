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
        ttl = settings.cache_ttl_seconds
        self._meta = TTLCache(ttl, 512)
        self._struct = TTLCache(ttl, settings.cache_max_documents)
        # Act HTML is up to ~1 MB, hence the small bound (Raspberry Pi sized).
        self._html = TTLCache(ttl, settings.cache_max_documents)
        self._keywords = TTLCache(KEYWORDS_TTL_SECONDS, 1)
        # Parsed PDF texts (a large code is ~0.5 MB of text); parsing takes seconds on a Pi.
        self._pdf = TTLCache(ttl, 8)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, params: dict[str, str | int]) -> dict[str, Any]:
        data = await self._http.get_json("/acts/search", params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamBadResponseError("ELI: nieoczekiwany format wyników wyszukiwania.")
        return data

    async def act(self, act_id: ActId) -> dict[str, Any]:
        cached = self._meta.get(act_id.eli)
        if cached is not None:
            return cached
        data = await self._http.get_json(act_id.api_path)
        if not isinstance(data, dict) or "title" not in data:
            raise UpstreamBadResponseError("ELI: nieoczekiwany format metadanych aktu.")
        self._meta.set(act_id.eli, data)
        return data

    async def struct(self, act_id: ActId) -> list[dict[str, Any]]:
        cached = self._struct.get(act_id.eli)
        if cached is not None:
            return cached
        data = await self._http.get_json(f"{act_id.api_path}/struct")
        if not isinstance(data, list):
            raise UpstreamBadResponseError("ELI: nieoczekiwany format struktury aktu.")
        self._struct.set(act_id.eli, data)
        return data

    async def html(self, act_id: ActId) -> str:
        cached = self._html.get(act_id.eli)
        if cached is not None:
            return cached
        text = await self._http.get_text(f"{act_id.api_path}/text.html")
        self._html.set(act_id.eli, text)
        return text

    async def pdf_text(self, act_id: ActId) -> PdfText:
        """Official PDF of the act, read into lines (in a worker thread: CPU-bound)."""
        cached = self._pdf.get(act_id.eli)
        if cached is not None:
            return cached
        data = await self._http.get_bytes(f"{act_id.api_path}/text.pdf", accept="application/pdf")
        try:
            parsed = await asyncio.to_thread(read_pdf, data)
        except Exception as exc:  # pypdf raises many types on damaged files
            raise UpstreamBadResponseError(
                f"ELI: nie udało się odczytać PDF {act_id.eli}."
            ) from exc
        self._pdf.set(act_id.eli, parsed)
        return parsed

    async def keywords(self) -> list[str]:
        cached = self._keywords.get("all")
        if cached is not None:
            return cached
        data = await self._http.get_json("/keywords")
        if not isinstance(data, list):
            raise UpstreamBadResponseError("ELI: nieoczekiwany format słownika haseł.")
        words = [str(w) for w in data]
        self._keywords.set("all", words)
        return words
