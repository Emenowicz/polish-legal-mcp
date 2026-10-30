"""CJEU case law on real CELLAR responses captured 2026-10-30 (Schrems II C-311/18, Satamedia
C-73/07, Costa v ENEL 6/64, joined cases C-313/23 and C-316/23, RODO case-law metadata)."""

from typing import Any

import pytest

from polish_legal_mcp.errors import InvalidInputError, UpstreamNotFoundError
from polish_legal_mcp.services.cjeu import describe_location


@pytest.fixture
async def cjeu(settings) -> Any:
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.cjeu import CjeuService

    client = EurLexClient(settings, backoff_base=0)
    yield CjeuService(client)
    await client.aclose()


def test_cellar_location_codes():
    assert describe_location("A17P1LD") == "art. 17 ust. 1 lit. d"
    assert describe_location("A04PT7") == "art. 4 pkt 7"
    assert describe_location("A45") == "art. 45"
    assert describe_location("XYZ") == "XYZ"


async def test_judgment_by_case_number_with_paragraph_and_citation(cjeu, eurlex_mock):
    result = await cjeu.get_judgment("C‑311/18", paragraphs="134")  # non-breaking hyphen
    assert (result.celex, result.document_type, result.court) == (
        "62018CJ0311",
        "wyrok",
        "Trybunał Sprawiedliwości",
    )
    assert result.citation == (
        "wyrok Trybunału Sprawiedliwości z dnia 16 lipca 2020 r., Data Protection Commissioner "
        "przeciwko Facebook Ireland Limited i Maximillianowi Schremsowi, C-311/18, "
        "EU:C:2020:559, pkt 134"
    )
    assert result.language_of_case == "angielski"
    assert result.paragraph_count == 203
    assert result.paragraphs[0].number == 134
    assert result.paragraphs[0].text.startswith("W tym względzie, jak zauważył Rzecznik generalny")
    assert result.operative_part and result.operative_part.startswith("1) Artykuł 2 ust. 1 i 2")
    rodo = next(a for a in result.interpreted_acts if a.celex == "32016R0679")
    assert "art. 46 ust. 2 lit. c" in rodo.provisions
    assert any("tłumaczeniem" in w for w in result.warnings)


async def test_search_text_returns_paragraph_numbers(cjeu, eurlex_mock):
    result = await cjeu.get_judgment(
        "62018CJ0311", search_text="Tarcz", include_operative_part=False
    )
    assert result.matches and all("tarcz" in m.text.lower() for m in result.matches)
    assert result.operative_part is None and result.paragraphs == []


async def test_case_lookup_falls_back_to_full_text_for_joined_cases_and_variants(cjeu, eurlex_mock):
    joined = await cjeu.get_judgment("C-316/23")
    assert joined.celex == "62023CJ0313" and joined.case_number == "C-313/23 i C-316/23"
    # Exact identifier lookup finds nothing for C-73/07; the full-text step lists judgment, order
    # and AG opinion; the judgment is chosen.
    satamedia = await cjeu.get_judgment("C-73/07", paragraphs="33-34")
    assert satamedia.celex == "62007CJ0073"
    assert satamedia.other_documents == ["62007CC0073", "62007CO0073"]
    assert [p.number for p in satamedia.paragraphs] == [33, 34]
    assert satamedia.citation and satamedia.citation.endswith("EU:C:2008:727, pkt 33–34")


async def test_old_layout_returns_metadata_and_operative_part_only(cjeu, eurlex_mock):
    result = await cjeu.get_judgment("6/64", paragraphs="1")
    assert result.celex == "61964CJ0006" and result.paragraph_count == 0
    assert result.paragraphs == []
    assert result.operative_part
    assert any("starszy układ" in w for w in result.warnings)


async def test_unknown_case_is_not_found_and_bad_input_rejected(cjeu, eurlex_mock):
    with pytest.raises(UpstreamNotFoundError, match="Nie dowodzi"):
        await cjeu.get_judgment("C-999/99")
    with pytest.raises(InvalidInputError):
        await cjeu.get_judgment("C-311/18; DROP")
    with pytest.raises(InvalidInputError):
        await cjeu.get_judgment("C-311/18", paragraphs="1-50")


async def test_judgments_interpreting_rodo_article(cjeu, eurlex_mock):
    by_article = await cjeu.search_for_act("RODO", "17", 5)
    assert by_article.act_celex == "32016R0679"
    assert by_article.items[0].case_number == "C-26/22 i C-64/22"
    assert "art. 17 ust. 1 lit. d" in by_article.items[0].provisions
    assert all(any(p.startswith("art. 17") for p in i.provisions) for i in by_article.items)
    newest = await cjeu.search_for_act("32016R0679", None, 5)
    dates = [i.decision_date for i in newest.items]
    assert dates == sorted(dates, reverse=True)  # type: ignore[type-var]
    assert any("niepełne" in w for w in newest.warnings)
    with pytest.raises(InvalidInputError):
        await cjeu.search_for_act("ustawa o podatku", None, 5)
