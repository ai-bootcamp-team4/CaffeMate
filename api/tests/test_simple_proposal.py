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
    original = builder.build(state=state, evidence_records=[])

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
    )

    original_candidate = original.candidates[0]
    recalculated_candidate = recalculated.candidates[0]
    assert original_candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
    assert original_candidate["financial_summary"]["initial_cash"]["base"] == 220_321_000
    assert recalculated_candidate["financial_summary"]["initial_cash"]["base"] == 210_321_000
    assert recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 12_600_000
    assert (
        "property-input:property-input-2"
        in recalculated_candidate["financial_summary"]["initial_cash"]["provenance_refs"]
    )
    assert (
        "property-input:property-input-2"
        in recalculated_candidate["financial_summary"]["monthly_fixed_cost"]["provenance_refs"]
    )
