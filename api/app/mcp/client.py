import json
from collections.abc import Callable
from typing import Any, Protocol, cast

import httpx2
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import Implementation

from app.contracts.schema_registry import ContractRegistry, McpContractValidator
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import StrictModel
from app.mcp.result_validation import validate_mcp_result
from app.mcp.scope import ScopeTokenSigner
from app.workflows.models import HeadFence

PROTOCOL_REVISION = "2026-07-28"
CLIENT_INFO = Implementation(name="caffemate-control-api", version="1.0.0")


class IdentityTokenProvider(Protocol):
    def token_for(self, audience: str) -> str: ...


class GoogleIdentityTokenProvider:
    def __init__(self, fetch: Callable[[Request, str], str | None] | None = None) -> None:
        self._fetch = fetch or id_token.fetch_id_token

    def token_for(self, audience: str) -> str:
        token = self._fetch(Request(), audience)
        if not token:
            raise RuntimeError("Google service identity token was not returned")
        return token


class McpClientError(ExternalExecutionUnavailableError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.mcp_code = code


class McpCallOutcome(StrictModel):
    request_id: str
    tool_name: str
    tool_version: str
    status: str
    is_complete: bool
    structured_content: dict[str, Any]


class McpHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        audience: str,
        identity_provider: IdentityTokenProvider,
        scope_signer: ScopeTokenSigner,
        contracts: McpContractValidator | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not audience:
            raise ValueError("MCP URL and audience are required")
        self._endpoint = f"{base_url.rstrip('/')}/mcp"
        self._audience = audience
        self._identity_provider = identity_provider
        self._scope_signer = scope_signer
        self._contracts = contracts or ContractRegistry()
        self._transport = transport

    async def call_tool(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        tool_name: str,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> McpCallOutcome:
        try:
            self._contracts.validate_mcp_tool_input(tool_name, arguments)
            tool_version = self._contracts.mcp_tool_version(tool_name)
        except ContractValidationError as error:
            raise McpClientError("MCP_TOOL_INPUT_REJECTED") from error

        scope_token = self._scope_signer.issue(
            venture_project_id=venture_project_id,
            workflow_run_id=workflow_run_id,
            head=head,
        )
        scope = self._scope_signer.verify(scope_token)
        headers = {
            "Authorization": f"Bearer {self._identity_provider.token_for(self._audience)}",
            "X-CaffeMate-Scope-Token": scope_token,
        }
        if traceparent:
            headers["traceparent"] = traceparent

        request_ids: list[str] = []

        async def capture_request_id(request: httpx2.Request) -> None:
            if request.headers.get("Mcp-Method") != "tools/call":
                return
            try:
                body = json.loads(request.content)
                request_id = body["id"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise McpClientError("MCP_REQUEST_CORRELATION_FAILED") from error
            if isinstance(request_id, bool) or not isinstance(request_id, str | int):
                raise McpClientError("MCP_REQUEST_CORRELATION_FAILED")
            request_ids.append(str(request_id))

        try:
            async with httpx2.AsyncClient(
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                event_hooks={"request": [capture_request_id]},
            ) as http_client:
                transport = streamable_http_client(
                    self._endpoint,
                    http_client=http_client,
                    terminate_on_close=False,
                )
                async with Client(
                    transport,
                    mode=PROTOCOL_REVISION,
                    client_info=CLIENT_INFO,
                    read_timeout_seconds=timeout_seconds,
                    input_required_max_rounds=0,
                ) as client:
                    result = await client.call_tool(
                        tool_name,
                        arguments,
                        read_timeout_seconds=timeout_seconds,
                    )
        except McpClientError:
            raise
        except Exception as error:
            raise McpClientError("MCP_TRANSPORT_ERROR") from error

        if len(request_ids) != 1:
            raise McpClientError("MCP_REQUEST_CORRELATION_FAILED")
        if not isinstance(result.structured_content, dict):
            raise McpClientError("MCP_STRUCTURED_CONTENT_MISSING")
        structured_content = cast(dict[str, Any], result.structured_content)
        meta_tool_version = None
        if isinstance(result.meta, dict):
            value = result.meta.get("com.caffemate/toolVersion")
            meta_tool_version = value if isinstance(value, str) else None

        validation = validate_mcp_result(
            structured_content=structured_content,
            scope=scope,
            expected_request_id=request_ids[0],
            expected_tool_name=tool_name,
            expected_tool_version=tool_version,
            meta_tool_version=meta_tool_version,
            is_error=result.is_error,
            contracts=self._contracts,
        )
        if not validation.accepted or validation.status is None:
            code = validation.errors[0].code if validation.errors else "MCP_RESULT_REJECTED"
            raise McpClientError(code)
        return McpCallOutcome(
            request_id=request_ids[0],
            tool_name=tool_name,
            tool_version=tool_version,
            status=validation.status,
            is_complete=validation.is_complete,
            structured_content=structured_content,
        )
