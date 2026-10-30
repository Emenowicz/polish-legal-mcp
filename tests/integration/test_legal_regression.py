"""Legal regression cases on real ELI metadata/text (captured 2026-10-29); 'today' = 2026-10-29.

1. existing provision, VERIFIED narrow case       -> DU/2000/856 via TJ DU/2024/1835
2. amended after the consolidated text             -> DU/2015/1322: TJ 2024/98 (HTML) vs 2025/817 (PDF)
3. delayed entry into force                        -> DU/2026/1353 (enters 2027-09-27)
4. repealed act requested for an earlier date      -> DU/1996/405 (repealed 2015-10-01)
5. unresolvable legal state (must not be VERIFIED) -> DU/2015/1322 art. 4a, 2025-06-01 and 2027
6. nonexistent structural unit                     -> DU/2015/1322 art. 999
7. unavailable official source                     -> ELI answering 503 (synthetic)
"""

import re
from datetime import date

import respx

from polish_legal_mcp.models import ActId
from polish_legal_mcp.models import VerificationStatus as VS
from tests.conftest import ELI, eli_fixture_response, load_json


async def test_1_existing_provision_verified(legal, eli_mock):
    result = await legal.get_provision(ActId.parse("Dz.U. 2000 poz. 856"), "2", "1")
    v = result.verification
    assert result.wording_source.eli == "DU/2024/1835"
    assert result.text.startswith(
        "1. W okresie od dnia wejścia w życie ustawy do dnia 31 sierpnia 2003 r."
    )
    assert (v.unit_existence.status, v.exact_wording.status) == (VS.VERIFIED, VS.VERIFIED)
    assert v.temporal_applicability.status == VS.VERIFIED
    assert v.amendment_completeness.status == VS.UNVERIFIED
    assert v.substantive_relevance.status == VS.UNVERIFIED
    assert result.citation.formatted == (
        "art. 2 ust. 1 ustawy z dnia 14 lipca 2000 r. o restrukturyzacji finansowej górnictwa "
        "siarki (Dz. U. z 2024 r. poz. 1835)"
    )


async def test_2_html_wording_confirmed_by_newest_pdf_consolidated_text(legal, eli_mock):
    # Newest TJ DU/2025/817 (state 2025-06-11) is PDF-only; the 2024 HTML wording of art. 11
    # ust. 1 matches it except for the updated address of the Labour Code.
    result = await legal.get_provision(ActId.parse("DU/2015/1322"), "11", "1")
    assert result.text_origin == "html_confirmed_by_pdf"
    assert result.wording_source.eli == "DU/2025/817"
    assert result.pdf_page == 6
    assert result.text.endswith("Kodeks pracy (Dz. U. z 2025 r. poz. 277).")
    assert result.changes_after_source == []
    assert result.verification.exact_wording.status == VS.VERIFIED
    assert result.verification.temporal_applicability.status == VS.VERIFIED
    assert result.citation.publication_eli == "DU/2025/817"


async def test_2b_changed_provision_read_from_pdf_is_unverified(legal, eli_mock):
    # art. 6 gained pkt 4 by DU/2025/603: the current wording exists only in the PDF.
    result = await legal.get_provision(ActId.parse("DU/2015/1322"), "6")
    assert result.text_origin == "pdf_automatic"
    assert "4) przeznaczonych do zbycia albo nieodpłatnego przekazania" in result.text
    assert result.earlier_wording and "4) przeznaczonych" not in result.earlier_wording
    assert result.earlier_wording_source.eli == "DU/2024/98"
    assert result.verification.exact_wording.status == VS.UNVERIFIED
    assert result.verification.temporal_applicability.status == VS.UNVERIFIED
    assert "PDF" in result.verification.exact_wording.detail


async def test_2c_pdf_unavailable_falls_back_to_html_and_lists_changes(legal):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ELI}/acts/DU/2025/817/text.pdf").respond(404)
        router.get(url__regex=rf"^{re.escape(ELI)}/acts/").mock(side_effect=eli_fixture_response)
        result = await legal.get_provision(ActId.parse("DU/2015/1322"), "11", "1")
    assert result.text_origin == "html"
    assert result.wording_source.eli == "DU/2024/98"
    assert {c.eli for c in result.changes_after_source} == {"DU/2024/1907", "DU/2025/603"}
    assert result.verification.temporal_applicability.status == VS.UNVERIFIED
    assert any("Nie odczytano PDF" in w for w in result.warnings)


