"""Judgments citing a provision, on real SAOS responses captured 2026-10-30
(search for Civil Code art. 5; judgment texts trimmed to 2000 characters to keep fixtures small)."""

from collections.abc import Iterator

import pytest
import respx

from polish_legal_mcp.errors import InvalidInputError
from polish_legal_mcp.models import ActId
from polish_legal_mcp.services.judgments import cited_units
from tests.conftest import SAOS, load_json

KC = ActId("DU", 1964, 93)
IDS = (554870, 554447, 554457, 554454)


@pytest.fixture
def saos_provision() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=SAOS, assert_all_called=False) as router:
        router.get("/search/judgments").respond(json=load_json("saos/search_kc_art5.json"))
        for judgment_id in IDS:
            router.get(f"/judgments/{judgment_id}").respond(
                json=load_json(f"saos/judgment_{judgment_id}.json")
            )
        yield router


def test_cited_units_parses_saos_entries():
    text = (
        "Ustawa - Kodeks cywilny (Dz. U. z 1964 r. Nr 16, poz. 93 - art. 5; art. 22(1); "
        "art. 98 § 1(1); art. 7 ust. 1; art. 411 pkt. 2; art. 75 c)"
    )
    assert [(a, p) for a, p, _ in cited_units(text)] == [
        ("5", None),
        ("22_1", None),
        ("98", "1_1"),
        ("7", "1"),
        ("411", None),
        ("75c", None),
    ]
    assert cited_units("Ustawa bez listy przepisów (Dz. U. z 2004 r. Nr 251, poz. 2507 - )") == []


async def test_only_judgments_citing_the_article_are_returned(judgments, saos_provision):
    result = await judgments.for_provision(KC, "Kodeks cywilny", "5")
    params = saos_provision.calls[0].request.url.params
    assert params["lawJournalEntryCode"] == "1964/93"
    assert params["referencedRegulation"] == '"art. 5"'
    # 554870 and 554454 cite other Civil Code articles only (e.g. art. 58, art. 22(1)).
    assert [j.judgment_id for j in result.items] == [554447, 554457]
    assert result.items[0].matched_references == ["art. 5", "art. 5 ust. 3"]
    assert result.items[1].case_numbers == ["II C 2145/25"]
    assert (result.total_candidates, result.candidates_checked, result.has_more) == (35873, 4, True)
    assert "nie musi oznaczać jego wykładni" in result.warnings[0]


async def test_paragraph_and_superscript_articles(judgments, saos_provision):
    by_paragraph = await judgments.for_provision(KC, None, "art. 5", "ust. 3")
    assert [j.judgment_id for j in by_paragraph.items] == [554447]
    assert by_paragraph.provision == "art. 5 ust. 3"

    superscript = await judgments.for_provision(KC, None, "22¹")
    assert saos_provision.calls.last.request.url.params["referencedRegulation"] == '"art. 22(1)"'
    assert {j.judgment_id for j in superscript.items} == set(IDS)  # all cite art. 22(1)


async def test_no_confirmed_judgment_is_not_reported_as_no_case_law(judgments, saos_provision):
    result = await judgments.for_provision(KC, None, "999")
    assert result.items == []
    assert any("Nie dowodzi to braku orzecznictwa" in w for w in result.warnings)


async def test_monitor_polski_is_refused(judgments):
    with pytest.raises(InvalidInputError):
        await judgments.for_provision(ActId("MP", 2020, 1), None, "1")
