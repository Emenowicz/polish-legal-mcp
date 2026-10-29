# polish-legal-mcp

Free, open-source **remote MCP server** that lets Claude (web, desktop, mobile) and other MCP
clients retrieve, inspect and cite **official Polish legislation** (ELI API of the Sejm —
Dziennik Ustaw, Monitor Polski) and **court decisions** (SAOS), with explicit, component-level
verification of what could and could not be established.

> **Ważne / Important**
> - Serwer jest narzędziem pomocniczym, **nie** źródłem opinii prawnych. Każde twierdzenie bez
>   statusu `VERIFIED` należy traktować jako niezweryfikowane.
> - **Nie wklejaj do publicznych usług AI informacji poufnych, niejawnych, wewnętrznych dokumentów
>   urzędu, danych osobowych ani opisów spraw zawierających takie dane.**
> - Projekt nie jest oficjalnym narzędziem żadnego organu ani instytucji publicznej.

## What it does

Main use case: helping public-administration employees prepare applications, letters and legal justifications with **correct, sourced citations**:

> „Przygotowuję wniosek dotyczący zakwaterowania żołnierza. Znajdź odpowiednie przepisy, podaj
> ich aktualne brzmienie, wskaż artykuły i przygotuj propozycję uzasadnienia."

Claude does the language work; this server supplies authoritative source material, links and
verification results. It never uses an extra LLM and never fabricates acts, articles, dates or
case numbers.

## MCP tools

| Tool | Purpose |
|---|---|
| `search_legal_acts` | Search acts by title fragments / ELI keyword; base acts ranked before amending acts. |
| `get_legal_act` | Metadata, status, dates, text formats, consolidated texts, paged table of contents. |
| `get_legal_provision` | Exact wording of art./ust./§/pkt/lit. from official HTML, citation, later amendments, verification report. |
| `get_legal_act_amendments` | Amending acts with ELI dates, pending amendments, amendments after the latest consolidated text, TK rulings. |
| `verify_legal_citation` | Parse a citation ("art. 61 § 1 k.p.a.", "… (Dz. U. z 2025 r. poz. 817)"), resolve the act, check unit, wording and date. |
| `find_legal_basis` | Lexical candidate discovery (ELI keyword vocabulary + title stems). Not semantic, not advice. |
| `search_judgments` | SAOS search (common courts, Supreme Court, Constitutional Tribunal, KIO) with paging. |
| `get_judgment` | Judgment metadata, legal bases, referenced regulations, paged text, phrase excerpts. |

Tool names are English; descriptions and messages are Polish. All outputs are typed (structured
content) and bounded.

### Verification statuses
`VERIFIED`, `UNVERIFIED`, `NOT_FOUND`, `NOT_APPLICABLE`, `SOURCE_UNAVAILABLE` — reported
separately for source authenticity, act existence, unit existence, exact wording, act in force on
the date, temporal applicability of the wording, amendment completeness (always `UNVERIFIED`)
and substantive relevance (always `UNVERIFIED`). Full semantics:
[docs/legal-verification.md](docs/legal-verification.md).

Example (real data, 2026-10-29): art. 61 § 1 k.p.a. → wording read from consolidated text
Dz. U. 2024 poz. 572 (`exact_wording: VERIFIED`), but the current consolidated text Dz. U. 2025
poz. 1691 exists only as PDF and an amendment followed → `temporal_applicability: UNVERIFIED`.

