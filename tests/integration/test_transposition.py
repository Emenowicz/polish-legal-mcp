"""Polish implementing measures on real CELLAR responses captured 2026-10-30 (directive 2014/24
on public procurement, directive 2019/1937 on whistleblowers); ELI metadata mocked."""

import pytest

from polish_legal_mcp.errors import InvalidInputError
from tests.conftest import ELI


@pytest.fixture
async def eu_with_eli(settings):
    from polish_legal_mcp.clients.eli import EliClient
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.eu_law import EuLawService

    eurlex, eli = EurLexClient(settings, backoff_base=0), EliClient(settings, backoff_base=0)
    yield EuLawService(eurlex, eli=eli)
    await eurlex.aclose()
    await eli.aclose()


async def test_measures_are_deduplicated_mapped_to_eli_and_cross_checked(eu_with_eli, eurlex_mock):
    # Synthetic ELI metadata for one position; the others answer 404 (cross-check skipped).
    eurlex_mock.get(f"{ELI}/acts/DU/2019/2019").respond(
        json={
            "title": "Ustawa z dnia 11 września 2019 r. - Prawo zamówień publicznych",
            "status": "obowiązujący",
        }
    )
    eurlex_mock.get(url__regex=rf"^{ELI}/acts/").respond(404)
    result = await eu_with_eli.transposition("dyrektywa 2014/24/UE", limit=50)
    assert result.act_celex == "32014L0024" and result.total_measures == 15
    by_eli = {m.eli: m for m in result.measures}
    pzp = by_eli["DU/2019/2019"]
    assert pzp.eli_title and "Prawo zamówień publicznych" in pzp.eli_title
    assert pzp.eli_status == "obowiązujący"
    # One measure notified for three directives keeps its three CELLAR ids but appears once.
    assert len(by_eli["DU/2016/1125"].cellar_ids) == 3
    assert result.measures[0].eli == "DU/2025/1165"  # newest first
    assert "nie dowodzi pełnej" in result.warnings[0]


async def test_measure_without_dziennik_ustaw_number_has_no_eli(eu_with_eli, eurlex_mock):
    result = await eu_with_eli.transposition("32019L1937")
    assert len(result.measures) == 1  # notified twice, same act
    assert result.measures[0].eli is None
    assert "sygnalistów" in (result.measures[0].title or "")


async def test_unknown_act_rejected(eu_with_eli):
    with pytest.raises(InvalidInputError):
        await eu_with_eli.transposition("ustawa o czymś")
