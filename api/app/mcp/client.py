import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar, cast

import httpx2
import rfc8785
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
MAX_TRANSPORT_ATTEMPTS = 3
TRANSPORT_BACKOFF_SECONDS = (0.25, 0.75)
ExceptionT = TypeVar("ExceptionT", bound=BaseException)


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


class _RetryableMcpTransportError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class _TerminalMcpHttpError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class McpCallOutcome(StrictModel):
    request_id: str
    tool_name: str
    tool_version: str
    status: str
    is_complete: bool
    structured_content: dict[str, Any]
    traceparent: str | None = None


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
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not base_url or not audience:
            raise ValueError("MCP URL and audience are required")
        self._endpoint = f"{base_url.rstrip('/')}/mcp"
        self._audience = audience
        self._identity_provider = identity_provider
        self._scope_signer = scope_signer
        self._contracts = contracts or ContractRegistry()
        self._transport = transport
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or asyncio.sleep

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
        effective_traceparent = traceparent or self._traceparent(
            venture_project_id=venture_project_id,
            workflow_run_id=workflow_run_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        headers["traceparent"] = effective_traceparent

        deadline = self._monotonic() + timeout_seconds
        logical_request_id: str | None = None
        last_retryable: _RetryableMcpTransportError | None = None
        for attempt in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise McpClientError("MCP_TIMED_OUT")
            if attempt > 1:
                assert last_retryable is not None
                delay = self._retry_delay(attempt, last_retryable.retry_after_seconds)
                if delay >= remaining:
                    raise McpClientError("MCP_TIMED_OUT")
                await self._sleep(delay)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise McpClientError("MCP_TIMED_OUT")
            try:
                result, request_id = await self._call_once(
                    headers=headers,
                    tool_name=tool_name,
                    arguments=arguments,
                    timeout_seconds=remaining,
                )
            except Exception as error:
                terminal = self._find_exception(error, _TerminalMcpHttpError)
                if terminal is not None:
                    raise McpClientError(terminal.code) from error
                retryable = self._classify_retryable(error)
                if retryable is None:
                    if isinstance(error, McpClientError):
                        raise
                    raise McpClientError("MCP_TRANSPORT_ERROR") from error
                last_retryable = retryable
                if attempt == MAX_TRANSPORT_ATTEMPTS:
                    raise McpClientError(retryable.code) from error
                continue
            if logical_request_id is None:
                logical_request_id = request_id
            elif request_id != logical_request_id:
                raise McpClientError("MCP_REQUEST_CORRELATION_FAILED")
            break
        else:
            raise McpClientError("MCP_TRANSPORT_ERROR")

        assert logical_request_id is not None
        request_id = logical_request_id

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
            expected_request_id=request_id,
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
            request_id=request_id,
            tool_name=tool_name,
            tool_version=tool_version,
            status=validation.status,
            is_complete=validation.is_complete,
            structured_content=structured_content,
            traceparent=effective_traceparent,
        )

    async def _call_once(
        self,
        *,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: float,
    ) -> tuple[Any, str]:
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

        async def classify_response(response: httpx2.Response) -> None:
            status = response.status_code
            if status in {408, 429} or 500 <= status <= 599:
                raise _RetryableMcpTransportError(
                    self._retryable_code(status),
                    retry_after_seconds=self._retry_after_seconds(response),
                )
            if status >= 400:
                code = {
                    400: "MCP_REQUEST_INVALID",
                    401: "MCP_UNAUTHENTICATED",
                    403: "MCP_FORBIDDEN",
                }.get(status, "MCP_HTTP_TERMINAL")
                raise _TerminalMcpHttpError(code)

        try:
            async with httpx2.AsyncClient(
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                event_hooks={
                    "request": [capture_request_id],
                    "response": [classify_response],
                },
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
        except Exception:
            raise

        if len(request_ids) != 1:
            raise McpClientError("MCP_REQUEST_CORRELATION_FAILED")
        return result, request_ids[0]

    @staticmethod
    def _retry_delay(attempt: int, retry_after_seconds: float | None) -> float:
        if retry_after_seconds is not None:
            return retry_after_seconds
        return TRANSPORT_BACKOFF_SECONDS[attempt - 2]

    @staticmethod
    def _retryable_code(status: int) -> str:
        if status == 408:
            return "MCP_TIMED_OUT"
        if status == 429:
            return "MCP_RATE_LIMITED"
        return "MCP_SERVER_UNAVAILABLE"

    def _classify_retryable(self, error: BaseException) -> _RetryableMcpTransportError | None:
        explicit = self._find_exception(error, _RetryableMcpTransportError)
        if explicit is not None:
            return explicit
        if self._find_exception(error, httpx2.TimeoutException) is not None:
            return _RetryableMcpTransportError("MCP_TIMED_OUT")
        if self._find_exception(error, httpx2.NetworkError) is not None:
            return _RetryableMcpTransportError("MCP_NETWORK_ERROR")
        return None

    @staticmethod
    def _find_exception(
        error: BaseException,
        expected: type[ExceptionT],
    ) -> ExceptionT | None:
        pending: list[BaseException] = [error]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, expected):
                return current
            if isinstance(current, BaseExceptionGroup):
                pending.extend(current.exceptions)
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
        return None

    def _retry_after_seconds(self, response: httpx2.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                seconds = target.timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                return None
        return seconds if 0 <= seconds <= 2 else None

    @staticmethod
    def _traceparent(
        *,
        venture_project_id: str,
        workflow_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        seed = rfc8785.dumps(
            {
                "venture_project_id": venture_project_id,
                "workflow_run_id": workflow_run_id,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )
        digest = hashlib.sha256(seed).hexdigest()
        return f"00-{digest[:32]}-{digest[32:48]}-01"
