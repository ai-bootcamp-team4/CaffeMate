from app.domain.errors import PersistenceUnavailableError
from app.workflows.models import (
    StartWorkflowCommand,
    WorkflowProgress,
    WorkflowRun,
)


class UnavailableWorkflowRepository:
    def start(self, command: StartWorkflowCommand) -> WorkflowRun:
        del command
        raise PersistenceUnavailableError("Workflow persistence is not configured")

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        del project_id, workflow_run_id, user_id
        raise PersistenceUnavailableError("Workflow persistence is not configured")
