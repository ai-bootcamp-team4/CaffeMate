import logging

from app.domain.errors import WorkflowPreconditionError
from app.observability import record_safe_metric, tracer
from app.results.models import ResultView
from app.results.service import ResultService
from app.workflows.dispatch import WorkflowDispatcher
from app.workflows.models import WorkflowCode, WorkflowProgress, WorkflowRun
from app.workflows.service import WorkflowService

logger = logging.getLogger(__name__)


class FirstProposalService:
    def __init__(
        self,
        workflows: WorkflowService,
        results: ResultService,
        dispatcher: WorkflowDispatcher | None = None,
    ) -> None:
        self._workflows = workflows
        self._results = results
        self._dispatcher = dispatcher

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
        with tracer().start_as_current_span(
            "caffemate.workflow.first_proposal",
            attributes={"caffemate.workflow.code": workflow_code.value},
        ):
            try:
                result = self._workflows.start(
                    project_id=project_id,
                    user_id=user_id,
                    workflow_code=workflow_code,
                    idempotency_key=idempotency_key,
                )
            except Exception:
                record_safe_metric(
                    "CAFFEMATE_WORKFLOW_REQUEST",
                    workflow_code=workflow_code.value,
                    result_status="ERROR",
                )
                raise
            record_safe_metric(
                "CAFFEMATE_WORKFLOW_REQUEST",
                workflow_code=workflow_code.value,
                result_status="ACCEPTED",
            )
            if result.status.value == "QUEUED":
                self.dispatch(result.workflow_run_id)
            return result

    def dispatch(self, workflow_run_id: str) -> None:
        if self._dispatcher is None:
            return
        try:
            self._dispatcher.dispatch(workflow_run_id)
        except Exception:  # noqa: BLE001 - durable outbox remains authoritative
            logger.exception("Immediate workflow dispatch failed; durable outbox remains pending")
            record_safe_metric(
                "CAFFEMATE_WORKFLOW_DISPATCH",
                workflow_code=WorkflowCode.FIRST_PROPOSAL.value,
                result_status="RETRY_PENDING",
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
