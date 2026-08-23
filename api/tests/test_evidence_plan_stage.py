from datetime import UTC, date, datetime, timedelta
from typing import Any

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
from app.workflows.evidence_plan import PLANNER_VERSION, EvidencePlanStageHandler
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext


def stage_context(
    preference: CafeTypePreference = CafeTypePreference.OPEN_TO_BOTH,
) -> StageContext:
    now = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    head = HeadFence(
        workflow_generation=1,
        state_version=1,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id=None,
        policy_snapshot_id="policy-1",
        index_generation_id=None,
        seed_registry_id=None,
    )
    state = VentureState(
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
            resolution_status=AreaResolutionStatus.RESOLVED,
            administrative_code="4111756000",
            display_name="경기도 수원시 영통구 원천동",
            boundary_version="2026-01",
            coverage_profile=CoverageProfile.N1_NATIONWIDE_CONDITIONAL,
        ),
        updated_at=now,
    )
    claim_context = StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="claim-plan-1",
            stage_code="CLAIM_PLAN",
            input_digest="a" * 64,
            lease_token="lease-token",
            lease_expires_at=now + timedelta(seconds=90),
            attempt=1,
            head=head,
        ),
        project_id="project-1",
        state=state,
        dependency_results={
            "AREA_RESOLUTION": {
                "area_resolution": {
                    "resolution_status": "RESOLVED",
                    "selected": {
                        "administrative_code": "4111756000",
                        "display_name": "경기도 수원시 영통구 원천동",
                        "boundary_version": "2026-01",
                        "match_kind": "EXACT",
                    },
                }
            }
        },
    )
    claim_result = ClaimPlanStageHandler(today=lambda: date(2026, 8, 21)).execute(
        claim_context
    )
    return StageContext(
        lease=claim_context.lease.model_copy(
            update={"stage_run_id": "evidence-plan-1", "stage_code": "EVIDENCE_PLAN"}
        ),
        project_id="project-1",
        state=state,
        dependency_results={"CLAIM_PLAN": claim_result},
    )


def actions(result: dict[str, object]) -> list[dict[str, Any]]:
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)
    return [
        action
        for claim_plan in plan["claim_plans"]
        for field in ("support_actions", "counter_actions")
        for action in claim_plan[field]
    ]


def test_complete_plan_is_generated_without_agent_runtime() -> None:
    result = EvidencePlanStageHandler().execute(stage_context())

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)
    assert plan["status"] == "COMPLETE"
    assert len(plan["claims"]) == 9
    assert len(plan["claim_plans"]) == 9
    assert len(actions(result)) == 14
    assert plan["missing_claim_ids"] == [
        "claim:FRANCHISE_UNIVERSE_ELIGIBILITY",
        "claim:FRANCHISE_DISCLOSURE_AVAILABILITY",
    ]
    assert plan["reason_codes"] == ["MCP_CAPABILITY_UNAVAILABLE"]
    assert plan["planner_trace"]["planner_version"] == PLANNER_VERSION
    assert plan["planner_trace"]["plan_digest"].startswith("sha256:")
    assert {action["action_id"] for action in actions(result)} == {
        f"action-{index:02d}" for index in range(1, 15)
    }


def test_same_claim_plan_produces_identical_output() -> None:
    context = stage_context()

    assert EvidencePlanStageHandler().execute(context) == EvidencePlanStageHandler().execute(
        context
    )


def test_plan_digest_changes_when_claim_contract_changes() -> None:
    original = stage_context(CafeTypePreference.INDEPENDENT_ONLY)
    changed = original.model_copy(deep=True)
    changed.dependency_results["CLAIM_PLAN"]["claim_plan"]["claims"][0][
        "materiality"
    ] = "MEDIUM"

    original_plan = EvidencePlanStageHandler().execute(original)["evidence_plan"]
    changed_plan = EvidencePlanStageHandler().execute(changed)["evidence_plan"]
    assert isinstance(original_plan, dict)
    assert isinstance(changed_plan, dict)
    assert (
        original_plan["planner_trace"]["plan_digest"]
        != changed_plan["planner_trace"]["plan_digest"]
    )


def test_rules_use_typed_tools_for_each_claim() -> None:
    result = EvidencePlanStageHandler().execute(stage_context())
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)
    tools_by_claim = {
        value["claim_id"]: {
            action["tool_name"]
            for field in ("support_actions", "counter_actions")
            for action in value[field]
        }
        for value in plan["claim_plans"]
    }

    assert tools_by_claim == {
        "claim:AREA_PROFILE": {"get_area_profile"},
        "claim:AREA_CAFE_COMPETITION": {"search_cafe_observations"},
        "claim:AREA_BUSINESS_CHURN": {"search_cafe_observations"},
        "claim:AREA_DEMAND_SIGNALS": {"search_cafe_observations"},
        "claim:INDEPENDENT_STARTUP_COST_BENCHMARK": {
            "retrieve_official_documents"
        },
        "claim:INDEPENDENT_OPERATING_COST_BENCHMARK": {
            "retrieve_official_documents"
        },
        "claim:FRANCHISE_UNIVERSE_ELIGIBILITY": set(),
        "claim:FRANCHISE_DISCLOSURE_AVAILABILITY": set(),
        "claim:CAFE_OPENING_REQUIRED_PROCEDURES": {
            "retrieve_official_documents"
        },
    }


@pytest.mark.parametrize(
    ("preference", "claim_count", "action_count"),
    [
        (CafeTypePreference.OPEN_TO_BOTH, 9, 14),
        (CafeTypePreference.INDEPENDENT_ONLY, 7, 14),
        (CafeTypePreference.FRANCHISE_ONLY, 7, 10),
    ],
)
def test_branch_preferences_keep_plan_within_bounded_action_budget(
    preference: CafeTypePreference,
    claim_count: int,
    action_count: int,
) -> None:
    result = EvidencePlanStageHandler().execute(stage_context(preference))
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)

    assert len(plan["claim_plans"]) == claim_count
    assert len(actions(result)) == action_count
    assert action_count <= plan["planning_constraints"]["max_total_actions"]


def test_unknown_claim_type_is_a_nonretryable_contract_failure() -> None:
    context = stage_context(CafeTypePreference.INDEPENDENT_ONLY)
    claim = context.dependency_results["CLAIM_PLAN"]["claim_plan"]["claims"][0]
    claim["claim_type"] = "UNREGISTERED_CLAIM"

    with pytest.raises(ContractValidationError, match="Unsupported deterministic"):
        EvidencePlanStageHandler().execute(context)


def test_rule_without_a_production_connector_becomes_explicit_missing_evidence() -> None:
    context = stage_context(CafeTypePreference.FRANCHISE_ONLY)
    result = EvidencePlanStageHandler().execute(context)
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)

    assert "claim:FRANCHISE_UNIVERSE_ELIGIBILITY" in plan["missing_claim_ids"]
    assert not any(
        action["tool_name"] == "list_franchise_universe" for action in actions(result)
    )


def test_invalid_area_identity_is_rejected_before_mcp_execution() -> None:
    context = stage_context(CafeTypePreference.INDEPENDENT_ONLY)
    claim = context.dependency_results["CLAIM_PLAN"]["claim_plan"]["claims"][0]
    claim["geographic_scope"]["scope_id"] = "invalid"

    with pytest.raises(ContractValidationError, match="MCP tool get_area_profile input"):
        EvidencePlanStageHandler().execute(context)
