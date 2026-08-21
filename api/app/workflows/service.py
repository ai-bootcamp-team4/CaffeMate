from app.workflows.models import (
    CancelWorkflowCommand,
    StartWorkflowCommand,
    WorkflowCode,
    WorkflowEvent,
    WorkflowRun,
)
from app.workflows.repository import WorkflowRepository
from app.workflows.start_guard import WorkflowStartGuard


class WorkflowService:
    def __init__(
        self,
        repository: WorkflowRepository,
        *,
        start_guard: WorkflowStartGuard | None = None,
    ) -> None:
        self._repository = repository
        self._start_guard = start_guard

    def start(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun:
        if self._start_guard is not None:
            self._start_guard.validate(workflow_code)
        return self._repository.start(
            StartWorkflowCommand(
                project_id=project_id,
                user_id=user_id,
                workflow_code=workflow_code,
                idempotency_key=idempotency_key,
            )
        )

    def get(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowRun:
        return self._repository.get(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    def list_events(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> list[WorkflowEvent]:
        return self._repository.list_events(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    def cancel(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> WorkflowRun:
        return self._repository.cancel(
            CancelWorkflowCommand(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
        )
