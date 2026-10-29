"""Shared fixtures. Upstream APIs are mocked with respx using real responses saved in
tests/fixtures (captured from api.sejm.gov.pl and saos.org.pl on 2026-10-29) unless a test
states that its data is synthetic."""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.clients.saos import SaosClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.services.judgments import JudgmentService
from polish_legal_mcp.services.legal import LegalService

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 29)
ELI = "https://api.sejm.gov.pl/eli"
SAOS = "https://www.saos.org.pl/api"
ACT_PATH = re.compile(r"^/eli/acts/(DU|MP)/(\d{4})/(\d+)(/struct|/text\.html)?$")


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def eli_fixture_response(request: httpx.Request) -> httpx.Response:
    """Serve tests/fixtures/eli/<PUB>_<YEAR>_<POS>[.struct.json|.html.gz]; 404 otherwise."""
    match = ACT_PATH.match(request.url.path)
    if not match:
        return httpx.Response(404)
    stem = f"{match.group(1)}_{match.group(2)}_{match.group(3)}"
    suffix = match.group(4)
    if suffix == "/text.html":
        path = FIXTURES / "eli" / f"{stem}.html.gz"
        if not path.exists():
            return httpx.Response(404)
        return httpx.Response(200, text=gzip.decompress(path.read_bytes()).decode("utf-8"))
    path = FIXTURES / "eli" / (f"{stem}.struct.json" if suffix else f"{stem}.json")
    if not path.exists():
        return httpx.Response(404, json={"message": "not found"})
    return httpx.Response(
        200, content=path.read_bytes(), headers={"Content-Type": "application/json"}
    )


def make_settings(**kwargs: Any) -> Settings:
    """Settings that ignore any local .env file."""
    return Settings(_env_file=None, **kwargs)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def settings() -> Settings:
    return make_settings(http_max_retries=2)


@pytest.fixture
def eli_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ELI}/keywords").respond(json=load_json("eli/keywords.json"))
        router.get(
            f"{ELI}/acts/search",
            params__contains={"title": "o restrukturyzacji finansowej górnictwa siarki"},
        ).respond(json=load_json("eli/search_gornictwo_siarki.json"))
        router.get(f"{ELI}/acts/search").respond(json=load_json("eli/search_kpa.json"))
        router.get(url__regex=rf"^{re.escape(ELI)}/acts/(DU|MP)/").mock(
            side_effect=eli_fixture_response
        )
        yield router


@pytest.fixture
def saos_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=SAOS, assert_all_called=False) as router:
        router.get("/search/judgments").respond(json=load_json("saos/search_page0.json"))
        router.get("/judgments/554287").respond(json=load_json("saos/judgment_554287.json"))
        router.get("/judgments/1").respond(
            404, json={"error": {"httpStatus": "404", "reason": "ELEMENT DOES NOT EXIST"}}
        )
        yield router


@pytest.fixture
async def legal(settings: Settings) -> Any:
    client = EliClient(settings, backoff_base=0)
    yield LegalService(client, today=lambda: TODAY)
    await client.aclose()


@pytest.fixture
async def judgments(settings: Settings) -> Any:
    client = SaosClient(settings, backoff_base=0)
    yield JudgmentService(client)
    await client.aclose()


EU = "https://publications.europa.eu"
_SPARQL_MARKER = re.compile(r"# plmcp:(\w+)")
_SPARQL_CELEX = re.compile(r'"0?(\d{5}[RLD]\d{4})')


def eurlex_fixture_response(request: httpx.Request) -> httpx.Response:
    """Serve tests/fixtures/eurlex: SPARQL by query marker + CELEX, documents by CELEX."""
    if request.url.path == "/webapi/rdf/sparql":
        query = request.url.params["query"]
        marker = _SPARQL_MARKER.search(query)
        celex = _SPARQL_CELEX.search(query)
        if marker and celex:
            path = FIXTURES / "eurlex" / f"sparql_{marker.group(1)}_3{celex.group(1)[-9:]}.json"
            if path.exists():
                return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(200, json={"head": {"vars": []}, "results": {"bindings": []}})
    name = request.url.path.rsplit("/", 1)[-1]
    for suffix in (".xhtml.gz", ".html.gz"):
        path = FIXTURES / "eurlex" / f"{name}{suffix}"
        if path.exists():
            return httpx.Response(200, text=gzip.decompress(path.read_bytes()).decode("utf-8"))
    return httpx.Response(404)


@pytest.fixture
def eurlex_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=rf"^{re.escape(EU)}/").mock(side_effect=eurlex_fixture_response)
        yield router


@pytest.fixture
async def eu_law(settings: Settings) -> Any:
    from polish_legal_mcp.clients.eurlex import EurLexClient
    from polish_legal_mcp.services.eu_law import EuLawService

    client = EurLexClient(settings, backoff_base=0)
    yield EuLawService(client, today=lambda: TODAY)
    await client.aclose()
