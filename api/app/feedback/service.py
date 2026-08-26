import hashlib
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

import rfc8785

from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import (
    ContractValidationError,
    FeedbackPreconditionError,
    PersistenceUnavailableError,
)
from app.domain.models import FounderState
from app.feedback.intent import (
    affected_feedback_stages,
    apply_feedback_operations,
    validate_intent_delta_result,
)
from app.feedback.models import (
    FeedbackPreview,
    FeedbackPreviewRecord,
    FeedbackPreviewStatus,
    FeedbackResolution,
)
from app.feedback.repository import FeedbackRepository
from app.projects.service import ProjectService
from app.results.service import ResultService
from app.workflows.models import HeadFence


class FeedbackAgentRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...


class FeedbackService:
    def __init__(
        self,
        repository: FeedbackRepository,
        projects: ProjectService,
        results: ResultService,
        runtime: FeedbackAgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._projects = projects
        self._results = results
        self._runtime = runtime
        self._task_factory = task_factory or AgentTaskFactory()
        self._new_id = new_id or (lambda: str(uuid4()))

    def create_preview(
        self,
        *,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        user_input: str,
    ) -> FeedbackPreview:
        project = self._projects.get_project(project_id=project_id, user_id=user_id)
        if project.state is None:
            raise FeedbackPreconditionError("Feedback requires confirmed onboarding")
        result = self._results.get_current(project_id=project_id, user_id=user_id)
        # 사용자 의도: 후보를 선택한 뒤에도 현재 자금·지역 조건을 바로 바꿀 수 있어야 한다.
        # 선택 자체가 State 버전을 올리므로 이전 결과 Head를 요구하면 정상 경로가 항상 막힌다.
        # 기존 결과는 후보 문맥으로만 사용하고, 변경안은 권위 있는 현재 Head와 State에 묶는다.
        current_head = result.current_head
        preview_id = self._new_id()
        task = self._task_factory.build_intent_delta(
            project_id=project_id,
            workflow_run_id=result.workflow_run_id,
            preview_id=preview_id,
            head=current_head,
            state=project.state,
            latest_user_input=user_input,
            current_candidate_refs=[
                candidate["candidate_id"]
                for candidate in result.candidates
                if isinstance(candidate.get("candidate_id"), str)
            ],
        )
        digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "project_id": project_id,
                    "result_bundle_id": result.result_bundle_id,
                    "head": current_head.model_dump(mode="json"),
                    "user_input": user_input.strip(),
                }
            )
        ).digest()
        record = self._repository.begin_preview(
            preview_id=preview_id,
            project_id=project_id,
            user_id=user_id,
            result_bundle_id=result.result_bundle_id,
            source_workflow_run_id=result.workflow_run_id,
            base_state_version=project.state.state_version,
            head_json=current_head.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            request_digest=digest,
            user_input=user_input.strip(),
            task=task,
        )
        if record.status != FeedbackPreviewStatus.PROCESSING:
            return self._public(record)
        runtime_result = self._runtime.invoke(record.task)
        payload = validate_intent_delta_result(
            task=record.task,
            result=runtime_result,
            current_head=record.head,
        )
        status = self._preview_status(runtime_result, payload)
        completed = self._repository.complete_preview(
            preview_id=record.preview_id,
            project_id=project_id,
            user_id=user_id,
            expected_head_json=record.head.model_dump(mode="json"),
            agent_result=runtime_result,
            proposal=payload,
            status=status,
        )
        return self._public(completed)

    def get_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
    ) -> FeedbackPreview:
        return self._public(
            self._repository.get_preview(
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
            )
        )

    def confirm_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        expected_head: HeadFence,
        proposal_digest: str,
    ) -> FeedbackResolution:
        record, state, workflow = self._repository.confirm_preview(
            preview_id=preview_id,
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            expected_head=expected_head,
            proposal_digest=proposal_digest,
        )
        return FeedbackResolution(
            preview=self._public(record),
            state_version=state.state_version,
            workflow=workflow,
        )

    def cancel_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> FeedbackResolution:
        record = self._repository.cancel_preview(
            preview_id=preview_id,
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        return FeedbackResolution(preview=self._public(record))

    @staticmethod
    def _preview_status(
        runtime_result: dict[str, Any],
        payload: dict[str, Any] | None,
    ) -> FeedbackPreviewStatus:
        if runtime_result["status"] == "INVALID":
            raise ContractValidationError("INTENT_DELTA Agent rejected its input")
        if payload is None:
            return (
                FeedbackPreviewStatus.CLARIFICATION_REQUIRED
                if runtime_result["status"] == "NEEDS_HUMAN"
                else FeedbackPreviewStatus.UNSUPPORTED
            )
        return {
            "PROPOSE_DELTA": FeedbackPreviewStatus.REVIEW_REQUIRED,
            "CLARIFY": FeedbackPreviewStatus.CLARIFICATION_REQUIRED,
            "NOOP": FeedbackPreviewStatus.NOOP,
            "UNSUPPORTED": FeedbackPreviewStatus.UNSUPPORTED,
        }[payload["decision"]]

    @classmethod
    def _public(cls, record: FeedbackPreviewRecord) -> FeedbackPreview:
        state_projection = record.task["payload"]["current_state_projection"]
        before = dict(state_projection["founder"])
        proposal = record.proposal or {}
        operations = proposal.get("operations", [])
        after = (
            apply_feedback_operations(
                FounderState.model_validate(before),
                operations,
            ).model_dump(mode="json")
            if record.status == FeedbackPreviewStatus.REVIEW_REQUIRED
            else None
        )
        field_paths = {
            operation["field_path"]
            for operation in operations
            if isinstance(operation, dict) and isinstance(operation.get("field_path"), str)
        }
        candidate_refs = record.task["payload"]["current_candidate_refs"]
        return FeedbackPreview(
            preview_id=record.preview_id,
            project_id=record.project_id,
            result_bundle_id=record.result_bundle_id,
            source_workflow_run_id=record.source_workflow_run_id,
            base_state_version=record.base_state_version,
            head=record.head,
            status=record.status,
            latest_user_input=record.user_input,
            before_founder=before,
            after_founder=after,
            operations=operations,
            clarifying_questions=proposal.get("clarifying_questions", []),
            affected_candidate_ids=(candidate_refs if operations else []),
            affected_stage_codes=affected_feedback_stages(field_paths),
            risk_flags=proposal.get("risk_flags", []),
            proposal_digest=record.proposal_digest,
            agent_trace={
                "task_id": record.task["task_id"],
                "invocation_id": record.task["invocation_id"],
                "input_digest": record.task["input_digest"],
                "prompt_version": record.task["prompt_version"],
            },
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class UnavailableFeedbackService:
    def create_preview(self, **_: object) -> FeedbackPreview:
        raise PersistenceUnavailableError("Feedback persistence or Agent Runtime is unavailable")

    def get_preview(self, **_: object) -> FeedbackPreview:
        raise PersistenceUnavailableError("Feedback persistence or Agent Runtime is unavailable")

    def confirm_preview(self, **_: object) -> FeedbackResolution:
        raise PersistenceUnavailableError("Feedback persistence or Agent Runtime is unavailable")

    def cancel_preview(self, **_: object) -> FeedbackResolution:
        raise PersistenceUnavailableError("Feedback persistence or Agent Runtime is unavailable")
