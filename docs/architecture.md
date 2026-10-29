# Architecture

```
Claude (web / desktop / mobile)
   │  HTTPS, OAuth 2.1 (DCR/CIMD, PKCE)        ← Anthropic infrastructure 160.79.104.0/21
   ▼
Tailscale Funnel (TLS) on the Raspberry Pi  →  127.0.0.1:8000
   ▼
uvicorn ─ Starlette app (FastMCP http_app)
   ├─ HostOriginGuard (allowed: base_url host, loopback)
   ├─ GitHubProvider / OAuthProxy  (/authorize, /token, /register, /.well-known/*, /auth/callback)
   ├─ /health                       (public, no upstream calls)
   └─ /mcp  Streamable HTTP, stateless
        ├─ AuthMiddleware (GitHub login allowlist)
        ├─ RateLimitingMiddleware (per user)
        └─ tools (server.py) ─► services ─► clients ─► official APIs only
                                 legal.py      eli.py   api.sejm.gov.pl/eli
                                 judgments.py  saos.py  www.saos.org.pl/api
                                 eu_law.py     eurlex.py publications.europa.eu (EUR-Lex)
```

| Module | Responsibility |
|---|---|
| `config.py` | Env settings (`PLMCP_*`), fail-closed production validation. |
| `clients/http.py` | Shared `httpx.AsyncClient` per upstream: timeouts, pooling, retry with backoff on 429/5xx/transport errors honouring `Retry-After` (≤10 s), size limit, path guard, TTL+LRU cache. |
| `clients/eli.py`, `clients/saos.py` | Thin typed wrappers, caching of metadata/struct/HTML/keywords/judgments. |
| `parsers/legal_html.py` | Struct-based unit resolution and HTML wording extraction (lxml). |
| `parsers/entry_into_force.py` | Staged entry-into-force detection, Polish date parsing. |
| `parsers/citations.py` | Citation parsing, verified code aliases. |
| `services/legal.py` | Version selection, verification rules, citations, amendments, search, lexical legal basis. |
| `clients/eurlex.py`, `parsers/eu_html.py`, `services/eu_law.py` | EU law: CELLAR documents (redirects only within the host) and SPARQL metadata, OJ/consolidated article extraction, version choice, corrigenda/amendment checks. |
| `services/judgments.py` | SAOS search/details normalisation, HTML stripping, paging, excerpts. |
| `server.py` | FastMCP server, Polish tool descriptions, auth, middleware, health. |
| `main.py` | Entry point: logging, uvicorn with proxy headers and graceful shutdown. |

## Decisions (ADR summary)

1. **FastMCP 4 + Streamable HTTP, stateless.** Required by Claude remote connectors; stateless
   mode lets any request be served alone (no sticky sessions behind Funnel).
2. **Struct + full HTML instead of the fragment endpoint.** The fragment endpoint is ambiguous in
   consolidated texts and silent on missing units (see api-research.md). lxml parses a 0.8 MB act
   in ~10 ms.
3. **Component-level verification, no global boolean.** See legal-verification.md.
4. **No PDF parsing** (user decision). PDF-only texts return links and `UNVERIFIED`.
5. **No database, Redis, vector store or LLM.** In-memory TTL caches (metadata/struct/HTML 1 h,
   keyword vocabulary 24 h) bounded for a Raspberry Pi. OAuth registrations live in FastMCP's
   encrypted file store on a Docker volume.
6. **GitHub OAuth with login allowlist** (user decision). The only authenticated option that
   works for an individual Claude custom connector, free, built into FastMCP.
7. **Deployment: Raspberry Pi 4/5 (arm64) + Tailscale Funnel** (user decision). No port
   forwarding, works behind CGNAT, home IP hidden, automatic TLS.
8. **`httpx` for upstreams** although FastMCP uses `httpx2`: respx mocking and the spec require it.
9. **`find_legal_basis` implemented as lexical matching**, clearly labelled, instead of being
   deferred; it never claims semantic relevance.

## Extension points

New official sources follow the same pattern: a client in `clients/` built on `UpstreamClient`
(fixed base URL, validated identifiers only), a service returning Pydantic models with explicit
provenance and warnings, and a thin tool in `server.py` with a Polish description. No source
interface/ABC is introduced until a second source of the same kind exists.
