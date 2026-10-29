"""EU provisions end to end on real CELLAR/EUR-Lex responses captured 2026-10-29 ('today' = then)."""

import re
from datetime import date

import respx

from polish_legal_mcp.models import VerificationStatus as VS
from tests.conftest import EU, eurlex_fixture_response


async def test_rodo_uses_consolidated_text_with_polish_corrigenda(eu_law, eurlex_mock):
    result = await eu_law.get_provision("RODO", "4", point="1")
    v = result.verification
    assert result.celex == "32016R0679"
    assert result.text.startswith("1) „dane osobowe” oznaczają wszelkie informacje")
    assert v.exact_wording.status == VS.VERIFIED
    assert v.temporal_applicability.status == VS.VERIFIED
    assert [c.celex for c in result.polish_corrigenda] == ["32016R0679R(02)", "32016R0679R(03)"]
    assert result.citation.endswith("(Dz. Urz. UE L 119 z 4.5.2016, str. 1, ze sprost.)")
    assert any("charakter informacyjny" in w for w in result.warnings)


async def test_amendment_not_yet_consolidated_is_unverified(eu_law, eurlex_mock):
    result = await eu_law.get_provision("dyrektywa 2014/24/UE", "4")
    assert result.text and "Kwoty progowe" in result.structural_path[0]
    temporal = result.verification.temporal_applicability
    assert temporal.status == VS.UNVERIFIED and "32026L0806 (od 2026-05-10)" in temporal.detail
    assert any("Dyrektywa wiąże państwa członkowskie" in w for w in result.warnings)


async def test_before_entry_into_force_not_applicable(eu_law, eurlex_mock):
    result = await eu_law.get_provision("32016R0679", "4", point="1", as_of=date(2016, 5, 1))
    assert result.verification.act_in_force_on_date.status == VS.NOT_APPLICABLE
    assert result.verification.temporal_applicability.status == VS.NOT_APPLICABLE


async def test_oj_fallback_without_corrigenda_is_unverified(eu_law):
    # Synthetic: the consolidated version is unavailable, so the 2016 OJ text is used.
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{EU}/resource/celex/02016R0679-20160504").respond(404)
        router.get(url__regex=rf"^{re.escape(EU)}/").mock(side_effect=eurlex_fixture_response)
        result = await eu_law.get_provision("RODO", "4", point="1")
    assert result.text.startswith("1) „dane osobowe” oznaczają informacje o")
    temporal = result.verification.temporal_applicability
    assert temporal.status == VS.UNVERIFIED
    assert "32016R0679R(02)" in temporal.detail and "32016R0679R(03)" in temporal.detail


async def test_unknown_act_not_found(eu_law, eurlex_mock):
    result = await eu_law.get_provision("32016R9999", "1")
    assert result.verification.act_existence.status == VS.NOT_FOUND
    assert "Nie dowodzi" in result.verification.act_existence.detail


async def test_source_unavailable(eu_law):
    with respx.mock(base_url=EU) as router:
        router.route().respond(503)
        result = await eu_law.get_provision("RODO", "28")
    assert result.verification.act_existence.status == VS.SOURCE_UNAVAILABLE


async def test_eu_citation_routed_from_verify_legal_citation(legal, eu_law, eurlex_mock):
    legal._eu = eu_law
    result = await legal.verify_citation("art. 28 ust. 9 RODO")
    assert result.provision is None and result.eu_provision is not None
    assert result.eu_provision.text.endswith(
        "o których mowa w ust. 3 i 4, mają formę pisemną, w tym formę elektroniczną."
    )
    assert result.resolution_method.startswith("akt UE 32016R0679")
