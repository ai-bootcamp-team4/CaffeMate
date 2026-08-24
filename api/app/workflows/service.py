from app.workflows.models import (
    StartWorkflowCommand,
    WorkflowCode,
    WorkflowProgress,
    WorkflowRun,
)
from app.workflows.repository import WorkflowRepository


class WorkflowService:
    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def start(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun:
        return self._repository.start(
            StartWorkflowCommand(
                project_id=project_id,
                user_id=user_id,
                workflow_code=workflow_code,
                idempotency_key=idempotency_key,
            )
        )

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        return self._repository.get_progress(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )
