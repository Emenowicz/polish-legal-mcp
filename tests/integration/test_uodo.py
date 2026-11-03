"""UODO decisions on real Portal Orzeczeń UODO API responses captured 2026-11-03
(tests/fixtures/uodo; in meta_*.json the staff e-mail and editor name are replaced with
placeholders). The mock ignores the date filter and serves the captured search lists."""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
import respx

from polish_legal_mcp.clients.uodo import SEARCH, UodoClient
from polish_legal_mcp.errors import InvalidInputError, UpstreamNotFoundError
from polish_legal_mcp.services.uodo import UodoService
from tests.conftest import FIXTURES, load_json

UODO = "https://orzeczenia.uodo.gov.pl/api"
SEARCH_PATH = f"/api{SEARCH}/"
ITEMS = "/api/documents/public/items/"
DOCUMENTS = {
    "urn:ndoc:gov:pl:uodo:2026:dke_561_5": "dke_561_5_2026",
    "urn:ndoc:gov:pl:uodo:2021:dkn_5131_51": "dkn_5131_51_2021",
}


def portal(court_names_status: int = 200):
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path  # percent-decoded
        count = int(request.url.params.get("count", "100"))
        if path.startswith(SEARCH_PATH):
            conditions = path[len(SEARCH_PATH) :]
            if conditions.startswith("refid:in:"):
                if court_names_status != 200:
                    return httpx.Response(court_names_status)
                return httpx.Response(200, json=load_json("uodo/names_dkn_5131_51_2021.json"))
            if "refname:glob:*DKE.561.5.2026*" in conditions:
                return httpx.Response(200, json=load_json("uodo/search_dke_561_5_2026.json"))
            if "refname:glob:*ZSPR.405.67.2019*" in conditions:
                # Synthetic list: the real refname of a record with two signatures, and a
                # longer signature that only contains the requested one.
                return httpx.Response(
                    200,
                    json=[
                        {
                            "refid": "urn:ndoc:gov:pl:uodo:2019:zspr_405_67_x",
                            "refname": "ZSPR.405.67.2019.II",
                        },
                        {
                            "refid": "urn:ndoc:gov:pl:uodo:2019:zspr_405_67",
                            "refname": "ZSPR.421.2.2019 ZSPR.405.67.2019",
                        },
                    ],
                )
            if "content_pl:glob:*monitor*/content_pl:glob:*kamer*" in conditions:
                items = load_json("uodo/search_monitor_kamer.json")
                return httpx.Response(200, json=items[:count])
            return httpx.Response(200, json=[])
        for refid, stem in DOCUMENTS.items():
            if path == f"{ITEMS}{refid}/meta.json":
                return httpx.Response(200, json=load_json(f"uodo/meta_{stem}.json"))
            if path == f"{ITEMS}{refid}:0/body.html":
                html = gzip.decompress((FIXTURES / "uodo" / f"body_{stem}.html.gz").read_bytes())
                return httpx.Response(200, text=html.decode("utf-8"))
        return httpx.Response(404)

    return respond


@pytest.fixture
def uodo_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=UODO, assert_all_called=False) as router:
        router.route().mock(side_effect=portal())
        yield router


@pytest.fixture
async def uodo(settings):
    client = UodoClient(settings, backoff_base=0)
    yield UodoService(client)
    await client.aclose()


async def test_search_words_dates_and_paging(uodo, uodo_mock):
    result = await uodo.search("Monitor kamer", date_from=date(2025, 1, 1), limit=5)
    request = uodo_mock.calls.last.request
    assert request.url.path == (
        f"{SEARCH_PATH}publicator_subtype:eq:uodo/content_pl:glob:*monitor*"
        "/content_pl:glob:*kamer*/date_announcement:ge:2025-01-01"
    )
    assert request.url.params["count"] == "6"
    assert request.url.params["order"] == "-date_announcement"
    assert result.query_words == ["monitor", "kamer"]
    assert result.has_more and len(result.items) == 5
    first = result.items[0]
    assert first.signature == "DKE.561.5.2026"
    assert first.decision_date == date(2026, 9, 5)
    assert first.status == "nieprawomocna"
    assert first.citation == (
        "decyzja Prezesa Urzędu Ochrony Danych Osobowych z dnia 5 września 2026 r., DKE.561.5.2026"
    )
    assert first.source_url == (
        "https://orzeczenia.uodo.gov.pl/document/urn:ndoc:gov:pl:uodo:2026:dke_561_5/content"
    )
    assert "bez odmiany" in result.warnings[0]


