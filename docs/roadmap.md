# Roadmap

Ordered by value for legal work (lawyers, public-administration staff) × feasibility from
**official** sources. The rules in [CLAUDE.md](../CLAUDE.md) and
[legal-verification.md](legal-verification.md) apply to every item (official sources only, no
fabricated data, `VERIFIED` only when positively established, no database/LLM, Raspberry Pi
sized). Current gaps: [limitations.md](limitations.md). Evidence for every source decision:
[api-research.md](api-research.md).

## Now
- **Deploy to the Pi**: `ssh -t tradingpi 'sudo bash ~/pi-setup.sh'` (needs the owner's sudo),
  then tick "Deployed to the Pi" in [implementation-progress.md](implementation-progress.md).

## Done (2026-10-30)
| Item | Tool / change | Notes |
|---|---|---|
| CJEU case law | `get_cjeu_judgment`, `search_cjeu_judgments` | CELLAR: Polish text, numbered paragraphs, operative part, language of the case, citation with ECLI and "pkt N"; decisions interpreting an act/article from CELLAR annotations. |
| Judgments citing a provision | `find_judgments_for_provision` | SAOS act + "art. N" search, each judgment confirmed against its cited-provision metadata. |
| EU act search by title | `search_eu_acts` | Virtuoso full text on Polish titles, base legislative acts in force first. |
| Directive → Polish implementing acts | `get_eu_transposition` | National implementing measures notified by Poland, mapped to ELI and cross-checked with the ELI title. |
| Pending bills in the Sejm | `find_pending_bills` | API Sejmu (current term), matched by the act's name in bill titles, with the current stage. |
| Weekly live checks | `.github/workflows/live.yml` | Real-API smoke tests + dependency audit every Monday. |
| Google Workspace domain allowlist | `PLMCP_ALLOWED_GOOGLE_DOMAINS` | Only accounts managed by the domain (`hd` claim matching the e-mail). |
| Single-flight cache | `TTLCache.get_or_load` | Parallel requests share one download/PDF parse (Pi CPU). |
| Grouped units | HTML + PDF | Any article in "Art. 164–179. (uchylone)" resolves to the group (repealed). |
| Staged amending acts | `get_legal_provision` warning | Pending amendments listed; amending acts already in force whose change of this act is dated later are flagged (informational, never widens `VERIFIED`). |
| Case-law signal for legal basis | `find_legal_basis(include_case_law=True)` | Acts most cited in matching SAOS judgments (procedural acts excluded); longer stems. |
| Transitional provisions pointer | `get_legal_provision` (`transitional_provisions`) | Chapters of transitional provisions with their article range (from the struct) and the introducing act of codes (ELI "Przepisy wprowadzające", e.g. k.c. → DU/1964/94). |
| Polish inflection in searches | `find_legal_basis`, `search_eu_acts` | Fleeting e of "-ek" nouns (podatek → podatku), whole short words as prefixes (system → systemu), three most selective words for EUR-Lex (VAT directive in 1 s instead of 12 s). |
| Maintenance pages | `UpstreamClient` | SAOS "Przerwa techniczna" (200 + HTML) → `SOURCE_UNAVAILABLE`, not "bad JSON". |

## Later
Nothing open: remaining limits are listed below as deliberate decisions, with evidence.

## Blocked — no official machine-readable source (checked 2026-10-30)
- **Administrative courts (WSA/NSA)**: CBOSA has no API or open-data set; HTML scraping is out
  of scope. Biggest remaining gap for administrative and tax law.
- **RCL government drafts** (legislacja.gov.pl): no API; only the `rclLink` of bills in the Sejm.
- **Tax interpretations** (Eureka): only undocumented internal endpoints.
- **Local law and ministerial journals**: no unified official API; ELI covers Dz.U. and M.P. only.

## Not planned
- **Automatic application of transitional provisions** and a full model of staged entry into
  force: deciding whether a transitional rule applies to a case is legal analysis, and the
  amending acts are PDF-only since 2025. The server points to the transitional chapters and
  introducing acts and flags staged amending acts; the temporal rule is not widened.
- **Extracting annex tables from PDFs**: text extraction breaks table layout – in the stamp duty
  act (DU/2025/1154, checked 2026-10-30) a fee "616 zł" comes out glued to the next item
  ("616 zł 2) zezwolenie na ekshumację"), i.e. attributed to the wrong row. The PDF link is
  returned instead.
- **A full Polish morphological dictionary** (e.g. Morfologik): tens of MB for the Raspberry Pi
  for a candidate-search tool; prefix stems plus the fleeting e cover the common cases.
- **Commentaries, doctrine, case notes (glosy)**: no official open source; licensed databases
  (LEX, Legalis) only.
- **Full-text or semantic search over all law**: needs a database or embeddings/LLM, which the
  architecture excludes. Revisit only as an explicit architecture decision.
- **Unofficial mirrors or compilations as a wording source** (incl. ELI `U` texts).
- **Drafting letters or opinions in the server**: that is Claude's job; the server supplies
  sources and verification.
- **Storing users' documents or case data**: the server stays stateless; client data must not
  leave the user's Claude conversation.
