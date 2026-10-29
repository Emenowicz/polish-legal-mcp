"""Unauthenticated disk-write bounds: body cap, endpoint rate limits, culling of the OAuth store."""

import asyncio
import json
from pathlib import Path

import fastmcp
import httpx
import pytest
from starlette.responses import PlainTextResponse

from polish_legal_mcp.security import RequestGuard, build_oauth_storage
from polish_legal_mcp.server import build_app
from tests.mcp.test_http_auth import client_for, prod_settings

REGISTRATION = {
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    "client_name": "test",
    "token_endpoint_auth_method": "none",
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    return tmp_path


async def test_oversized_registration_rejected_before_storage(home):
    app = build_app(prod_settings())
    body = json.dumps({**REGISTRATION, "client_name": "x" * 1_000_000})
    async with client_for(app) as http:
        response = await http.post(
            "/register", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413
    assert not list((home / "oauth-store").rglob("*.json"))


async def test_registrations_use_our_culled_store(home):
    app = build_app(prod_settings())
    async with client_for(app) as http:
        response = await http.post("/register", json=REGISTRATION)
    assert response.status_code == 201
    assert list((home / "oauth-store").rglob("*.json"))


async def test_chunked_body_without_length_is_bounded():
    async def ok(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    guard = RequestGuard(ok, max_body_bytes=1000)

    async def chunks():
        for _ in range(5):
            yield b"x" * 400

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://t"
    ) as http:
        too_big = await http.post("/mcp", content=chunks())
        small = await http.post("/mcp", content=b"x" * 10)
    assert too_big.status_code == 413
    assert small.status_code == 200


async def test_unauthenticated_endpoints_rate_limited():
    async def ok(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    guard = RequestGuard(ok, limits={("POST", "/register"): 3})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://t"
    ) as http:
        codes = [(await http.post("/register", content=b"{}")).status_code for _ in range(4)]
        other = await http.post("/mcp", content=b"{}")
    assert codes == [200, 200, 200, 429]
    assert other.status_code == 200


async def test_cull_removes_expired_entries(tmp_path):
    wrapped, store = build_oauth_storage(tmp_path, "k" * 48)
    await wrapped.put(key="short", value={"a": 1}, collection="tx", ttl=1)
    await wrapped.put(key="long", value={"a": 2}, collection="tx", ttl=3600)
    await asyncio.sleep(1.2)
    await store.cull()
    remaining = [p.name for p in (tmp_path / "oauth-store" / "tx").glob("*.json")]
    assert remaining == ["long.json"]
    assert await wrapped.get(key="long", collection="tx") == {"a": 2}
