from typing import Protocol

from app.feedback.models import FeedbackPreviewRecord, FeedbackPreviewStatus


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
