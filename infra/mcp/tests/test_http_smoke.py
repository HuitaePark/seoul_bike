"""Opt-in Streamable HTTP checks against the isolated fake-backend Compose."""
import json
import os

import pytest
from fastmcp import Client

BUSINESS_URL = os.environ.get("MCP_SMOKE_BUSINESS_URL")
RAG_URL = os.environ.get("MCP_SMOKE_RAG_URL")
RAG_STORE = os.environ.get("MCP_SMOKE_RAG_STORE", "memory")


def assert_rag_backend(result):
    """A successful response must come from the requested backend, not a fallback."""
    assert RAG_STORE in {"memory", "pgvector"}, "MCP_SMOKE_RAG_STORE must be memory or pgvector"
    assert result["source"].startswith(RAG_STORE + ":")
    expected = {"model": "hash-bigram-256", "dim": 256} if RAG_STORE == "pgvector" else {
        "model": None, "dim": None}
    assert result["embedding"] == expected


@pytest.mark.asyncio
@pytest.mark.skipif(not BUSINESS_URL, reason="set MCP_SMOKE_BUSINESS_URL for the independent fake server")
async def test_business_http_discovery_calculation_and_approval_boundary():
    async with Client(BUSINESS_URL) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert len(tools) == 11
        assert tools["get_station_detail"].annotations.read_only_hint is True
        assert tools["submit_plan"].annotations.destructive_hint is True
        for name in ("submit_plan", "propose_route_change", "add_constraint"):
            assert {"approval_id", "task_id"} <= tools[name].input_schema["properties"].keys()
        risk = await client.call_tool("get_risk_stations", {"region_id": "gwangjin-seongdong"})
        assert not risk.is_error
        assert risk.structured_content["stations"]
        assert "stationId" in risk.structured_content["stations"][0]
        candidate = await client.call_tool("request_route_plan", {
            "shift_id": "gwangjin-seongdong:2026-10-21:AM", "kind": "PLAN"})
        assert not candidate.is_error
        result = candidate.structured_content
        assert {"candidateId", "plan", "requiredApprovers"} <= result.keys()
        refused = await client.call_tool("submit_plan", {"plan_id": result["plan"]["planId"]},
                                         raise_on_error=False)
        assert refused.is_error
        assert json.loads(refused.content[0].text)["code"] == "APPROVAL_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.skipif(not RAG_URL, reason="set MCP_SMOKE_RAG_URL for the independent RAG server")
async def test_rag_http_search_and_read_only_mounted_resources():
    async with Client(RAG_URL) as client:
        assert [tool.name for tool in await client.list_tools()] == ["search_docs"]
        index = json.loads((await client.read_resource("doc://index"))[0].text)
        assert {"ops-rebalancing-standard", "notice-seoul-fireworks-2025",
                "handover-shift-summary-template", "ref-forecast-kim-2023"} <= {
                    doc["doc_id"] for doc in index}
        result = await client.call_tool("search_docs", {"query": "불꽃축제 대여 반납 중지", "top_k": 3})
        assert not result.is_error and result.structured_content["chunks"]
        assert_rag_backend(result.structured_content)
        chunk = result.structured_content["chunks"][0]
        assert {"citation", "basis", "uri", "doc_id"} <= chunk.keys()
        assert chunk["doc_id"] == "notice-seoul-fireworks-2025"
        document = (await client.read_resource("doc://" + chunk["doc_id"]))[0].text
        assert document.startswith("---") and "불꽃" in document


@pytest.mark.asyncio
@pytest.mark.skipif(not (BUSINESS_URL and RAG_URL), reason="set both MCP URLs for a grouped client")
async def test_grouped_client_uses_agent_tool_prefixes():
    from pathlib import Path

    config = json.loads((Path(__file__).resolve().parents[3] / "infra/mcp/client.host.example.json").read_text())
    config["mcpServers"]["business"]["url"] = BUSINESS_URL
    config["mcpServers"]["rag"]["url"] = RAG_URL
    async with Client(config) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert len(names) == 12
        assert {"business_request_route_plan", "business_get_station_detail", "rag_search_docs"} <= names
        result = await client.call_tool("rag_search_docs", {"query": "운영 기준", "top_k": 1})
        assert not result.is_error and result.structured_content["chunks"]
        assert_rag_backend(result.structured_content)
