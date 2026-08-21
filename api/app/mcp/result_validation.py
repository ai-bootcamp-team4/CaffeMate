from typing import Any

from pydantic import Field

from app.contracts.schema_registry import ContractRegistry, McpContractValidator
from app.domain.errors import ContractValidationError
from app.domain.models import StrictModel
from app.mcp.scope import ScopeClaims


class McpResultError(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class McpResultValidation(StrictModel):
    accepted: bool
    is_complete: bool
    status: str | None
    errors: list[McpResultError]


def validate_mcp_result(
    *,
    structured_content: dict[str, Any],
    scope: ScopeClaims,
    expected_request_id: str,
    expected_tool_name: str,
    expected_tool_version: str,
    meta_tool_version: str | None,
    is_error: bool,
    contracts: McpContractValidator | None = None,
) -> McpResultValidation:
    if is_error:
        return _rejected("MCP_TRANSPORT_ERROR", "MCP response set isError=true")

    validator = contracts or ContractRegistry()
    try:
        validator.validate_mcp_tool_result(expected_tool_name, structured_content)
    except ContractValidationError as error:
        return _rejected("MCP_TOOL_CONTRACT_MISMATCH", str(error))

    errors: list[McpResultError] = []
    evidence_projects = {
        record["project_id"]
        for record in structured_content["evidence_records"]
        if isinstance(record, dict) and "project_id" in record
    }
    if (
        structured_content["project_id"] != scope.venture_project_id
        or evidence_projects - {scope.venture_project_id}
    ):
        errors.append(
            McpResultError(
                code="MCP_PROJECT_SCOPE_MISMATCH",
                message="MCP result project does not match the signed scope",
            )
        )
    if structured_content["request_id"] != expected_request_id:
        errors.append(
            McpResultError(
                code="MCP_REQUEST_ID_MISMATCH",
                message="MCP result request id does not match the call",
            )
        )
    if structured_content["tool_name"] != expected_tool_name:
        errors.append(
            McpResultError(
                code="MCP_TOOL_CONTRACT_MISMATCH",
                message="MCP result tool name does not match the call",
            )
        )
    if (
        structured_content["tool_version"] != expected_tool_version
        or meta_tool_version != expected_tool_version
    ):
        errors.append(
            McpResultError(
                code="MCP_TOOL_CONTRACT_MISMATCH",
                message="MCP result tool version does not match manifest and metadata",
            )
        )

    status = structured_content["status"]
    if status == "ERROR":
        errors.append(
            McpResultError(
                code="MCP_DOMAIN_ERROR",
                message="MCP tool returned domain status ERROR",
            )
        )
    return McpResultValidation(
        accepted=not errors,
        is_complete=not errors and status == "OK",
        status=status,
        errors=errors,
    )


def _rejected(code: str, message: str) -> McpResultValidation:
    return McpResultValidation(
        accepted=False,
        is_complete=False,
        status=None,
        errors=[McpResultError(code=code, message=message)],
    )
