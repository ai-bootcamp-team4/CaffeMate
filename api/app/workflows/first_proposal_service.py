from app.domain.errors import WorkflowPreconditionError
from app.results.models import ResultView
from app.results.service import ResultService
from app.workflows.models import WorkflowCode, WorkflowProgress, WorkflowRun
from app.workflows.service import WorkflowService


class FirstProposalService:
    def __init__(self, workflows: WorkflowService, results: ResultService) -> None:
        self._workflows = workflows
        self._results = results

    def run(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun:
        if workflow_code != WorkflowCode.FIRST_PROPOSAL:
            raise WorkflowPreconditionError("Unsupported workflow code")
        return self._workflows.start(
            project_id=project_id,
            user_id=user_id,
            workflow_code=workflow_code,
            idempotency_key=idempotency_key,
        )

    def progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        return self._workflows.get_progress(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    def result(self, *, project_id: str, user_id: str) -> ResultView:
        return self._results.get_current(project_id=project_id, user_id=user_id)
