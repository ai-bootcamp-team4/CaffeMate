from app.candidates.models import (
    CandidateDecision,
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    RankBasis,
    ReviewStatus,
    RiskSeverity,
)
from app.domain.models import CaseType, FranchiseEligibility
from app.finance.models import CapitalGateStatus


def rank_candidates(values: list[CandidateDecisionInput]) -> list[CandidateDecision]:
    _reject_duplicate_ids(values)
    classified = [(value, _classify(value)) for value in values]
    reviewable = [
        item for item in classified if item[1].review_status != ReviewStatus.EXCLUDED
    ]
    excluded = [
        decision
        for _value, decision in classified
        if decision.review_status == ReviewStatus.EXCLUDED
    ]

    recommended = sorted(
        (item for item in reviewable if item[1].review_status == ReviewStatus.REVIEW_RECOMMENDED),
        key=lambda item: _recommended_key(item[0]),
    )
    conditional = sorted(
        (item for item in reviewable if item[1].review_status == ReviewStatus.CONDITIONAL_REVIEW),
        key=lambda item: _conditional_key(item[0]),
    )

    ranked: list[CandidateDecision] = []
    for position, (_value, decision) in enumerate([*recommended, *conditional], start=1):
        ranked.append(
            decision.model_copy(
                update={
                    "rank": position,
                    "is_primary_next_review": position == 1,
                }
            )
        )
    return [*ranked, *sorted(excluded, key=lambda decision: decision.candidate_id)]


def _classify(value: CandidateDecisionInput) -> CandidateDecision:
    exclusion_reasons = _exclusion_reasons(value)
    if exclusion_reasons:
        return CandidateDecision(
            candidate_id=value.candidate_id,
            review_status=ReviewStatus.EXCLUDED,
            reason_codes=exclusion_reasons,
            rank=None,
            rank_basis=RankBasis.NOT_RANKED,
            is_primary_next_review=False,
        )

    conditional_reasons = _conditional_reasons(value)
    if conditional_reasons:
        return CandidateDecision(
            candidate_id=value.candidate_id,
            review_status=ReviewStatus.CONDITIONAL_REVIEW,
            reason_codes=conditional_reasons,
            rank_basis=RankBasis.NEXT_REVIEW_PRIORITY,
            is_primary_next_review=False,
        )

    return CandidateDecision(
        candidate_id=value.candidate_id,
        review_status=ReviewStatus.REVIEW_RECOMMENDED,
        reason_codes=["CURRENT_CONSTRAINTS_SATISFIED"],
        rank_basis=RankBasis.ECONOMIC_AND_FOUNDER_FIT,
        is_primary_next_review=False,
    )


def _exclusion_reasons(value: CandidateDecisionInput) -> list[str]:
    reasons = list(value.confirmed_hard_constraint_codes)
    if value.capital_gate.status == CapitalGateStatus.FAIL:
        reasons.append(value.capital_gate.reason_code)
    if value.founder_fit == FounderFitStatus.FAIL:
        reasons.append("FOUNDER_FIT_HARD_CONFLICT")
    if value.case_type == CaseType.FRANCHISE:
        if value.franchise_eligibility == FranchiseEligibility.INELIGIBLE:
            reasons.append("FRANCHISE_INELIGIBLE")
        elif (
            value.franchise_eligibility != FranchiseEligibility.VERIFIED
            or not value.franchise_eligibility_evidence_refs
        ):
            reasons.append("FRANCHISE_ELIGIBILITY_UNVERIFIED")
        if value.franchise_availability == FranchiseAvailability.UNAVAILABLE:
            reasons.append("FRANCHISE_UNAVAILABLE_IN_AREA")
    return sorted(set(reasons))


def _conditional_reasons(value: CandidateDecisionInput) -> list[str]:
    reasons: list[str] = []
    if value.capital_gate.status == CapitalGateStatus.CONDITIONAL:
        reasons.append(value.capital_gate.reason_code)
    if value.founder_fit == FounderFitStatus.CONDITIONAL:
        reasons.append("FOUNDER_FIT_REQUIRES_CONFIRMATION")
    if value.finance.unknown_cost_fields:
        reasons.append("MATERIAL_COST_UNKNOWN")
    if value.material_missing_fields:
        reasons.append("MATERIAL_FIELD_MISSING")
    if any(risk.severity == RiskSeverity.CRITICAL for risk in value.risks):
        reasons.append("CRITICAL_RISK_REQUIRES_REVIEW")
    if value.case_type == CaseType.FRANCHISE and value.franchise_availability in {
        FranchiseAvailability.HQ_CONFIRMATION_REQUIRED,
        FranchiseAvailability.UNKNOWN,
    }:
        reasons.append("FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED")
    return sorted(set(reasons))


def _recommended_key(value: CandidateDecisionInput) -> tuple[int, int, int, int, str]:
    return (
        _risk_count(value, RiskSeverity.HIGH),
        _founder_burden_order(value.founder_burden),
        _known_or_max(value.finance.monthly_fixed_cost.base),
        _known_or_max(value.finance.initial_cash.base),
        value.candidate_id,
    )


def _conditional_key(value: CandidateDecisionInput) -> tuple[int, int, int, int, str]:
    return (
        _risk_count(value, RiskSeverity.CRITICAL),
        len(set(value.material_missing_fields) | set(value.finance.unknown_cost_fields)),
        _risk_count(value, RiskSeverity.HIGH),
        _founder_burden_order(value.founder_burden),
        value.candidate_id,
    )


def _risk_count(value: CandidateDecisionInput, severity: RiskSeverity) -> int:
    return sum(risk.severity == severity for risk in value.risks)


def _known_or_max(value: int | None) -> int:
    return value if value is not None else 2**63 - 1


def _founder_burden_order(value: FounderBurdenLevel) -> int:
    return {
        FounderBurdenLevel.LOW: 0,
        FounderBurdenLevel.MEDIUM: 1,
        FounderBurdenLevel.HIGH: 2,
        FounderBurdenLevel.UNKNOWN: 3,
    }[value]


def _reject_duplicate_ids(values: list[CandidateDecisionInput]) -> None:
    ids = [value.candidate_id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate_id must be unique within a result bundle")
