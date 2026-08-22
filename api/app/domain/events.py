from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.domain.models import AreaState, FounderState, StrictModel


class ProjectCreated(StrictModel):
    event_id: str
    event_type: Literal["PROJECT_CREATED"] = "PROJECT_CREATED"
    project_id: str
    user_id: str
    occurred_at: datetime


class OnboardingConfirmed(StrictModel):
    event_id: str
    event_type: Literal["ONBOARDING_CONFIRMED"] = "ONBOARDING_CONFIRMED"
    project_id: str
    user_id: str
    occurred_at: datetime
    founder: FounderState
    area: AreaState | None = None


class FeedbackChangeConfirmed(StrictModel):
    event_id: str
    event_type: Literal["FEEDBACK_CHANGE_CONFIRMED"] = "FEEDBACK_CHANGE_CONFIRMED"
    project_id: str
    user_id: str
    occurred_at: datetime
    preview_id: str
    expected_state_version: int = Field(ge=1)
    proposal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    operations: list[dict[str, Any]] = Field(min_length=1)


class CandidateSelected(StrictModel):
    event_id: str
    event_type: Literal["CANDIDATE_SELECTED"] = "CANDIDATE_SELECTED"
    project_id: str
    user_id: str
    occurred_at: datetime
    selection_id: str
    result_bundle_id: str
    expected_state_version: int = Field(ge=1)
    candidate: dict[str, Any]


class DocumentClaimsApplied(StrictModel):
    event_id: str
    event_type: Literal["DOCUMENT_CLAIMS_APPLIED"] = "DOCUMENT_CLAIMS_APPLIED"
    project_id: str
    user_id: str
    occurred_at: datetime
    application_id: str
    document_id: str
    document_revision_id: str
    expected_state_version: int = Field(ge=1)
    active_case_id: str
    confirmed_claim_ids: list[str]
    conflict_ids: list[str]


DomainEvent = (
    ProjectCreated
    | OnboardingConfirmed
    | FeedbackChangeConfirmed
    | CandidateSelected
    | DocumentClaimsApplied
)


class ConfirmOnboardingCommand(StrictModel):
    project_id: str
    user_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)
    founder: FounderState
    area: AreaState | None = None
