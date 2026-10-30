# Roadmap

Ordered by value for legal work (lawyers, public-administration staff) × feasibility from
**official** sources. No dates: items move up when their source is confirmed. The rules in
[CLAUDE.md](../CLAUDE.md) and [legal-verification.md](legal-verification.md) apply to every item
(official sources only, no fabricated data, `VERIFIED` only when positively established, no
database/LLM, Raspberry Pi sized). Current gaps: [limitations.md](limitations.md).

## Now
- **Deploy PDF reading (f10b23f) to the Pi**: `ssh -t tradingpi 'sudo bash ~/pi-setup.sh'`, check
  the journal for the PDF path, tick "Deployed to the Pi" in
  [implementation-progress.md](implementation-progress.md).

## Next — official source confirmed
| Item | Who it helps | Source / status | Notes |
|---|---|---|---|
| **CJEU case law** (`get_cjeu_judgment`: judgment by case number/CELEX/ECLI, paragraph by number) | anyone applying EU law (RODO, public procurement, consumer law) | CELLAR, CELEX sector 6. Verified 2026-10-30: `62018CJ0311` (Schrems II) returns Polish XHTML with numbered paragraphs (`coj-count`) | Same client as `get_eu_provision`. Older (pre-2004) judgments may lack a Polish version → `NOT_FOUND` in Polish, link to EUR-Lex. To verify: SPARQL for "judgments interpreting act X". |
| **Judgments citing a given provision** (in `get_legal_provision` / `search_judgments`) | litigators: "how do courts read art. X" | SAOS. `legal_base` / `referenced_regulation` filters exist; search items carry **no** `referencedRegulations`, so confirming the article needs one `/judgments/{id}` fetch per hit | Cap at ~10 fetches per call (rate limits, Pi). Result is a candidate list, never "complete case law". |
| **Weekly live smoke run** (GitHub Actions cron, `pytest -m live`) | maintainers | ELI, SAOS, CELLAR | Catches upstream format changes before users do. |
| **EU act search by title** (`search_eu_acts`) | users who don't know the CELEX/number | CELLAR SPARQL (titles already queried for metadata) | Today `get_eu_provision` needs the number or an alias. |
| **Allowlist by Google Workspace domain** (`hd` claim) | a law firm/office giving access to all staff | Google OIDC | Keep email allowlist; domain is an additional option. |

## Later — known gaps in current tools
- **Staged entry into force of amending acts** (each amending provision's own date) and
  **transitional provisions**: today only warned about. Needs parsing the amending act's final
  provisions; acts since 2025 are PDF-only, so results stay `UNVERIFIED`.
- **PDF: annexes, tables and grouped units** ("Art. 102–119") — not split out today.
- **`find_legal_basis`**: Polish stemmer instead of the crude suffix cut (`ponytail:` in
  `services/legal.py`); optionally use SAOS judgments' referenced regulations as a signal.
- **Per-key cache locking** so concurrent misses don't fetch the same document twice
  (`ponytail:` in `clients/http.py`) — only if load grows.

## Needs research — official source not confirmed
- **Administrative courts (WSA/NSA)**: CBOSA (orzeczenia.nsa.gov.pl) is NSA's official database
  but has **no API**. Scraping its HTML is out of scope unless explicitly decided (fragile,
  terms of use). Biggest gap for administrative and tax law.
- **EU directive → Polish implementing act** (national transposition measures): check whether
  CELLAR exposes them for Poland in machine-readable form.
- **Tax interpretations** (Eureka, Ministry of Finance), **ministerial official journals** and
  **local law** (voivodeship official journals): no unified official API known; each needs
  research before any work.
- **Other official sources**: Sejm API (legislative process — pending amendments), RCL
  (government drafts), GUS REGON, GUGiK/Geoportal — for the public-administration use case.

## Not planned
- **Commentaries, doctrine, case notes (glosy)**: no official open source; licensed databases
  (LEX, Legalis) only.
- **Full-text or semantic search over all law**: needs a database or embeddings/LLM, which the
  architecture excludes. Revisit only as an explicit architecture decision.
- **Unofficial mirrors or compilations as a wording source** (incl. ELI `U` texts).
- **Drafting letters or opinions in the server**: that is Claude's job; the server supplies
  sources and verification.
- **Storing users' documents or case data**: the server stays stateless; client data must not
  leave the user's Claude conversation.
