"""Opt-in checks against the real official APIs: `uv run pytest -m live`.

They depend on upstream availability and current data, so they are excluded from CI.
"""

import pytest

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.clients.saos import SaosClient
from polish_legal_mcp.models import ActId
from polish_legal_mcp.models import VerificationStatus as VS
from polish_legal_mcp.services.judgments import JudgmentService
from polish_legal_mcp.services.legal import LegalService

pytestmark = pytest.mark.live


@pytest.fixture
async def live_legal(settings):
    client = EliClient(settings)
    yield LegalService(client)
    await client.aclose()


@pytest.fixture
async def live_judgments(settings):
    client = SaosClient(settings)
    yield JudgmentService(client)
    await client.aclose()


async def test_live_search_finds_base_act(live_legal):
    result = await live_legal.search_acts("gospodarce nieruchomo", act_type="Ustawa", limit=20)
    assert "DU/1997/741" in [a.eli for a in result.items]


async def test_live_provision_has_official_source(live_legal):
    result = await live_legal.get_provision(ActId.parse("DU/1960/168"), "61", "1")
    assert result.text and result.text.startswith("§ 1.")
    assert result.verification.source_authenticity.status == VS.VERIFIED
    assert result.wording_source and (
        result.wording_source.html_url or result.wording_source.pdf_url
    )


async def test_live_saos_search_and_get(live_judgments):
    found = await live_judgments.search("nieruchomości Skarbu Państwa", limit=2)
    assert found.total_results > 0
    detail = await live_judgments.get(found.items[0].judgment_id, text_limit=500)
    assert detail.source_url.startswith("https://www.saos.org.pl/judgments/")


async def test_live_eu_rodo_corrected_wording(settings):
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.eu_law import EuLawService

    client = EurLexClient(settings)
    result = await EuLawService(client).get_provision("RODO", "4", point="1")
    await client.aclose()
    assert result.text and "wszelkie informacje" in result.text
    assert result.official_journal == "Dz. Urz. UE L 119 z 4.5.2016, str. 1"


async def test_live_pdf_consolidated_text_confirms_html(live_legal):
    result = await live_legal.get_provision(ActId.parse("DU/1964/93"), "5")
    assert result.text_origin in ("html_confirmed_by_pdf", "pdf_automatic")
    assert result.pdf_page is not None
    assert result.wording_source and not result.wording_source.html_available


async def test_live_judgments_for_civil_code_article(live_legal, live_judgments):
    base, title = await live_legal.base_act(ActId.parse("DU/2026/795"))  # consolidated text of k.c.
    assert base.eli == "DU/1964/93"
    result = await live_judgments.for_provision(base, title, "415")
    assert result.items and all("art. 415" in j.matched_references for j in result.items)


async def test_live_cjeu_judgment_and_case_law_for_rodo(settings):
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.cjeu import CjeuService

    client = EurLexClient(settings)
    service = CjeuService(client)
    judgment = await service.get_judgment("C-311/18", paragraphs="134")
    found = await service.search_for_act("RODO", "17", 3)
    await client.aclose()
    assert judgment.citation and "EU:C:2020:559, pkt 134" in judgment.citation
    assert judgment.paragraphs and judgment.operative_part
    assert found.items and found.items[0].ecli


async def test_live_eu_act_search_finds_rodo(settings):
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.eu_law import EuLawService

    client = EurLexClient(settings)
    result = await EuLawService(client).search_acts("ochrona danych osobowych", limit=5)
    await client.aclose()
    assert "32016R0679" in [i.celex for i in result.items]
