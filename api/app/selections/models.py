from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.domain.models import StrictModel
from app.workflows.models import HeadFence, WorkflowRun


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


class PropertyTermsInput(StrictModel):
    address: str = Field(min_length=1, max_length=256)
    area_sqm: float = Field(gt=0, le=1000)
    floor: str | None = Field(default=None, max_length=32)
    deposit_krw: int = Field(ge=0, le=10_000_000_000)
    monthly_rent_krw: int = Field(ge=0, le=1_000_000_000)
    management_fee_krw: int = Field(ge=0, le=1_000_000_000)
    key_money_krw: int | None = Field(default=None, ge=0, le=10_000_000_000)


class ApplyPropertyTermsRequest(StrictModel):
    expected_state_version: int = Field(ge=1)
    terms: PropertyTermsInput


class PropertyTermsApplication(StrictModel):
    property_input_id: str
    project_id: str
    selection_id: str
    candidate_id: str
    applied_state_version: int = Field(ge=2)
    terms: PropertyTermsInput
    previous_financial_summary: dict[str, Any]
    recompute_workflow: WorkflowRun
    input_kind: str = "USER_CONFIRMED_PROPERTY_TERMS"
    is_demo_fixture: bool = False
    created_at: datetime
