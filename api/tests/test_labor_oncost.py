from typing import Any, cast

from app.candidates.seed_registry import PaidStaffFteProfile
from app.finance.labor_benchmark import MinimumWageReference
from app.finance.labor_oncost import (
    EmployerInsuranceComponent,
    EmployerSocialInsuranceReference,
    derive_employer_oncost_floor,
    resolve_seed_employer_oncosts,
)
from app.finance.models import CostCategory, MoneyRange, ValueProvenance


def _minimum_wage() -> MinimumWageReference:
    return MinimumWageReference(
        evidence_ref="cost-reference:kr-minimum-wage-2026",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        hourly_rate_krw=10_320,
        monthly_equivalent_hours=209,
        monthly_equivalent_krw=2_156_880,
        source_title="최저임금위원회 연도별 최저임금",
        source_ref="https://www.minimumwage.go.kr/english/introduce/minWage.do",
        data_date="2025-08-05",
    )


def _insurance() -> EmployerSocialInsuranceReference:
    def component(name: str, rate_ppm: int, source_ref: str) -> EmployerInsuranceComponent:
        return EmployerInsuranceComponent(
            component=name,
            employer_rate_ppm=rate_ppm,
            evidence_ref=f"cost-reference:2026:{name.lower()}",
            source_title=f"official {name}",
            source_ref=source_ref,
            data_date="2026-01-01",
        )

    return EmployerSocialInsuranceReference(
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        workplace_employee_upper_bound=149,
        components=(
            component("NATIONAL_PENSION", 47_500, "https://www.nps.or.kr/"),
            component("HEALTH_LONG_TERM_CARE", 40_674, "https://www.nhis.or.kr/"),
            component("UNEMPLOYMENT_BENEFIT", 9_000, "https://www.moel.go.kr/"),
            component(
                "EMPLOYMENT_STABILIZATION_VOCATIONAL",
                2_500,
                "https://www.moel.go.kr/",
            ),
        ),
        unsupported_components=("WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED",),
        excluded_adjustments=(
            "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
            "EXEMPTIONS_NOT_APPLIED",
            "SUPPORT_PROGRAMS_NOT_APPLIED",
        ),
    )


def test_employer_oncost_is_separate_from_wages_and_uses_exact_2026_fixed_rates() -> None:
    derived = derive_employer_oncost_floor(
        minimum_wage=_minimum_wage(),
        social_insurance=_insurance(),
        paid_staff_fte=PaidStaffFteProfile(low=1, base=2, high=7),
    )

    assert derived.employer_rate_ppm == 99_674
    assert derived.payroll_floor == MoneyRange(
        low=2_156_880,
        base=4_313_760,
        high=15_098_160,
    )
    assert derived.amount == MoneyRange(
        low=214_985,
        base=429_970,
        high=1_504_894,
    )
    assert derived.formula_code == "EMPLOYER_SOCIAL_INSURANCE_FLOOR_V1"


def test_zero_paid_staff_fte_has_zero_employer_oncost_without_needing_a_rate() -> None:
    resolution = resolve_seed_employer_oncosts(
        seeds=[("owner-only-v1", PaidStaffFteProfile(low=0, base=0, high=0))],
        minimum_wage_references=[],
        social_insurance_references=[],
        as_of="2026-08-25",
    )

    line = resolution.overrides[0].as_cost_line()
    assert line.category == CostCategory.MONTHLY_EMPLOYER_ONCOST
    assert line.amount == MoneyRange(low=0, base=0, high=0)
    assert line.provenance == ValueProvenance.DERIVED


def test_missing_social_insurance_reference_is_unknown_not_zero() -> None:
    resolution = resolve_seed_employer_oncosts(
        seeds=[("staffed-v1", PaidStaffFteProfile(low=1, base=2, high=4))],
        minimum_wage_references=[_minimum_wage()],
        social_insurance_references=[],
        as_of="2026-08-25",
    )

    line = resolution.overrides[0].as_cost_line()
    assert line.category == CostCategory.MONTHLY_EMPLOYER_ONCOST
    assert line.amount == MoneyRange(low=None, base=None, high=None)
    assert line.provenance == ValueProvenance.UNKNOWN
    assert line.evidence_ref is None


def test_missing_minimum_wage_reference_is_unknown_not_zero() -> None:
    resolution = resolve_seed_employer_oncosts(
        seeds=[("staffed-v1", PaidStaffFteProfile(low=1, base=2, high=4))],
        minimum_wage_references=[],
        social_insurance_references=[_insurance()],
        as_of="2026-08-25",
    )

    line = resolution.overrides[0].as_cost_line()
    assert line.amount == MoneyRange(low=None, base=None, high=None)
    assert line.provenance == ValueProvenance.UNKNOWN
    assert line.evidence_ref is None


def test_workers_compensation_is_explicitly_excluded_from_the_grounded_rate() -> None:
    resolution = resolve_seed_employer_oncosts(
        seeds=[("staffed-v1", PaidStaffFteProfile(low=1, base=2, high=4))],
        minimum_wage_references=[_minimum_wage()],
        social_insurance_references=[_insurance()],
        as_of="2026-08-25",
    )

    line = resolution.overrides[0].as_cost_line()
    assert line.provenance == ValueProvenance.BENCHMARK
    assert line.evidence_ref is not None
    source = resolution.sources[line.evidence_ref]
    derivation = cast(dict[str, Any], source["derivation"])
    inputs = cast(dict[str, Any], derivation["inputs"])
    assert inputs["employer_rate_ppm"] == 99_674
    assert inputs["employer_rate_bps_decimal"] == "996.74"
    assert inputs["payroll_basis_exclusions"] == [
        "FOUNDER_AND_SELF_LABOR_EXCLUDED"
    ]
    assert inputs["unsupported_components"] == [
        "WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED"
    ]
    assert set(inputs["excluded_adjustments"]) == {
        "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
        "EXEMPTIONS_NOT_APPLIED",
        "SUPPORT_PROGRAMS_NOT_APPLIED",
    }
