from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.domain.models import StrictModel
from app.workflows.models import HeadFence, WorkflowRun


class FeedbackPreviewStatus(StrEnum):
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NOOP = "NOOP"
    UNSUPPORTED = "UNSUPPORTED"
    EXPIRED = "EXPIRED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class FeedbackPreviewRecord(StrictModel):
    preview_id: str
    project_id: str
    owner_user_id: str
    result_bundle_id: str
    source_workflow_run_id: str
    base_state_version: int = Field(ge=1)
    head: HeadFence
    idempotency_key: str
    user_input: str
    task: dict[str, Any]
    agent_result: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    proposal_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    status: FeedbackPreviewStatus
    resolution_idempotency_key: str | None = None
    resolution_request_digest: bytes | None = None
    confirmed_event_id: str | None = None
    confirmed_state_version: int | None = Field(default=None, ge=1)
    recompute_workflow_run_id: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FeedbackPreview(StrictModel):
    preview_id: str
    project_id: str
    result_bundle_id: str
    source_workflow_run_id: str
    base_state_version: int = Field(ge=1)
    head: HeadFence
    status: FeedbackPreviewStatus
    latest_user_input: str
    before_founder: dict[str, Any]
    after_founder: dict[str, Any] | None
    operations: list[dict[str, Any]]
    clarifying_questions: list[str]
    affected_candidate_ids: list[str]
    affected_stage_codes: list[str]
    risk_flags: list[str]
    proposal_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    agent_trace: dict[str, str]
    created_at: datetime
    updated_at: datetime


class CreateFeedbackPreviewRequest(StrictModel):
    input: str = Field(min_length=1, max_length=8000)


class ConfirmFeedbackRequest(StrictModel):
    expected_head: HeadFence
    proposal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FeedbackResolution(StrictModel):
    preview: FeedbackPreview
    state_version: int | None = Field(default=None, ge=1)
    workflow: WorkflowRun | None = None
