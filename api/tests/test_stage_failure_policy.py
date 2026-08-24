from http import HTTPStatus

import pytest

from app.agents.runtime import AgentRuntimeError
from app.domain.errors import (
    ContractValidationError,
    ExternalExecutionUnavailableError,
    PersistenceUnavailableError,
    StageLeaseRejectedError,
)
from app.mcp.client import McpClientError
from app.workflows.failure_policy import StageExecutionFailurePolicy


@pytest.mark.parametrize(
    ("error", "code", "retryable", "http_status"),
    [
        (
            AgentRuntimeError("RUNTIME_TIMED_OUT"),
            "RUNTIME_TIMED_OUT",
            False,
            HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        (
            McpClientError("MCP_TRANSPORT_ERROR"),
            "MCP_TRANSPORT_ERROR",
            False,
            HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        (
            ContractValidationError("invalid"),
            "CONTRACT_VALIDATION_FAILED",
            False,
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        (
            StageLeaseRejectedError("stale"),
            "STAGE_LEASE_REJECTED",
            False,
            HTTPStatus.CONFLICT,
        ),
        (
            PersistenceUnavailableError("database"),
            "PERSISTENCE_UNAVAILABLE",
            True,
            HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        (
            ExternalExecutionUnavailableError("external"),
            "EXTERNAL_EXECUTION_UNAVAILABLE",
            False,
            HTTPStatus.SERVICE_UNAVAILABLE,
        ),
    ],
)
def test_stage_failure_policy_preserves_existing_worker_contract(
    error: Exception,
    code: str,
    retryable: bool,
    http_status: int,
) -> None:
    failure = StageExecutionFailurePolicy.classify(error)

    assert failure.code == code
    assert failure.retryable is retryable
    assert failure.http_status == http_status


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AgentRuntimeError("RUNTIME_TIMED_OUT"), True),
        (AgentRuntimeError("RUNTIME_AGENT_OUTPUT_INVALID"), True),
        (AgentRuntimeError("RUNTIME_UNAUTHENTICATED"), False),
        (AgentRuntimeError("RUNTIME_TASK_DIGEST_MISMATCH"), False),
        (McpClientError("MCP_SERVER_UNAVAILABLE"), True),
        (McpClientError("MCP_UNAUTHENTICATED"), False),
        (McpClientError("MCP_PROJECT_SCOPE_MISMATCH"), False),
        (ExternalExecutionUnavailableError("external"), True),
    ],
)
def test_only_transient_or_model_output_failures_can_degrade(
    error: ExternalExecutionUnavailableError,
    expected: bool,
) -> None:
    assert StageExecutionFailurePolicy.can_degrade(error) is expected
