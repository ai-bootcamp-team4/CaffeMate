from typing import Protocol

from app.workflows.models import (
    CancelWorkflowCommand,
    StartWorkflowCommand,
    WorkflowEvent,
    WorkflowProgress,
    WorkflowRun,
)


class WorkflowRepository(Protocol):
    def start(self, command: StartWorkflowCommand) -> WorkflowRun: ...

    def get(self, *, project_id: str, workflow_run_id: str, user_id: str) -> WorkflowRun: ...

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress: ...

    def list_events(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> list[WorkflowEvent]: ...

    def cancel(self, command: CancelWorkflowCommand) -> WorkflowRun: ...
