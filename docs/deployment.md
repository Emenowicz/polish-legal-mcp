# Deployment

Three modes: local development, local Docker, and the production target — a Raspberry Pi 4/5
(64-bit OS) at home, published on the internet with **Tailscale Funnel**.

## 1. Local development
```bash
brew install uv            # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run polish-legal-mcp    # http://127.0.0.1:8000/mcp, no auth (development)
```
Inspect with the FastMCP client or MCP Inspector (`npx @modelcontextprotocol/inspector`), URL
`http://127.0.0.1:8000/mcp`, transport "Streamable HTTP".

## 2. Local Docker
```bash
cp .env.example .env       # keep PLMCP_ENV=development for a local test
docker compose up -d --build
curl -fsS http://127.0.0.1:8000/health
```

## 3. Raspberry Pi + Tailscale Funnel (production)

### 3.1 Pi preparation
1. Raspberry Pi OS **64-bit** (Bookworm or newer). Check: `uname -m` → `aarch64`.
2. Docker: `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER` (re-login).
3. Clone: `git clone https://github.com/Emenowicz/polish-legal-mcp.git && cd polish-legal-mcp`.

### 3.2 Tailscale Funnel
1. `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
2. In the Tailscale admin console: enable **MagicDNS** and **HTTPS certificates** (DNS page), and
   make sure the tailnet policy grants the `funnel` node attribute (new tailnets have it; the CLI
   prints a link to enable it otherwise).
3. Note the Pi's public name: `tailscale status --json | jq -r .Self.DNSName` →
   e.g. `pi.tail1234.ts.net.` → `BASE_URL=https://pi.tail1234.ts.net`.

### 3.3 GitHub OAuth App
GitHub → Settings → Developer settings → OAuth Apps → *New OAuth App*:
- Homepage URL: `https://pi.tail1234.ts.net`
- Authorization callback URL: `https://pi.tail1234.ts.net/auth/callback`

Generate a client secret.

### 3.4 Configuration
```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT signing key
```
`.env`:
```
PLMCP_ENV=production
PLMCP_AUTH_MODE=github
PLMCP_BASE_URL=https://pi.tail1234.ts.net
PLMCP_GITHUB_CLIENT_ID=Ov23...
PLMCP_GITHUB_CLIENT_SECRET=...
PLMCP_JWT_SIGNING_KEY=<48+ random chars>
PLMCP_ALLOWED_GITHUB_LOGINS=Emenowicz
```
`chmod 600 .env`.

### 3.5 Start
```bash
docker compose up -d --build          # builds natively for arm64 on the Pi
docker compose ps                     # STATUS: healthy
sudo tailscale funnel --bg 8000       # https://pi.tail1234.ts.net -> http://127.0.0.1:8000
tailscale funnel status
```
`restart: unless-stopped` and the systemd-managed `tailscaled` bring both back after a reboot
(`funnel --bg` configuration persists). Update: `git pull && docker compose up -d --build`.
Stop publishing: `sudo tailscale funnel --https=443 off`.

### 3.6 Verification (run from outside your LAN, e.g. a phone hotspot)
```bash
BASE=https://pi.tail1234.ts.net
curl -fsS $BASE/health                                        # {"status":"ok",...}
curl -si -X POST $BASE/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -iE '^HTTP|www-authenticate'
# -> HTTP/1.1 401 ... WWW-Authenticate: Bearer ... resource_metadata="$BASE/.well-known/oauth-protected-resource/mcp"
curl -fsS $BASE/.well-known/oauth-protected-resource/mcp      # "resource": "$BASE/mcp"
curl -fsS $BASE/.well-known/oauth-authorization-server | jq '.registration_endpoint, .code_challenge_methods_supported'
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example' $BASE/health   # 421 (Funnel may already reject)
```
Authenticated end-to-end check (opens a browser for GitHub sign-in):
```bash
uv run python - <<'PY'
import asyncio
from fastmcp import Client
async def main():
    async with Client("https://pi.tail1234.ts.net/mcp", auth="oauth") as c:
        print([t.name for t in await c.list_tools()])
        r = await c.call_tool("get_legal_provision", {"act_id": "DU/1960/168", "article": "61", "paragraph": "1"})
        print(r.structured_content["verification"]["temporal_applicability"])
asyncio.run(main())
PY
```

### 3.7 Local MCP verification (development mode, no auth)
```bash
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream -H MCP-Protocol-Version:2025-06-18'
curl -s -X POST localhost:8000/mcp $H -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
curl -s -X POST localhost:8000/mcp $H -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
curl -s -X POST localhost:8000/mcp $H -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_legal_provision","arguments":{"act_id":"DU/1960/168","article":"61","paragraph":"1"}}}'
# error handling: invalid argument -> result with isError: true
curl -s -X POST localhost:8000/mcp $H -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_legal_provision","arguments":{"act_id":"DU/1960/168","article":"drop"}}}'
```

## 4. Claude connector
1. claude.ai → **Settings → Connectors → Add custom connector** (Free plan: one custom connector).
2. Name: `Prawo PL (ELI/SAOS)`, URL: `https://pi.tail1234.ts.net/mcp`. Leave OAuth client
   ID/secret empty (Claude registers itself via DCR/CIMD).
3. **Connect** → FastMCP consent page → GitHub sign-in (account on the allowlist) → back to Claude.
4. In a chat, enable the connector in the tools menu and ask, e.g.
   *"Znajdź ustawę o gospodarce nieruchomościami."* Claude should call `search_legal_acts`.
5. Mobile (Android/iOS): connectors added on claude.ai are available in the Claude app on the same
   account; open a chat, enable the connector in the tools menu, repeat the prompts.

Details: <https://claude.com/docs/connectors/custom/add-unlisted.md>. Remote connectors are called
from Anthropic's infrastructure, so the server must be reachable from the internet — a LAN-only
server will not work, even from a phone on the same Wi-Fi.

## Appendix: VPS instead of a Pi
Same image and `.env`; replace Funnel with Caddy for TLS:
```
# /etc/caddy/Caddyfile
mcp.example.pl {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```
Set `PLMCP_BASE_URL=https://mcp.example.pl` and the GitHub callback accordingly; open only
ports 80/443 in the firewall.
