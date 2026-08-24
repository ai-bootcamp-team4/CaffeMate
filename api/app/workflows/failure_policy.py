from http import HTTPStatus

from app.agents.runtime import AgentRuntimeError
from app.domain.errors import (
    ContractValidationError,
    ExternalExecutionUnavailableError,
    PersistenceUnavailableError,
    StageLeaseRejectedError,
)
from app.domain.models import StrictModel
from app.mcp.client import McpClientError


class StageExecutionFailure(StrictModel):
    code: str
    retryable: bool
    http_status: int


class StageExecutionFailurePolicy:
    """One translation policy for stage exceptions crossing the worker boundary."""

    _DEGRADABLE_AGENT_CODES = frozenset(
        {
            "RUNTIME_AGENT_OUTPUT_INVALID",
            "RUNTIME_RESULT_SCHEMA_INVALID",
            "RUNTIME_STREAM_TRANSPORT_FAILED",
            "RUNTIME_TIMED_OUT",
            "RUNTIME_TRANSPORT_FAILED",
            "SAFETY_BLOCKED",
        }
    )
    _DEGRADABLE_MCP_CODES = frozenset(
        {
            "MCP_DOMAIN_ERROR",
            "MCP_NETWORK_ERROR",
            "MCP_RATE_LIMITED",
            "MCP_SERVER_UNAVAILABLE",
            "MCP_TIMED_OUT",
            "MCP_TRANSPORT_ERROR",
        }
    )

    @classmethod
    def can_degrade(cls, error: ExternalExecutionUnavailableError) -> bool:
        if isinstance(error, AgentRuntimeError):
            return error.runtime_code in cls._DEGRADABLE_AGENT_CODES
        if isinstance(error, McpClientError):
            return error.mcp_code in cls._DEGRADABLE_MCP_CODES
        return type(error) is ExternalExecutionUnavailableError

    @staticmethod
    def classify(error: Exception) -> StageExecutionFailure:
        if isinstance(error, AgentRuntimeError):
            return StageExecutionFailure(
                code=error.runtime_code,
                retryable=False,
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if isinstance(error, McpClientError):
            return StageExecutionFailure(
                code=error.mcp_code,
                retryable=False,
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if isinstance(error, ContractValidationError):
            return StageExecutionFailure(
                code="CONTRACT_VALIDATION_FAILED",
                retryable=False,
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if isinstance(error, StageLeaseRejectedError):
            return StageExecutionFailure(
                code="STAGE_LEASE_REJECTED",
                retryable=False,
                http_status=HTTPStatus.CONFLICT,
            )
        if isinstance(error, PersistenceUnavailableError):
            return StageExecutionFailure(
                code="PERSISTENCE_UNAVAILABLE",
                retryable=True,
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if isinstance(error, ExternalExecutionUnavailableError):
            return StageExecutionFailure(
                code="EXTERNAL_EXECUTION_UNAVAILABLE",
                retryable=False,
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        raise TypeError(f"Unsupported stage execution error: {type(error).__name__}")


STAGE_EXECUTION_ERRORS = (
    AgentRuntimeError,
    McpClientError,
    ContractValidationError,
    StageLeaseRejectedError,
    PersistenceUnavailableError,
    ExternalExecutionUnavailableError,
)
