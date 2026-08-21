from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.models import FounderState, StrictModel


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


DomainEvent = ProjectCreated | OnboardingConfirmed


class ConfirmOnboardingCommand(StrictModel):
    project_id: str
    user_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)
    founder: FounderState
