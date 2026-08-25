import pytest
from pydantic import ValidationError

from app.candidates.models import (
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    RankBasis,
    ReviewStatus,
    RiskSeverity,
    RiskSignal,
)
from app.candidates.ranking import rank_candidates
from app.domain.models import CaseType, FranchiseEligibility
from app.finance.models import (
    CapitalGateResult,
    CapitalGateStatus,
    FinanceResult,
    MoneyRange,
)


def finance(
    *,
    initial_base: int = 40_000_000,
    monthly_base: int = 6_000_000,
    unknown_fields: list[str] | None = None,
) -> FinanceResult:
    unknown_fields = unknown_fields or []
    if unknown_fields:
        initial = MoneyRange(low=None, base=None, high=None)
        monthly = MoneyRange(low=None, base=None, high=None)
    else:
        initial = MoneyRange(
            low=initial_base - 5_000_000,
            base=initial_base,
            high=initial_base + 5_000_000,
        )
        monthly = MoneyRange(
            low=monthly_base - 500_000,
            base=monthly_base,
            high=monthly_base + 500_000,
        )
    return FinanceResult(
        initial_cash=initial,
        monthly_fixed_cost=monthly,
        break_even_monthly_sales_krw=None,
        required_daily_orders=None,
        unknown_cost_fields=unknown_fields,
    )


def capital(status: CapitalGateStatus = CapitalGateStatus.PASS) -> CapitalGateResult:
    return CapitalGateResult(status=status, reason_code=f"CAPITAL_{status.value}")


def independent(
    candidate_id: str,
    *,
    finance_result: FinanceResult | None = None,
    capital_status: CapitalGateStatus = CapitalGateStatus.PASS,
    founder_fit: FounderFitStatus = FounderFitStatus.PASS,
    founder_burden: FounderBurdenLevel = FounderBurdenLevel.MEDIUM,
    hard_codes: list[str] | None = None,
    missing_fields: list[str] | None = None,
    risks: list[RiskSignal] | None = None,
) -> CandidateDecisionInput:
    return CandidateDecisionInput(
        candidate_id=candidate_id,
        case_type=CaseType.INDEPENDENT,
        finance=finance_result or finance(),
        capital_gate=capital(capital_status),
        founder_fit=founder_fit,
        founder_burden=founder_burden,
        confirmed_hard_constraint_codes=hard_codes or [],
        material_missing_fields=missing_fields or [],
        risks=risks or [],
    )


def franchise(
    candidate_id: str,
    *,
    eligibility: FranchiseEligibility = FranchiseEligibility.VERIFIED,
    eligibility_evidence: list[str] | None = None,
    availability: FranchiseAvailability = FranchiseAvailability.AVAILABLE,
    finance_result: FinanceResult | None = None,
    missing_fields: list[str] | None = None,
) -> CandidateDecisionInput:
    return CandidateDecisionInput(
        candidate_id=candidate_id,
        case_type=CaseType.FRANCHISE,
        finance=finance_result or finance(),
        capital_gate=capital(),
        founder_fit=FounderFitStatus.PASS,
        founder_burden=FounderBurdenLevel.MEDIUM,
        material_missing_fields=missing_fields or [],
        franchise_eligibility=eligibility,
        franchise_eligibility_evidence_refs=(
            ["evidence-eligibility"] if eligibility_evidence is None else eligibility_evidence
        ),
        franchise_availability=availability,
    )


def test_recommended_candidates_rank_before_conditional_candidates() -> None:
    results = rank_candidates(
        [
            franchise(
                "brand-a",
                availability=FranchiseAvailability.HQ_CONFIRMATION_REQUIRED,
                finance_result=finance(unknown_fields=["royalty"]),
                missing_fields=["territory_availability"],
            ),
            independent("independent-a"),
        ]
    )

    assert [(result.candidate_id, result.rank) for result in results] == [
        ("independent-a", 1),
        ("brand-a", 2),
    ]
    assert results[0].review_status == ReviewStatus.REVIEW_RECOMMENDED
    assert results[0].rank_basis == RankBasis.ECONOMIC_AND_FOUNDER_FIT
    assert results[0].is_primary_next_review is True
    assert results[1].review_status == ReviewStatus.CONDITIONAL_REVIEW
    assert results[1].rank_basis == RankBasis.NEXT_REVIEW_PRIORITY
    assert set(results[1].reason_codes) >= {
        "MATERIAL_COST_UNKNOWN",
        "MATERIAL_FIELD_MISSING",
    }
    assert "FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED" not in results[1].reason_codes


