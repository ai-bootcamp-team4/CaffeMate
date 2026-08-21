from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BorrowingIntent(StrEnum):
    YES = "YES"
    NO = "NO"
    UNDECIDED = "UNDECIDED"


class CafeTypePreference(StrEnum):
    OPEN_TO_BOTH = "OPEN_TO_BOTH"
    INDEPENDENT_ONLY = "INDEPENDENT_ONLY"
    FRANCHISE_ONLY = "FRANCHISE_ONLY"


class OperationMode(StrEnum):
    DIRECT_FULL_TIME = "DIRECT_FULL_TIME"
    DIRECT_PART_TIME = "DIRECT_PART_TIME"
    EMPLOYEE_LED = "EMPLOYEE_LED"
    UNDECIDED = "UNDECIDED"


class VentureStatus(StrEnum):
    ONBOARDING = "ONBOARDING"
    ANALYZING = "ANALYZING"
    RESULT_READY = "RESULT_READY"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    RECOMPUTE_REQUIRED = "RECOMPUTE_REQUIRED"


class AreaResolutionStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    RESOLVED = "RESOLVED"


class CoverageProfile(StrEnum):
    N0_NATIONWIDE_FACTS = "N0_NATIONWIDE_FACTS"
    N1_NATIONWIDE_CONDITIONAL = "N1_NATIONWIDE_CONDITIONAL"
    R2_REGIONAL_CONNECTOR = "R2_REGIONAL_CONNECTOR"
    C3_CASE_ARTIFACT = "C3_CASE_ARTIFACT"


class CaseType(StrEnum):
    INDEPENDENT = "INDEPENDENT"
    FRANCHISE = "FRANCHISE"


class CaseMaturity(StrEnum):
    CONCEPT = "CONCEPT"
    CANDIDATE = "CANDIDATE"
    PROPERTY_LINKED = "PROPERTY_LINKED"
    DOCUMENT_LINKED = "DOCUMENT_LINKED"


class CaseStatus(StrEnum):
    DRAFT = "DRAFT"
    CONDITIONALLY_REVIEWABLE = "CONDITIONALLY_REVIEWABLE"
    EXCLUDED = "EXCLUDED"
    SELECTED = "SELECTED"


class FranchiseEligibility(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    INELIGIBLE = "INELIGIBLE"


class FounderState(StrictModel):
    target_area_input: str = Field(min_length=1)
    own_funds_krw: int = Field(ge=0)
    borrowing_intent: BorrowingIntent
    cafe_type_preference: CafeTypePreference
    operation_mode: OperationMode
    current_work_status: str | None = None
    desired_opening_period: str | None = None
    prior_cafe_experience: str | None = None
    preferences: list[str] = Field(default_factory=list)
    avoidances: list[str] = Field(default_factory=list)
    max_loss_krw: int | None = Field(default=None, ge=0)


class AreaState(StrictModel):
    resolution_status: AreaResolutionStatus
    administrative_code: str | None = None
    display_name: str | None = None
    boundary_version: str | None = None
    coverage_profile: CoverageProfile
    evidence_ids: list[str] = Field(default_factory=list)
    unavailable_fields: list[str] = Field(default_factory=list)


class VentureCase(StrictModel):
    case_id: str = Field(min_length=1)
    case_type: CaseType
    maturity: CaseMaturity
    status: CaseStatus
    display_name: str | None = None
    franchise_eligibility: FranchiseEligibility | None = None
    confirmed_claim_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class VentureState(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    state_version: int = Field(ge=1)
    status: VentureStatus
    active_case_id: str | None = None
    founder: FounderState
    area: AreaState
    venture_cases: list[VentureCase] = Field(default_factory=list)
    evidence_snapshot_id: str | None = None
    policy_version: str | None = None
    assumption_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    updated_at: datetime


class Project(StrictModel):
    project_id: str
    user_id: str
    created_at: datetime
    state: VentureState | None = None
