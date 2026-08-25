from collections.abc import Callable

from app.candidates.models import (
    CandidateDecision,
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    RankBasis,
    RankFactor,
    RankTrace,
    ReviewStatus,
    RiskSeverity,
)
from app.domain.models import CaseType, FranchiseEligibility
from app.finance.models import CapitalGateStatus

type RankValue = int | str | None
type RankFactorSpec = tuple[str, Callable[[CandidateDecisionInput], RankValue]]

_RECOMMENDED_FACTORS: tuple[RankFactorSpec, ...] = (
    ("HIGH_RISK_COUNT", lambda value: _risk_count(value, RiskSeverity.HIGH)),
    ("FOUNDER_BURDEN", lambda value: _founder_burden_order(value.founder_burden)),
    ("MONTHLY_FIXED_COST_BASE_KRW", lambda value: value.finance.monthly_fixed_cost.base),
    ("INITIAL_CASH_BASE_KRW", lambda value: value.finance.initial_cash.base),
)
_CONDITIONAL_FACTORS: tuple[RankFactorSpec, ...] = (
    ("CRITICAL_RISK_COUNT", lambda value: _risk_count(value, RiskSeverity.CRITICAL)),
    ("MATERIAL_GAP_COUNT", lambda value: _material_gap_count(value)),
    ("HIGH_RISK_COUNT", lambda value: _risk_count(value, RiskSeverity.HIGH)),
    ("FOUNDER_BURDEN", lambda value: _founder_burden_order(value.founder_burden)),
)


def rank_candidates(values: list[CandidateDecisionInput]) -> list[CandidateDecision]:
    _reject_duplicate_ids(values)
    classified = [(value, _classify(value)) for value in values]
    reviewable = [
        item for item in classified if item[1].review_status != ReviewStatus.EXCLUDED
    ]
    excluded = [
        (value, decision)
        for value, decision in classified
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
    ordered = [*recommended, *conditional]

    ranked: list[CandidateDecision] = []
    for position, (value, decision) in enumerate(ordered, start=1):
        compared = _comparison_neighbor(ordered, position - 1)
        ranked.append(
            decision.model_copy(
                update={
                    "rank": position,
                    "is_primary_next_review": position == 1,
                    "rank_trace": _rank_trace(value, decision, compared),
                }
            )
        )
    return [
        *ranked,
        *[
            decision.model_copy(
                update={
                    "rank_trace": RankTrace(
                        ranking_class="EXCLUDED",
                        factors=[],
                    )
                }
            )
            for _value, decision in sorted(excluded, key=lambda item: item[1].candidate_id)
        ],
    ]


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
    reasons: list[str] = list(value.conditional_reason_codes)
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
    # Area approval is intentionally not a review-status reason. A specific
    # address is decided by HQ and is projected as external verification work.
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
        _material_gap_count(value),
        _risk_count(value, RiskSeverity.HIGH),
        _founder_burden_order(value.founder_burden),
        value.candidate_id,
    )


def _material_gap_count(value: CandidateDecisionInput) -> int:
    return len(set(value.material_missing_fields) | set(value.finance.unknown_cost_fields))


def _rank_trace(
    value: CandidateDecisionInput,
    decision: CandidateDecision,
    compared: tuple[CandidateDecisionInput, CandidateDecision] | None,
) -> RankTrace:
    specs = (
        _RECOMMENDED_FACTORS
        if decision.review_status == ReviewStatus.REVIEW_RECOMMENDED
        else _CONDITIONAL_FACTORS
    )
    factors = [
        RankFactor(factor_code=code, value=extractor(value), direction="ASC")
        for code, extractor in specs
    ]
    decisive_factor_code: str | None = None
    compared_candidate_id: str | None = None
    tie_break_used = False
    if compared is not None:
        compared_value, compared_decision = compared
        compared_candidate_id = compared_value.candidate_id
        if compared_decision.review_status != decision.review_status:
            decisive_factor_code = "REVIEW_STATUS"
        else:
            decisive_factor_code = _first_different_factor(value, compared_value, specs)
            tie_break_used = decisive_factor_code is None
    return RankTrace(
        ranking_class=decision.review_status.value,
        factors=factors,
        decisive_factor_code=decisive_factor_code,
        compared_candidate_id=compared_candidate_id,
        tie_break_used=tie_break_used,
    )


def _comparison_neighbor(
    ordered: list[tuple[CandidateDecisionInput, CandidateDecision]],
    index: int,
) -> tuple[CandidateDecisionInput, CandidateDecision] | None:
    if len(ordered) < 2:
        return None
    if index == 0:
        return ordered[1]
    return ordered[index - 1]


def _first_different_factor(
    left: CandidateDecisionInput,
    right: CandidateDecisionInput,
    specs: tuple[RankFactorSpec, ...],
) -> str | None:
    for code, extractor in specs:
        if extractor(left) != extractor(right):
            return code
    return None


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
