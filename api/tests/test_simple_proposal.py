"""사용자는 입력 선호에 맞는 최대 세 가지 후보를 즉시 비교할 수 있어야 한다."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.candidates.seed_registry import CommercialPropertyClass, IndependentSeedRegistry
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    BorrowingIntent,
    CafeTypePreference,
    CoverageProfile,
    FounderState,
    OperationMode,
    VentureState,
    VentureStatus,
)
from app.finance.case_facts import CaseFactRecord, CaseFactResolver, PropertyContext
from app.finance.labor_benchmark import MinimumWageReference
from app.finance.labor_oncost import (
    EmployerInsuranceComponent,
    EmployerSocialInsuranceReference,
    replay_employer_oncost_minimum_wage_references,
    replay_employer_social_insurance_references,
)
from app.finance.property_benchmark import PropertyRentBenchmark
from app.results.models import ResultOutcomeStatus
from app.workflows.simple_proposal import SimpleProposalBuilder


def _franchise_universe() -> list[dict[str, object]]:
    """Verified franchise inputs are explicit; the builder must not invent a brand."""

    return [
        {
            "brand_id": "kr-ediya-coffee",
            "display_name": "이디야커피",
            "individual_franchise_eligibility": "VERIFIED",
            "evidence_refs": ["franchise-eligibility:ediya"],
            "finance_profile": {
                "currency": "KRW",
                "coverage": "PARTIAL",
                "value_kind": "EVIDENCED_FACT",
                "known_initial_cost_range_krw": {
                    "low": 27_000_000,
                    "base": 27_000_000,
                    "high": 27_000_000,
                },
                "reference_area_sqm": None,
                "monthly_royalty_krw": 250_000,
                "sales_royalty_bps": None,
                "evidence_refs": ["franchise-cost:ediya"],
                "source_refs": ["https://example.com/ediya"],
                "scope_note": "unit test fixture",
                "missing_costs": [
                    "DEPOSIT",
                    "ACQUISITION_OR_PREMIUM",
                    "CONSTRUCTION",
                    "EQUIPMENT",
                    "OPERATING_RESERVE",
                ],
            },
        }
    ]


def _state(preference: CafeTypePreference) -> VentureState:
    return VentureState(
        project_id="project-1",
        user_id="user-1",
        state_version=1,
        status=VentureStatus.ANALYZING,
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=preference,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
        area=AreaState(
            resolution_status=AreaResolutionStatus.RESOLVED,
            area_id="area-1",
            coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
        ),
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
    )




def _minimum_wage_reference() -> MinimumWageReference:
    return MinimumWageReference(
        evidence_ref="cost-reference:kr-minimum-wage-2026",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        hourly_rate_krw=10_320,
        monthly_equivalent_hours=209,
        monthly_equivalent_krw=2_156_880,
        source_title="최저임금위원회 연도별 최저임금",
        source_ref="https://www.minimumwage.go.kr/minWage/policy/decisionMain.do",
        data_date="2025-08-05",
    )


def _social_insurance_reference() -> EmployerSocialInsuranceReference:
    components = (
        ("NATIONAL_PENSION", 47_500, "https://www.nps.or.kr/"),
        ("HEALTH_LONG_TERM_CARE", 40_674, "https://www.nhis.or.kr/"),
        ("UNEMPLOYMENT_BENEFIT", 9_000, "https://www.moel.go.kr/"),
        ("EMPLOYMENT_STABILIZATION_VOCATIONAL", 2_500, "https://www.moel.go.kr/"),
    )
    return EmployerSocialInsuranceReference(
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        workplace_employee_upper_bound=149,
        components=tuple(
            EmployerInsuranceComponent(
                component=name,
                employer_rate_ppm=rate,
                evidence_ref=f"cost-reference:2026:{name.lower()}",
                source_title=f"official {name}",
                source_ref=source_ref,
                data_date="2026-01-01",
            )
            for name, rate, source_ref in components
        ),
        unsupported_components=("WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED",),
        excluded_adjustments=(
            "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
            "EXEMPTIONS_NOT_APPLIED",
            "SUPPORT_PROGRAMS_NOT_APPLIED",
        ),
    )


def _seoul_small_retail_benchmark() -> PropertyRentBenchmark:
    return PropertyRentBenchmark(
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


@pytest.mark.parametrize(
    ("preference", "allowed_types"),
    [
        (CafeTypePreference.INDEPENDENT_ONLY, {"INDEPENDENT"}),
        (CafeTypePreference.FRANCHISE_ONLY, {"FRANCHISE"}),
        (
            CafeTypePreference.OPEN_TO_BOTH,
            {"INDEPENDENT", "FRANCHISE"},
        ),
    ],
)
def test_builder_returns_ranked_candidates_for_selected_path(
    preference: CafeTypePreference,
    allowed_types: set[str],
) -> None:
    result = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(preference),
        evidence_records=[],
        franchise_universe=_franchise_universe(),
    )

    assert result.outcome_status == ResultOutcomeStatus.REVIEWABLE_CANDIDATES
    assert 1 <= len(result.candidates) <= 3
    assert {candidate["case_type"] for candidate in result.candidates} == allowed_types
    assert [candidate["rank"] for candidate in result.candidates] == list(
        range(1, len(result.candidates) + 1)
    )
    assert result.primary_candidate_id == result.candidates[0]["candidate_id"]
    assert all(candidate["gate_results"] for candidate in result.candidates)
    assert all(candidate["rank_trace"] for candidate in result.candidates)
    assert all(candidate["decision_inputs"] for candidate in result.candidates)
    assert all(
        all(
            signal["decision_role"] == "CONTEXT_ONLY"
            for signal in candidate["market_signals"]
        )
        for candidate in result.candidates
    )


def test_capital_fail_exposes_authoritative_public_blocker_and_shortfall() -> None:
    base_state = _state(CafeTypePreference.INDEPENDENT_ONLY)
    state = base_state.model_copy(
        update={
            "founder": base_state.founder.model_copy(
                update={"own_funds_krw": 10_000_000}
            )
        }
    )

    result = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=state,
        evidence_records=[],
    )

    assert result.outcome_status == ResultOutcomeStatus.NO_REVIEWABLE_CANDIDATES
    candidate = result.candidates[0]
    gate = candidate["gate_results"][0]
    assert candidate["review_status"] == "EXCLUDED"
    assert gate["status"] == "FAIL"
    assert gate["reason_code"] == "MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS"
    assert gate["metrics"]["own_funds_krw"] == 10_000_000
    assert gate["metrics"]["minimum_required_krw"] == candidate["financial_summary"][
        "initial_cash"
    ]["low"]
    assert gate["metrics"]["shortfall_krw"] == (
        gate["metrics"]["minimum_required_krw"] - 10_000_000
    )


def test_builder_keeps_registered_assumptions_distinct_from_evidence() -> None:
    result = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.INDEPENDENT_ONLY),
        evidence_records=[],
    )

    assert all(candidate["evidence_refs"] == [] for candidate in result.candidates)
    assert all(candidate["assumption_refs"] for candidate in result.candidates)


def test_registry_exposes_four_general_models_and_activates_conditional_interest() -> None:
    """사용자는 기본 네 유형과 명시적으로 원하는 조건부 유형만 추천받는다."""

    registry = IndependentSeedRegistry.load_default()
    founder = _state(CafeTypePreference.INDEPENDENT_ONLY).founder

    assert [model.model_id for model in registry.select(founder)] == [
        "independent-small-takeout-v1",
        "independent-balanced-v1",
        "independent-seating-focused-v1",
        "independent-specialty-v1",
    ]

    dessert_founder = founder.model_copy(
        update={"preferences": ["직접 만든 디저트와 베이커리 중심"]}
    )
    dessert_ids = [model.model_id for model in registry.select(dessert_founder)]
    assert dessert_ids[0] == "independent-dessert-bakery-v1"
    assert "independent-destination-experience-v1" not in dessert_ids


def test_registry_prioritizes_the_general_model_matching_founder_preference() -> None:
    """명시한 운영 방향은 비용 순서보다 먼저 Proposal Agent 입력에 반영한다."""

    registry = IndependentSeedRegistry.load_default()
    founder = _state(CafeTypePreference.INDEPENDENT_ONLY).founder.model_copy(
        update={"preferences": ["원두와 핸드드립 중심의 스페셜티 카페"]}
    )

    assert registry.select(founder)[0].model_id == "independent-specialty-v1"


def test_builder_preserves_matching_independent_agent_advice() -> None:
    registry = IndependentSeedRegistry.load_default()
    seed = registry.select(_state(CafeTypePreference.INDEPENDENT_ONLY).founder)[0]
    support_ref = seed.support_refs[0]
    assessments = [
        {
            "axis": axis,
            "signal": "UNKNOWN",
            "summary": f"{axis} 검토 결과입니다.",
            "input_field_refs": [],
            "claim_refs": [],
            "evidence_refs": [],
            "assumption_refs": [support_ref],
            "missing_context": ["실제 점포 자료"],
        }
        for axis in (
            "CAPITAL_FIT",
            "OPERATING_FIT",
            "USER_PREFERENCE_FIT",
            "AREA_FIT",
            "EVIDENCE_COMPLETENESS",
        )
    ]

    result = SimpleProposalBuilder(registry).build(
        state=_state(CafeTypePreference.INDEPENDENT_ONLY),
        evidence_records=[],
        agent_proposals=[
            {
                "proposal_id": f"proposal:{seed.model_id}",
                "case_type": "INDEPENDENT",
                "display_name": seed.display_name,
                "seed_or_brand_id": seed.model_id,
                "adjusted_parameters": [
                    {
                        "field_path": "operations.seats",
                        "value": {"kind": "INTEGER", "value": 6},
                        "unit": "seat",
                        "support_refs": [support_ref],
                    }
                ],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": seed.support_refs,
                "fit_assessments": assessments,
                "missing_fields": [],
                "warnings": [],
            }
        ],
    )

    assert len(result.candidates) == 1
    assert result.candidates[0]["agent_advisory"] == {
        "fit_assessments": assessments,
        "adjusted_parameters": [
            {
                "field_path": "operations.seats",
                "value": {"kind": "INTEGER", "value": 6},
                "unit": "seat",
                "support_refs": [support_ref],
            }
        ],
        "missing_fields": [],
        "warnings": [],
    }
    assert result.candidates[0]["independent_model"]["adjusted_fields"] == [
        "operations.seats"
    ]


def test_builder_replaces_selected_model_property_costs_with_user_input() -> None:
    builder = SimpleProposalBuilder(IndependentSeedRegistry.load_default())
    state = _state(CafeTypePreference.INDEPENDENT_ONLY)
    original = builder.build(
        state=state,
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[_social_insurance_reference()],
    )

    recalculated = builder.build(
        state=state,
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[_social_insurance_reference()],
        property_context=PropertyContext(
            property_input_id="property-input-1",
            source_id="independent-small-takeout-v1",
            address="서울특별시 마포구 공덕동 실제 점포",
            area_sqm=33,
            floor="1층",
            deposit_krw=30_000_000,
            monthly_rent_krw=2_200_000,
            management_fee_krw=200_000,
            key_money_krw=10_000_000,
        ),
    )

    original_candidate = next(
        candidate
        for candidate in original.candidates
        if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    recalculated_candidate = next(
        candidate
        for candidate in recalculated.candidates
        if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    assert original_candidate["financial_summary"]["initial_cash"]["base"] == 139_500_000
    assert recalculated_candidate["financial_summary"]["initial_cash"]["base"] == 134_500_000
    assert recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_107_493
    assert (
        "property-input:property-input-1"
        in recalculated_candidate["financial_summary"]["initial_cash"]["provenance_refs"]
    )
    resolved = {item["field"]: item for item in recalculated_candidate["decision_inputs"]}
    assert resolved["DEPOSIT"]["resolution_status"] == "RESOLVED_USER_CONFIRMED"
    assert resolved["DEPOSIT"]["resolution_action"]["action_type"] == "NONE"
    assert resolved["MONTHLY_OCCUPANCY"]["resolution_status"] == (
        "RESOLVED_USER_CONFIRMED"
    )
    assert resolved["MONTHLY_OCCUPANCY"]["resolution_action"]["action_type"] == "NONE"


def test_builder_uses_regional_rent_benchmark_before_seed_occupancy_assumption() -> None:
    builder = SimpleProposalBuilder(IndependentSeedRegistry.load_default())
    state = _state(CafeTypePreference.INDEPENDENT_ONLY)

    result = builder.build(
        state=state,
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[_social_insurance_reference()],
        property_rent_benchmarks=[_seoul_small_retail_benchmark()],
    )

    candidate = next(
        value
        for value in result.candidates
        if value["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    occupancy = next(
        value for value in candidate["decision_inputs"] if value["field"] == "MONTHLY_OCCUPANCY"
    )
    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_624_326
    assert occupancy["value_range_krw"]["base"] == 2_916_833
    assert occupancy["provenance"] == "BENCHMARK"
    assert occupancy["resolution_status"] == "RESOLVED_BENCHMARK"
    assert occupancy["source_title"] == "한국부동산원 상업용부동산 임대동향조사"
    assert occupancy["geographic_scope"]["scope_id"] == "11"
    assert occupancy["derivation"] == {
        "formula_code": "REB_EFFECTIVE_RENT_TO_MONTHLY_OCCUPANCY_V1",
        "inputs": {
            "effective_rent_krw_per_sqm_month": 95_000,
            "conversion_rate_bps": 680,
            "area_sqm": {"low": 20, "base": 30, "high": 40},
            "deposit_base_krw": 35_000_000,
            "management_fee_ratio_bps": 1_000,
        },
        "coverage_status": "PARENT_REGION",
        "floor_basis": "FIRST_FLOOR",
    }


def test_actual_property_terms_replace_regional_rent_benchmark() -> None:
    builder = SimpleProposalBuilder(IndependentSeedRegistry.load_default())
    state = _state(CafeTypePreference.INDEPENDENT_ONLY)

    result = builder.build(
        state=state,
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[_social_insurance_reference()],
        property_rent_benchmarks=[_seoul_small_retail_benchmark()],
        property_context=PropertyContext(
            property_input_id="actual-store",
            source_id="independent-small-takeout-v1",
            address="서울특별시 마포구 공덕동 실제 점포",
            area_sqm=33,
            floor="1층",
            deposit_krw=30_000_000,
            monthly_rent_krw=2_200_000,
            management_fee_krw=200_000,
            key_money_krw=10_000_000,
        ),
    )

    candidate = next(
        value
        for value in result.candidates
        if value["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    occupancy = next(
        value for value in candidate["decision_inputs"] if value["field"] == "MONTHLY_OCCUPANCY"
    )
    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_107_493
    assert occupancy["value_range_krw"]["base"] == 2_400_000
    assert occupancy["provenance"] == "USER_INPUT"


def test_builder_replaces_selected_franchise_property_costs_with_user_input() -> None:
    builder = SimpleProposalBuilder(IndependentSeedRegistry.load_default())
    state = _state(CafeTypePreference.FRANCHISE_ONLY)
    original = builder.build(
        state=state,
        evidence_records=[],
        franchise_universe=_franchise_universe(),
    )

    recalculated = builder.build(
        state=state,
        evidence_records=[],
        property_context=PropertyContext(
            property_input_id="property-input-2",
            source_id="kr-ediya-coffee",
            address="서울특별시 성동구 성수동1가 실제 점포",
            area_sqm=66,
            floor="1층",
            deposit_krw=30_000_000,
            monthly_rent_krw=2_200_000,
            management_fee_krw=200_000,
            key_money_krw=10_000_000,
        ),
        franchise_universe=_franchise_universe(),
    )

    original_candidate = original.candidates[0]
    recalculated_candidate = recalculated.candidates[0]
    assert original_candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
    assert original_candidate["financial_summary"]["initial_cash"]["base"] == 152_000_000
    assert recalculated_candidate["financial_summary"]["initial_cash"]["base"] == 147_000_000
    assert recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_250_000
    assert (
        "property-input:property-input-2"
        in recalculated_candidate["financial_summary"]["initial_cash"]["provenance_refs"]
    )
    assert (
        "property-input:property-input-2"
        in recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["provenance_refs"]
    )


def test_sales_percentage_royalty_changes_break_even_without_changing_monthly_fixed_cost() -> None:
    universe = deepcopy(_franchise_universe())
    profile = universe[0]["finance_profile"]
    assert isinstance(profile, dict)
    profile["monthly_royalty_krw"] = None
    profile["sales_royalty_bps"] = 300
    profile["missing_costs"] = [
        value for value in profile["missing_costs"] if value != "ROYALTY"
    ]

    candidate = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.FRANCHISE_ONLY),
        evidence_records=[],
        franchise_universe=universe,
    ).candidates[0]

    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 7_600_000
    assert candidate["financial_summary"]["base_contribution_margin_bps"] == 6_500
    assert candidate["financial_summary"]["variable_cost_rate_bps"] == 300
    assert candidate["financial_summary"]["effective_contribution_margin_bps"] == 6_200
    assert candidate["financial_summary"]["break_even_monthly_sales_krw"] == 12_258_065
    royalty = next(
        value for value in candidate["decision_inputs"] if value["field"] == "SALES_ROYALTY"
    )
    assert royalty["value_bps"] == 300
    assert royalty["value_range_krw"] is None
    assert royalty["provenance"] == "FACT"
    assert royalty["applied_to"] == [
        "EFFECTIVE_CONTRIBUTION_MARGIN",
        "BREAK_EVEN_MONTHLY_SALES",
        "REQUIRED_DAILY_ORDERS",
    ]


def test_fixed_monthly_royalty_remains_fixed_cost_not_variable_margin_rate() -> None:
    candidate = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.FRANCHISE_ONLY),
        evidence_records=[],
        franchise_universe=_franchise_universe(),
    ).candidates[0]

    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 7_850_000
    assert candidate["financial_summary"]["base_contribution_margin_bps"] == 6_500
    assert candidate["financial_summary"]["variable_cost_rate_bps"] == 0
    assert candidate["financial_summary"]["effective_contribution_margin_bps"] == 6_500
    assert not any(
        value["field"] == "SALES_ROYALTY" for value in candidate["decision_inputs"]
    )


def test_confirmed_percentage_royalty_materializes_without_profile_variable_rate() -> None:
    universe = deepcopy(_franchise_universe())
    profile = universe[0]["finance_profile"]
    assert isinstance(profile, dict)
    profile["monthly_royalty_krw"] = None
    profile["sales_royalty_bps"] = None
    profile["missing_costs"] = [
        value for value in profile["missing_costs"] if value != "ROYALTY"
    ]
    resolution = CaseFactResolver().resolve(
        records=[
            CaseFactRecord(
                claim_id="royalty-contract-3pct",
                source_id="kr-ediya-coffee",
                claim_type="ROYALTY",
                value=3.0,
                unit="%",
                materiality="HIGH",
                document_type="FRANCHISE_AGREEMENT",
                document_id="franchise-agreement-1",
                document_revision_id="franchise-agreement-r1",
                original_filename="franchise-agreement.pdf",
                anchor={"document_revision_id": "franchise-agreement-r1", "page_index": 4},
                created_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
            )
        ],
        open_conflict_keys=set(),
    )

    candidate = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.FRANCHISE_ONLY),
        evidence_records=[],
        case_fact_resolution=resolution,
        franchise_universe=universe,
    ).candidates[0]

    assert candidate["financial_summary"]["variable_cost_rate_bps"] == 300
    assert candidate["financial_summary"]["effective_contribution_margin_bps"] == 6_200
    royalty = next(
        value for value in candidate["decision_inputs"] if value["field"] == "SALES_ROYALTY"
    )
    assert royalty["value_bps"] == 300
    assert royalty["provenance"] == "USER_INPUT"
    assert royalty["source_title"] == "franchise-agreement.pdf"
    assert royalty["source_anchor"].startswith("franchise-agreement-r1#page=5")


def test_franchise_hq_confirmation_is_external_not_generic_missing_input() -> None:
    result = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.FRANCHISE_ONLY),
        evidence_records=[],
        franchise_universe=_franchise_universe(),
    )

    candidate = result.candidates[0]
    assert all(
        missing["field"] != "지역 출점 가능 여부"
        for missing in candidate["missing_fields"]
    )
    assert candidate["verification_requirements"] == [
        {
            "requirement_id": "FRANCHISE_AREA_APPROVAL",
            "status": "EXTERNAL_CONFIRMATION_REQUIRED",
            "decision_role": "VERIFICATION_ONLY",
            "resolver": "FRANCHISE_HQ",
            "reason_code": "FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED",
            "required_evidence": ["DATED_HQ_WRITTEN_CONFIRMATION"],
            "resolution_action": {
                "action_type": "EXTERNAL_CONFIRMATION",
                "target_fields": ["franchise.area_availability"],
                "accepted_document_types": [],
            },
            "why_caffemate_cannot_resolve": (
                "특정 후보 주소의 출점 승인 여부는 해당 프랜차이즈 본사가 결정합니다."
            ),
        }
    ]


def test_builder_adds_employer_oncost_separately_from_monthly_labor() -> None:
    builder = SimpleProposalBuilder(IndependentSeedRegistry.load_default())
    grounded = builder.build(
        state=_state(CafeTypePreference.INDEPENDENT_ONLY),
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[_social_insurance_reference()],
    )

    def seating(bundle: Any) -> dict[str, Any]:
        return next(
            candidate
            for candidate in bundle.candidates
            if candidate["independent_model"]["model_id"] == "independent-seating-focused-v1"
        )

    after = seating(grounded)
    after_labor = next(
        value for value in after["decision_inputs"] if value["field"] == "MONTHLY_LABOR"
    )
    oncost = next(
        value
        for value in after["decision_inputs"]
        if value["field"] == "MONTHLY_EMPLOYER_ONCOST"
    )

    assert after_labor["value_range_krw"] == {
        "low": 5_000_000,
        "base": 9_000_000,
        "high": 15_098_160,
    }
    assert oncost["value_range_krw"] == {
        "low": 429_970,
        "base": 859_940,
        "high": 1_504_894,
    }
    assert oncost["provenance"] == "BENCHMARK"
    assert oncost["resolution_status"] == "RESOLVED_BENCHMARK"
    assert oncost["limitation_code"] == (
        "OFFICIAL_EMPLOYER_ONCOST_FLOOR_EXCLUDES_WORKERS_COMP_AND_ADJUSTMENTS"
    )
    assert oncost["derivation"]["inputs"]["employer_rate_ppm"] == 99_674
    assert oncost["derivation"]["inputs"]["employer_rate_bps_decimal"] == "996.74"
    assert oncost["derivation"]["inputs"]["payroll_basis_exclusions"] == [
        "FOUNDER_AND_SELF_LABOR_EXCLUDED"
    ]
    assert set(oncost["derivation"]["inputs"]["excluded_adjustments"]) == {
        "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
        "EXEMPTIONS_NOT_APPLIED",
        "SUPPORT_PROGRAMS_NOT_APPLIED",
    }
    monthly_base_without_oncost = sum(
        value["value_range_krw"]["base"]
        for value in after["decision_inputs"]
        if value["decision_role"] == "FINANCE_INPUT"
        and value["value_range_krw"] is not None
        and value["field"] != "MONTHLY_EMPLOYER_ONCOST"
        and "MONTHLY_FIXED_COST" in value["applied_to"]
    )
    assert after["financial_summary"]["monthly_fixed_cost"]["base"] == (
        monthly_base_without_oncost + 859_940
    )
    assert after["financial_summary"]["break_even_monthly_sales_krw"] == 34_451_517

    balanced = next(
        candidate
        for candidate in grounded.candidates
        if candidate["independent_model"]["model_id"] == "independent-balanced-v1"
    )
    assert next(
        value
        for value in balanced["decision_inputs"]
        if value["field"] == "MONTHLY_LABOR"
    )["provenance"] == "ASSUMPTION"
    replay_bundle = {"candidates": [balanced]}
    replayed_wages = replay_employer_oncost_minimum_wage_references(replay_bundle)
    replayed_schedules = replay_employer_social_insurance_references(replay_bundle)
    assert len(replayed_wages) == 1
    assert replayed_wages[0].monthly_equivalent_krw == 2_156_880
    assert len(replayed_schedules) == 1
    assert replayed_schedules[0].employer_rate_ppm == 99_674


def test_builder_fails_closed_when_employer_oncost_schedule_is_missing() -> None:
    bundle = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.INDEPENDENT_ONLY),
        evidence_records=[],
        minimum_wage_references=[_minimum_wage_reference()],
        employer_social_insurance_references=[],
    )
    candidate = next(
        value
        for value in bundle.candidates
        if value["independent_model"]["model_id"] == "independent-balanced-v1"
    )
    oncost = next(
        value
        for value in candidate["decision_inputs"]
        if value["field"] == "MONTHLY_EMPLOYER_ONCOST"
    )

    assert oncost["provenance"] == "UNKNOWN"
    assert oncost["resolution_status"] == "INPUT_REQUIRED"
    assert oncost["value_range_krw"] == {"low": None, "base": None, "high": None}
    assert candidate["financial_summary"]["monthly_fixed_cost"]["low"] is None
    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] is None
    assert candidate["financial_summary"]["monthly_fixed_cost"]["high"] is None
    assert candidate["financial_summary"]["break_even_monthly_sales_krw"] is None
    assert "MONTHLY_EMPLOYER_ONCOST" in candidate["financial_summary"][
        "unknown_cost_fields"
    ]
