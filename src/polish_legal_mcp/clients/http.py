"""Shared upstream HTTP client: retries, Retry-After, size limits, safe paths, TTL cache."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import (
    InvalidInputError,
    UpstreamBadResponseError,
    UpstreamNotFoundError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 10.0


class TTLCache:
    """Small in-process LRU cache with per-entry TTL."""

    # ponytail: no per-key locking; concurrent misses may fetch the same document twice.

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        self._data[key] = (time.monotonic() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class UpstreamClient:
    """GET-only client bound to one official API base URL."""

    def __init__(
        self,
        name: str,
        base_url: str,
        settings: Settings,
        *,
        backoff_base: float = 0.5,
    ) -> None:
        self.name = name
        self._settings = settings
        self._backoff_base = backoff_base
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        body = await self._get(path, params, accept="application/json")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise UpstreamBadResponseError(
                f"{self.name}: odpowiedź nie jest poprawnym JSON-em."
            ) from exc

    async def get_text(self, path: str, accept: str = "text/html") -> str:
        body = await self._get(path, None, accept=accept)
        return body.decode("utf-8", errors="replace")

    async def _get(self, path: str, params: Mapping[str, str | int] | None, accept: str) -> bytes:
        # Paths are built from validated identifiers only; refuse anything URL-like.
        if not path.startswith("/") or "://" in path or ".." in path or "\\" in path:
            raise InvalidInputError("Niedozwolona ścieżka zapytania do źródła.")
        retries = self._settings.http_max_retries
        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                async with self._client.stream(
                    "GET", path, params=params, headers={"Accept": accept}
                ) as response:
                    status = response.status_code
                    logger.info(
                        "upstream request",
                        extra={
                            "fields": {
                                "upstream": self.name,
                                "path": path,
                                "status": status,
                                "attempt": attempt + 1,
                                "ms": round((time.monotonic() - started) * 1000),
                            }
                        },
                    )
                    if status in RETRYABLE_STATUSES:
                        delay = _retry_after_seconds(response)
                        if delay is None:
                            delay = self._backoff_base * 2**attempt
                        if attempt < retries and delay <= MAX_RETRY_AFTER_SECONDS:
                            await asyncio.sleep(delay)
                            continue
                        raise UpstreamUnavailableError(
                            f"{self.name}: źródło chwilowo niedostępne (HTTP {status})."
                        )
                    if status == 404:
                        raise UpstreamNotFoundError(
                            f"{self.name}: brak zasobu (HTTP 404). Nie dowodzi to, że akt lub "
                            "orzeczenie nie istnieje w innych źródłach lub wersjach."
                        )
                    if status >= 400:
                        raise UpstreamBadResponseError(
                            f"{self.name}: źródło odrzuciło zapytanie (HTTP {status})."
                        )
                    return await self._read_limited(response)
            except httpx.TransportError as exc:
                if attempt < retries:
                    await asyncio.sleep(self._backoff_base * 2**attempt)
                    continue
                raise UpstreamUnavailableError(
                    f"{self.name}: brak połączenia lub przekroczony czas odpowiedzi."
                ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _read_limited(self, response: httpx.Response) -> bytes:
        limit = self._settings.http_max_response_bytes
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise UpstreamBadResponseError(f"{self.name}: odpowiedź przekracza limit rozmiaru.")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise UpstreamBadResponseError(f"{self.name}: odpowiedź przekracza limit rozmiaru.")
            chunks.append(chunk)
        return b"".join(chunks)
