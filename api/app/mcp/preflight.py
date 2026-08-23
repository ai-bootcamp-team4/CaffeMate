import hashlib
import json
from pathlib import Path
from typing import Any, cast

import httpx2
import rfc8785
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp_types import DiscoverResult, PaginatedRequestParams

from app.domain.models import StrictModel
from app.mcp.client import (
    CLIENT_INFO,
    PROTOCOL_REVISION,
    IdentityTokenProvider,
    McpClientError,
)
from app.mcp.scope import ScopeTokenSigner
from app.workflows.models import HeadFence

MAX_TOOL_LIST_PAGES = 100


class McpPreflightReport(StrictModel):
    protocol_revision: str
    manifest_digest: str
    tool_count: int


class McpManifestCatalog:
    def __init__(self, contract_directory: Path | None = None) -> None:
        self._directory = contract_directory or (
            Path(__file__).resolve().parents[3] / "docs" / "contracts"
        )
        self._manifest = self._read_json("mcp-tool-manifest.json")
        self._production_capabilities = self._read_json(
            "mcp-production-capabilities.json"
        )
        self._schema_documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in self._directory.glob("*.schema.json")
        }

    def projection(self) -> dict[str, Any]:
        tools = []
        for row in self._manifest["tools"]:
            tools.append(
                {
                    "name": row["name"],
                    "version": row["version"],
                    "inputSchema": self._schema_from_ref(row["input_schema_ref"]),
                    "outputSchema": self._schema_from_ref(row["output_schema_ref"]),
                }
            )
        return {
            "manifest_version": self._manifest["manifest_version"],
            "protocol_revision": self._manifest["protocol_revision"],
            "tools": tools,
        }

    def production_projection(self) -> dict[str, Any]:
        full = self.projection()
        production_names = self._production_capabilities.get("tools")
        if (
            self._production_capabilities.get("schema_version") != "1.0.0"
            or not isinstance(production_names, list)
            or not production_names
            or any(not isinstance(name, str) for name in production_names)
            or len(production_names) != len(set(production_names))
            or self._production_capabilities.get("manifest_digest")
            != f"sha256:{self.expected_digest()}"
        ):
            raise McpClientError("MCP_PRODUCTION_CAPABILITIES_INVALID")
        tools_by_name = {tool["name"]: tool for tool in full["tools"]}
        if any(name not in tools_by_name for name in production_names):
            raise McpClientError("MCP_PRODUCTION_CAPABILITIES_INVALID")
        return {
            "manifest_version": full["manifest_version"],
            "protocol_revision": full["protocol_revision"],
            "tools": [tools_by_name[name] for name in production_names],
        }

    def expected_digest(self) -> str:
        recorded = (self._directory / "mcp-tool-manifest.sha256").read_text(
            encoding="utf-8"
        ).split()[0]
        computed = _digest(self.projection())
        if recorded != computed:
            raise McpClientError("MCP_CHECKED_IN_MANIFEST_DIGEST_INVALID")
        return recorded

    def _read_json(self, name: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads((self._directory / name).read_text(encoding="utf-8")),
        )

    def _schema_from_ref(self, reference: str) -> dict[str, Any]:
        document_name, pointer = _split_ref(reference, "mcp-tool-contracts.schema.json")
        value = _resolve_pointer(self._schema_documents[document_name], pointer)
        schema = self._dereference(value, document_name, set())
        if not isinstance(schema, dict):
            raise McpClientError("MCP_CHECKED_IN_MANIFEST_INVALID")
        return schema

    def _dereference(
        self,
        value: Any,
        current_document: str,
        stack: set[str],
    ) -> Any:
        if isinstance(value, list):
            return [self._dereference(item, current_document, set(stack)) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str):
            document_name, pointer = _split_ref(reference, current_document)
            key = f"{document_name}#{pointer}"
            if key in stack:
                raise McpClientError("MCP_CHECKED_IN_MANIFEST_INVALID")
            target = _resolve_pointer(self._schema_documents[document_name], pointer)
            resolved = self._dereference(target, document_name, stack | {key})
            siblings = {
                name: self._dereference(item, current_document, set(stack))
                for name, item in value.items()
                if name != "$ref"
            }
            return resolved if not siblings else {"allOf": [resolved], **siblings}
        return {
            name: self._dereference(item, current_document, set(stack))
            for name, item in value.items()
        }


