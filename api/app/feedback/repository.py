from typing import Protocol

from app.domain.models import VentureState
from app.feedback.models import FeedbackPreviewRecord, FeedbackPreviewStatus
from app.workflows.models import HeadFence, WorkflowRun


class FeedbackRepository(Protocol):
    def begin_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        result_bundle_id: str,
        source_workflow_run_id: str,
        base_state_version: int,
        head_json: dict[str, object],
        idempotency_key: str,
        request_digest: bytes,
        user_input: str,
        task: dict[str, object],
    ) -> FeedbackPreviewRecord: ...

    def complete_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        expected_head_json: dict[str, object],
        agent_result: dict[str, object],
        proposal: dict[str, object] | None,
        status: FeedbackPreviewStatus,
    ) -> FeedbackPreviewRecord: ...

    def get_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
    ) -> FeedbackPreviewRecord: ...

    def confirm_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        expected_head: HeadFence,
        proposal_digest: str,
    ) -> tuple[FeedbackPreviewRecord, VentureState, WorkflowRun]: ...

    def cancel_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> FeedbackPreviewRecord: ...
