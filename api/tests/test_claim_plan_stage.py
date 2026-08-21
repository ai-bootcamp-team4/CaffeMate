from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.errors import ContractValidationError
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
from app.workflows.claim_plan import ClaimPlanStageHandler
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext


def context(preference: CafeTypePreference) -> StageContext:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="stage-2",
            stage_code="CLAIM_PLAN",
            input_digest="a" * 64,
            lease_token="token",
            lease_expires_at=now + timedelta(seconds=45),
            attempt=1,
            head=HeadFence(
                workflow_generation=1,
                state_version=1,
                founder_snapshot_id="founder-1",
                area_snapshot_id="area-1",
                evidence_snapshot_id=None,
                policy_snapshot_id="policy-1",
                index_generation_id=None,
                seed_registry_id=None,
            ),
        ),
        project_id="project-1",
        state=VentureState(
            project_id="project-1",
            user_id="user-1",
            state_version=1,
            status=VentureStatus.ANALYZING,
            founder=FounderState(
                target_area_input="수원 아주대 부근",
                own_funds_krw=50_000_000,
                borrowing_intent=BorrowingIntent.UNDECIDED,
                cafe_type_preference=preference,
                operation_mode=OperationMode.DIRECT_FULL_TIME,
            ),
            area=AreaState(
                resolution_status=AreaResolutionStatus.UNRESOLVED,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
            ),
            updated_at=now,
        ),
        dependency_results={
            "AREA_RESOLUTION": {
                "area_resolution": {
                    "resolution_status": "RESOLVED",
                    "selected": {
                        "administrative_code": "4111756000",
                        "display_name": "원천동",
                        "boundary_version": "2026-01",
                        "match_kind": "EXACT",
                    },
                }
            }
        },
    )


@pytest.mark.parametrize(
    ("preference", "included", "excluded"),
    [
        (
            CafeTypePreference.OPEN_TO_BOTH,
            {
                "INDEPENDENT_STARTUP_COST_BENCHMARK",
                "FRANCHISE_UNIVERSE_ELIGIBILITY",
            },
            set(),
        ),
        (
            CafeTypePreference.INDEPENDENT_ONLY,
            {"INDEPENDENT_STARTUP_COST_BENCHMARK"},
            {"FRANCHISE_UNIVERSE_ELIGIBILITY"},
        ),
        (
            CafeTypePreference.FRANCHISE_ONLY,
            {"FRANCHISE_UNIVERSE_ELIGIBILITY"},
            {"INDEPENDENT_STARTUP_COST_BENCHMARK"},
        ),
    ],
)
def test_claim_plan_selects_branch_specific_claims(
    preference: CafeTypePreference,
    included: set[str],
    excluded: set[str],
) -> None:
    result = ClaimPlanStageHandler(today=lambda: date(2026, 8, 21)).execute(
        context(preference)
    )
    plan = result["claim_plan"]
    claim_types = {claim["claim_type"] for claim in plan["claims"]}

    assert included <= claim_types
    assert not (excluded & claim_types)
    assert {
        "AREA_PROFILE",
        "AREA_CAFE_COMPETITION",
        "AREA_BUSINESS_CHURN",
        "AREA_DEMAND_SIGNALS",
        "CAFE_OPENING_REQUIRED_PROCEDURES",
    } <= claim_types
    assert plan["planning_constraints"]["as_of"] == "2026-08-21"
    assert len(plan["action_id_pool"]) == 20


def test_area_claims_are_pinned_to_resolved_administrative_boundary() -> None:
    result = ClaimPlanStageHandler(today=lambda: date(2026, 8, 21)).execute(
        context(CafeTypePreference.OPEN_TO_BOTH)
    )
    area_claims = [
        claim
        for claim in result["claim_plan"]["claims"]
        if claim["claim_type"].startswith("AREA_")
    ]

    assert area_claims
    assert {
        (
            claim["geographic_scope"]["scope_id"],
            claim["geographic_scope"]["boundary_version"],
        )
        for claim in area_claims
    } == {("4111756000", "2026-01")}


def test_unresolved_area_is_rejected_before_any_claim_is_created() -> None:
    value = context(CafeTypePreference.OPEN_TO_BOTH)
    value.dependency_results["AREA_RESOLUTION"]["area_resolution"][
        "resolution_status"
    ] = "AMBIGUOUS"

    with pytest.raises(ContractValidationError, match="resolved area"):
        ClaimPlanStageHandler(today=lambda: date(2026, 8, 21)).execute(value)
