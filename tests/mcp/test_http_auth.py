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


async def test_requests_are_access_logged_without_query(secured_app, caplog):
    with caplog.at_level("INFO", logger="polish_legal_mcp.access"):
        async with client_for(secured_app) as http:
            await http.get("/authorize?code=secret-code&state=x")
            await http.get("/health", headers={"Host": "evil.example"})
    records = [r for r in caplog.records if r.name == "polish_legal_mcp.access"]
    assert [r.fields["path"] for r in records] == ["/authorize", "/health"]  # type: ignore[attr-defined]
    assert records[1].fields["status"] == 421  # type: ignore[attr-defined]
    assert all("secret-code" not in str(r.__dict__) for r in records)


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
        allowed_emails="Anna@example.com",
    )


def test_google_allowlist_requires_verified_email():
    check = allowlist_check(google_settings())
    # tokeninfo returns "true" as a string, userinfo v2 a boolean
    assert check(_ctx(email="anna@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert check(_ctx(email="ANNA@example.com", email_verified=True))  # type: ignore[arg-type]
    assert not check(_ctx(email="anna@example.com", email_verified="false"))  # type: ignore[arg-type]
    assert not check(_ctx(email="other@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert not check(_ctx(login="anna@example.com"))  # type: ignore[arg-type]


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
    assert response.text != "Forbidden Origin"


@pytest.mark.parametrize("make", [prod_settings, google_settings])
async def test_cimd_advertised_as_claude_requires(make, tmp_path, monkeypatch):
    # Claude uses CIMD only if metadata has both of these; otherwise it falls back to DCR.
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    async with client_for(build_app(make())) as http:
        asm = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert asm["client_id_metadata_document_supported"] is True
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert "S256" in asm["code_challenge_methods_supported"]


def test_denied_identity_logged_once(caplog):
    check = allowlist_check(google_settings())
    with caplog.at_level("WARNING", logger="polish_legal_mcp.server"):
        for _ in range(3):
            check(_ctx(email="me@example.com", email_verified="true"))  # type: ignore[arg-type]
    denied = [r for r in caplog.records if "not on allowlist" in r.getMessage()]
    assert len(denied) == 1
    assert denied[0].fields["identity"] == "google:me@example.com"  # type: ignore[attr-defined]


def test_google_workspace_domain_requires_matching_hd():
    settings = make_settings(
        **{
            **google_settings().model_dump(),
            "allowed_emails": [],
            "allowed_google_domains": ["Firm.pl"],
        }
    )
    check = allowlist_check(settings)

    def ctx(email, hd=None):
        return _ctx(email=email, email_verified="true", google_user_data={"hd": hd} if hd else {})

    assert check(ctx("anna@firm.pl", "firm.pl"))  # type: ignore[arg-type]
    # Personal Google account registered with a company address: no hd.
    assert not check(ctx("anna@firm.pl"))  # type: ignore[arg-type]
    # Managed account of another domain, or hd not matching the e-mail.
    assert not check(ctx("anna@other.pl", "other.pl"))  # type: ignore[arg-type]
    assert not check(ctx("anna@other.pl", "firm.pl"))  # type: ignore[arg-type]
    unverified = _ctx(
        email="anna@firm.pl", email_verified="false", google_user_data={"hd": "firm.pl"}
    )
    assert not check(unverified)  # type: ignore[arg-type]


def test_google_mode_requires_emails_or_domains():
    base = {**google_settings().model_dump(), "allowed_emails": []}
    with pytest.raises(ValueError, match="allowed_emails or allowed_google_domains"):
        make_settings(**base)
    assert make_settings(**{**base, "allowed_google_domains": ["firm.pl"]}).allowed_google_domains


def test_gmail_dots_are_ignored_but_other_domains_are_exact():
    settings = make_settings(
        **{
            **google_settings().model_dump(),
            "allowed_emails": ["jandoe@gmail.com", "x.y@example.com"],
        }
    )
    check = allowlist_check(settings)
    assert check(_ctx(email="jan.doe@gmail.com", email_verified="true"))  # type: ignore[arg-type]
    assert check(_ctx(email="Jan.Doe@googlemail.com", email_verified="true"))  # type: ignore[arg-type]
    assert check(_ctx(email="x.y@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert not check(_ctx(email="xy@example.com", email_verified="true"))  # type: ignore[arg-type]
    assert not check(_ctx(email="jan.doe2@gmail.com", email_verified="true"))  # type: ignore[arg-type]
