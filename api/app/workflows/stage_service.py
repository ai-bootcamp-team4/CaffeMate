from typing import Protocol

from app.domain.errors import (
    ExternalExecutionUnavailableError,
    PersistenceUnavailableError,
    StageLeaseRejectedError,
)
from app.workflows.models import StageLease


class StageLeaseAuthorizer(Protocol):
    def authorize(self, lease: StageLease) -> bool: ...


class StageExecutor(Protocol):
    def execute(self, lease: StageLease) -> dict[str, object]: ...


class StageExecution(Protocol):
    def execute(
        self,
        *,
        workflow_run_id: str,
        stage_run_id: str,
        lease: StageLease,
    ) -> dict[str, object]: ...


class StageExecutionService:
    def __init__(self, authorizer: StageLeaseAuthorizer, executor: StageExecutor) -> None:
        self._authorizer = authorizer
        self._executor = executor

    def execute(
        self,
        *,
        workflow_run_id: str,
        stage_run_id: str,
        lease: StageLease,
    ) -> dict[str, object]:
        if (
            lease.workflow_run_id != workflow_run_id
            or lease.stage_run_id != stage_run_id
            or not self._authorizer.authorize(lease)
        ):
            raise StageLeaseRejectedError("Stage lease is stale, invalid, or path-mismatched")
        return self._executor.execute(lease)


class UnavailableStageExecutor:
    def execute(self, lease: StageLease) -> dict[str, object]:
        del lease
        raise ExternalExecutionUnavailableError("Agent and MCP stage executor is not configured")


class UnavailableStageExecutionService:
    def execute(
        self,
        *,
        workflow_run_id: str,
        stage_run_id: str,
        lease: StageLease,
    ) -> dict[str, object]:
        del workflow_run_id, stage_run_id, lease
        raise PersistenceUnavailableError("Stage execution persistence is not configured")
