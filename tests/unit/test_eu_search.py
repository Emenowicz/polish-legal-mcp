"""EU act title search: term building (unit) and ranking on a real CELLAR response captured
2026-10-30 for "ochrona danych osobowych"."""

import pytest

from polish_legal_mcp.errors import InvalidInputError
from polish_legal_mcp.services.eu_law import title_terms


def test_title_terms_use_long_prefixes_and_drop_stop_words():
    assert title_terms("Ochrona danych osobowych") == ["ochron*", "danych", "osobowy*"]
    assert title_terms("zamówienia publiczne w sprawie UE") == ["zamówie*", "publicz*"]
    assert title_terms("dyrektywa 2014/24/UE") == ["dyrekty*"]
    assert title_terms("a i w") == []


async def test_search_ranks_base_legislative_acts_first(eu_law, eurlex_mock):
    result = await eu_law.search_acts("ochrona danych osobowych", limit=5)
    celex = [i.celex for i in result.items]
    assert "32016R0679" in celex[:3]
    first = result.items[0]
    assert first.legislative and not first.amending and first.in_force
    assert result.total_matched >= len(result.items)
    with pytest.raises(InvalidInputError):
        await eu_law.search_acts("w i 2016")
