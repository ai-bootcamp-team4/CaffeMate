from datetime import UTC, date, datetime, timedelta

import pytest

from app.agents.task_factory import AgentTaskFactory, compute_agent_input_digest
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
from tests.test_agent_boundary import evidence_record


def evidence_plan_context() -> StageContext:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    state = VentureState(
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
            resolution_status=AreaResolutionStatus.UNRESOLVED,
            coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
        ),
        updated_at=now,
    )
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
    claim_context = StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="claim-stage",
            stage_code="CLAIM_PLAN",
            input_digest="a" * 64,
            lease_token="token",
            lease_expires_at=now + timedelta(seconds=45),
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
                        "boundary_version": "2026-01",
                    },
                }
            }
        },
    )
    claim_plan = ClaimPlanStageHandler(today=lambda: date(2026, 8, 21)).execute(
        claim_context
    )
    return StageContext(
        lease=claim_context.lease.model_copy(
            update={"stage_run_id": "evidence-stage", "stage_code": "EVIDENCE_PLAN"}
        ),
        project_id="project-1",
        state=state,
        dependency_results={"CLAIM_PLAN": claim_plan},
    )


def test_evidence_plan_task_is_manifest_pinned_schema_valid_and_digest_bound() -> None:
    task = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-1",
    ).build_evidence_plan(evidence_plan_context())

    assert task["agent_name"] == "EVIDENCE_RESEARCHER"
    assert task["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"
    assert task["deadline_at"] == "2026-08-21T10:01:00Z"
    assert len(task["available_tool_catalog"]) == 10
    assert task["input_digest"] == compute_agent_input_digest(task)
    assert task["tool_manifest_digest"].startswith("sha256:")


def test_intent_delta_task_pins_state_candidates_and_editable_fields() -> None:
    context = evidence_plan_context()
    task = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-feedback",
    ).build_intent_delta(
        project_id=context.project_id,
        workflow_run_id=context.lease.workflow_run_id,
        preview_id="preview-1",
        head=context.lease.head,
        state=context.state,
        latest_user_input=" 자금은 4천만 원으로 바꿀래 ",
        current_candidate_refs=["candidate-2", "candidate-1", "candidate-1"],
    )

    assert task["task_type"] == "INTENT_DELTA"
    assert task["agent_name"] == "INTENT_INTERPRETER"
    assert task["deadline_at"] == "2026-08-21T10:00:30Z"
    assert task["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"
    assert task["available_tool_catalog"] == []
    assert task["payload"]["latest_user_input"] == "자금은 4천만 원으로 바꿀래"
    assert task["payload"]["current_candidate_refs"] == [
        "candidate-1",
        "candidate-2",
    ]
    assert task["payload"]["current_state_projection"]["state_version"] == 1
    assert "/founder/own_funds_krw" in task["payload"]["allowed_field_paths"]
    assert len(task["payload"]["operation_id_pool"]) == 20
    assert task["input_digest"] == compute_agent_input_digest(task)


def test_intent_delta_rejects_cross_project_state_before_runtime() -> None:
    context = evidence_plan_context()

    with pytest.raises(ContractValidationError, match="crossed project"):
        AgentTaskFactory().build_intent_delta(
            project_id="another-project",
            workflow_run_id=context.lease.workflow_run_id,
            preview_id="preview-1",
            head=context.lease.head,
            state=context.state,
            latest_user_input="자금을 바꿔줘",
            current_candidate_refs=[],
        )


def test_evidence_plan_digest_changes_with_claim_payload_but_not_invocation_id() -> None:
    factory = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-1",
    )
    first = factory.build_evidence_plan(evidence_plan_context())
    second_context = evidence_plan_context()
    second_context.dependency_results["CLAIM_PLAN"]["claim_plan"]["claims"][0][
        "required_freshness"
    ] = "P30D"
    second = factory.build_evidence_plan(second_context)
    changed_invocation = dict(first, invocation_id="invocation-2")

    assert first["input_digest"] != second["input_digest"]
    assert compute_agent_input_digest(changed_invocation) == first["input_digest"]


def test_missing_claim_plan_is_rejected_before_runtime_invocation() -> None:
    value = evidence_plan_context()
    value.dependency_results = {}

    with pytest.raises(ContractValidationError, match="Claim Plan"):
        AgentTaskFactory().build_evidence_plan(value)


def test_evidence_assess_task_contains_only_normalized_retrieval_inputs() -> None:
    value = evidence_plan_context()
    claim_plan = value.dependency_results["CLAIM_PLAN"]["claim_plan"]
    value.lease = value.lease.model_copy(
        update={"stage_run_id": "assess-stage", "stage_code": "EVIDENCE_ASSESS"}
    )
    value.dependency_results = {
        "EVIDENCE_RETRIEVAL": {
            "evidence_retrieval": {
                "claims": claim_plan["claims"],
                "executed_actions": [],
                "failed_actions": [],
                "completeness": "PARTIAL",
            }
        }
    }

    task = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-assess",
    ).build_evidence_assess(value)

    assert task["task_type"] == "EVIDENCE_ASSESS"
    assert task["deadline_at"] == "2026-08-21T10:01:00Z"
    assert task["tool_manifest_digest"] is None
    assert task["available_tool_catalog"] == []
    assert task["payload"] == {
        "claims": claim_plan["claims"],
        "executed_actions": [],
    }
    assert task["input_digest"] == compute_agent_input_digest(task)


def test_evidence_assess_projection_deduplicates_and_bounds_model_context() -> None:
    value = evidence_plan_context()
    claim_plan = value.dependency_results["CLAIM_PLAN"]["claim_plan"]
    value.lease = value.lease.model_copy(
        update={"stage_run_id": "assess-stage", "stage_code": "EVIDENCE_ASSESS"}
    )
    records = [evidence_record(f"evidence-{index}") for index in range(1, 6)]
    result = {
        "schema_version": "1.0.0",
        "request_id": "request-shared",
        "tool_name": "get_area_profile",
        "tool_version": "1.0.0",
        "status": "OK",
        "project_id": "project-1",
        "evidence_records": records,
        "missing_fields": [],
        "conflicts": [],
        "source_trace": [],
        "error_codes": [],
        "observed_at": "2026-08-21T09:00:00Z",
        "data": [{"metric": "bulk-row"}],
    }
    actions = [
        {
            "action_id": f"action-{polarity.lower()}",
            "claim_id": "claim:AREA_PROFILE",
            "polarity": polarity,
            "tool_name": "get_area_profile",
            "request_id": "request-shared",
            "structured_result": result,
        }
        for polarity in ("SUPPORT", "COUNTER")
    ]
    value.dependency_results = {
        "EVIDENCE_RETRIEVAL": {
            "evidence_retrieval": {
                "claims": claim_plan["claims"],
                "executed_actions": actions,
                "failed_actions": [],
                "completeness": "COMPLETE",
            }
        }
    }

    factory = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-assess",
    )
    task = factory.build_evidence_assess(value)
    projected = task["payload"]["executed_actions"]

    assert len(projected) == 1
    assert [
        record["evidence_id"]
        for record in projected[0]["structured_result"]["evidence_records"]
    ] == ["evidence-1", "evidence-2", "evidence-3"]
    assert projected[0]["structured_result"]["data"] == []
    assert len(actions) == 2
    assert len(actions[0]["structured_result"]["evidence_records"]) == 5
    assert actions[0]["structured_result"]["data"] == [{"metric": "bulk-row"}]
    assert task["input_digest"] == compute_agent_input_digest(task)