class McpManifestPreflight:
    def __init__(
        self,
        *,
        base_url: str,
        audience: str,
        identity_provider: IdentityTokenProvider,
        scope_signer: ScopeTokenSigner,
        catalog: McpManifestCatalog | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not audience:
            raise ValueError("MCP URL and audience are required")
        self._endpoint = f"{base_url.rstrip('/')}/mcp"
        self._audience = audience
        self._identity_provider = identity_provider
        self._scope_signer = scope_signer
        self._catalog = catalog or McpManifestCatalog()
        self._transport = transport

    async def run(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        traceparent: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> McpPreflightReport:
        scope_token = self._scope_signer.issue(
            venture_project_id=venture_project_id,
            workflow_run_id=workflow_run_id,
            head=head,
        )
        headers = {
            "Authorization": f"Bearer {self._identity_provider.token_for(self._audience)}",
            "X-CaffeMate-Scope-Token": scope_token,
        }
        if traceparent:
            headers["traceparent"] = traceparent
        try:
            async with httpx2.AsyncClient(
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as http_client:
                transport = streamable_http_client(
                    self._endpoint,
                    http_client=http_client,
                    terminate_on_close=False,
                )
                async with transport as streams:
                    async with ClientSession(
                        streams[0],
                        streams[1],
                        read_timeout_seconds=timeout_seconds,
                        client_info=CLIENT_INFO,
                    ) as session:
                        raw_discover = await session.send_discover(PROTOCOL_REVISION)
                        discover = DiscoverResult.model_validate(raw_discover)
                        if PROTOCOL_REVISION not in discover.supported_versions:
                            raise McpClientError("MCP_PROTOCOL_REVISION_MISMATCH")
                        session.adopt(discover)
                        observed_tools = await self._list_all_tools(session)
        except McpClientError:
            raise
        except Exception as error:
            nested = _find_mcp_client_error(error)
            if nested is not None:
                raise nested from error
            raise McpClientError("MCP_PREFLIGHT_TRANSPORT_ERROR") from error

        expected = self._catalog.production_projection()
        observed = {
            "manifest_version": expected["manifest_version"],
            "protocol_revision": PROTOCOL_REVISION,
            "tools": sorted(observed_tools, key=lambda tool: tool["name"]),
        }
        expected["tools"] = sorted(expected["tools"], key=lambda tool: tool["name"])
        if rfc8785.dumps(observed) != rfc8785.dumps(expected):
            raise McpClientError("MCP_MANIFEST_MISMATCH")
        digest = self._catalog.expected_digest()
        return McpPreflightReport(
            protocol_revision=PROTOCOL_REVISION,
            manifest_digest=digest,
            tool_count=len(observed_tools),
        )

    @staticmethod
    async def _list_all_tools(session: ClientSession) -> list[dict[str, Any]]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        tools: list[dict[str, Any]] = []
        for _page in range(MAX_TOOL_LIST_PAGES):
            result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            for tool in result.tools:
                version = tool.meta.get("com.caffemate/toolVersion") if tool.meta else None
                if not isinstance(version, str) or tool.output_schema is None:
                    raise McpClientError("MCP_MANIFEST_MISMATCH")
                tools.append(
                    {
                        "name": tool.name,
                        "version": version,
                        "inputSchema": tool.input_schema,
                        "outputSchema": tool.output_schema,
                    }
                )
            cursor = result.next_cursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise McpClientError("MCP_TOOL_LIST_PAGINATION_INVALID")
            seen_cursors.add(cursor)
        raise McpClientError("MCP_TOOL_LIST_PAGINATION_INVALID")


def _split_ref(reference: str, current_document: str) -> tuple[str, str]:
    raw_document, separator, fragment = reference.partition("#")
    document_name = raw_document.removeprefix("./") or current_document
    pointer = fragment if separator else ""
    return document_name, pointer


def _resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    if not pointer:
        return document
    if not pointer.startswith("/"):
        raise McpClientError("MCP_CHECKED_IN_MANIFEST_INVALID")
    current: Any = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise McpClientError("MCP_CHECKED_IN_MANIFEST_INVALID")
        current = current[token]
    return current


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _find_mcp_client_error(error: BaseException) -> McpClientError | None:
    if isinstance(error, McpClientError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            if match := _find_mcp_client_error(nested):
                return match
    return None