async def test_decision_by_signature(uodo, uodo_mock):
    result = await uodo.get(" dke.561.5.2026 ", search_text="monitoring", text_limit=1000)
    assert result.refid == "urn:ndoc:gov:pl:uodo:2026:dke_561_5"
    assert result.status == "nieprawomocna"
    assert result.warnings[0].startswith("Decyzja nieprawomocna")
    assert result.signed_by == "Prezes Urzędu Ochrony Danych Osobowych Mirosław Wróblewski"
    assert result.department == "Departament Kar i Egzekucji"
    # Portal staff (who entered or edited the record) never reach the output.
    dumped = result.model_dump_json()
    assert "pracownik@uodo.gov.pl" not in dumped and "Redaktor Portalu" not in dumped
    references = {r.name: r for r in result.references}
    assert references["Dz.U. 1960 nr 30 poz. 168"].eli_candidate == "DU/1960/168"
    assert references["C-212/13"].kind == "TSUE"
    assert len(result.text) == 1000 and result.has_more
    assert result.text.startswith("Decyzja\nDKE.561.5.2026")
    assert result.excerpts and all("monitoring" in e.lower() for e in result.excerpts)
    assert "opublikowana w portalu 2026-09-25" in result.source_note


@pytest.mark.parametrize(("names_status", "court_case"), [(200, "III OSK 3311/23"), (503, None)])
async def test_repealed_decision_names_the_court_ruling(settings, names_status, court_case):
    client = UodoClient(settings, backoff_base=0)
    with respx.mock(base_url=UODO, assert_all_called=False) as router:
        router.route().mock(side_effect=portal(names_status))
        result = await UodoService(client).get("urn:ndoc:gov:pl:uodo:2021:dkn_5131_51")
    await client.aclose()
    assert result.status == "uchylona" and result.status_date == date(2025, 3, 19)
    repeal = result.status_history[-1]
    assert repeal.event == "uchylenie"
    # Without the portal's names the identifier is shown instead of a made-up signature.
    assert repeal.court_case == (court_case or "urn:ndoc:court:pl:sa:2023:iii_osk_3311")
    assert result.status_history[1].event == "defended"  # undocumented use: kept as is
    assert result.warnings[0].startswith("Decyzja uchylona wg portalu UODO (2025-03-19")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": "art:eq:x"},
        {"query": "monitor*"},
        {"query": "a/b ccc"},
        {"query": "ab"},
        {"query": "jeden dwa trzy cztery pięć sześć"},
        {"signature": "DKE/561/5/2026"},
        {"signature": "DKE.561.5.2026/../x"},
        {},
    ],
)
async def test_invalid_search_never_reaches_the_portal(uodo, uodo_mock, kwargs):
    with pytest.raises(InvalidInputError):
        await uodo.search(**kwargs)
    assert uodo_mock.calls.call_count == 0


async def test_invalid_identifier_never_reaches_the_portal(uodo, uodo_mock):
    with pytest.raises(InvalidInputError):
        await uodo.get("urn:ndoc:gov:pl:uodo:2026:../meta")
    assert uodo_mock.calls.call_count == 0


async def test_signature_inside_a_record_with_two_signatures(uodo, uodo_mock):
    result = await uodo.search(signature="zspr.405.67.2019")
    assert [i.refid for i in result.items] == ["urn:ndoc:gov:pl:uodo:2019:zspr_405_67"]


def test_signature_shapes_seen_in_the_portal():
    from polish_legal_mcp.services.uodo import normalize_signature

    for signature in ("ZWAD.405.31.331.2019", "ZSOŚS.440.1.2019.II", "DS.523.6546.2024"):
        assert normalize_signature(signature.lower()) == signature
    # Two signatures in one record are cited like several case numbers.
    from polish_legal_mcp.services.uodo import _citation

    citation = _citation(
        "Decyzja Prezesa UODO nr ZSPR.421.2.2019",
        date(2024, 1, 17),
        "ZSPR.421.2.2019 ZSPR.405.67.2019",
    )
    assert citation and citation.endswith("2024 r., ZSPR.421.2.2019, ZSPR.405.67.2019")


async def test_unknown_signature_is_not_found(uodo, uodo_mock):
    with pytest.raises(UpstreamNotFoundError, match=r"DKE\.561\.99\.2026"):
        await uodo.get("DKE.561.99.2026")


async def test_search_words_are_not_logged(uodo, uodo_mock, caplog):
    caplog.set_level(logging.INFO, logger="polish_legal_mcp.clients.http")
    await uodo.search("sąsiedzi monitoring")
    logged = [json.dumps(r.__dict__.get("fields", {}), ensure_ascii=False) for r in caplog.records]
    assert logged and all("sąsiedzi" not in line for line in logged)
    assert caplog.records[0].__dict__["fields"]["path"] == SEARCH
