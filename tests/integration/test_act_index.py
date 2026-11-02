"""Refreshing the act index and using it in find_legal_basis, on real ELI data: the search page
for górnictwo siarki (base act DU/2000/856 + an amending act), the metadata and HTML of its
consolidated text DU/2024/1835 (captured 2026-10-29), and the search item and HTML of the
regulation DU/2023/1240 (captured 2026-11-01). Other searches answer with no hits (synthetic)."""

import re
from datetime import date

import pytest
import respx

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.errors import LegalMcpError
from polish_legal_mcp.services.act_index import ActIndex, load_act_index, refresh_act_index
from tests.conftest import ELI, TODAY, eli_fixture_response, load_json

EMPTY = {"items": [], "totalCount": 0}
# Tests that also use eli_mock request `index` first: built inside eli_mock, the nested respx
# router would not see these routes.


def _routes(router: respx.MockRouter, statutes: dict, regulations: dict = EMPTY) -> respx.Route:
    router.get(f"{ELI}/acts/search", params__contains={"type": "Ustawa"}).respond(json=statutes)
    router.get(f"{ELI}/acts/search").respond(json=regulations)
    return router.get(url__regex=rf"^{re.escape(ELI)}/acts/(DU|MP)/").mock(
        side_effect=eli_fixture_response
    )


@pytest.fixture
async def index(settings, tmp_path) -> ActIndex:
    eli = EliClient(settings, backoff_base=0)
    try:
        with respx.mock(assert_all_called=False) as router:
            _routes(router, load_json("eli/search_gornictwo_siarki.json"))
            return await refresh_act_index(eli, tmp_path / "act_index.sqlite", TODAY)
    finally:
        await eli.aclose()


async def test_refresh_indexes_base_statutes_and_their_articles(index):
    assert [a["ELI"] for a in index.acts] == ["DU/2000/856"]  # the amending act is dropped
    hits = await index.articles("kopalnie Siarkopol w Tarnobrzegu")
    assert [(h.act["ELI"], h.label) for h in hits] == [
        ("DU/2000/856", "art. 1 (tekst jednolity DU/2024/1835)")
    ]


async def test_second_refresh_refetches_nothing_and_glitches_keep_the_index(index, settings):
    eli = EliClient(settings, backoff_base=0)
    try:
        with respx.mock(assert_all_called=False) as router:
            acts = _routes(router, load_json("eli/search_gornictwo_siarki.json"))
            await refresh_act_index(eli, index.path, TODAY)
            assert acts.call_count == 0  # same text key: no struct, metadata or HTML requests
        with respx.mock(assert_all_called=False) as router:
            _routes(router, EMPTY)  # ELI answering "nothing in force" must not wipe the index
            with pytest.raises(LegalMcpError):
                await refresh_act_index(eli, index.path, TODAY)
    finally:
        await eli.aclose()
    reloaded = await load_act_index(index.path)
    assert reloaded is not None and [a["ELI"] for a in reloaded.acts] == ["DU/2000/856"]


async def test_find_legal_basis_points_at_the_matching_article(index, legal, eli_mock):
    legal.act_index = index
    result = await legal.find_legal_basis("kopalnie Siarkopol w Tarnobrzegu")
    hit = next(c for c in result.candidates if c.act.eli == "DU/2000/856")
    assert "treść aktów" in hit.match_method
    assert "art. 1 (tekst jednolity DU/2024/1835)" in hit.matched_terms
    assert "get_legal_provision" in result.warnings[0]


async def test_index_is_not_used_for_another_date(index, legal, eli_mock):
    legal.act_index = index
    result = await legal.find_legal_basis("kopalnie Siarkopol", as_of=date(2020, 1, 1))
    assert all("treść aktów" not in c.match_method for c in result.candidates)
    assert any("tylko dopasowania leksykalnego" in w for w in result.warnings)


async def test_transient_failure_is_retried_on_the_next_refresh(settings, tmp_path):
    path = tmp_path / "act_index.sqlite"
    statutes = load_json("eli/search_gornictwo_siarki.json")
    eli = EliClient(settings, backoff_base=0)
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(url__regex=r".*/text\.html$").respond(503)  # ELI hiccup on the text
            _routes(router, statutes)
            first = await refresh_act_index(eli, path, TODAY)
        assert await first.articles("kopalnie Siarkopol") == []
        with respx.mock(assert_all_called=False) as router:
            acts = _routes(router, statutes)
            second = await refresh_act_index(eli, path, TODAY)
        assert acts.call_count > 0  # the statute was fetched again, not frozen as "no text"
        assert [h.label for h in await second.articles("kopalnie Siarkopol")] == [
            "art. 1 (tekst jednolity DU/2024/1835)"
        ]
    finally:
        await eli.aclose()


async def test_regulation_texts_are_indexed_and_empty_html_is_skipped(settings, tmp_path):
    regulation = load_json("eli/search_mlodociani.json")
    eli = EliClient(settings, backoff_base=0)
    try:
        with respx.mock(assert_all_called=False) as router:
            _routes(router, EMPTY, regulation)
            index = await refresh_act_index(eli, tmp_path / "a.sqlite", TODAY)
        hits = await index.articles("prace z nadmiernym wysiłkiem fizycznym młodocianych")
        assert [(h.act["ELI"], h.label) for h in hits] == [
            ("DU/2023/1240", "załącznik (tekst pierwotny)")
        ]
        with respx.mock(assert_all_called=False) as router:
            router.get(url__regex=r".*/text\.html$").respond(200, text="")  # ELI's empty answer
            _routes(router, EMPTY, regulation)
            empty = await refresh_act_index(eli, tmp_path / "b.sqlite", TODAY)
        assert [a["ELI"] for a in empty.acts] == ["DU/2023/1240"]  # title still indexed
        assert await empty.articles("nadmiernym wysiłkiem fizycznym") == []
    finally:
        await eli.aclose()


async def test_malformed_struct_is_a_stable_answer_not_retried(settings, tmp_path):
    # ELI answers some /struct requests with broken JSON (seen 2026-11-02 for two statutes).
    path = tmp_path / "act_index.sqlite"
    statutes = load_json("eli/search_gornictwo_siarki.json")
    eli = EliClient(settings, backoff_base=0)
    try:
        for expected_struct_calls in (1, 0):
            with respx.mock(assert_all_called=False) as router:
                struct = router.get(url__regex=r".*/struct$").respond(200, text="{not json")
                _routes(router, statutes)
                index = await refresh_act_index(eli, path, TODAY)
            assert struct.call_count == expected_struct_calls  # second refresh: nothing refetched
        assert [h.label for h in await index.articles("kopalnie Siarkopol")] == [
            "art. 1 (tekst jednolity DU/2024/1835)"
        ]
    finally:
        await eli.aclose()
