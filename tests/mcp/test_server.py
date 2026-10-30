"""MCP protocol level: discovery, invocation, validation, error mapping (in-memory transport)."""

import json

import pytest
from fastmcp import Client

from polish_legal_mcp.config import Settings
from polish_legal_mcp.server import build_server

EXPECTED_TOOLS = {
    "search_legal_acts",
    "get_legal_act",
    "get_legal_provision",
    "get_eu_provision",
    "get_legal_act_amendments",
    "verify_legal_citation",
    "find_legal_basis",
    "search_judgments",
    "get_judgment",
    "find_judgments_for_provision",
    "search_cjeu_judgments",
    "search_eu_acts",
    "get_eu_transposition",
    "find_pending_bills",
    "get_cjeu_judgment",
}


@pytest.fixture
def mcp(settings: Settings):
    return build_server(settings, backoff_base=0)


async def test_tool_discovery(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.description and any(ch in tool.description for ch in "ąęółśżźćń")
        assert tool.annotations and tool.annotations.read_only_hint
        assert tool.output_schema  # structured output


async def test_structured_provision_result(mcp, eli_mock):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_legal_provision",
            {"act_id": "Dz.U. 2000 poz. 856", "article": "2", "paragraph": "1"},
        )
    data = result.structured_content
    assert data is not None
    assert data["verification"]["temporal_applicability"]["status"] in {"VERIFIED", "UNVERIFIED"}
    assert data["wording_source"]["eli"] == "DU/2024/1835"
    assert data["citation"]["official_url"].startswith("https://isap.sejm.gov.pl/")


async def test_judgment_tools(mcp, saos_mock):
    async with Client(mcp) as client:
        found = await client.call_tool("search_judgments", {"query": "nieruchomości", "limit": 3})
        detail = await client.call_tool("get_judgment", {"judgment_id": 554287, "text_limit": 500})
    assert found.structured_content and detail.structured_content
    assert found.structured_content["items"][0]["judgment_id"] == 554287
    assert detail.structured_content["has_more"] is True


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_legal_acts", {"query": "x" * 301}),
        ("search_legal_acts", {"query": "ab", "limit": 500}),
        ("get_legal_provision", {"act_id": "DU/1960/168", "article": "drop table"}),
        ("verify_legal_citation", {"citation": "a" * 1001}),
        ("find_legal_basis", {"issue_description": "x" * 2001}),
        ("search_judgments", {"query": "abc", "court_type": "ADMINISTRATIVE"}),
        ("get_judgment", {"judgment_id": 0}),
    ],
)
async def test_invalid_or_oversized_input_rejected(mcp, tool, args):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    assert result.is_error


async def test_upstream_errors_become_polish_tool_errors(mcp, eli_mock):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_legal_act",
            {"publisher": "DU", "year": 2015, "position": 99999},
            raise_on_error=False,
        )
    assert result.is_error
    message = result.content[0].text  # type: ignore[union-attr]
    assert "Nie dowodzi" in message and "Traceback" not in message


async def test_invalid_act_id_message(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_legal_act_amendments", {"act_id": "not an act id"}, raise_on_error=False
        )
    assert result.is_error and "Nie rozpoznano identyfikatora" in result.content[0].text  # type: ignore[union-attr]


async def test_responses_are_bounded(mcp, eli_mock):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_legal_act", {"publisher": "DU", "year": 2015, "position": 1322}
        )
    assert len(json.dumps(result.structured_content)) < 100_000
