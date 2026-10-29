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


def _ctx(**claims):
    return SimpleNamespace(token=SimpleNamespace(claims=claims) if claims else None)


def test_github_allowlist_is_case_insensitive_and_denies_others():
    check = allowlist_check(prod_settings())
    assert check(_ctx(login="emenowicz"))  # type: ignore[arg-type]
    assert not check(_ctx(login="someone-else"))  # type: ignore[arg-type]
    assert not check(_ctx())  # type: ignore[arg-type]


def google_settings() -> Settings:
    return make_settings(
        env="production",
        auth_mode="google",
        base_url=BASE,
        google_client_id="123.apps.googleusercontent.com",
        google_client_secret="test-secret",
        jwt_signing_key="k" * 48,
        allowed_emails="Angela@example.com",
    )


def test_google_allowlist_requires_verified_email():
    check = allowlist_check(google_settings())
    # tokeninfo returns "true" as a string, userinfo v2 a boolean
    assert check(_ctx(email="angela@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert check(_ctx(email="ANGELA@example.com", email_verified=True))  # type: ignore[arg-type]
    assert not check(_ctx(email="angela@example.com", email_verified="false"))  # type: ignore[arg-type]
    assert not check(_ctx(email="other@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert not check(_ctx(login="angela@example.com"))  # type: ignore[arg-type]


async def test_google_mode_serves_oauth_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    app = build_app(google_settings())
    async with client_for(app) as http:
        response = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        prm = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert response.status_code == 401
    assert prm["resource"] == f"{BASE}/mcp"


async def test_consent_post_behind_tls_proxy_is_not_forbidden_origin(secured_app):
    # Funnel terminates TLS and forwards plain http, while the browser sends the https Origin.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured_app), base_url="http://pi.example.ts.net"
    ) as http:
        response = await http.post("/consent", data={"action": "approve"}, headers={"Origin": BASE})
        foreign = await http.post(
            "/consent", data={"action": "approve"}, headers={"Origin": "https://evil.example"}
        )
    assert response.text != "Forbidden Origin"
    assert foreign.status_code == 403


@pytest.mark.parametrize("make", [prod_settings, google_settings])
async def test_cimd_advertised_as_claude_requires(make, tmp_path, monkeypatch):
    # Claude uses CIMD only if metadata has both of these; otherwise it falls back to DCR.
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    async with client_for(build_app(make())) as http:
        asm = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert asm["client_id_metadata_document_supported"] is True
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert "S256" in asm["code_challenge_methods_supported"]
