# Implementation progress

Status legend: **DONE** (implemented and checked locally), **VERIFIED** (executed against the
real target), **NOT EXECUTED** (requires infrastructure/accounts not available to the implementer).

## Phase 0 — Research and feasibility — DONE
- [x] FastMCP 4.1.4 docs and installed source (auth, HTTP app, middleware, storage)
- [x] ELI OpenAPI and live probes (search, metadata, struct, HTML, fragments, statuses, coverage)
- [x] SAOS search/details live probes (filters, paging, court types)
- [x] Claude connector auth requirements (DCR/CIMD, 401 + PRM, callback, egress IPs)
- [x] Capability matrix, ADRs, risks → `api-research.md`, `architecture.md`, `limitations.md`

## Phase 1 — Foundation — DONE
- [x] uv project, pinned lockfile, Python 3.13
- [x] Settings with fail-closed production validation; JSON logging without query content
- [x] Shared upstream HTTP client (timeouts, pooling, retries, Retry-After, size limit, path guard, TTL cache)
- [x] FastMCP server, `/health`, Streamable HTTP (stateless) — curl initialize/tools/list/tools/call OK

## Phase 2 — ELI — DONE
- [x] Search (title/keyword/type/in-force), act details with paged TOC, text formats and links
- [x] Provision retrieval via struct + HTML; footnotes separated; repeal markers detected
- [x] Citation formatting from metadata only

## Phase 3 — SAOS — DONE
- [x] Search with court type/date filters and paging; judgment details with bounded, paged text,
      excerpts, legal bases and referenced regulations

## Phase 4 — Legal verification — DONE
- [x] Component-level `VerificationReport`, documented semantics
- [x] Version selection (consolidated texts, PDF-only handling), intervening amendments and TK rulings
- [x] Staged entry-into-force heuristics; future-date handling
- [x] `verify_legal_citation`, `get_legal_act_amendments`, lexical `find_legal_basis`
- [x] 7 legal regression cases + "never VERIFIED when state unknown" tests

## Phase 5 — Security and deployment — DONE / remote deployment VERIFIED (2026-10-29)
- [x] OAuth (OAuthProxy: GitHub or Google, CIMD/DCR, PKCE), allowlist, rate limiting, Host guard
- [x] Dockerfile (multi-stage, non-root, healthcheck), compose (loopback port, read-only, limits)
- [x] Container verified locally on arm64 (Apple Silicon): healthy, uid 10001, 401 with
      `resource_metadata`, PRM `resource` = `<base>/mcp`, foreign Host → 421
- [x] Raspberry Pi + Tailscale Funnel runbook (`deployment.md`)
- [x] Deployment on a Raspberry Pi 5 (Debian 13, arm64) — **VERIFIED**: Docker-free variant
      (`deploy/pi-setup.sh`, hardened systemd unit, dedicated `plmcp` user, env file root:root 600);
      service active/enabled, `/health` 200 on 127.0.0.1:8000
- [x] Tailscale Funnel public reachability — **VERIFIED** from outside the tailnet:
      `https://<host>.<tailnet>.ts.net/health` 200 (Let's Encrypt cert), `/mcp` → 401 with
      `resource_metadata`, PRM `resource` = `<base>/mcp`, AS metadata with `registration_endpoint`,
      CIMD and PKCE S256, foreign Host → 421. A newly enabled Funnel reset TLS handshakes for
      ~3 minutes before becoming stable.
- [x] Unauthenticated disk-write bounds (body cap, `/register`/`/authorize` rate limits, hourly
      cull of the OAuth store, 64 MiB state filesystem) — tested locally; Pi rollout pending re-run
      of `pi-setup.sh`
- [x] Google sign-in mode (allowlist by verified e-mail), installer `--reconfigure`; CIMD
      advertised and tested in both modes (Claude's consent page showed "Verified domain: claude.ai")
- [x] Fixed: consent form rejected as "Forbidden Origin" behind Funnel (https Origin vs http app)
- [x] Fixed: installer did not reinstall the app on source-only updates
- [x] Fixed: Gmail allowlist entries now ignore dots (Google returns the dotted address)
- [x] Real OAuth sign-in round trip — **VERIFIED** 2026-10-29 with Google sign-in via the Claude
      connector (CIMD), allowlisted account — **NOT EXECUTED** (pending the Claude connector sign-in)

## Phase 6 — Claude and mobile validation — VERIFIED
- [x] Claude custom connector added and authenticated — **VERIFIED** 2026-10-29 (Google sign-in,
      consent page completed on an iPhone)
- [x] Tool invocation from Claude — **VERIFIED** for ELI tools (server logs at 17:52 show
      `find_legal_basis` searches and `get_legal_provision` on the Civil Code)
- [x] All 9 tools invoked from Claude — **VERIFIED** 2026-10-29 by the user (ELI, SAOS, EU law;
      e.g. art. 5 k.c., art. 118 k.c., RODO art. 6 ust. 1 lit. f, SN I PK 134/15); calls seen in
      the server journal
- [x] Tool use from the Claude mobile app — **VERIFIED** 2026-10-29 (reported by the user; sign-in
      consent was completed on an iPhone)

Reproducible steps: `deployment.md` §3–4.

## EU law (first version) — DONE locally / deployment pending
- [x] `get_eu_provision`: CELLAR documents + SPARQL metadata, consolidated version with Polish
      corrigenda and amendments as wording source, OJ fallback, citation with OJ reference
- [x] `verify_legal_citation` routes EU citations (e.g. "art. 28 ust. 9 RODO")
- [x] Tests on real documents (RODO OJ + consolidated, directive 2014/24 consolidated 2026-01-01)
- [x] Deployed to the Pi (fd459ec) and tried from Claude — **VERIFIED** 2026-10-29

## Quality gates (local, 2026-10-29)
- `uv run pytest`: 119 passed (offline); `uv run pytest -m live`: 3 passed (real APIs)
- `ruff check`, `ruff format --check`, `pyright`: clean
- `pip-audit` on locked runtime deps: no known vulnerabilities
- `docker build` (linux/arm64): OK, image 247 MB
