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


async def test_prewarm_loads_configured_codes_in_production_only(monkeypatch):
    from polish_legal_mcp import server
    from tests.conftest import make_settings

    called: list[str] = []

    class FakeLegal:
        async def get_provision(self, act_id, article):
            called.append(act_id.eli)
            if act_id.eli == "DU/1964/296":
                raise RuntimeError("upstream down")  # one failure must not stop the others

    monkeypatch.setattr(server, "PREWARM_DELAY_SECONDS", 0)
    prod = make_settings(
        **{
            **prod_settings().model_dump(),
            "prewarm_acts": ["DU/1964/93", "DU/1964/296", "DU/1960/168"],
        }
    )
    async with server.prewarm(FakeLegal(), prod):  # type: ignore[arg-type]
        for _ in range(50):
            if len(called) == 3:
                break
            await asyncio.sleep(0.01)
    assert called == ["DU/1964/93", "DU/1964/296", "DU/1960/168"]

    called.clear()
    async with server.prewarm(FakeLegal(), make_settings()):  # type: ignore[arg-type]
        await asyncio.sleep(0.02)
    assert called == []
