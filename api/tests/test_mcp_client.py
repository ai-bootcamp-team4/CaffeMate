import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.mcp.client import McpClientError, McpHttpClient
from app.mcp.scope import ScopeTokenSigner
from app.workflows.models import HeadFence

SECRET = "scope-secret-that-is-longer-than-thirty-two-bytes"


class FakeIdentityProvider:
    def __init__(self) -> None:
        self.audiences: list[str] = []

    def token_for(self, audience: str) -> str:
        self.audiences.append(audience)
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


def valid_result(
    request_id: str,
    *,
    project_id: str = "project-1",
    status: str = "OK",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "request_id": request_id,
        "tool_name": "resolve_area",
        "tool_version": "1.0.0",
        "status": status,
        "project_id": project_id,
        "evidence_records": [],
        "missing_fields": [],
        "conflicts": [],
        "source_trace": [],
        "error_codes": [],
        "observed_at": "2026-08-21T10:00:00Z",
        "data": [
            {
                "administrative_code": "4111755000",
                "display_name": "원천동",
                "boundary_version": "2026-01",
                "match_kind": "EXACT",
            }
        ],
    }


def rpc_response(
    body: dict[str, Any],
    *,
    project_id: str = "project-1",
    status: str = "OK",
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": body["id"],
        "result": {
            "resultType": "complete",
            "content": [],
            "structuredContent": valid_result(
                str(body["id"]), project_id=project_id, status=status
            ),
            "isError": False,
            "_meta": {"com.caffemate/toolVersion": "1.0.0"},
        },
    }


def list_tools_response(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": body["id"],
        "result": {
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
            "tools": [
                {
                    "name": "resolve_area",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                    "_meta": {"com.caffemate/toolVersion": "1.0.0"},
                }
            ],
        },
    }


def transport_for(
    *,
    project_id: str = "project-1",
    status: str = "OK",
    sse: bool = False,
    inspect_request: Callable[[httpx2.Request, dict[str, Any]], None] | None = None,
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if inspect_request:
            inspect_request(request, body)
        if body["method"] == "tools/list":
            response_body = list_tools_response(body)
        else:
            response_body = rpc_response(body, project_id=project_id, status=status)
        if sse:
            payload = f"event: message\ndata: {json.dumps(response_body)}\n\n"
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=payload)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=response_body,
        )

    return httpx2.MockTransport(handler)


def client(
    transport: httpx2.AsyncBaseTransport,
    identity: FakeIdentityProvider | None = None,
) -> tuple[McpHttpClient, FakeIdentityProvider]:
    identity = identity or FakeIdentityProvider()
    signer = ScopeTokenSigner(
        secret=SECRET,
        issuer="caffemate-control-api",
        audience="caffemate-mcp",
    )
    return (
        McpHttpClient(
            base_url="https://mcp.example",
            audience="https://mcp.example",
            identity_provider=identity,
            scope_signer=signer,
            transport=transport,
        ),
        identity,
    )


def call(mcp_client: McpHttpClient):
    return asyncio.run(
        mcp_client.call_tool(
            venture_project_id="project-1",
            workflow_run_id="workflow-1",
            head=head(),
            tool_name="resolve_area",
            arguments={"query": "수원 아주대", "country_code": "KR", "limit": 3},
            traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
        )
    )


def test_calls_official_modern_transport_with_identity_scope_and_trace() -> None:
    seen_methods: list[str] = []

    def inspect_request(request: httpx2.Request, body: dict[str, Any]) -> None:
        seen_methods.append(body["method"])
        assert request.url.path == "/mcp"
        assert request.headers["Authorization"] == "Bearer cloud-run-id-token"
        assert request.headers["X-CaffeMate-Scope-Token"]
        assert request.headers["MCP-Protocol-Version"] == "2026-07-28"
        assert request.headers["Mcp-Method"] == body["method"]
        assert request.headers["traceparent"].startswith("00-")
        assert "Mcp-Session-Id" not in request.headers
        if body["method"] == "tools/call":
            assert request.headers["Mcp-Name"] == "resolve_area"
            assert body["params"]["arguments"]["country_code"] == "KR"
            meta = body["params"]["_meta"]
            assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
            assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "caffemate-control-api"

    mcp_client, identity = client(transport_for(inspect_request=inspect_request))

    outcome = call(mcp_client)

    assert outcome.status == "OK"
    assert outcome.is_complete is True
    assert outcome.structured_content["project_id"] == "project-1"
    assert identity.audiences == ["https://mcp.example"]
    assert seen_methods == ["tools/call", "tools/list"]


def test_accepts_request_scoped_sse_and_preserves_partial_status() -> None:
    mcp_client, _ = client(transport_for(status="PARTIAL", sse=True))

    outcome = call(mcp_client)

    assert outcome.status == "PARTIAL"
    assert outcome.is_complete is False


def test_invalid_input_is_rejected_before_identity_or_http() -> None:
    calls = 0
    identity = FakeIdentityProvider()

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    mcp_client, _ = client(httpx2.MockTransport(handler), identity)

    with pytest.raises(McpClientError, match="MCP_TOOL_INPUT_REJECTED"):
        asyncio.run(
            mcp_client.call_tool(
                venture_project_id="project-1",
                workflow_run_id="workflow-1",
                head=head(),
                tool_name="resolve_area",
                arguments={"query": "", "country_code": "KR", "limit": 99},
            )
        )

    assert identity.audiences == []
    assert calls == 0


def test_cross_project_result_is_rejected_without_leaking_payload() -> None:
    mcp_client, _ = client(transport_for(project_id="another-project"))

    with pytest.raises(McpClientError) as captured:
        call(mcp_client)

    assert captured.value.mcp_code == "MCP_PROJECT_SCOPE_MISMATCH"
    assert str(captured.value) == "MCP_PROJECT_SCOPE_MISMATCH"
    assert "another-project" not in str(captured.value)


def test_protocol_error_is_reported_as_safe_transport_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": -32020, "message": "secret provider detail"},
            },
        )

    mcp_client, _ = client(httpx2.MockTransport(handler))

    with pytest.raises(McpClientError) as captured:
        call(mcp_client)

    assert captured.value.mcp_code == "MCP_TRANSPORT_ERROR"
    assert "secret provider detail" not in str(captured.value)
