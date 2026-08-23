import asyncio
import copy
import json
from typing import Any

import httpx2
import pytest

from app.mcp.client import McpClientError
from app.mcp.preflight import McpManifestCatalog, McpManifestPreflight
from app.mcp.scope import ScopeTokenSigner
from app.workflows.models import HeadFence

SECRET = "scope-secret-that-is-longer-than-thirty-two-bytes"


class FakeIdentityProvider:
    def token_for(self, audience: str) -> str:
        assert audience == "https://mcp.example"
        return "cloud-run-id-token"


def head() -> HeadFence:
    return HeadFence(
        workflow_generation=1,
        state_version=2,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id="evidence-1",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )


def preflight(transport: httpx2.AsyncBaseTransport) -> McpManifestPreflight:
    return McpManifestPreflight(
        base_url="https://mcp.example",
        audience="https://mcp.example",
        identity_provider=FakeIdentityProvider(),
        scope_signer=ScopeTokenSigner(
            secret=SECRET,
            issuer="caffemate-control-api",
            audience="caffemate-mcp",
        ),
        transport=transport,
    )


def run(value: McpManifestPreflight):
    return asyncio.run(
        value.run(
            venture_project_id="project-1",
            workflow_run_id="workflow-1",
            head=head(),
        )
    )


def mock_transport(
    *,
    tools: list[dict[str, Any]] | None = None,
    supported_versions: list[str] | None = None,
    repeated_cursor: bool = False,
) -> httpx2.MockTransport:
    expected = McpManifestCatalog().production_projection()
    served_tools = copy.deepcopy(tools if tools is not None else expected["tools"])

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer cloud-run-id-token"
        assert request.headers["X-CaffeMate-Scope-Token"]
        assert request.headers["MCP-Protocol-Version"] == "2026-07-28"
        assert request.headers["Mcp-Method"] == body["method"]
        if body["method"] == "server/discover":
            result = {
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
                "supportedVersions": supported_versions or ["2026-07-28"],
                "capabilities": {"tools": {}},
            }
        else:
            cursor = body.get("params", {}).get("cursor")
            if cursor is None:
                page = served_tools[:2]
                next_cursor = "page-2" if len(served_tools) > 2 else None
            else:
                page = served_tools[2:]
                next_cursor = "page-2" if repeated_cursor else None
            result = {
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
                "nextCursor": next_cursor,
                "tools": [
                    {
                        "name": tool["name"],
                        "inputSchema": tool["inputSchema"],
                        "outputSchema": tool["outputSchema"],
                        "_meta": {"com.caffemate/toolVersion": tool["version"]},
                    }
                    for tool in page
                ],
            }
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": body["id"], "result": result},
        )

    return httpx2.MockTransport(handler)


def test_preflight_consumes_all_pages_and_matches_checked_in_digest() -> None:
    report = run(preflight(mock_transport()))

    assert report.protocol_revision == "2026-07-28"
    assert report.tool_count == 7
    assert report.manifest_digest == (
        "0a16a2c31e21819e015f6b23de7e62b47576d1b80cbe44698b228a3008477e4c"
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "schema", "version"])
def test_preflight_rejects_any_manifest_drift(mutation: str) -> None:
    tools = copy.deepcopy(McpManifestCatalog().production_projection()["tools"])
    if mutation == "missing":
        tools.pop()
    elif mutation == "extra":
        tools.append({**copy.deepcopy(tools[0]), "name": "unexpected_tool"})
    elif mutation == "schema":
        tools[0]["inputSchema"]["properties"]["limit"]["maximum"] = 11
    else:
        tools[0]["version"] = "2.0.0"

    with pytest.raises(McpClientError, match="MCP_MANIFEST_MISMATCH"):
        run(preflight(mock_transport(tools=tools)))


def test_preflight_rejects_unsupported_protocol_without_listing_tools() -> None:
    with pytest.raises(McpClientError, match="MCP_PROTOCOL_REVISION_MISMATCH"):
        run(
            preflight(
                mock_transport(supported_versions=["2025-11-25"]),
            )
        )


def test_preflight_rejects_repeated_pagination_cursor() -> None:
    with pytest.raises(McpClientError, match="MCP_TOOL_LIST_PAGINATION_INVALID"):
        run(preflight(mock_transport(repeated_cursor=True)))
