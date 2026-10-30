# API research (Phase 0)

All facts below were verified by live requests on **2026-10-29** unless marked otherwise.

## ELI API — `https://api.sejm.gov.pl/eli` (OpenAPI: `/openapi/`)

| Capability | Endpoint | Notes |
|---|---|---|
| Publishers | `GET /acts` | `DU` (Dziennik Ustaw, 98 008 acts), `MP` (Monitor Polski, 66 784). |
| Search | `GET /acts/search` | `title` (substring, multi-word), `keyword` (comma list; vocabulary at `/keywords`, 2 477 terms, includes names such as "Skarb Państwa"), `publisher`, `year`, `type`, `inForce=1`, date ranges (`date*`, `dateEffect*`, `pubDate*`), `limit` (**default 500**), `offset`, `sortBy`, `sortDir`. Response `{count, totalCount, items, offset, searchQuery}`. No relevance ranking, no full text. |
| Act metadata | `GET /acts/{pub}/{year}/{pos}` | `status`, `inForce` (today), `entryIntoForce`, `validFrom`, `repealDate`, `expirationDate`, `legalStatusDate` (consolidated texts), `texts[]`, `references{}`, `address` (ISAP id). |
| Structure | `GET .../struct` | JSON tree; node ids equal HTML element ids (`chpt_2-arti_11-pass_3-pint_2`); types `part, book, titl, bran, chpt, schp, arti, pass (ust.), para (§), pint (pkt), lett (lit.)`. Consolidated texts: `part_1` "Treść obwieszczenia", `part_2` "Załącznik - Tekst jednolity …". |
| HTML text | `GET .../text.html` | 0.7–0.9 MB for a mid-size act. Footnotes in `a.gloss-link .tooltip-text`; cited-act addresses in `span.xHidden` (part of the published text, collapsed by CSS); repealed units read `(uchylony)`. Base-act HTML is the **original promulgated text**, not a maintained one. |
| HTML fragment | `GET .../text.html/{tree}` | **Not used**: returns 200 + empty body for missing units, and in consolidated texts `art=11` resolved to an article quoted in the notice body instead of the annex. |
| Files | `GET .../text.pdf`, `GET .../text/{type}/{fileName}` | Types: `H` html, `O`/`I` promulgated PDF, `T` act PDF, `U` *tekst ujednolicony* (informational), `W` docx (not in the OpenAPI enum but returned). |
| Vocabularies | `/statuses` (15), `/references` (20), `/keywords`, `/types` | |

Reference types used: `Akty zmieniające` (with `date` = effective date, may be in the future —
e.g. KC ← DU/2026/507 effective 2028-11-01), `Orzeczenie TK` (often without date → the ruling's
own metadata is fetched), `Inf. o tekście jednolitym` (consolidated-text notices),
`Tekst jednolity dla aktu` (notice → base act). `Nowelizacje po tekście jednolitym` exists in the
vocabulary but was not seen populated; the server computes it.

Status traps: superseded consolidated-text notices become `wygaśnięcie aktu`/`NOT_IN_FORCE`;
amending acts absorbed into a consolidated text are `akt objęty tekstem jednolitym`; acts not yet
in force can be `obowiązujący` (e.g. DU/2026/1353, entry into force 2027-09-27).

**Coverage cliff**: HTML present for 100 % of 2024 acts, 0 % of 2025 and 2026 acts (no DOCX
either: texts `O`, `I`, `T` are PDF). The PDFs are born-digital: body text 10 pt, footnotes 9 pt,
footnote markers 6–6.5 pt as "1" + ")", numbering superscripts 6.5 pt as "[1]" (art. 22¹), page
headers "Dziennik Ustaw – N – Poz. M". ELI's struct writes art. 22¹ as `22_1` and the HTML as
`22<sup>1</sup>`.

ISAP page for humans: `https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id={address}`.

## SAOS — `https://www.saos.org.pl/api`

| Capability | Endpoint | Notes |
|---|---|---|
| Search | `GET /search/judgments` | `all`, `legalBase`, `referencedRegulation`, `lawJournalEntryCode` (`year/entry`, original citation only), `courtType` (`COMMON, SUPREME, CONSTITUTIONAL_TRIBUNAL, NATIONAL_APPEAL_CHAMBER, ADMINISTRATIVE`), `ccCourtType`, `judgmentTypes`, `judgmentDateFrom/To`, `pageSize` 1–100, `pageNumber`, `sortingField`, `sortingDirection`. Response: `links` (with `next`), `items`, `queryTemplate`, `info.totalResults`. |
| Details | `GET /judgments/{id}` | `{links, data}`; `textContent` is HTML (seen 44 k chars); `referencedRegulations{journalYear, journalNo, journalEntry, text}`; `source.judgmentUrl`. 404 → JSON error. |
| Public page | `https://www.saos.org.pl/judgments/{id}` | |

`courtType=ADMINISTRATIVE` returned **0** results: no WSA/NSA case law.

Judgments citing a provision (verified 2026-10-30): `lawJournalEntryCode={year}/{entry}` of the
**original** Dz.U. publication + `referencedRegulation="art. N"` (quoted phrase; superscripts as
`art. 22(1)`). Search items carry no `referencedRegulations`, so each candidate needs
`/judgments/{id}`; its regulation text ends with the cited units ("… poz. 93 - art. 5; art. 98 §
1(1); art. 75 c)"). Share of the first 10 candidates confirmed by those units: k.c. art. 5 7/10,
art. 415 10/10; k.p.c. art. 98 10/10; k.p. art. 22 9/10; k.p.a. art. 61 4/10. Newer acts are
poorly indexed: `2019/2019` (Prawo zamówień publicznych 2019) → 0 judgments; 2023+ judgments
citing *Prawo zamówień publicznych* are linked to the 2004 act (`2004/177`).

