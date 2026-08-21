from copy import deepcopy

import pytest

from app.agents.boundary import validate_agent_boundary
from app.workflows.models import HeadFence
from tests.test_contract_schemas import agent_task, agent_task_result, head_fence


def current_head() -> HeadFence:
    return HeadFence.model_validate(head_fence())


def error_codes(task: dict[str, object], result: dict[str, object]) -> set[str]:
    validation = validate_agent_boundary(task=task, result=result, current_head=current_head())
    return {error.code for error in validation.errors}


def test_valid_typed_result_crosses_boundary() -> None:
    validation = validate_agent_boundary(
        task=agent_task(),
        result=agent_task_result(),
        current_head=current_head(),
    )

    assert validation.accepted is True
    assert validation.errors == []


@pytest.mark.parametrize("field", list(head_fence()))
def test_every_echoed_head_dimension_is_fenced(field: str) -> None:
    task = agent_task()
    result = agent_task_result()
    seen = deepcopy(result["head_fence_seen"])
    assert isinstance(seen, dict)
    seen[field] = 99 if field in {"workflow_generation", "state_version"} else "changed"
    result["head_fence_seen"] = seen

    assert "FENCE_ECHO_MISMATCH" in error_codes(task, result)


@pytest.mark.parametrize("field", list(head_fence()))
def test_every_current_head_dimension_rejects_stale_task(field: str) -> None:
    task = agent_task()
    result = agent_task_result()
    stale = deepcopy(task["head_fence"])
    assert isinstance(stale, dict)
    stale[field] = 99 if field in {"workflow_generation", "state_version"} else "stale"
    task["head_fence"] = stale
    result["head_fence_seen"] = deepcopy(stale)

    assert "CURRENT_HEAD_MISMATCH" in error_codes(task, result)


def test_task_identity_echo_mismatch_is_rejected() -> None:
    result = agent_task_result()
    result["task_id"] = "another-task"

    assert "TASK_ECHO_MISMATCH" in error_codes(agent_task(), result)


def test_unallocated_operation_id_is_rejected() -> None:
    result = agent_task_result()
    result["payload"] = {
        "decision": "PROPOSE_DELTA",
        "operations": [
            {
                "op_id": "invented-op",
                "kind": "SET",
                "field_path": "venture.preferences.scale",
                "expected_old_value": {"kind": "STRING", "value": "large"},
                "typed_value": {"kind": "STRING", "value": "small"},
                "unit": None,
                "semantic_kind": "SOFT_PREFERENCE",
                "source_span": {"start": 0, "end": 8},
                "ambiguity_codes": [],
            }
        ],
        "clarifying_questions": [],
        "affected_workflow_codes": ["FIRST_PROPOSAL"],
        "risk_flags": [],
    }

    assert "UNALLOCATED_OUTPUT_ID" in error_codes(agent_task(), result)


def test_reference_not_in_frozen_input_is_rejected() -> None:
    result = agent_task_result()
    result["evidence_refs"] = ["invented-evidence"]

    assert "UNSUPPORTED_REFERENCE" in error_codes(agent_task(), result)


def test_schema_invalid_result_stops_before_semantic_acceptance() -> None:
    result = agent_task_result()
    result["payload"] = None
    validation = validate_agent_boundary(
        task=agent_task(),
        result=result,
        current_head=current_head(),
    )

    assert validation.accepted is False
    assert [error.code for error in validation.errors] == ["CONTRACT_SCHEMA_INVALID"]