## Requirements
- Python 3.12+ (3.13 pinned in `.python-version`), [uv](https://docs.astral.sh/uv/)
- Docker (for container deployment); a Raspberry Pi 4/5 with 64-bit OS for the reference setup
- For remote use: Tailscale account (free) and a GitHub OAuth App (free)

## Quick start (local)
```bash
uv sync
uv run polish-legal-mcp              # http://127.0.0.1:8000/mcp (development, no auth)
curl -fsS http://127.0.0.1:8000/health
```

## Configuration
Environment variables with prefix `PLMCP_` (see [.env.example](.env.example)):

| Variable | Default | Notes |
|---|---|---|
| `PLMCP_ENV` | `development` | `production` requires GitHub OAuth or refuses to start. |
| `PLMCP_HOST` / `PLMCP_PORT` | `127.0.0.1` / `8000` | Docker image sets `0.0.0.0`. |
| `PLMCP_AUTH_MODE` | `none` | `github` for remote use. |
| `PLMCP_BASE_URL` | – | Public HTTPS URL without `/mcp`. |
| `PLMCP_GITHUB_CLIENT_ID` / `_SECRET` | – | GitHub OAuth App. |
| `PLMCP_JWT_SIGNING_KEY` | – | ≥32 random chars. |
| `PLMCP_ALLOWED_GITHUB_LOGINS` | – | Comma-separated allowlist. |
| `PLMCP_RATE_LIMIT_PER_SECOND` / `_BURST` | `2` / `20` | Per user. |
| `PLMCP_HTTP_TIMEOUT_SECONDS`, `PLMCP_HTTP_MAX_RETRIES` | `20`, `3` | Upstream calls. |
| `PLMCP_CACHE_TTL_SECONDS`, `PLMCP_CACHE_MAX_DOCUMENTS` | `3600`, `32` | In-memory cache. |

## Testing
```bash
uv run pytest                 # offline suite: real captured ELI/SAOS fixtures, mocked with respx
uv run pytest -m live         # optional smoke tests against the real APIs
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

## Docker
```bash
docker compose up -d --build   # loopback-only port, non-root, read-only FS, healthcheck
docker compose ps
```

## Remote deployment (Raspberry Pi + Tailscale Funnel)
Short version — full runbook with verification commands in [docs/deployment.md](docs/deployment.md):
1. Pi OS 64-bit, Docker, `sudo tailscale up`, enable MagicDNS + HTTPS + Funnel in the tailnet.
2. GitHub OAuth App with callback `https://<pi>.<tailnet>.ts.net/auth/callback`.
3. `.env` with `PLMCP_ENV=production`, GitHub credentials, JWT key, allowed logins.
4. `docker compose up -d --build && sudo tailscale funnel --bg 8000`.
5. From outside the LAN: `curl https://<pi>.<tailnet>.ts.net/health` and expect `401` on `/mcp`.

A VPS + Caddy variant is in the same document.

## Claude integration (incl. mobile)
1. claude.ai → Settings → Connectors → **Add custom connector** (the Free plan allows one).
2. URL `https://<pi>.<tailnet>.ts.net/mcp`; leave OAuth client ID/secret empty.
3. Connect → consent page → sign in with an allowlisted GitHub account.
4. Enable the connector in a chat and ask in Polish, e.g.:
   - „Znajdź ustawę o gospodarce nieruchomościami."
   - „Podaj artykuły dotyczące zakwaterowania żołnierzy."
   - „Sprawdź, czy art. 61 § 1 k.p.a. obowiązuje na dzień 29 października 2026 roku."
   - „Znajdź orzeczenia dotyczące gospodarowania nieruchomościami Skarbu Państwa."
   - „Zweryfikuj podstawę prawną: art. 61 § 1 k.p.a. i podaj link do oficjalnego źródła."
   - „Podaj propozycję uzasadnienia wniosku na podstawie znalezionych przepisów, wyraźnie oznaczając
     wszystkie nieweryfikowane twierdzenia."
5. Mobile: the connector is available in the Claude app (Android/iOS) on the same account; the
   server is reached through Anthropic's infrastructure, so it must be publicly reachable.

No Anthropic API key and no running Claude Code are needed after deployment. The server cannot
guarantee Claude's final wording — it supplies evidence; check the statuses.

## Security
HTTPS via Funnel, OAuth 2.1 (DCR/CIMD, PKCE) with GitHub sign-in and a login allowlist, fail-closed
production config, Host guard, per-user rate limits, bounded inputs/outputs, no arbitrary URL
fetching, no logging of tool arguments, hardened container. Details: [docs/security.md](docs/security.md).

## Known limitations
- ELI has **no HTML for acts published since 2025** → current consolidated texts are PDF-only;
  wording then comes from the newest HTML version and is marked `UNVERIFIED` for today's date.
- SAOS has **no administrative-court (WSA/NSA) judgments** and is not complete.
- Search is lexical (titles/keywords), not full-text or semantic.
- Amendment completeness, staged entry into force of amending acts and transitional provisions
  are not verified.

See [docs/limitations.md](docs/limitations.md).

## Roadmap
- Optional PDF text extraction for 2025+ consolidated texts (always marked `UNVERIFIED`).
- Additional official sources: Sejm API (legislative process), RCL (government drafts),
  GUGiK/Geoportal (parcels), GUS REGON (entities), administrative case law if an official API appears.
- Hybrid legal retrieval (RAG) only after evaluating completeness, licensing, update cadence and
  the risk of outdated versions.
- Drafting helpers for official letters with explicit verification warnings.

## Documentation
[architecture](docs/architecture.md) · [API research](docs/api-research.md) ·
[legal verification](docs/legal-verification.md) · [security](docs/security.md) ·
[deployment](docs/deployment.md) · [limitations](docs/limitations.md) ·
[progress](docs/implementation-progress.md)

## License
MIT — see [LICENSE](LICENSE). Legal texts and judgments come from public official sources
(api.sejm.gov.pl, saos.org.pl).
