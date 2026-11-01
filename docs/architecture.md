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
| `services/judgments.py` | SAOS search/details normalisation, HTML stripping, paging, excerpts; judgments citing a provision (confirmed per judgment); acts cited for an issue. |
| `parsers/cjeu_html.py`, `services/cjeu.py` | CJEU decisions from CELLAR: numbered paragraphs, operative part, language of the case, citation with ECLI; decisions interpreting an act/article (SPARQL annotations). |
| `clients/sejm.py`, `services/legislation.py` | API Sejmu: legislative processes of the current term, bills naming a given act, current stage. |
| `server.py` | FastMCP server, Polish tool descriptions, auth, middleware, health. |
| `main.py` | Entry point: logging, uvicorn with proxy headers and graceful shutdown. |

## Decisions (ADR summary)

1. **FastMCP 4 + Streamable HTTP, stateless.** Required by Claude remote connectors; stateless
   mode lets any request be served alone (no sticky sessions behind Funnel).
2. **Struct + full HTML instead of the fragment endpoint.** The fragment endpoint is ambiguous in
   consolidated texts and silent on missing units (see api-research.md). lxml parses a 0.8 MB act
   in ~10 ms.
3. **Component-level verification, no global boolean.** See legal-verification.md.
4. **PDF reading with cross-check** (user decision, revised 2026-10-29): newest consolidated texts
   are PDF-only since 2025; `pypdf` (pure Python) reads them, unchanged provisions are confirmed
   against the last HTML version, changed ones are returned as automatic reads (`UNVERIFIED`).
5. **No database server, Redis, vector store or LLM.** In-memory TTL caches (metadata/struct/HTML
   1 h, keyword vocabulary 24 h) bounded for a Raspberry Pi. OAuth registrations live in
   FastMCP's encrypted file store on a Docker volume. Exception: the act index SQLite file of
   decision 9.
6. **GitHub OAuth with login allowlist** (user decision). The only authenticated option that
   works for an individual Claude custom connector, free, built into FastMCP.
7. **Deployment: Raspberry Pi 4/5 (arm64) + Tailscale Funnel** (user decision). No port
   forwarding, works behind CGNAT, home IP hidden, automatic TLS.
8. **`httpx` for upstreams** although FastMCP uses `httpx2`: respx mocking and the spec require it.
9. **`find_legal_basis`: lexical matching plus an optional act index** (decision 2026-11-01).
   The index (`services/act_index.py`, `PLMCP_ACT_INDEX`) keeps titles + ELI keywords of
   statutes and regulations in force and the division/chapter headings of base statutes,
   ranked by BM25 in memory, and the article texts of base statutes (newest text with HTML) in
   SQLite FTS5 — one local file (`PLMCP_ACT_INDEX_PATH`) of public ELI data, refreshed
   incrementally (a statute is refetched only when ELI lists a new consolidated text for it).
   Ranking: the three best lexical hits stay on top, then title/heading and article hits are
   interleaved. Measured with `scripts/eval_legal_basis.py` on 38 queries
   (`tests/fixtures/legal_basis_eval.json`: 24 written by the developer, 14 real Q&A questions
   whose published answers name the act): recall@10 **8/38 lexical only → 29/38 with the
   index** (real questions 3/14 → 11/14), no query lost. Those 38 are all answered by statutes;
   10 further real questions answered by a regulation (matched by title only) gave 1/10 → 2/10:
   regulations remain the weak spot. File ~98 MB; first build 11–15 min,
   network-bound (~5,000 ELI requests), later refreshes ~1 min; Linux arm64 memory: +~150 MB
   peak during the first build (HTML parsed one at a time), +~50 MB once loaded. Measured on the same set and dropped: titles + keywords only
   (6–11/38), titles + headings without article texts (22/38), and a local embedding model
   (multilingual-e5-small, int8 ONNX, ~+230 MB RAM on Linux arm64): 23/38 over titles +
   headings and 28/38 on top of the article texts, i.e. no gain once provisions are searched.
   Article hits name the ELI text they come from, because consolidated texts since 2025 are
   PDF-only and the index reads HTML.

## Extension points

New official sources follow the same pattern: a client in `clients/` built on `UpstreamClient`
(fixed base URL, validated identifiers only), a service returning Pydantic models with explicit
provenance and warnings, and a thin tool in `server.py` with a Polish description. No source
interface/ABC is introduced until a second source of the same kind exists.
