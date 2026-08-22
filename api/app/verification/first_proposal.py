from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, text

from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
    Project,
)
from app.results.models import ResultFreshness, ResultView
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode, WorkflowProgress, WorkflowRun, WorkflowStatus


class ProjectOperations(Protocol):
    def create_project(self, *, user_id: str, idempotency_key: str) -> Project: ...

    def confirm_onboarding(
        self,
        *,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        founder: FounderState,
    ) -> Project: ...


class WorkflowOperations(Protocol):
    def start(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun: ...

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress: ...

    def cancel(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> WorkflowRun: ...


class ResultOperations(Protocol):
    def get_current(self, *, project_id: str, user_id: str) -> ResultView: ...


class CanaryCleaner(Protocol):
    def cleanup(self, *, project_id: str, user_id: str) -> None: ...


class FirstProposalCanaryError(RuntimeError):
    def __init__(self, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class FirstProposalCanaryReport:
    status: str
    workflow_status: str
    stage_count: int
    candidate_count: int
    candidate_case_types: tuple[str, ...]
    result_freshness: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "workflow_status": self.workflow_status,
            "stage_count": self.stage_count,
            "candidate_count": self.candidate_count,
            "candidate_case_types": list(self.candidate_case_types),
            "result_freshness": self.result_freshness,
        }


class PostgresFirstProposalCanaryCleaner:
    """Delete only resources owned by one generated canary identity."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def cleanup(self, *, project_id: str, user_id: str) -> None:
        with self._engine.begin() as connection:
            owned = connection.execute(
                text(
                    "SELECT project_id FROM venture_projects "
                    "WHERE project_id=:project_id AND owner_user_id=:user_id FOR UPDATE"
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one_or_none()
            if owned is not None:
                connection.execute(
                    text(
                        """
                        DELETE FROM workflow_outbox queued
                        WHERE queued.aggregate_id IN (
                            SELECT run.workflow_run_id
                            FROM workflow_runs run
                            WHERE run.project_id=:project_id
                            UNION
                            SELECT stage.stage_run_id
                            FROM stage_runs stage
                            JOIN workflow_runs run
                              ON run.workflow_run_id=stage.workflow_run_id
                            WHERE run.project_id=:project_id
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM workflow_runs run
                            WHERE run.project_id=:project_id
                              AND queued.payload_json->>'workflow_run_id'=run.workflow_run_id
                        )
                        """
                    ),
                    {"project_id": project_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM venture_projects "
                        "WHERE project_id=:project_id AND owner_user_id=:user_id"
                    ),
                    {"project_id": project_id, "user_id": user_id},
                )
            connection.execute(
                text("DELETE FROM workflow_idempotency_records WHERE user_id=:user_id"),
                {"user_id": user_id},
            )
            connection.execute(
                text("DELETE FROM idempotency_records WHERE user_id=:user_id"),
                {"user_id": user_id},
            )

        with self._engine.connect() as connection:
            remaining = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM venture_projects
                       WHERE project_id=:project_id OR owner_user_id=:user_id)
                    + (SELECT COUNT(*) FROM idempotency_records WHERE user_id=:user_id)
                    + (SELECT COUNT(*) FROM workflow_idempotency_records WHERE user_id=:user_id)
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one()
        if remaining != 0:
            raise FirstProposalCanaryError("CANARY_CLEANUP_INCOMPLETE")


class FirstProposalCanary:
    _terminal_statuses = {
        WorkflowStatus.WAITING_FOR_HUMAN,
        WorkflowStatus.SUCCEEDED,
        WorkflowStatus.PARTIAL,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.STALE,
    }

    def __init__(
        self,
        *,
        projects: ProjectOperations,
        workflows: WorkflowOperations,
        results: ResultOperations,
        cleaner: CanaryCleaner,
        now: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._projects = projects
        self._workflows = workflows
        self._results = results
        self._cleaner = cleaner
        self._now = now
        self._wait = wait
        self._new_id = new_id or (lambda: uuid4().hex)

    def run(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> FirstProposalCanaryReport:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Canary timeout and poll interval must be positive")
        canary_id = self._new_id()
        user_id = f"first-proposal-canary-{canary_id}"
        project: Project | None = None
        workflow: WorkflowRun | None = None
        try:
            project = self._projects.create_project(
                user_id=user_id,
                idempotency_key=f"{canary_id}:create",
            )
            self._projects.confirm_onboarding(
                project_id=project.project_id,
                user_id=user_id,
                idempotency_key=f"{canary_id}:onboarding",
                founder=FounderState(
                    target_area_input="경기도 수원시 영통구 월드컵로 206",
                    own_funds_krw=70_000_000,
                    borrowing_intent=BorrowingIntent.UNDECIDED,
                    cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
                    operation_mode=OperationMode.DIRECT_FULL_TIME,
                    preferences=["대학가 생활권", "개인카페와 프랜차이즈 비교"],
                ),
            )
            workflow = self._workflows.start(
                project_id=project.project_id,
                user_id=user_id,
                workflow_code=WorkflowCode.FIRST_PROPOSAL,
                idempotency_key=f"{canary_id}:workflow",
            )
            progress = self._await_terminal(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=workflow.workflow_run_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            return self._validate_result(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=workflow.workflow_run_id,
                progress=progress,
            )
        except Exception:
            if project is not None and workflow is not None:
                self._cancel_if_active(
                    project_id=project.project_id,
                    user_id=user_id,
                    workflow_run_id=workflow.workflow_run_id,
                    idempotency_key=f"{canary_id}:cancel",
                )
            raise
        finally:
            if project is not None:
                self._cleaner.cleanup(project_id=project.project_id, user_id=user_id)

    def _await_terminal(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> WorkflowProgress:
        deadline = self._now() + timeout_seconds
        while True:
            progress = self._workflows.get_progress(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            if progress.status in self._terminal_statuses:
                return progress
            if self._now() >= deadline:
                raise FirstProposalCanaryError(
                    "CANARY_TIMED_OUT",
                    {
                        "workflow_status": progress.status.value,
                        "completed_stage_count": progress.completed_stage_count,
                        "current_stage_codes": progress.current_stage_codes,
                    },
                )
            self._wait(min(poll_interval_seconds, max(0.0, deadline - self._now())))

    def _validate_result(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        progress: WorkflowProgress,
    ) -> FirstProposalCanaryReport:
        if progress.status != WorkflowStatus.SUCCEEDED:
            raise FirstProposalCanaryError(
                "CANARY_WORKFLOW_NOT_SUCCEEDED",
                {
                    "workflow_status": progress.status.value,
                    "completed_stage_count": progress.completed_stage_count,
                    "terminal_reason_codes": progress.terminal_reason_codes,
                    "human_review_requests": [
                        request.model_dump(mode="json")
                        for request in progress.human_review_requests
                    ],
                },
            )
        expected_stages = {stage.value for stage in FirstProposalStage}
        observed_stages = {stage.stage_code for stage in progress.stages}
        unsuccessful = [
            stage.stage_code for stage in progress.stages if stage.status.value != "SUCCEEDED"
        ]
        if (
            len(progress.stages) != len(expected_stages)
            or observed_stages != expected_stages
            or unsuccessful
        ):
            raise FirstProposalCanaryError(
                "CANARY_STAGE_SET_INVALID",
                {
                    "expected_stage_count": len(expected_stages),
                    "observed_stage_count": len(progress.stages),
                    "missing_stage_codes": sorted(expected_stages - observed_stages),
                    "unexpected_stage_codes": sorted(observed_stages - expected_stages),
                    "unsuccessful_stage_codes": unsuccessful,
                },
            )
        result = self._results.get_current(project_id=project_id, user_id=user_id)
        if (
            result.workflow_run_id != workflow_run_id
            or result.freshness != ResultFreshness.CURRENT
            or not result.candidates
            or result.primary_candidate_id
            not in {candidate.get("candidate_id") for candidate in result.candidates}
        ):
            raise FirstProposalCanaryError(
                "CANARY_RESULT_INVALID",
                {
                    "result_workflow_matches": result.workflow_run_id == workflow_run_id,
                    "result_freshness": result.freshness.value,
                    "candidate_count": len(result.candidates),
                },
            )
        case_types = sorted(
            {
                str(candidate["case_type"])
                for candidate in result.candidates
                if candidate.get("case_type") in {"INDEPENDENT", "FRANCHISE"}
            }
        )
        if not case_types:
            raise FirstProposalCanaryError("CANARY_CANDIDATE_TYPE_INVALID")
        return FirstProposalCanaryReport(
            status="verified",
            workflow_status=progress.status.value,
            stage_count=len(progress.stages),
            candidate_count=len(result.candidates),
            candidate_case_types=tuple(case_types),
            result_freshness=result.freshness.value,
        )

    def _cancel_if_active(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        idempotency_key: str,
    ) -> None:
        try:
            progress = self._workflows.get_progress(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            if progress.status not in self._terminal_statuses:
                self._workflows.cancel(
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            # Cleanup remains project-scoped and is the final containment boundary.
            return