async def test_3_delayed_entry_into_force(legal, eli_mock):
    result = await legal.get_provision(ActId.parse("DU/2026/1353"), "1")
    v = result.verification
    assert v.act_in_force_on_date.status == VS.NOT_APPLICABLE
    assert "2027-09-27" in v.act_in_force_on_date.detail
    assert v.temporal_applicability.status == VS.NOT_APPLICABLE
    # 2026 acts have no HTML in ELI: wording cannot be read automatically.
    assert v.exact_wording.status == VS.UNVERIFIED
    assert result.wording_source.pdf_url


async def test_4_repealed_act_on_earlier_date(legal, eli_mock):
    earlier = await legal.get_provision(ActId.parse("DU/1996/405"), "1", as_of=date(2010, 1, 1))
    later = await legal.get_provision(ActId.parse("DU/1996/405"), "1", as_of=date(2020, 1, 1))
    assert earlier.verification.act_in_force_on_date.status == VS.VERIFIED
    assert earlier.verification.temporal_applicability.status != VS.VERIFIED
    assert later.verification.act_in_force_on_date.status == VS.NOT_APPLICABLE
    assert later.verification.temporal_applicability.status == VS.NOT_APPLICABLE


async def test_5_unresolvable_state_never_verified(legal, eli_mock):
    # Before the newest TJ's state date only the 2024 HTML applies, with amendments in between;
    # a future date can never be verified.
    for as_of in (date(2025, 6, 1), date(2027, 1, 1)):
        result = await legal.get_provision(ActId.parse("DU/2015/1322"), "4a", as_of=as_of)
        v = result.verification
        assert v.temporal_applicability.status == VS.UNVERIFIED, as_of
        # Source text checks may pass, but nothing claims the legal state as a whole.
        assert v.amendment_completeness.status == VS.UNVERIFIED
        assert v.substantive_relevance.status == VS.UNVERIFIED


async def test_6_nonexistent_unit(legal, eli_mock):
    result = await legal.get_provision(ActId.parse("DU/2015/1322"), "999")
    assert result.verification.act_existence.status == VS.VERIFIED
    assert result.verification.unit_existence.status == VS.NOT_FOUND
    assert "w pobranej wersji tekstu" in result.verification.unit_existence.detail
    assert result.verification.temporal_applicability.status == VS.UNVERIFIED
    assert result.text is None and result.citation is None


async def test_7_unavailable_source(legal):
    with respx.mock(base_url=ELI) as router:
        router.route().respond(503)
        result = await legal.get_provision(ActId.parse("DU/2015/1322"), "1")
    v = result.verification
    assert v.act_existence.status == VS.SOURCE_UNAVAILABLE
    assert v.temporal_applicability.status == VS.SOURCE_UNAVAILABLE
    assert result.text is None


async def test_missing_act_is_not_proof_of_nonexistence(legal, eli_mock):
    result = await legal.get_provision(ActId.parse("DU/2015/99999"), "1")
    assert result.verification.act_existence.status == VS.NOT_FOUND
    assert "Nie dowodzi" in result.verification.act_existence.detail


async def test_explicit_consolidated_text_is_respected(legal, eli_mock):
    result = await legal.get_provision(ActId.parse("DU/2024/98"), "2")
    assert result.wording_source.eli == "DU/2024/98"
    assert result.base_act.eli == "DU/2015/1322"
    assert any("wskazanego tekstu jednolitego" in w for w in result.warnings)


async def test_repealed_unit_in_consolidated_text(legal, eli_mock):
    result = await legal.get_provision(
        ActId.parse("DU/2024/98"), "7", "1", "7", as_of=date(2024, 1, 20)
    )
    assert result.text == "7) (uchylony)"
    assert result.verification.temporal_applicability.status == VS.NOT_APPLICABLE


async def test_amendments_listing(legal, eli_mock):
    result = await legal.get_amendments(ActId.parse("DU/2015/1322"), limit=3)
    assert result.amendments_total == 21
    assert [a.eli for a in result.amendments] == ["DU/2025/603", "DU/2024/1907", "DU/2023/1872"]
    assert result.amendments[0].title.startswith("Ustawa z dnia 4 kwietnia 2025 r.")
    assert result.latest_consolidated_text.eli == "DU/2025/817"
    assert result.after_latest_consolidated_text == []


async def test_delayed_amendment_is_pending(legal, eli_mock):
    result = await legal.get_amendments(ActId.parse("DU/1964/93"), limit=5)
    assert "DU/2026/507" in result.pending_on_date  # effective 2028-11-01


async def test_verify_citation_end_to_end(legal, eli_mock):
    result = await legal.verify_citation("art. 11 ust. 1 ustawy (Dz. U. z 2024 r. poz. 98)")
    assert result.resolution_method == "adres publikacji / ELI w cytacie"
    assert result.provision.wording_source.eli == "DU/2024/98"
    assert result.verification.act_existence.status == VS.VERIFIED
    assert result.verification.temporal_applicability.status == VS.UNVERIFIED
    assert any("merytorycznie" in q for q in result.unresolved_questions)


