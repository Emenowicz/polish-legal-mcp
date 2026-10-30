"""Pending bills on real Sejm API responses captured 2026-10-30 (term 10; the process list is
trimmed to the 92 processes with "odeks" in the title)."""

from collections.abc import Iterator

import pytest
import respx

from polish_legal_mcp.errors import InvalidInputError
from polish_legal_mcp.services.legislation import act_name_phrases
from tests.conftest import load_json

SEJM = "https://api.sejm.gov.pl/sejm"


@pytest.fixture
def sejm_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=SEJM, assert_all_called=False) as router:
        router.get("/term").respond(json=load_json("sejm/term.json"))
        router.get("/term10/processes").respond(json=load_json("sejm/processes_kodeks.json"))
        for number in ("2447", "1318", "3234"):
            router.get(f"/term10/processes/{number}").respond(
                json=load_json(f"sejm/process_{number}.json")
            )
        yield router


@pytest.fixture
async def legislation(settings):
    from polish_legal_mcp.clients.sejm import SejmClient
    from polish_legal_mcp.services.legislation import LegislationService

    client = SejmClient(settings, backoff_base=0)
    yield LegislationService(client)
    await client.aclose()


def test_act_name_phrases_include_genitive_of_codes():
    assert act_name_phrases("Ustawa z dnia 23 kwietnia 1964 r. – Kodeks cywilny") == [
        "kodeks cywilny",
        "kodeksu cywilnego",
    ]
    assert act_name_phrases("Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy")[1] == "kodeksu pracy"
    assert act_name_phrases("Ustawa z dnia 21 sierpnia 1997 r. o gospodarce nieruchomościami") == [
        "o gospodarce nieruchomościami"
    ]


async def test_pending_bills_naming_the_civil_code(legislation, sejm_mock):
    result = await legislation.pending_bills(
        "DU/1964/93", "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny"
    )
    assert result.term == 10
    assert [i.number for i in result.items] == ["2447", "1318", "3234"]  # newest first
    assert all("kodeksu cywilnego" in i.title.lower() for i in result.items)
    stage = result.items[1].current_stage or ""
    assert stage.startswith("II czytanie na posiedzeniu Sejmu (2025-12-17)")
    assert result.items[0].sejm_url.endswith("PrzebiegProc.xsp?nr=2447")
    assert "nie są prawem obowiązującym" in result.warnings[0]
    # Passed or closed processes are never reported as pending.
    assert result.pending_scanned < 92


async def test_query_only_and_bad_input(legislation, sejm_mock):
    result = await legislation.pending_bills(query="Kodeksu karnego")
    assert all("kodeksu karnego" in i.title.lower() for i in result.items)
    with pytest.raises(InvalidInputError):
        await legislation.pending_bills()
