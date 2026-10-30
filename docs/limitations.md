# Known limitations

## Data coverage
- **No HTML in ELI for acts published since 2025** (measured 2026-10-29: 0 of 1900 acts from 2025,
  0 of 1422 from 2026; 100 % of 2024). Current consolidated texts are therefore usually PDF-only.
  The server reads those PDFs: unchanged provisions are confirmed against the last HTML version
  (`VERIFIED`), changed or new ones are read automatically from the PDF and stay `UNVERIFIED`
  (check the PDF page given). Tables, annexes (załączniki) and grouped units such as
  "Art. 102–119" are not split out of PDFs. The first read of a large code takes several seconds
  on a Raspberry Pi (then cached for an hour).
- The *tekst ujednolicony* (type `U`) files are informational compilations, not official texts;
  they are listed with `official: false` and never used as a wording source.
- **SAOS has no administrative-court judgments** (`courtType=ADMINISTRATIVE` → 0 results). WSA/NSA
  case law (CBOSA) is not available through an official API and is not integrated.
- SAOS `lawJournalEntryCode` indexes only the originally cited publication; use
  `referenced_regulation` (title text) instead.

## EU law
- `get_eu_provision` needs the act's number, CELEX or a known alias (RODO); there is no topic
  search for EU acts and `find_legal_basis` covers Polish acts only.
- The wording comes from the newest EUR-Lex consolidated version valid on the date. Consolidated
  texts are informational; the Official Journal (with corrigenda) is binding.
- Amendments that EUR-Lex has not consolidated yet (e.g. directive 2026/806 amending directive
  2014/24 as of 2026-05-10) make the result `UNVERIFIED`; the wording is then possibly outdated.
- Older documents without article markup are not parsed (the tool says so and links EUR-Lex).
- Directives bind Member States; for domestic cases the Polish implementing act usually applies.
- No CJEU case law.

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

## Operations
- Single process, in-memory cache (per container, ≤32 HTML documents). No horizontal scaling
  concerns at expected load; the HTTP transport is stateless, so replicas are possible.
