# Known limitations

## Data coverage
- **No HTML in ELI for acts published since 2025** (measured 2026-10-29: 0 of 1900 acts from 2025,
  0 of 1422 from 2026; 100 % of 2024). Current consolidated texts are therefore usually PDF-only.
  The server does not parse PDFs; it returns the PDF link, the newest HTML version and the list of
  later amendments, with `UNVERIFIED` wording/temporal statuses.
- The *tekst ujednolicony* (type `U`) files are informational compilations, not official texts;
  they are listed with `official: false` and never used as a wording source.
- **SAOS has no administrative-court judgments** (`courtType=ADMINISTRATIVE` → 0 results). WSA/NSA
  case law (CBOSA) is not available through an official API and is not integrated.
- SAOS `lawJournalEntryCode` indexes only the originally cited publication; use
  `referenced_regulation` (title text) instead.

## EU law
- Only Polish journals (Dziennik Ustaw, Monitor Polski) are covered. EU regulations and
  directives (e.g. GDPR/RODO) are not published there and are not available; the server
  instructions tell Claude to say so and refer to EUR-Lex. Possible extension: EUR-Lex (CELLAR)
  as an official source.

## Search
- ELI title search is substring-based without relevance ranking; titles are inflected, so stems
  work better ("zakwaterowa", "gospodarce nieruchomo").
- `find_legal_basis` is lexical (ELI keyword vocabulary + title stems). It misses acts whose
  titles/keywords do not contain the words (e.g. zakwaterowanie provisions inside *ustawa o
  obronie Ojczyzny*). Treat results as candidates only.

## Verification
- Amendment lists come from ELI metadata; completeness is not provable.
- The ELI date of an amending act is a single date; staged entry into force of amending acts is
  not modelled.
- Staged entry into force of the act itself is detected heuristically from its entry-into-force
  clause; unparseable periods ("pierwszego dnia miesiąca następującego…") keep results `UNVERIFIED`.
- Transitional provisions are not analysed.
- Articles with superscript numbering (e.g. art. 98¹) are not parsed from citations.

## Operations
- Single process, in-memory cache (per container, ≤32 HTML documents). No horizontal scaling
  concerns at expected load; the HTTP transport is stateless, so replicas are possible.
- Remote deployment, Claude connector and mobile access were not executed by the implementer
  (see implementation-progress.md).
