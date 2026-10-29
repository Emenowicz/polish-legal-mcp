"""HTTP transport, health check, OAuth discovery and allowlist (no real GitHub calls)."""

from types import SimpleNamespace

import fastmcp
import httpx
import pytest

from polish_legal_mcp.config import Settings
from polish_legal_mcp.server import allowlist_check, build_app
from tests.conftest import make_settings

BASE = "https://pi.example.ts.net"


def prod_settings() -> Settings:
    return make_settings(
        env="production",
        auth_mode="github",
        base_url=BASE,
        github_client_id="Ov23test",
        github_client_secret="test-secret",
        jwt_signing_key="k" * 48,
        allowed_github_logins=["Emenowicz"],
    )


@pytest.fixture
def secured_app(tmp_path, monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    return build_app(prod_settings())


def client_for(app, host: str = "pi.example.ts.net") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{host}")


async def test_health_is_public():
    async with client_for(build_app(make_settings()), host="127.0.0.1") as http:
        response = await http.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


async def test_unauthenticated_mcp_request_gets_401_with_metadata_pointer(secured_app):
    async with client_for(secured_app) as http:
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer") and "resource_metadata=" in challenge


async def test_protected_resource_metadata(secured_app):
    async with client_for(secured_app) as http:
        prm = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        asm = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert prm["resource"] == f"{BASE}/mcp"
    assert prm["authorization_servers"][0].rstrip("/") == BASE
    assert "S256" in asm["code_challenge_methods_supported"]
    assert asm.get("registration_endpoint")  # DCR for Claude custom connectors


async def test_foreign_host_header_rejected(secured_app):
    # ASGITransport derives scope["server"] from the URL, so spoof only the Host header.
    async with client_for(secured_app) as http:
        response = await http.get("/health", headers={"Host": "evil.example"})
    assert response.status_code == 421


def test_allowlist_check_is_case_insensitive_and_denies_others():
    check = allowlist_check(["Emenowicz"])
    token = lambda login: SimpleNamespace(token=SimpleNamespace(claims={"login": login}))  # noqa: E731
    assert check(token("emenowicz"))  # type: ignore[arg-type]
    assert not check(token("someone-else"))  # type: ignore[arg-type]
    assert not check(SimpleNamespace(token=None))  # type: ignore[arg-type]
