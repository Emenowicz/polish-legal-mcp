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
- `find_judgments_for_provision` relies on SAOS's cited-provision metadata: good for codes and
  older acts, sparse for newer acts (e.g. 0 judgments linked to Prawo zamówień publicznych 2019).
  "Cited" is not "interpreted"; an empty result is not proof that there is no case law.

## EU law
- `search_eu_acts` matches words of Polish titles with crude prefix stemming (no relevance
  ranking; nominative forms of short words such as "dane" do not match "danych");
  `find_legal_basis` covers Polish acts only.
- The wording comes from the newest EUR-Lex consolidated version valid on the date. Consolidated
  texts are informational; the Official Journal (with corrigenda) is binding.
- Amendments that EUR-Lex has not consolidated yet (e.g. directive 2026/806 amending directive
  2014/24 as of 2026-05-10) make the result `UNVERIFIED`; the wording is then possibly outdated.
- Older documents without article markup are not parsed (the tool says so and links EUR-Lex).
- Directives bind Member States; for domestic cases the Polish implementing act usually applies.
- `get_eu_transposition` lists measures as notified by Poland: no article-level mapping, notices
  can lack a Dz.U. number (then no ELI id) and include amending acts; it does not prove complete
  or correct transposition.
- CJEU case law: links between decisions and acts/articles are CELLAR metadata and can be
  incomplete (article annotations are often missing for recent decisions). The Polish text is a
  translation; the language of the case is authentic. Old translations without numbered
  paragraphs (e.g. 6/64) return metadata and the operative part only; Polish versions of
  General Court decisions are often missing. Treaties and the Charter (CELEX sector 1) cannot be
  used as the act in `search_cjeu_judgments`.

## Legislative process
- `find_pending_bills` matches the act's name in bill titles (current term only); bills amending
  "niektórych innych ustaw" without naming the act are missed, and government drafts before they
  reach the Sejm (RCL) are not covered.

## Search
- ELI title search is substring-based without relevance ranking; titles are inflected, so stems
  work better ("zakwaterowa", "gospodarce nieruchomo").
- `find_legal_basis` is lexical (ELI keyword vocabulary + title stems). It misses acts whose
  titles/keywords do not contain the words (e.g. zakwaterowanie provisions inside *ustawa o
  obronie Ojczyzny*). Treat results as candidates only.

## Verification
- Amendment lists come from ELI metadata; completeness is not provable.
- The ELI date of an amending act is a single date per amended act; staged entry into force of
  amending acts is not modelled in the temporal rule. `get_legal_provision` lists amendments dated
  after the requested date and flags amending acts already in force whose changes to this act are
  dated later (staged entry: check their final provisions); the flag is informational and does
  not change `VERIFIED`, because ELI does not show whether such an act changed this act earlier.
- Staged entry into force of the act itself is detected heuristically from its entry-into-force
  clause; unparseable periods ("pierwszego dnia miesiąca następującego…") keep results `UNVERIFIED`.
- Transitional provisions are not analysed.

## Operations
- Single process, in-memory cache (per container, ≤32 HTML documents). No horizontal scaling
  concerns at expected load; the HTTP transport is stateless, so replicas are possible.
