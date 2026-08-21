from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.domain.models import StrictModel
from app.workflows.models import HeadFence


class ChecklistStatus(StrEnum):
    REQUIRED = "REQUIRED"
    MISSING_FROM_RESULT = "MISSING_FROM_RESULT"
    HQ_CONFIRMATION_REQUIRED = "HQ_CONFIRMATION_REQUIRED"


class EvidenceChecklistItem(StrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    title: str = Field(min_length=1)
    status: ChecklistStatus
    reason: str = Field(min_length=1)


class SelectCandidateRequest(StrictModel):
    result_bundle_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    expected_head: HeadFence


class CandidateSelection(StrictModel):
    selection_id: str
    project_id: str
    result_bundle_id: str
    candidate_id: str
    selected_state_version: int = Field(ge=1)
    candidate: dict[str, Any]
    required_evidence: list[EvidenceChecklistItem]
    property_intake_enabled: bool = True
    document_intake_enabled: bool = True
    is_final_go_decision: bool = False
    created_at: datetime
