from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agents.task_factory import AgentTaskFactory
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
from app.workflows.evidence_plan import EvidencePlanStageHandler
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext


def stage_context() -> StageContext:
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
    return StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="evidence-plan-1",
            stage_code="EVIDENCE_PLAN",
            input_digest="a" * 64,
            lease_token="lease-token",
            lease_expires_at=now + timedelta(seconds=45),
            attempt=1,
            head=head,
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
                cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
                operation_mode=OperationMode.DIRECT_FULL_TIME,
            ),
            area=AreaState(
                resolution_status=AreaResolutionStatus.RESOLVED,
                administrative_code="41117550",
                display_name="경기도 수원시 영통구 원천동",
                boundary_version="2026-01",
                coverage_profile=CoverageProfile.N1_NATIONWIDE_CONDITIONAL,
            ),
            updated_at=now,
        ),
        dependency_results={
            "CLAIM_PLAN": {
                "claim_plan": {
                    "claims": [
                        {
                            "claim_id": "claim:AREA_PROFILE",
                            "claim_type": "AREA_PROFILE",
                            "materiality": "HIGH",
                            "geographic_scope": {
                                "scope_type": "ADMINISTRATIVE_AREA",
                                "scope_id": "41117550",
                                "boundary_version": "2026-01",
                            },
                            "required_freshness": "P365D",
                        }
                    ],
                    "planning_constraints": {
                        "as_of": "2026-08-21",
                        "max_actions_per_claim": 2,
                        "max_total_actions": 4,
                        "allowed_tools": ["get_source_health"],
                    },
                    "action_id_pool": ["action-01", "action-02"],
                }
            }
        },
    )


def complete_result(task: dict[str, Any]) -> dict[str, Any]:
    scope = task["payload"]["claims"][0]["geographic_scope"]
    return {
        "schema_version": "1.0.0",
        "task_id": task["task_id"],
        "invocation_id": task["invocation_id"],
        "agent_name": task["agent_name"],
        "task_type": task["task_type"],
        "workflow_run_id": task["workflow_run_id"],
        "stage_run_id": task["stage_run_id"],
        "venture_project_id": task["venture_project_id"],
        "head_fence_seen": deepcopy(task["head_fence"]),
        "input_digest": task["input_digest"],
        "output_schema_id": task["output_schema_id"],
        "status": "COMPLETE",
        "payload": {
            "claim_plans": [
                {
                    "claim_id": "claim:AREA_PROFILE",
                    "route": "MCP_STRUCTURED",
                    "support_actions": [
                        {
                            "action_id": "action-01",
                            "claim_id": "claim:AREA_PROFILE",
                            "polarity": "SUPPORT",
                            "tool_name": "get_source_health",
                            "tool_version": "1.0.0",
                            "typed_arguments": {
                                "source_ids": ["area-profile"],
                                "as_of": "2026-08-21",
                            },
                            "required_authority": ["PRIMARY_DATA"],
                            "date_constraints": {
                                "as_of": "2026-08-21",
                                "max_age_days": 365,
                            },
                            "scope_constraints": scope,
                        }
                    ],
                    "counter_actions": [],
                    "stop_condition": "공식 자료를 확보하면 종료",
                    "abstain_condition": "공식 자료가 없으면 기권",
                }
            ]
        },
        "evidence_refs": [],
        "missing_claim_ids": [],
        "reason_codes": [],
        "warnings": [],
    }


class FakeRuntime:
    def __init__(self, status: str = "COMPLETE") -> None:
        self.status = status
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        result = complete_result(task)
        if self.status != "COMPLETE":
            result.update(
                status=self.status,
                payload=None,
                reason_codes=["INSUFFICIENT_CONTEXT"],
            )
        return result


def handler(runtime: FakeRuntime) -> EvidencePlanStageHandler:
    return EvidencePlanStageHandler(
        runtime,
        task_factory=AgentTaskFactory(
            now=lambda: datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
            new_invocation_id=lambda: "invocation-1",
        ),
    )


def test_complete_plan_crosses_runtime_boundary_and_becomes_stage_output() -> None:
    runtime = FakeRuntime()

    result = handler(runtime).execute(stage_context())

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    plan = result["evidence_plan"]
    assert isinstance(plan, dict)
    assert plan["status"] == "COMPLETE"
    assert plan["claim_plans"][0]["support_actions"][0]["action_id"] == "action-01"
    assert plan["agent_trace"]["invocation_id"] == "invocation-1"
    assert runtime.tasks[0]["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"


@pytest.mark.parametrize(
    ("status", "disposition"),
    [("NEEDS_HUMAN", "WAITING_FOR_HUMAN"), ("ABSTAIN", "ABSTAIN")],
)
def test_noncomplete_agent_status_stops_the_workflow_safely(
    status: str,
    disposition: str,
) -> None:
    result = handler(FakeRuntime(status)).execute(stage_context())

    assert result["stage_control"] == {
        "disposition": disposition,
        "reason_codes": ["INSUFFICIENT_CONTEXT"],
    }


def test_invalid_agent_input_status_is_a_nonretryable_contract_failure() -> None:
    with pytest.raises(ContractValidationError, match="rejected its input"):
        handler(FakeRuntime("INVALID")).execute(stage_context())


def test_unallocated_action_is_rejected_before_mcp_execution() -> None:
    class InvalidRuntime(FakeRuntime):
        def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
            result = complete_result(task)
            result["payload"]["claim_plans"][0]["support_actions"][0][
                "action_id"
            ] = "invented-action"
            return result

    with pytest.raises(ContractValidationError, match="UNALLOCATED_OUTPUT_ID"):
        handler(InvalidRuntime()).execute(stage_context())
