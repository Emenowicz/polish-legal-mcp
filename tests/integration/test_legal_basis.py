"""find_legal_basis with the case-law signal. SAOS: real responses captured 2026-10-30 (four
judgments citing the Civil Code); ELI: real metadata fixtures, search answering no hits
(synthetic) so that only the case-law signal contributes."""

import re

import respx

from polish_legal_mcp.models import ActId
from tests.conftest import ELI, SAOS, eli_fixture_response, load_json


async def test_cited_acts_counts_judgments_per_act(judgments):
    with respx.mock(base_url=SAOS, assert_all_called=False) as router:
        router.get("/search/judgments").respond(json=load_json("saos/search_kc_art5.json"))
        for judgment_id in (554870, 554447, 554457, 554454):
            router.get(f"/judgments/{judgment_id}").respond(
                json=load_json(f"saos/judgment_{judgment_id}.json")
            )
        acts, checked = await judgments.cited_acts("art 5")
    assert checked == 4
    counts = {a.eli: n for a, n, _ in acts}
    assert counts["DU/1964/93"] == 4  # every judgment cites the Civil Code


async def test_case_law_signal_ranks_cited_act_and_skips_procedural_acts(legal):
    cited = ([(ActId("DU", 1964, 296), 10, "k.p.c."), (ActId("DU", 1964, 93), 9, "k.c.")], 10)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ELI}/keywords").respond(json=load_json("eli/keywords.json"))
        router.get(f"{ELI}/acts/search").respond(
            json={"count": 0, "totalCount": 0, "items": [], "offset": 0}
        )
        router.get(url__regex=rf"^{re.escape(ELI)}/acts/").mock(side_effect=eli_fixture_response)
        result = await legal.find_legal_basis("zachowek po zmarłym ojcu", cited=cited)
    assert [c.act.eli for c in result.candidates] == ["DU/1964/93"]
    assert result.candidates[0].matched_terms == ["powołany w 9 z 10 orzeczeń SAOS"]
    assert any("Sygnał z orzecznictwa" in w for w in result.warnings)
    assert not any(c.act.eli == "DU/1964/296" for c in result.candidates)
