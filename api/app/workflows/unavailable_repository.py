from app.domain.errors import PersistenceUnavailableError
from app.workflows.models import (
    CancelWorkflowCommand,
    StartWorkflowCommand,
    WorkflowEvent,
    WorkflowProgress,
    WorkflowRun,
)


class UnavailableWorkflowRepository:
    def start(self, command: StartWorkflowCommand) -> WorkflowRun:
        del command
        raise PersistenceUnavailableError("Workflow persistence or policy is not configured")

    def get(self, *, project_id: str, workflow_run_id: str, user_id: str) -> WorkflowRun:
        del project_id, workflow_run_id, user_id
        raise PersistenceUnavailableError("Workflow persistence or policy is not configured")

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        del project_id, workflow_run_id, user_id
        raise PersistenceUnavailableError("Workflow persistence or policy is not configured")

    def list_events(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> list[WorkflowEvent]:
        del project_id, workflow_run_id, user_id
        raise PersistenceUnavailableError("Workflow persistence or policy is not configured")

    def cancel(self, command: CancelWorkflowCommand) -> WorkflowRun:
        del command
        raise PersistenceUnavailableError("Workflow persistence or policy is not configured")
