# Security

## Threat model
The data served is public, but the endpoint must not become an open proxy to official APIs, and
users may paste sensitive descriptions into prompts. Controls:

| Control | Implementation |
|---|---|
| HTTPS | Tailscale Funnel terminates TLS (Let's Encrypt certificate for `*.ts.net`). The container publishes only `127.0.0.1:8000`. |
| Authentication | OAuth 2.1 via FastMCP `GoogleProvider` or `GitHubProvider` (OAuthProxy): CIMD (used by Claude and Claude Code) and DCR, PKCE S256, consent screen, RFC 9728 metadata. Scopes: Google `openid email`, GitHub `read:user`. |
| Authorisation | `AuthMiddleware` allowlist: Google e-mails that Google marks as verified (`PLMCP_ALLOWED_EMAILS`) or GitHub logins (`PLMCP_ALLOWED_GITHUB_LOGINS`), case-insensitive. Other accounts can sign in but see no tools and cannot call any. |
| Fail closed | `PLMCP_ENV=production` refuses to start (exit 2) unless GitHub OAuth, an `https://` base URL, a ≥32-char JWT signing key and a non-empty allowlist are configured. |
| DNS rebinding / Host spoofing | FastMCP Host/Origin guard in strict mode; allowed hosts = base URL host + loopback (+ `PLMCP_ALLOWED_HOSTS`), others get 421; allowed origin = the public base URL (TLS ends at the proxy, so same-origin matching alone would reject our own consent form), others get 403. Claude's backend sends no `Origin` header (verified with a live connector). |
| Rate limiting | Token bucket per authenticated user (`PLMCP_RATE_LIMIT_PER_SECOND`, `PLMCP_RATE_LIMIT_BURST`). |
| No arbitrary URLs | Upstream base URLs are constants; paths are built from validated identifiers (`ActId`, integer ids); paths containing `://`, `..` or `\` are rejected; redirects are not followed. |
| Input bounds | Pydantic constraints on every tool argument (lengths, patterns, ranges); citations ≤1000 chars, descriptions ≤2000. |
| Output bounds | Provision text ≤12 000 chars, judgment text paged (≤20 000 per call), TOC paged, search ≤50 items. |
| Upstream response bounds | Max 10 MB per response, timeouts, ≤3 retries, `Retry-After` honoured only up to 10 s. |
| Error hygiene | `mask_error_details=True`; domain errors become Polish `ToolError` messages; no stack traces to clients. |
| Logging | JSON logs; tool arguments, search phrases and upstream query strings are not logged (httpx loggers at WARNING). Upstream paths contain only public act identifiers. Uvicorn access logs are disabled because OAuth callback URLs carry authorization codes; our own request log records method, path (no query string), status and duration. A signed-in account that is not on the allowlist is logged once (e-mail/login) so an empty tool list can be diagnosed. |
| Container | Non-root uid 10001, read-only root FS, `cap_drop: ALL`, `no-new-privileges`, memory/CPU/PID limits, only `/data` (OAuth store) writable. |
| Secrets | Only via environment / `.env` (git-ignored). OAuth store is Fernet-encrypted with a key derived from the JWT signing key. |
| Supply chain | `uv.lock` pinned, `uv sync --locked` in CI and Docker, `pip-audit` in CI. |

## Local development
`PLMCP_ENV=development` (default) allows `PLMCP_AUTH_MODE=none` and binds `127.0.0.1`. Binding a
non-loopback interface without auth logs a warning; never expose such an instance.

## Data protection
The server only reads public official sources and stores nothing about users except OAuth client
registrations/tokens needed for sign-in (encrypted, on the `/data` volume). Prompts are processed
by Claude (Anthropic), not by this server; only tool arguments reach it and they are not logged.

**Do not paste confidential, classified or internal information of your institution, personal
data or case files into a public AI service.** This project is not an official tool of any public
authority or institution.

## Unauthenticated disk writes (OAuth)
OAuth dynamic client registration (`/register`) and authorization requests (`/authorize`) must
work before sign-in, and FastMCP persists both — registrations without expiry or size limit
(measured: a 1 MB `client_name` was stored as a 1.8 MB file), and it never deletes expired
entries. On a host shared with other services this could fill the disk. Mitigations:

| Layer | Control |
|---|---|
| Host (guarantee) | `/var/lib/polish-legal-mcp` is a dedicated 64 MiB ext4 loop filesystem (16k inodes, `nodev,nosuid,noexec`) created by `deploy/pi-setup.sh`; the unit has `RequiresMountsFor=` so it never writes to the root filesystem. Worst case: sign-in stops working; other services are unaffected. |
| App | Request bodies over 64 KiB → 413; global hourly caps: `POST /register` 20, `GET /authorize` 60 → 429 (`security.RequestGuard`). Claude uses CIMD (clients cached in memory, not on disk) and does not call `/register`. |
| App | OAuth state uses our own encrypted file store, culled hourly so expired transactions/tokens are deleted (`security.periodic_cull`). |

Docker deployments get the app-level controls; the named volume is not size-capped — put it on
a size-limited filesystem if the host runs anything important.

## Known residual risks
- A sustained flood at the rate limits can make sign-in temporarily unavailable (limits reset
  hourly) — availability of this service only.
- Forwarded headers are not trusted (`proxy_headers=False`); behind Docker/Funnel the app sees a
  local IP. Nothing depends on the client IP — do not "fix" this by trusting `*`.

## Optional hardening
- Restrict Funnel exposure further with a Tailscale ACL or front it with Cloudflare + an IP
  allowlist for Anthropic's egress range `160.79.104.0/21` (OAuth sign-in in the browser must
  still reach the server, so allow the users' IPs as well).
- Rotate `PLMCP_JWT_SIGNING_KEY` to invalidate all issued tokens (clients re-authorise).
