"""Upstream error handling (synthetic responses)."""

import asyncio

import httpx
import pytest
import respx

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.clients.http import TTLCache, UpstreamClient
from polish_legal_mcp.errors import (
    InvalidInputError,
    UpstreamBadResponseError,
    UpstreamNotFoundError,
    UpstreamUnavailableError,
)
from polish_legal_mcp.models import ActId
from tests.conftest import ELI, make_settings

URL = f"{ELI}/acts/DU/1960/168"


@pytest.fixture
async def client(settings):
    c = UpstreamClient("ELI", ELI, settings, backoff_base=0)
    yield c
    await c.aclose()


@respx.mock
async def test_retries_transient_5xx(client):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    assert await client.get_json("/acts/DU/1960/168") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
async def test_honours_retry_after(client):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=[]),
        ]
    )
    assert await client.get_json("/acts/DU/1960/168") == []
    assert route.call_count == 2


@respx.mock
async def test_long_retry_after_fails_fast(client):
    route = respx.get(URL).respond(429, headers={"Retry-After": "3600"})
    with pytest.raises(UpstreamUnavailableError):
        await client.get_json("/acts/DU/1960/168")
    assert route.call_count == 1


@respx.mock
async def test_timeout_after_retries(client, settings):
    route = respx.get(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(UpstreamUnavailableError):
        await client.get_json("/acts/DU/1960/168")
    assert route.call_count == settings.http_max_retries + 1


@respx.mock
async def test_404_is_not_retried(client):
    route = respx.get(URL).respond(404)
    with pytest.raises(UpstreamNotFoundError, match="Nie dowodzi"):
        await client.get_json("/acts/DU/1960/168")
    assert route.call_count == 1


@respx.mock
async def test_400_is_bad_response(client):
    respx.get(URL).respond(400)
    with pytest.raises(UpstreamBadResponseError):
        await client.get_json("/acts/DU/1960/168")


@respx.mock
async def test_malformed_json(client):
    respx.get(URL).respond(200, text="<html>not json")
    with pytest.raises(UpstreamBadResponseError):
        await client.get_json("/acts/DU/1960/168")


@respx.mock
async def test_response_size_limit():
    settings = make_settings(http_max_response_bytes=100_000)
    c = UpstreamClient("ELI", ELI, settings, backoff_base=0)
    respx.get(URL).respond(200, content=b"x" * 100_001)
    with pytest.raises(UpstreamBadResponseError, match="limit"):
        await c.get_text("/acts/DU/1960/168")
    await c.aclose()


@pytest.mark.parametrize("path", ["acts", "/../etc", "/acts/https://evil.example", "/a\\b"])
async def test_refuses_unsafe_paths(client, path):
    with pytest.raises(InvalidInputError):
        await client.get_json(path)


@respx.mock
async def test_metadata_is_cached(settings):
    eli = EliClient(settings, backoff_base=0)
    route = respx.get(URL).respond(json={"title": "x"})
    await eli.act(ActId("DU", 1960, 168))
    await eli.act(ActId("DU", 1960, 168))
    assert route.call_count == 1
    await eli.aclose()


@respx.mock
async def test_unexpected_shape_rejected(settings):
    eli = EliClient(settings, backoff_base=0)
    respx.get(URL).respond(json=["not", "an", "object"])
    with pytest.raises(UpstreamBadResponseError):
        await eli.act(ActId("DU", 1960, 168))
    await eli.aclose()


async def test_cache_loads_once_for_concurrent_callers_and_does_not_cache_failures():
    cache = TTLCache(60, 4)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "doc"

    assert await asyncio.gather(*(cache.get_or_load("k", load) for _ in range(5))) == ["doc"] * 5
    assert calls == 1

    async def fail():
        raise UpstreamUnavailableError("x")

    with pytest.raises(UpstreamUnavailableError):
        await cache.get_or_load("bad", fail)
    assert await cache.get_or_load("bad", load) == "doc"  # retried, not cached


async def test_cancelled_caller_does_not_cancel_shared_load():
    cache = TTLCache(60, 4)
    started = asyncio.Event()

    async def load():
        started.set()
        await asyncio.sleep(0.02)
        return "doc"

    first = asyncio.create_task(cache.get_or_load("k", load))
    await started.wait()
    second = asyncio.create_task(cache.get_or_load("k", load))
    first.cancel()
    assert await second == "doc"


@respx.mock
async def test_maintenance_page_is_unavailable_not_bad_response(client):
    # Real SAOS answer during maintenance on 2026-10-30 (200, text/html), shortened.
    page = "<html><head><title>Przerwa techniczna</title></head><body><h1>Przerwa techniczna</h1>"
    respx.get(URL).respond(200, text=page, headers={"Content-Type": "text/html"})
    with pytest.raises(UpstreamUnavailableError, match="przerwa techniczna"):
        await client.get_json("/acts/DU/1960/168")
