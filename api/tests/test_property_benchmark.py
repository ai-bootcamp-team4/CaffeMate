from app.candidates.seed_registry import CommercialPropertyClass, SpaceProfile
from app.finance.models import MoneyRange
from app.finance.property_benchmark import (
    PropertyRentBenchmark,
    derive_monthly_occupancy_benchmark,
)


def test_reb_effective_rent_is_converted_to_monthly_occupancy_estimate() -> None:
    benchmark = PropertyRentBenchmark(
        evidence_ref="reb-rent:seoul:small-retail:2026Q2",
        region_code="11",
        region_name="서울",
        property_class=CommercialPropertyClass.SMALL_RETAIL,
        period="2026Q2",
        effective_rent_krw_per_sqm_month=95_000,
        conversion_rate_bps=680,
        coverage_status="PARENT_REGION",
        floor_basis="FIRST_FLOOR",
        source_title="한국부동산원 상업용부동산 임대동향조사",
        source_ref="https://www.reb.or.kr/r-one/",
        data_date="2026-06-30",
    )

    result = derive_monthly_occupancy_benchmark(
        benchmark=benchmark,
        area_profile=SpaceProfile(low=25, base=30, high=35),
        deposit_range=MoneyRange(low=20_000_000, base=35_000_000, high=60_000_000),
        management_fee_ratio_bps=1_000,
    )

    # REB effective rent includes the opportunity cost of the deposit, so the
    # registered base deposit is converted back out before management is added.
    assert result.amount == MoneyRange(low=2_394_333, base=2_916_833, high=3_439_333)
    assert result.evidence_ref == benchmark.evidence_ref
    assert result.formula_code == "REB_EFFECTIVE_RENT_TO_MONTHLY_OCCUPANCY_V1"
    assert result.assumptions == {
        "area_sqm": {"low": 25, "base": 30, "high": 35},
        "deposit_base_krw": 35_000_000,
        "management_fee_ratio_bps": 1_000,
    }


def test_property_benchmark_requires_matching_registered_property_class() -> None:
    benchmark = PropertyRentBenchmark(
        evidence_ref="reb-rent:seoul:large-retail:2026Q2",
        region_code="11",
        region_name="서울",
        property_class=CommercialPropertyClass.MEDIUM_LARGE_RETAIL,
        period="2026Q2",
        effective_rent_krw_per_sqm_month=110_000,
        conversion_rate_bps=650,
        coverage_status="PARENT_REGION",
        floor_basis="FIRST_FLOOR",
        source_title="한국부동산원 상업용부동산 임대동향조사",
        source_ref="https://www.reb.or.kr/r-one/",
        data_date="2026-06-30",
    )

    assert benchmark.property_class == CommercialPropertyClass.MEDIUM_LARGE_RETAIL