@pytest.mark.parametrize(
    ("eligibility", "evidence", "reason"),
    [
        (FranchiseEligibility.UNVERIFIED, [], "FRANCHISE_ELIGIBILITY_UNVERIFIED"),
        (FranchiseEligibility.VERIFIED, [], "FRANCHISE_ELIGIBILITY_UNVERIFIED"),
        (FranchiseEligibility.INELIGIBLE, ["official"], "FRANCHISE_INELIGIBLE"),
    ],
)
def test_franchise_without_verified_supported_eligibility_is_never_ranked(
    eligibility: FranchiseEligibility,
    evidence: list[str],
    reason: str,
) -> None:
    result = rank_candidates(
        [franchise("brand-a", eligibility=eligibility, eligibility_evidence=evidence)]
    )[0]

    assert result.review_status == ReviewStatus.EXCLUDED
    assert result.rank is None
    assert result.rank_basis == RankBasis.NOT_RANKED
    assert result.is_primary_next_review is False
    assert reason in result.reason_codes


def test_confirmed_area_unavailability_excludes_franchise() -> None:
    result = rank_candidates(
        [franchise("brand-a", availability=FranchiseAvailability.UNAVAILABLE)]
    )[0]

    assert result.review_status == ReviewStatus.EXCLUDED
    assert "FRANCHISE_UNAVAILABLE_IN_AREA" in result.reason_codes


def test_confirmed_capital_failure_excludes_and_preserves_reason() -> None:
    result = rank_candidates(
        [independent("independent-a", capital_status=CapitalGateStatus.FAIL)]
    )[0]

    assert result.review_status == ReviewStatus.EXCLUDED
    assert result.rank is None
    assert result.reason_codes == ["CAPITAL_FAIL"]


def test_missing_information_is_conditional_not_excluded() -> None:
    result = rank_candidates(
        [
            independent(
                "independent-a",
                finance_result=finance(unknown_fields=["premium"]),
                capital_status=CapitalGateStatus.CONDITIONAL,
                founder_fit=FounderFitStatus.CONDITIONAL,
            )
        ]
    )[0]

    assert result.review_status == ReviewStatus.CONDITIONAL_REVIEW
    assert result.rank == 1
    assert result.rank_basis == RankBasis.NEXT_REVIEW_PRIORITY
    assert result.is_primary_next_review is True


def test_conditional_ranking_prefers_fewer_material_gaps() -> None:
    results = rank_candidates(
        [
            independent("two-gaps", missing_fields=["rent", "premium"]),
            independent("one-gap", missing_fields=["rent"]),
        ]
    )

    assert [(result.candidate_id, result.rank) for result in results] == [
        ("one-gap", 1),
        ("two-gaps", 2),
    ]


def test_recommended_ranking_is_stable_and_risk_adjusted() -> None:
    high_risk = RiskSignal(risk_id="risk-1", severity=RiskSeverity.HIGH)
    lower_cost_with_risk = independent(
        "lower-cost",
        finance_result=finance(initial_base=30_000_000, monthly_base=4_000_000),
        risks=[high_risk],
    )
    higher_cost_without_risk = independent(
        "higher-cost",
        finance_result=finance(initial_base=45_000_000, monthly_base=7_000_000),
    )

    forward = rank_candidates([lower_cost_with_risk, higher_cost_without_risk])
    reverse = rank_candidates([higher_cost_without_risk, lower_cost_with_risk])

    assert [result.candidate_id for result in forward] == [
        "higher-cost",
        "lower-cost",
    ]
    assert forward == reverse
    assert forward[0].rank_trace is not None
    assert [factor.factor_code for factor in forward[0].rank_trace.factors] == [
        "HIGH_RISK_COUNT",
        "FOUNDER_BURDEN",
        "MONTHLY_FIXED_COST_BASE_KRW",
        "INITIAL_CASH_BASE_KRW",
    ]
    assert forward[0].rank_trace.decisive_factor_code == "HIGH_RISK_COUNT"
    assert forward[0].rank_trace.compared_candidate_id == "lower-cost"
    assert forward[0].rank_trace.tie_break_used is False


def test_rank_trace_does_not_present_stable_id_as_user_decisive_factor() -> None:
    results = rank_candidates([independent("candidate-b"), independent("candidate-a")])

    assert [result.candidate_id for result in results] == ["candidate-a", "candidate-b"]
    assert results[0].rank_trace is not None
    assert results[0].rank_trace.decisive_factor_code is None
    assert results[0].rank_trace.tie_break_used is True


def test_critical_risk_requires_conditional_review() -> None:
    result = rank_candidates(
        [
            independent(
                "independent-a",
                risks=[RiskSignal(risk_id="risk-1", severity=RiskSeverity.CRITICAL)],
            )
        ]
    )[0]

    assert result.review_status == ReviewStatus.CONDITIONAL_REVIEW
    assert "CRITICAL_RISK_REQUIRES_REVIEW" in result.reason_codes


def test_duplicate_candidate_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        rank_candidates([independent("same"), independent("same")])


def test_independent_candidate_rejects_franchise_fields() -> None:
    with pytest.raises(ValidationError, match="franchise eligibility"):
        CandidateDecisionInput(
            candidate_id="invalid",
            case_type=CaseType.INDEPENDENT,
            finance=finance(),
            capital_gate=capital(),
            founder_fit=FounderFitStatus.PASS,
            founder_burden=FounderBurdenLevel.MEDIUM,
            franchise_eligibility=FranchiseEligibility.VERIFIED,
        )
