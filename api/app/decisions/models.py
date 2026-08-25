from enum import StrEnum
from typing import Any

from pydantic import Field

from app.domain.models import StrictModel
from app.finance.models import MoneyRange, ValueProvenance


class ResolutionStatus(StrEnum):
    RESOLVED_FACT = "RESOLVED_FACT"
    RESOLVED_USER_CONFIRMED = "RESOLVED_USER_CONFIRMED"
    RESOLVED_BENCHMARK = "RESOLVED_BENCHMARK"
    RESOLVED_DERIVED = "RESOLVED_DERIVED"
    ASSUMED = "ASSUMED"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    DOCUMENT_REQUIRED = "DOCUMENT_REQUIRED"
    EXTERNAL_CONFIRMATION_REQUIRED = "EXTERNAL_CONFIRMATION_REQUIRED"
    UNSUPPORTED_BY_DATA = "UNSUPPORTED_BY_DATA"


class DecisionRole(StrEnum):
    FINANCE_INPUT = "FINANCE_INPUT"
    CONSTRAINT_INPUT = "CONSTRAINT_INPUT"
    VERIFICATION_ONLY = "VERIFICATION_ONLY"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class ResolutionActionType(StrEnum):
    PROPERTY_TERMS = "PROPERTY_TERMS"
    DOCUMENT_INTAKE = "DOCUMENT_INTAKE"
    USER_INPUT = "USER_INPUT"
    EXTERNAL_CONFIRMATION = "EXTERNAL_CONFIRMATION"
    NONE = "NONE"


class ResolutionAction(StrictModel):
    action_type: ResolutionActionType
    target_fields: list[str] = Field(default_factory=list)
    accepted_document_types: list[str] = Field(default_factory=list)


class DecisionDerivation(StrictModel):
    formula_code: str = Field(min_length=1)
    inputs: dict[str, Any]
    coverage_status: str = Field(min_length=1)
    floor_basis: str = Field(min_length=1)


class DecisionInput(StrictModel):
    field: str = Field(min_length=1)
    value_range_krw: MoneyRange
    provenance: ValueProvenance
    resolution_status: ResolutionStatus
    decision_role: DecisionRole
    source_title: str | None = None
    source_ref: str | None = None
    data_date: str | None = None
    geographic_scope: dict[str, Any] | None = None
    source_anchor: str | None = None
    applied_to: list[str] = Field(default_factory=list)
    replaceable_by: list[ResolutionActionType] = Field(default_factory=list)
    resolution_action: ResolutionAction
    limitation_code: str | None = None
    derivation: DecisionDerivation | None = None


class VerificationRequirement(StrictModel):
    requirement_id: str = Field(min_length=1)
    status: ResolutionStatus
    decision_role: DecisionRole
    resolver: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    required_evidence: list[str] = Field(default_factory=list)
    resolution_action: ResolutionAction
    why_caffemate_cannot_resolve: str = Field(min_length=1)
