"""사용자 피드백은 권위 상태를 검증한 뒤 단일 제안 실행만 다시 요청한다."""

from copy import deepcopy
from datetime import UTC, datetime
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
from app.feedback.intent import affected_feedback_stages, validate_intent_delta_result
from app.workflows.models import HeadFence

HEAD = HeadFence(
    workflow_generation=1,
    state_version=1,
    policy_snapshot_id="policy-1",
    seed_registry_id="seed-1",
)
STATE = VentureState(
    project_id="project-1",
    user_id="user-1",
    state_version=1,
    status=VentureStatus.RESULT_READY,
    founder=FounderState(
        target_area_input="서울특별시 마포구 공덕동",
        own_funds_krw=50_000_000,
        borrowing_intent=BorrowingIntent.NO,
        cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
        operation_mode=OperationMode.DIRECT_FULL_TIME,
    ),
    area=AreaState(
        resolution_status=AreaResolutionStatus.RESOLVED,
        coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
    ),
    updated_at=datetime(2026, 8, 24, tzinfo=UTC),
)


def intent_task() -> dict[str, Any]:
    return AgentTaskFactory().build_intent_delta(
        project_id=STATE.project_id,
        workflow_run_id="workflow-1",
        preview_id="preview-1",
        head=HEAD,
        state=STATE,
        latest_user_input="자금은 4천만 원으로 바꿀래",
        current_candidate_refs=["candidate-1"],
    )


def operation(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "op_id": task["payload"]["operation_id_pool"][0],
        "kind": "SET",
        "field_path": "/founder/own_funds_krw",
        "expected_old_value": {"kind": "INTEGER", "value": 50_000_000},
        "typed_value": {"kind": "INTEGER", "value": 40_000_000},
        "unit": "KRW",
        "semantic_kind": "HARD_CONSTRAINT",
        "source_span": {"start": 0, "end": 17},
        "ambiguity_codes": [],
    }


def result(task: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
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
            "decision": "PROPOSE_DELTA",
            "operations": operations,
            "clarifying_questions": [],
            "affected_workflow_codes": ["FIRST_PROPOSAL"],
            "risk_flags": [],
        },
        "evidence_refs": [],
        "missing_claim_ids": [],
        "reason_codes": [],
        "warnings": [],
    }


def test_valid_scalar_delta_crosses_schema_echo_and_state_preconditions() -> None:
    task = intent_task()

    payload = validate_intent_delta_result(
        task=task,
        result=result(task, [operation(task)]),
        current_head=HEAD,
    )

    assert payload is not None
    assert payload["operations"][0]["typed_value"]["value"] == 40_000_000


def test_unexpected_old_value_is_rejected_instead_of_overwriting_state() -> None:
    task = intent_task()
    changed = operation(task)
    changed["expected_old_value"] = {"kind": "INTEGER", "value": 30_000_000}

    with pytest.raises(ContractValidationError, match="precondition"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [changed]),
            current_head=HEAD,
        )


def test_duplicate_field_operations_are_rejected() -> None:
    task = intent_task()
    first = operation(task)
    second = deepcopy(first)
    second["op_id"] = task["payload"]["operation_id_pool"][1]

    with pytest.raises(ContractValidationError, match="duplicate fields"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [first, second]),
            current_head=HEAD,
        )


def test_blank_free_text_is_rejected_at_the_authority_boundary() -> None:
    task = intent_task()
    blank = operation(task)
    blank.update(
        field_path="/founder/preferences",
        kind="ADD",
        expected_old_value={"kind": "NULL", "value": None},
        typed_value={"kind": "STRING", "value": "   "},
        semantic_kind="SOFT_PREFERENCE",
        unit=None,
    )

    with pytest.raises(ContractValidationError, match="must be a string"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [blank]),
            current_head=HEAD,
        )


def test_unchanged_scalar_is_rejected() -> None:
    task = intent_task()
    unchanged = operation(task)
    unchanged["typed_value"] = {"kind": "INTEGER", "value": 50_000_000}

    with pytest.raises(ContractValidationError, match="unchanged"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [unchanged]),
            current_head=HEAD,
        )


def test_unset_already_null_max_loss_is_rejected() -> None:
    task = intent_task()
    unchanged = operation(task)
    unchanged.update(
        field_path="/founder/max_loss_krw",
        kind="UNSET",
        expected_old_value={"kind": "NULL", "value": None},
        typed_value={"kind": "NULL", "value": None},
        unit=None,
    )

    with pytest.raises(ContractValidationError, match="unchanged"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [unchanged]),
            current_head=HEAD,
        )


def test_unallowed_field_and_ambiguous_operation_are_rejected() -> None:
    task = intent_task()
    unallowed = operation(task)
    unallowed["field_path"] = "/area/administrative_code"
    with pytest.raises(ContractValidationError, match="not allowed"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [unallowed]),
            current_head=HEAD,
        )

    ambiguous = operation(task)
    ambiguous["ambiguity_codes"] = ["AMOUNT_AMBIGUOUS"]
    with pytest.raises(ContractValidationError, match="clarification"):
        validate_intent_delta_result(
            task=task,
            result=result(task, [ambiguous]),
            current_head=HEAD,
        )


def test_preference_collection_add_requires_absent_item_and_null_precondition() -> None:
    task = intent_task()
    add = operation(task)
    add.update(
        kind="ADD",
        field_path="/founder/preferences",
        expected_old_value={"kind": "NULL", "value": None},
        typed_value={"kind": "STRING", "value": "작은 규모"},
        semantic_kind="SOFT_PREFERENCE",
        unit=None,
    )

    payload = validate_intent_delta_result(
        task=task,
        result=result(task, [add]),
        current_head=HEAD,
    )

    assert payload is not None
    assert payload["operations"][0]["field_path"] == "/founder/preferences"


def test_every_feedback_change_restarts_only_the_single_proposal_execution() -> None:
    assert affected_feedback_stages({"/founder/target_area_input"}) == ["RUN_PROPOSAL"]
    assert affected_feedback_stages({"/founder/cafe_type_preference"}) == [
        "RUN_PROPOSAL"
    ]
    assert affected_feedback_stages({"/founder/own_funds_krw"}) == ["RUN_PROPOSAL"]
