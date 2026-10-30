"""Client for the Sejm API – legislative processes (https://api.sejm.gov.pl/sejm)."""

from __future__ import annotations

from typing import Any

from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import InvalidInputError, UpstreamBadResponseError

SEJM_API = "https://api.sejm.gov.pl/sejm"
PAGE = 1000
MAX_PAGES = 5
# Only what the tools use; the full list is ~0.9 MB per 1000 processes.
_LIST_FIELDS = ("number", "title", "documentType", "processStartDate", "passed", "closureDate")


class SejmClient:
    def __init__(self, settings: Settings, *, backoff_base: float = 0.5) -> None:
        self._http = UpstreamClient("API Sejmu", SEJM_API, settings, backoff_base=backoff_base)
        self._cache = TTLCache(settings.cache_ttl_seconds, 64)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def current_term(self) -> int:
        async def load() -> int:
            data = await self._http.get_json("/term")
            current = [t for t in data if isinstance(t, dict) and t.get("current")]
            if not current or not isinstance(current[0].get("num"), int):
                raise UpstreamBadResponseError("API Sejmu: nie ustalono bieżącej kadencji.")
            return current[0]["num"]

        return await self._cache.get_or_load("term", load)

    async def processes(self, term: int) -> list[dict[str, Any]]:
        """All legislative processes of a term (trimmed to the fields we use)."""

        async def load() -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            for page in range(MAX_PAGES):
                data = await self._http.get_json(
                    f"/term{int(term)}/processes", {"limit": PAGE, "offset": page * PAGE}
                )
                if not isinstance(data, list):
                    raise UpstreamBadResponseError(
                        "API Sejmu: nieoczekiwany format listy procesów."
                    )
                items.extend({k: p.get(k) for k in _LIST_FIELDS} for p in data)
                if len(data) < PAGE:
                    break
            return items

        return await self._cache.get_or_load(f"processes:{int(term)}", load)

    async def process(self, term: int, number: str) -> dict[str, Any]:
        if not number.isdigit():
            raise InvalidInputError("Numer procesu legislacyjnego musi być liczbą.")

        async def load() -> dict[str, Any]:
            data = await self._http.get_json(f"/term{int(term)}/processes/{int(number)}")
            if not isinstance(data, dict) or "title" not in data:
                raise UpstreamBadResponseError("API Sejmu: nieoczekiwany format procesu.")
            return data

        return await self._cache.get_or_load(f"process:{int(term)}:{int(number)}", load)