## EU Publications Office (CELLAR) — `https://publications.europa.eu` (verified 2026-10-29)

| Capability | Endpoint | Notes |
|---|---|---|
| Document | `GET /resource/celex/{CELEX}` with `Accept: application/xhtml+xml`, `Accept-Language: pol` | 303 redirects within the host to the Polish manifestation. OJ format: `div#art_28`, paragraphs `div#028.003`, points in two-cell tables, header `oj-hd-oj` "L 119/1", `oj-hd-date`. Consolidated (`0…-YYYYMMDD`): `div.norm` + `span.no-parag`, `grid-container` lists, header table of modifiers (`p.arrow a[title=CELEX]` ►C1/►M1). Older acts (e.g. 32001R1049 OJ) have no article ids. |
| Metadata | `GET /webapi/rdf/sparql` (`Accept: application/sparql-results+json`, 406 for plain JSON) | `cdm:resource_legal_id_celex`, `…_date_entry-into-force` (several: entry into force + application), `…_date_end-of-validity` (9999-12-31 = none), `…_in-force`, `…_eli`, `cdm:expression_title` (POL), consolidated versions by CELEX prefix `0…-`, corrigenda `{CELEX}R(nn)` with a Polish expression, amending acts via `cdm:resource_legal_amends_resource_legal`. |

EU act title search (verified 2026-10-30): Virtuoso full text on Polish titles,
`?title bif:contains "'ochron*' AND 'danych' AND 'osobowy*'"` + CELEX regex for sector 3; no
stemming and no relevance ranking (we rank base legislative acts in force first). Wildcards need
≥4 leading letters (`'dan*'` → HTTP 500) and short prefixes are slow: `'zamówie*' AND 'public*'`
15.9 s vs `'zamówie*' AND 'publiczn*'` 4.1 s; exact words ~0.3–0.6 s.

CJEU case law (verified 2026-10-30): documents `GET /resource/celex/6YYYY{C|T}{J|O|C}NNNN`
(J judgment, O order, C Advocate General's opinion) in Polish XHTML; numbered paragraphs are
top-level two-cell tables with `p.count` (2008–2013) or `p.coj-count` (2014+), the operative part
follows "orzeka, co następuje"; the 1964 translation of 6/64 has no numbered paragraphs.
SPARQL: `cdm:expression_case-law_identifier_case "Sprawa C-311/18"` (typed `xsd:string`, ~0.3 s;
misses e.g. C-73/07 and joined cases, so the fallback is `bif:contains '"C-316/23"'` on Polish
identifiers, ~0.8 s), `cdm:case-law_ecli`, `cdm:work_date_document`, Polish `expression_title`
("heading#parties#procedure#keywords#Sprawa …"), `cdm:case-law_interpretes_resource_legal`
(decision → act) with the article on a reified `owl:Axiom`
(`ann:reference_to_modified_location`, e.g. `A17P1LD` = art. 17 ust. 1 lit. d, `A04PT7` = art. 4
pkt 7). Pseudo-CELEX `…_RES` rows are ignored. Recent decisions often lack article annotations.

No registration or key. EUR-Lex pages: `https://eur-lex.europa.eu/legal-content/PL/TXT/?uri=CELEX:{CELEX}`.

## FastMCP / MCP (PyPI, 2026-10-29)

fastmcp 4.1.4 (depends on `mcp` 2.3.0 → `httpx2` internally), pydantic 2.14.1,
pydantic-settings 2.16.2, httpx 0.28.1, lxml 6.1.3, pytest 9.2.0, pytest-asyncio 1.4.1,
respx 0.23.1, ruff 0.17.3, pyright 1.1.414, uvicorn 0.54.0. Exact versions are pinned in `uv.lock`.

- Streamable HTTP via `mcp.http_app(path="/mcp", stateless_http=True)`; FastMCP 4 negotiates the
  sessionless `2026-07-28` protocol and the legacy handshake per connection.
- `@mcp.custom_route` routes bypass auth (used for `/health`).
- Host/Origin guard: `host_origin_protection=True` + `allowed_hosts` (421 on foreign Host).
- `GitHubProvider` (OAuthProxy) exposes DCR **and** CIMD to MCP clients, PKCE S256, RFC 9728
  protected-resource metadata, and stores client registrations in an encrypted file store under
  `FASTMCP_HOME` (no Redis needed).
- No built-in user allowlist → `AuthMiddleware` with a custom check on the `login` claim.
- `RateLimitingMiddleware(get_client_id=...)` (defaults to one global bucket).
- `httpx2` is used by FastMCP; our upstream clients use `httpx` so respx can mock them.

## Claude remote connectors (claude.com/docs/connectors/building/authentication)

- Same infrastructure for claude.ai web, Desktop, mobile and Cowork; requests originate from
  `160.79.104.0/21`.
- Auth types: OAuth with DCR or CIMD (default), Anthropic-held credentials (directory listings),
  static request headers (**beta, organisation Owners only**), or none. For an individual
  (Free/Pro) custom connector the practical authenticated option is **OAuth with DCR/CIMD**.
- Requirements implemented here: 401 with `WWW-Authenticate: Bearer resource_metadata=…`;
  PRM `resource` exactly equals the MCP URL; first `authorization_servers` entry is used;
  PKCE S256 advertised; `/token` accepts form-urlencoded; OAuth endpoints answer within 10 s;
  redirect `https://claude.ai/api/mcp/auth_callback` (and loopback for Claude Code) accepted by
  OAuthProxy's default redirect policy.

## Not implemented (future sources)
GUGiK/Geoportal, GUS REGON, Sejm API (legislative process), RCL, CBOSA. See README roadmap.
