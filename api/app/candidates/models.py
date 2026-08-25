from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.models import CaseType, FranchiseEligibility, StrictModel
from app.finance.models import CapitalGateResult, FinanceResult


class FounderFitStatus(StrEnum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


class FounderBurdenLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class FranchiseAvailability(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AVAILABLE = "AVAILABLE"
    HQ_CONFIRMATION_REQUIRED = "HQ_CONFIRMATION_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class RiskSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskSignal(StrictModel):
    risk_id: str = Field(min_length=1)
    severity: RiskSeverity


class CandidateDecisionInput(StrictModel):
    candidate_id: str = Field(min_length=1)
    case_type: CaseType
    finance: FinanceResult
    capital_gate: CapitalGateResult
    founder_fit: FounderFitStatus
    founder_burden: FounderBurdenLevel
    confirmed_hard_constraint_codes: list[str] = Field(default_factory=list)
    material_missing_fields: list[str] = Field(default_factory=list)
    conditional_reason_codes: list[str] = Field(default_factory=list)
    risks: list[RiskSignal] = Field(default_factory=list)
    franchise_eligibility: FranchiseEligibility = FranchiseEligibility.NOT_APPLICABLE
    franchise_eligibility_evidence_refs: list[str] = Field(default_factory=list)
    franchise_availability: FranchiseAvailability = FranchiseAvailability.NOT_APPLICABLE

    @model_validator(mode="after")
    def case_specific_fields_are_consistent(self) -> "CandidateDecisionInput":
        if (
            self.founder_burden == FounderBurdenLevel.UNKNOWN
            and self.founder_fit == FounderFitStatus.PASS
        ):
            raise ValueError("UNKNOWN founder burden cannot have PASS founder fit")
        if self.case_type == CaseType.INDEPENDENT:
            if self.franchise_eligibility != FranchiseEligibility.NOT_APPLICABLE:
                raise ValueError("independent candidate cannot have franchise eligibility")
            if self.franchise_eligibility_evidence_refs:
                raise ValueError("independent candidate cannot have franchise eligibility evidence")
            if self.franchise_availability != FranchiseAvailability.NOT_APPLICABLE:
                raise ValueError("independent candidate cannot have franchise availability")
        elif self.franchise_availability == FranchiseAvailability.NOT_APPLICABLE:
            raise ValueError("franchise candidate requires an availability status")
        return self


class ReviewStatus(StrEnum):
    REVIEW_RECOMMENDED = "REVIEW_RECOMMENDED"
    CONDITIONAL_REVIEW = "CONDITIONAL_REVIEW"
    EXCLUDED = "EXCLUDED"


class RankBasis(StrEnum):
    ECONOMIC_AND_FOUNDER_FIT = "ECONOMIC_AND_FOUNDER_FIT"
    NEXT_REVIEW_PRIORITY = "NEXT_REVIEW_PRIORITY"
    NOT_RANKED = "NOT_RANKED"


class RankFactor(StrictModel):
    factor_code: str = Field(min_length=1)
    value: int | str | None
    direction: str = Field(pattern=r"^(ASC|DESC)$")


class RankTrace(StrictModel):
    ranking_class: str = Field(min_length=1)
    factors: list[RankFactor]
    decisive_factor_code: str | None = None
    compared_candidate_id: str | None = None
    tie_break_used: bool = False


class CandidateDecision(StrictModel):
    candidate_id: str
    review_status: ReviewStatus
    reason_codes: list[str]
    rank: int | None = Field(default=None, ge=1)
    rank_basis: RankBasis
    is_primary_next_review: bool
    rank_trace: RankTrace | None = None
