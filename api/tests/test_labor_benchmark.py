from app.candidates.seed_registry import PaidStaffFteProfile
from app.finance.labor_benchmark import (
    MinimumWageReference,
    derive_monthly_labor_floor,
    resolve_seed_labor_benchmarks,
)
from app.finance.models import CostCategory, MoneyRange, ValueProvenance


def _reference() -> MinimumWageReference:
    return MinimumWageReference(
        evidence_ref="minimum-wage:2026",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        hourly_rate_krw=10_320,
        monthly_equivalent_hours=209,
        monthly_equivalent_krw=2_156_880,
        source_title="최저임금위원회 연도별 최저임금",
        source_ref="https://www.minimumwage.go.kr/minWage/policy/decisionMain.do",
        data_date="2025-08-05",
    )


def test_minimum_wage_raises_only_seed_labor_points_below_the_legal_floor() -> None:
    derived = derive_monthly_labor_floor(
        reference=_reference(),
        paid_staff_fte=PaidStaffFteProfile(low=1, base=2, high=7),
        seed_labor=MoneyRange(low=1_000_000, base=5_000_000, high=15_000_000),
    )

    assert derived.amount == MoneyRange(
        low=2_156_880,
        base=5_000_000,
        high=15_098_160,
    )
    assert derived.legal_floor == MoneyRange(
        low=2_156_880,
        base=4_313_760,
        high=15_098_160,
    )
    assert derived.formula_code == "MINIMUM_WAGE_FTE_FLOOR_V1"


def test_minimum_wage_does_not_lower_an_existing_higher_labor_assumption() -> None:
    derived = derive_monthly_labor_floor(
        reference=_reference(),
        paid_staff_fte=PaidStaffFteProfile(low=1, base=2, high=4),
        seed_labor=MoneyRange(low=3_000_000, base=6_000_000, high=10_000_000),
    )

    assert derived.amount == MoneyRange(
        low=3_000_000,
        base=6_000_000,
        high=10_000_000,
    )


def test_zero_paid_staff_fte_does_not_create_a_labor_benchmark_override() -> None:
    resolution = resolve_seed_labor_benchmarks(
        seeds=[
            (
                "owner-operated-v1",
                PaidStaffFteProfile(low=0, base=0, high=0),
                MoneyRange(low=0, base=0, high=0),
            )
        ],
        references=[_reference()],
        as_of="2026-08-25",
    )

    assert resolution.overrides == ()
    assert resolution.sources == {}


def test_labor_benchmark_is_a_monthly_labor_benchmark_not_actual_payroll() -> None:
    resolution = resolve_seed_labor_benchmarks(
        seeds=[
            (
                "staffed-v1",
                PaidStaffFteProfile(low=1, base=2, high=7),
                MoneyRange(low=1_000_000, base=5_000_000, high=15_000_000),
            )
        ],
        references=[_reference()],
        as_of="2026-08-25",
    )

    assert len(resolution.overrides) == 1
    line = resolution.overrides[0].as_cost_line()
    assert line.category == CostCategory.MONTHLY_LABOR
    assert line.provenance == ValueProvenance.BENCHMARK
    assert line.evidence_ref == "minimum-wage:2026:derived:staffed-v1"
    source = resolution.sources[line.evidence_ref]
    assert source["geographic_scope"] == {
        "scope_type": "NATIONAL",
        "scope_id": "KR",
        "boundary_version": None,
    }
    assert source["derivation"]["inputs"]["monthly_equivalent_krw"] == 2_156_880
    assert source["derivation"]["inputs"]["paid_staff_fte"] == {
        "low": 1.0,
        "base": 2.0,
        "high": 7.0,
    }