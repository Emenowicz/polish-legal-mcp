"""Client for SAOS (System Analizy Orzeczeń Sądowych, https://www.saos.org.pl/api)."""

from __future__ import annotations

from typing import Any

from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import InvalidInputError, UpstreamBadResponseError

SAOS_API = "https://www.saos.org.pl/api"


class SaosClient:
    def __init__(self, settings: Settings, *, backoff_base: float = 0.5) -> None:
        self._http = UpstreamClient("SAOS", SAOS_API, settings, backoff_base=backoff_base)
        self._judgments = TTLCache(settings.cache_ttl_seconds, settings.cache_max_documents)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, params: dict[str, str | int]) -> dict[str, Any]:
        data = await self._http.get_json("/search/judgments", params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamBadResponseError("SAOS: nieoczekiwany format wyników wyszukiwania.")
        return data

    async def judgment(self, judgment_id: int) -> dict[str, Any]:
        if judgment_id <= 0:
            raise InvalidInputError("Identyfikator orzeczenia musi być dodatnią liczbą całkowitą.")

        async def load() -> dict[str, Any]:
            data = await self._http.get_json(f"/judgments/{judgment_id}")
            if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
                raise UpstreamBadResponseError("SAOS: nieoczekiwany format orzeczenia.")
            return data["data"]

        return await self._judgments.get_or_load(str(judgment_id), load)
