"""SAOS search/retrieval on real responses captured 2026-10-29."""

from datetime import date

import pytest
import respx

from polish_legal_mcp.errors import UpstreamNotFoundError
from polish_legal_mcp.services.judgments import html_to_text
from tests.conftest import SAOS, load_json


async def test_search_maps_results_and_pagination(judgments, saos_mock):
    result = await judgments.search(
        "nieruchomości Skarbu Państwa", court_type="COMMON", date_from=date(2020, 1, 1), limit=3
    )
    params = saos_mock.calls.last.request.url.params
    assert params["all"] == "nieruchomości Skarbu Państwa"
    assert (params["courtType"], params["judgmentDateFrom"], params["pageSize"]) == (
        "COMMON",
        "2020-01-01",
        "3",
    )
    assert result.total_results == 50609 and result.has_more
    first = result.items[0]
    assert first.case_numbers == ["I C 101/26"]
    assert first.court_name == "Sąd Rejonowy w Świnoujściu"
    assert "<em>" not in (first.excerpt or "")
    assert first.source_url == "https://www.saos.org.pl/judgments/554287"
    assert "WSA/NSA" in result.coverage_note


async def test_supreme_court_name(judgments):
    with respx.mock:
        respx.get(f"{SAOS}/search/judgments").respond(
            json=load_json("saos/search_supreme_nieruchomosci.json")
        )
        result = await judgments.search("nieruchomości Skarbu Państwa", court_type="SUPREME")
    assert result.items[0].court_name == "Sąd Najwyższy"
    assert result.items[0].case_numbers == ["V CSK 314/09"]


async def test_get_judgment_text_is_bounded(judgments, saos_mock):
    result = await judgments.get(554287, text_offset=0, text_limit=500, search_text="art. 113")
    assert result.case_numbers == ["I C 101/26"]
    assert result.judgment_date == date(2026, 10, 15)
    assert result.text.startswith("Sygn. akt I C 101/26")
    assert len(result.text) == 500 and result.has_more
    assert result.text_length_total > 20_000
    assert result.excerpts and all("art. 113" in e.lower() for e in result.excerpts)
    assert result.legal_bases == ["art. 677 kc"]
    assert result.referenced_regulations[0].eli_candidate == "DU/1964/296"
    assert result.original_source_url and result.original_source_url.startswith("https://")


async def test_get_judgment_paging(judgments, saos_mock):
    first = await judgments.get(554287, text_limit=1000)
    second = await judgments.get(554287, text_offset=1000, text_limit=1000)
    assert first.text[-50:] != second.text[:50] and second.text_offset == 1000


async def test_missing_judgment(judgments, saos_mock):
    with pytest.raises(UpstreamNotFoundError):
        await judgments.get(1)


def test_html_to_text_keeps_anonymised_text():
    html = '<p>Powód <span class="anon-block">W. K.</span> wniósł <em>pozew</em>.</p><p>Drugi</p>'
    assert html_to_text(html) == "Powód W. K. wniósł pozew.\nDrugi"
