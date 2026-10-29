# Legal verification model

The server answers *narrow, separately named questions* about a legal source. There is no
single "verified" flag. Every provision result carries a `VerificationReport` with eight
components, each with a `status` and a Polish `detail` explaining why.

## Statuses

| Status | Meaning | Never means |
|---|---|---|
| `VERIFIED` | The specific question was answered positively from official ELI metadata or official ELI text retrieved in this request. | That an HTTP request succeeded; that the provision applies to the user's case. |
| `UNVERIFIED` | The question could not be answered positively (missing data, PDF-only text, intervening amendments, future date, ambiguous structure, heuristics inconclusive). | That the answer is "no". |
| `NOT_FOUND` | The thing was not found **in the consulted source/version** (e.g. ELI 404, unit absent from the struct of the selected text). | That it does not exist anywhere or in another version. |
| `NOT_APPLICABLE` | Official metadata/text positively shows non-applicability on the date: act not yet in force, act repealed/expired, or unit marked `(uchylony)`/`(pominięty)` with no later changes. | A substantive legal conclusion. |
| `SOURCE_UNAVAILABLE` | The official source could not be reached or returned unusable data (timeouts, 429/5xx after retries, text missing for a unit present in the structure). | Anything about the law itself. |

## Components

| Component | VERIFIED when | Otherwise |
|---|---|---|
| `source_authenticity` | Data came from `https://api.sejm.gov.pl/eli` (Kancelaria Sejmu). | `SOURCE_UNAVAILABLE` if nothing could be fetched. |
| `act_existence` | ELI returned metadata for the act. | `NOT_FOUND` on 404 (with "not proof of nonexistence" note), `SOURCE_UNAVAILABLE` on outage. |
| `unit_existence` | The unit (art./ust./§/pkt/lit.) exists in the ELI structure **and** HTML of the selected text. | `NOT_FOUND` (absent in that version), `UNVERIFIED` (PDF-only or ambiguous), `SOURCE_UNAVAILABLE`. |
| `exact_wording` | The wording was read from the official HTML of the selected text. Only whitespace is normalized; footnotes are returned separately. | `UNVERIFIED` for PDF-only texts. |
| `act_in_force_on_date` | ELI `entryIntoForce`/`validFrom` ≤ date, no `repealDate`/`expirationDate` ≤ date, date not in the future, and no contradictory `inForce`. | `NOT_APPLICABLE` (not yet in force / lost force), `UNVERIFIED` (missing dates, future date, `NOT_IN_FORCE` without an end date). |
| `temporal_applicability` | See rule below. | `UNVERIFIED` or `NOT_APPLICABLE`. |
| `amendment_completeness` | Never. ELI reference lists cannot be proven complete. | Always `UNVERIFIED`. |
| `substantive_relevance` | Never. Matching a citation does not establish relevance. | Always `UNVERIFIED`. |

### The temporal rule (the only path to `temporal_applicability = VERIFIED`)

All of the following must hold:

1. `act_in_force_on_date` is `VERIFIED`;
2. the wording was actually read from HTML;
3. the requested date is not in the future (Europe/Warsaw);
4. the selected text has a known legal-state date (`legalStatusDate` for consolidated texts,
   entry into force for original texts);
5. ELI lists **no** amending act and **no** Constitutional Tribunal ruling dated within
   `[min(state date, requested date), max(state date, requested date)]`, and none without a date
   that could fall in that window;
6. the act's own entry-into-force clause has no unresolved staged entry ("z wyjątkiem …") — exceptions
   must be absolute dates or computable periods from promulgation, all ≤ the requested date;
7. no footnote of the unit carries a date later than the requested date.

If any condition fails the result is `UNVERIFIED` and the detail lists the reason (e.g. the
intervening amending acts with their ELI dates). If all hold but the unit is marked
`(uchylony)`, the result is `NOT_APPLICABLE`.

### Wording source selection

ELI publishes consolidated texts (*teksty jednolite*) as separate notices (*obwieszczenia*).
For a requested date D the server scans the act's consolidated texts newest-first and picks:

- the newest consolidated text whose legal-state date ≤ D as the **official reference** for the
  citation (even if PDF-only), and
- the newest consolidated text with HTML and legal-state date ≤ D as the **wording source**;
  if none exists, the original text of the act.

When the two differ (common: ELI has no HTML for anything published since 2025) a warning names
the newer PDF-only text, and all amendments between the wording source and D are listed, so the
result is `UNVERIFIED`. Passing a consolidated-text ELI explicitly forces that text as the source.

## Example (real data, 2026-10-29)

`get_legal_provision(act_id="DU/1960/168", article="61", paragraph="1")` (k.p.a.):

- wording source `DU/2024/572` (TJ, state 2024-03-27, HTML) — `exact_wording: VERIFIED`
- latest official TJ `DU/2025/1691` (state 2025-11-03, PDF only)
- changes after source: `DU/2025/769` (2025-07-13)
- `temporal_applicability: UNVERIFIED` — the server refuses to claim the 2024 wording is current
- citation: `art. 61 § 1 ustawy z dnia 14 czerwca 1960 r. Kodeks postępowania administracyjnego (Dz. U. z 2025 r. poz. 1691)`

`get_legal_provision(act_id="Dz.U. 2000 poz. 856", article="2", paragraph="1")` (no amendments
after TJ `DU/2024/1835`, state 2024-12-03) → `temporal_applicability: VERIFIED`, with the
caveat that this relies on the completeness of ELI metadata.

## Citations

`Citation.formatted` is built only from metadata: the act type must map to a known genitive
form (ustawa→ustawy, rozporządzenie→rozporządzenia, …) and the publication address must be
known. Otherwise `formatted` is `null` and only structured fields are returned. The publication
used is the newest official consolidated text as of the date (or the original publication),
with `, z późn. zm.` appended when ELI lists amendments effective after that text and ≤ the date.

## What is deliberately not done

- Applying amendments to reconstruct wording (not a text substitution problem).
- Analysing transitional provisions (*przepisy przejściowe*).
- Treating judgments as statutory law, or SAOS as complete.
