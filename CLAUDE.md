# CLAUDE.md — polish-legal-mcp

Remote MCP server (FastMCP 4, Streamable HTTP) exposing official Polish legislation (ELI API of
the Sejm) and case law (SAOS) to Claude. Read `docs/legal-verification.md` before touching
anything in `services/legal.py`.

## Commands
```bash
uv sync                                   # install (uv.lock is authoritative; use --locked in CI)
uv run polish-legal-mcp                   # dev server on 127.0.0.1:8000/mcp, no auth
uv run pytest                             # offline tests (default excludes -m live)
uv run pytest -m live                     # real ELI/SAOS smoke tests
uv run ruff check . && uv run ruff format --check . && uv run pyright
docker compose up -d --build              # container (needs .env)
```

## Source-of-truth rules (non-negotiable)
- Only official sources: `api.sejm.gov.pl/eli`, `www.saos.org.pl/api` and the EU Publications
  Office (`publications.europa.eu`, behind EUR-Lex). Never substitute an
  unofficial source silently; ELI `U` texts (*tekst ujednolicony*) are informational only.
- Never fabricate acts, article numbers, wording, dates, amendments, case numbers or statuses.
  If data is missing, return `UNVERIFIED`/`NOT_FOUND` with a Polish explanation.
- `VERIFIED` must mean the specific question was positively answered from official data — never
  "the HTTP call worked". A 404 is not proof of nonexistence. A judgment is not a statute.
- `temporal_applicability` may be `VERIFIED` only under the rule in docs/legal-verification.md.
  Any change that widens that rule needs a regression test showing why it is safe.
- Do not build normalised citations without the metadata to support them (`formatted=None`).
- Text read automatically from a PDF (`text_origin = pdf_automatic`) is never `VERIFIED`; only an
  exact match with official HTML (`html_confirmed_by_pdf`) may confirm wording.

## Architecture constraints
- Layers: `server.py` (tools, thin) → `services/` (logic, Pydantic models) → `clients/`
  (HTTP only) → official APIs. Tools never call httpx directly.
- Unit lookup uses ELI `/struct` + full `text.html` (lxml). Do not use the `text.html/{tree}`
  fragment endpoint (ambiguous in consolidated texts, 200 on missing units).
- Upstream calls go through `clients/http.UpstreamClient` (retries, Retry-After, size limit, path
  guard). Paths are built only from validated identifiers (`ActId`, ints). No user-supplied URLs.
- Stateless HTTP, no database/Redis/vector store/LLM. In-memory caches must stay bounded (Pi).
- Responses must be bounded (truncate/paginate); never return a whole act.

## Conventions
- Python 3.12+, type hints everywhere, Pyright clean, Ruff (line length 100, E501 ignored for
  Polish strings). English identifiers/comments; Polish tool descriptions, field descriptions,
  user-facing messages and warnings.
- Errors: raise `errors.*Error` (Polish message) in services; `server._run` maps them to
  `ToolError`. Unexpected exceptions stay masked (`mask_error_details=True`).
- Tests: respx with real captured fixtures in `tests/fixtures` (record date in the docstring);
  label synthetic data explicitly. Live tests carry `@pytest.mark.live`.
- Mark deliberate shortcuts with a `ponytail:` comment naming the ceiling and upgrade path.

## Security requirements
- Production (`PLMCP_ENV=production`) must fail closed without GitHub OAuth, https base URL,
  ≥32-char JWT key and a non-empty login allowlist. Keep the `AuthMiddleware` allowlist.
- Never log tool arguments, search phrases or upstream query strings.
- Keep the container non-root, read-only, loopback-published.

## Adding a new official API
1. Research and record capabilities/limits in `docs/api-research.md` (with the date verified).
2. Add `clients/<name>.py` on top of `UpstreamClient` with a constant base URL.
3. Add a service returning Pydantic models with provenance (official URL) and warnings.
4. Add a thin tool with a Polish description stating coverage limits.
5. Capture real fixtures, add offline tests and a live smoke test; update README and docs.
