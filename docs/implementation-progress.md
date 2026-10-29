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

## Phase 5 — Security and deployment — DONE (local) / NOT EXECUTED (remote)
- [x] GitHub OAuth (OAuthProxy, DCR/CIMD, PKCE), login allowlist, rate limiting, Host guard
- [x] Dockerfile (multi-stage, non-root, healthcheck), compose (loopback port, read-only, limits)
- [x] Container verified locally on arm64 (Apple Silicon): healthy, uid 10001, 401 with
      `resource_metadata`, PRM `resource` = `<base>/mcp`, foreign Host → 421
- [x] Raspberry Pi + Tailscale Funnel runbook (`deployment.md`)
- [ ] Deployment on the Raspberry Pi — **NOT EXECUTED** (no access to the device)
- [ ] Tailscale Funnel public reachability — **NOT EXECUTED**
- [ ] Real GitHub OAuth sign-in round trip — **NOT EXECUTED** (needs the OAuth App on the final URL)

## Phase 6 — Claude and mobile validation — NOT EXECUTED
- [ ] Claude custom connector added and authenticated — **NOT EXECUTED**
- [ ] Tool discovery/invocation from Claude (ELI and SAOS prompts) — **NOT EXECUTED**
- [ ] Android/iOS access — **NOT EXECUTED**

Reproducible steps: `deployment.md` §3–4.

## Quality gates (local, 2026-10-29)
- `uv run pytest`: 119 passed (offline); `uv run pytest -m live`: 3 passed (real APIs)
- `ruff check`, `ruff format --check`, `pyright`: clean
- `pip-audit` on locked runtime deps: no known vulnerabilities
- `docker build` (linux/arm64): OK, image 247 MB
