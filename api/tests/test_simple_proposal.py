"""사용자는 입력 선호에 맞는 최대 세 가지 후보를 즉시 비교할 수 있어야 한다."""

from datetime import UTC, datetime

import pytest

from app.candidates.seed_registry import IndependentSeedRegistry
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
from app.results.models import ResultOutcomeStatus
from app.workflows.simple_proposal import PropertyCostOverride, SimpleProposalBuilder


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


def test_builder_keeps_registered_assumptions_distinct_from_evidence() -> None:
    result = SimpleProposalBuilder(IndependentSeedRegistry.load_default()).build(
        state=_state(CafeTypePreference.INDEPENDENT_ONLY),
        evidence_records=[],
    )

    assert all(candidate["evidence_refs"] == [] for candidate in result.candidates)
    assert all(candidate["assumption_refs"] for candidate in result.candidates)


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
    original = builder.build(state=state, evidence_records=[])

    recalculated = builder.build(
        state=state,
        evidence_records=[],
        property_cost_override=PropertyCostOverride(
            property_input_id="property-input-1",
            source_id="independent-small-takeout-v1",
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
    assert recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_000_000
    assert (
        "property-input:property-input-1"
        in recalculated_candidate["financial_summary"]["initial_cash"]["provenance_refs"]
    )


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
        property_cost_override=PropertyCostOverride(
            property_input_id="property-input-2",
            source_id="kr-ediya-coffee",
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