async def test_verify_citation_by_title(legal, eli_mock):
    result = await legal.verify_citation(
        "art. 2 ust. 1 ustawy z dnia 14 lipca 2000 r. o restrukturyzacji finansowej górnictwa siarki"
    )
    assert result.resolution_method == "wyszukanie po tytule (ELI)"
    assert result.provision.base_act.eli == "DU/2000/856"


async def test_verify_citation_unresolvable_act(legal):
    with respx.mock(base_url=ELI) as router:
        router.get("/acts/search").respond(json={"count": 0, "items": [], "totalCount": 0})
        result = await legal.verify_citation("art. 1 ustawy o czymś nieistniejącym")
    assert result.provision is None
    assert result.unresolved_questions


async def test_search_ranks_base_acts_first(legal, eli_mock):
    result = await legal.search_acts("Kodeks postępowania administracyjnego", limit=50)
    flags = [i.is_amending_act or i.is_consolidated_text_notice for i in result.items]
    assert flags == sorted(flags)  # base acts first, stable otherwise
    # In the raw ELI order the code itself comes after notices and amending acts.
    base_acts = [
        i.eli for i in result.items if not (i.is_amending_act or i.is_consolidated_text_notice)
    ]
    assert "DU/1960/168" in [i.eli for i in result.items[: len(base_acts)]]


async def test_unknown_keyword_suggests_vocabulary(legal, eli_mock):
    import pytest

    from polish_legal_mcp.errors import InvalidInputError

    with pytest.raises(InvalidInputError, match="nieruchomości"):
        await legal.search_acts("x" * 3, keyword="nieruchomosci")


async def test_get_act_toc_is_paginated(legal, eli_mock):
    details = await legal.get_act(ActId.parse("DU/2015/1322"), toc_offset=0, toc_limit=5)
    assert details.toc_total > 100 and len(details.toc) == 5 and details.toc_has_more
    assert any(t.kind == "U" and not t.official for t in details.texts)
    assert details.consolidated_texts[0] == "DU/2025/817"


async def test_get_act_pdf_only_warns(legal, eli_mock):
    details = await legal.get_act(ActId.parse("DU/2025/817"))
    assert details.toc == []
    assert details.consolidated_text_of == "DU/2015/1322"
    assert any("PDF" in w for w in details.warnings)


async def test_tk_rulings_get_title_and_date_from_their_own_record(legal, eli_mock):
    result = await legal.get_amendments(ActId.parse("DU/1996/405"))
    tk = result.constitutional_tribunal_rulings
    assert [t.eli for t in tk] == ["DU/2003/15"]
    assert tk[0].title and tk[0].title.startswith("Wyrok Trybunału Konstytucyjnego")
    assert tk[0].effective_date == date(2003, 1, 8)


async def test_change_on_consolidated_state_date_counts_as_included(legal):
    # Synthetic metadata: an amendment effective exactly on the TJ state date.
    meta = {"references": {"Akty zmieniające": [{"id": "DU/2026/184", "date": "2026-05-19"}]}}
    lo, hi = date(2026, 5, 19), date(2026, 10, 29)
    inclusive, _ = await legal._changes(meta, lo, hi)
    exclusive, _ = await legal._changes(meta, lo, hi, include_lo=False)
    assert [c.eli for c in inclusive] == ["DU/2026/184"]
    assert exclusive == []


async def test_pending_amendments_flag_staged_amending_acts(legal):
    # Synthetic: DU/2024/1907 made to enter into force on 2024-11-01 while ELI dates its change of
    # DU/2015/1322 on 2025-01-01 (real date) – i.e. an amending act entering in stages.
    staged = load_json("eli/DU_2024_1907.json") | {"entryIntoForce": "2024-11-01"}
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ELI}/acts/DU/2024/1907").respond(json=staged)
        router.get(url__regex=rf"^{re.escape(ELI)}/acts/").mock(side_effect=eli_fixture_response)
        result = await legal.get_provision(
            ActId.parse("DU/2015/1322"), "11", "1", as_of=date(2024, 12, 1)
        )
    note = next(w for w in result.warnings if w.startswith("Zmiany tego aktu datowane po"))
    assert (
        "DU/2024/1907 (zmiany tego aktu od 2025-01-01) – ustawa zmieniająca obowiązuje już od 2024-11-01"
        in note
    )
    assert "DU/2025/603 (zmiany tego aktu od 2025-05-23)." in note
