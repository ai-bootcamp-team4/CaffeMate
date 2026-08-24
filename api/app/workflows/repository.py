from typing import Protocol

from app.workflows.models import (
    StartWorkflowCommand,
    WorkflowProgress,
    WorkflowRun,
)


class WorkflowRepository(Protocol):
    def start(self, command: StartWorkflowCommand) -> WorkflowRun: ...

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress: ...
