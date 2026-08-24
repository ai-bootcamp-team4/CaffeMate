from typing import Any, cast

from app.results.service import ResultService
from app.workflows.first_proposal_service import FirstProposalService
from app.workflows.models import WorkflowCode
from app.workflows.service import WorkflowService


class WorkflowFixture:
    def __init__(self) -> None:
        self.start_call: dict[str, Any] | None = None
        self.progress_call: dict[str, Any] | None = None

    def start(self, **kwargs: Any) -> object:
        self.start_call = kwargs
        return "run"

    def get_progress(self, **kwargs: Any) -> object:
        self.progress_call = kwargs
        return "progress"


class ResultFixture:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    def get_current(self, **kwargs: Any) -> object:
        self.call = kwargs
        return "result"


def test_first_proposal_facade_fixes_workflow_code_and_delegates_public_path() -> None:
    workflows = WorkflowFixture()
    results = ResultFixture()
    service = FirstProposalService(
        cast(WorkflowService, workflows),
        cast(ResultService, results),
    )

    assert service.run(
        project_id="project-1",
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="run-1",
    ) == "run"
    assert service.progress(
        project_id="project-1",
        workflow_run_id="workflow-1",
        user_id="user-1",
    ) == "progress"
    assert service.result(project_id="project-1", user_id="user-1") == "result"

    assert workflows.start_call == {
        "project_id": "project-1",
        "user_id": "user-1",
        "workflow_code": "FIRST_PROPOSAL",
        "idempotency_key": "run-1",
    }
    assert workflows.progress_call == {
        "project_id": "project-1",
        "workflow_run_id": "workflow-1",
        "user_id": "user-1",
    }
    assert results.call == {"project_id": "project-1", "user_id": "user-1"}
