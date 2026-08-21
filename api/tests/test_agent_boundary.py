import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.boundary import validate_agent_boundary
from app.workflows.models import HeadFence
from tests.test_contract_schemas import agent_task, agent_task_result, head_fence

_AGENT_FIXTURES = json.loads(
    (Path(__file__).parents[2] / "agents" / "fixtures" / "task-matrix.json").read_text()
)["cases"]


def current_head() -> HeadFence:
    return HeadFence.model_validate(head_fence())


def error_codes(task: dict[str, object], result: dict[str, object]) -> set[str]:
    validation = validate_agent_boundary(task=task, result=result, current_head=current_head())
    return {error.code for error in validation.errors}


def fixture_task_result(task_type: str) -> tuple[dict[str, object], dict[str, object]]:
    fixture = next(
        item
        for item in _AGENT_FIXTURES
        if item["task"]["task_type"] == task_type and item["result"]["status"] == "COMPLETE"
    )
    return deepcopy(fixture["task"]), deepcopy(fixture["result"])


def fixture_head(task: dict[str, object]) -> HeadFence:
    return HeadFence.model_validate(task["head_fence"])


def evidence_record(
    evidence_id: str,
    *,
    value_kind: str = "EVIDENCED_FACT",
    scope_id: str = "11200690",
    freshness_status: str = "FRESH",
) -> dict[str, object]:
    unknown = value_kind == "UNKNOWN"
    return {
        "schema_version": "2.0.0",
        "evidence_id": evidence_id,
        "project_id": "project-1",
        "claim_type": "AREA_POPULATION",
        "value": {"kind": "NULL", "value": None} if unknown else {"kind": "INTEGER", "value": 1000},
        "value_kind": value_kind,
        "unit": None,
        "geographic_scope": {
            "scope_type": "ADMINISTRATIVE_AREA",
            "scope_id": scope_id,
            "boundary_version": "2026-01",
        },
        "source": {
            "title": "fixture source",
            "source_ref": "fixture://source",
            "authority": "PRIMARY_OFFICIAL",
            "source_type": "DATASET",
            "published_or_data_date": None if unknown else "2026-08-01",
            "source_observed_at": "2026-08-21T09:00:00Z",
            "document_version": None,
            "checksum": None,
        },
        "original_anchor": {
            "anchor_type": "DATASET_ROW",
            "locator": f"row:{evidence_id}",
            "excerpt_hash": None,
        },
        "freshness_status": freshness_status,
        "conflict_status": "NONE",
        "retrieved_at": "2026-08-21T09:00:00Z",
        "missing_context": ["value unavailable"] if unknown else [],
        "durable_evidence_refs": [],
    }


def attach_evidence_action(task: dict[str, object], evidence: dict[str, object]) -> None:
    payload = task["payload"]
    assert isinstance(payload, dict)
    payload["executed_actions"] = [
        {
            "action_id": "action-1",
            "claim_id": "claim-1",
            "polarity": "SUPPORT",
            "tool_name": "get_area_profile",
            "request_id": "request-1",
            "structured_result": {
                "schema_version": "1.0.0",
                "request_id": "request-1",
                "tool_name": "get_area_profile",
                "tool_version": "1.0.0",
                "status": "OK",
                "project_id": "project-1",
                "evidence_records": [evidence],
                "missing_fields": [],
                "conflicts": [],
                "source_trace": [],
                "error_codes": [],
                "observed_at": "2026-08-21T09:00:00Z",
                "data": [],
            },
        }
    ]


def test_valid_typed_result_crosses_boundary() -> None:
    validation = validate_agent_boundary(
        task=agent_task(),
        result=agent_task_result(),
        current_head=current_head(),
    )

    assert validation.accepted is True
    assert validation.errors == []


def test_evidence_plan_rejects_tool_outside_backend_allowlist() -> None:
    task, result = fixture_task_result("EVIDENCE_PLAN")
    payload = task["payload"]
    assert isinstance(payload, dict)
    constraints = payload["planning_constraints"]
    assert isinstance(constraints, dict)
    constraints["allowed_tools"] = ["get_area_profile"]

    assert "EVIDENCE_PLAN_TOOL_NOT_ALLOWED" in error_codes(task, result)


def test_evidence_plan_rejects_duplicate_action_ids_across_polarities() -> None:
    task, result = fixture_task_result("EVIDENCE_PLAN")
    payload = result["payload"]
    assert isinstance(payload, dict)
    plan = payload["claim_plans"][0]
    plan["counter_actions"][0]["action_id"] = plan["support_actions"][0]["action_id"]

    assert "EVIDENCE_PLAN_ACTION_DUPLICATED" in error_codes(task, result)


def test_evidence_plan_rejects_action_scope_that_differs_from_claim() -> None:
    task, result = fixture_task_result("EVIDENCE_PLAN")
    payload = result["payload"]
    assert isinstance(payload, dict)
    plan = payload["claim_plans"][0]
    plan["support_actions"][0]["scope_constraints"]["scope_id"] = "other-area"

    assert "EVIDENCE_PLAN_ACTION_CONTEXT_MISMATCH" in error_codes(task, result)


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


@pytest.mark.parametrize(
    ("value_kind", "freshness_status"),
    [("DECLARED_ASSUMPTION", "NOT_APPLICABLE"), ("UNKNOWN", "UNKNOWN")],
)
def test_assumption_or_unknown_cannot_be_used_as_evidence_coverage(
    value_kind: str,
    freshness_status: str,
) -> None:
    task, result = fixture_task_result("PROPOSE_INDEPENDENT")
    evidence = evidence_record(
        f"ev-{value_kind.lower()}",
        value_kind=value_kind,
        freshness_status=freshness_status,
    )
    payload = task["payload"]
    assert isinstance(payload, dict)
    payload["evidence_records"] = [evidence]
    result_payload = result["payload"]
    assert isinstance(result_payload, dict)
    proposals = result_payload["candidate_proposals"]
    assert isinstance(proposals, list)
    proposal = proposals[0]
    assert isinstance(proposal, dict)
    proposal["evidence_refs"] = [evidence["evidence_id"]]
    result["evidence_refs"] = [evidence["evidence_id"]]

    validation = validate_agent_boundary(
        task=task,
        result=result,
        current_head=fixture_head(task),
    )

    assert "ASSUMPTION_USED_AS_EVIDENCE" in {error.code for error in validation.errors}


def test_non_monotonic_money_range_is_rejected() -> None:
    task, result = fixture_task_result("PROPOSE_INDEPENDENT")
    payload = task["payload"]
    assert isinstance(payload, dict)
    seeds = payload["model_seeds"]
    assert isinstance(seeds, list)
    seed = seeds[0]
    assert isinstance(seed, dict)
    seed["allowed_parameters"] = [
        {
            "field_path": "finance.initial_cash",
            "value_kind": "MONEY_RANGE",
            "unit": "KRW",
            "minimum": 0,
            "maximum": 1000,
        }
    ]
    result_payload = result["payload"]
    assert isinstance(result_payload, dict)
    proposals = result_payload["candidate_proposals"]
    assert isinstance(proposals, list)
    proposal = proposals[0]
    assert isinstance(proposal, dict)
    proposal["adjusted_parameters"] = [
        {
            "field_path": "finance.initial_cash",
            "value": {
                "kind": "MONEY_RANGE",
                "currency": "KRW",
                "low": 900,
                "base": 500,
                "high": 700,
            },
            "unit": "KRW",
            "support_refs": ["seed-registry-1"],
        }
    ]

    validation = validate_agent_boundary(
        task=task,
        result=result,
        current_head=fixture_head(task),
    )

    assert "MONEY_RANGE_NON_MONOTONIC" in {error.code for error in validation.errors}


def test_valid_evidence_assessment_candidate_ref_is_supported_by_executed_evidence() -> None:
    task, result = fixture_task_result("EVIDENCE_ASSESS")
    evidence = evidence_record("ev-current")
    attach_evidence_action(task, evidence)
    result["payload"] = {
        "assessments": [
            {
                "claim_id": "claim-1",
                "candidate_ref": "ev-current",
                "relation": "SUPPORTS",
                "scope_status": "MATCH",
                "date_status": "MATCH",
                "freshness_status": "FRESH",
                "anchor_status": "VALID",
                "authority_status": "ACCEPTABLE",
                "missing_context": [],
            }
        ],
        "missing_claims": [],
        "conflict_proposals": [],
    }
    result["evidence_refs"] = ["ev-current"]

    validation = validate_agent_boundary(
        task=task,
        result=result,
        current_head=fixture_head(task),
    )

    assert validation.accepted is True
    assert validation.errors == []


def test_unknown_evidence_can_be_ambiguous_without_becoming_evidence_coverage() -> None:
    task, result = fixture_task_result("EVIDENCE_ASSESS")
    evidence = evidence_record(
        "ev-unknown-candidate",
        value_kind="UNKNOWN",
        freshness_status="UNKNOWN",
    )
    attach_evidence_action(task, evidence)
    result["payload"] = {
        "assessments": [
            {
                "claim_id": "claim-1",
                "candidate_ref": "ev-unknown-candidate",
                "relation": "AMBIGUOUS",
                "scope_status": "UNKNOWN",
                "date_status": "UNKNOWN",
                "freshness_status": "UNKNOWN",
                "anchor_status": "MISSING",
                "authority_status": "UNKNOWN",
                "missing_context": ["not usable as evidence"],
            }
        ],
        "missing_claims": ["claim-1"],
        "conflict_proposals": [],
    }
    result["evidence_refs"] = []

    validation = validate_agent_boundary(
        task=task,
        result=result,
        current_head=fixture_head(task),
    )

    assert validation.accepted is True
    assert validation.errors == []


def test_evidence_assessment_cannot_overstate_scope_or_freshness() -> None:
    task, result = fixture_task_result("EVIDENCE_ASSESS")
    evidence = evidence_record(
        "ev-stale-other-scope",
        scope_id="26110520",
        freshness_status="STALE",
    )
    attach_evidence_action(task, evidence)
    result["payload"] = {
        "assessments": [
            {
                "claim_id": "claim-1",
                "candidate_ref": "ev-stale-other-scope",
                "relation": "SUPPORTS",
                "scope_status": "MATCH",
                "date_status": "MATCH",
                "freshness_status": "FRESH",
                "anchor_status": "VALID",
                "authority_status": "ACCEPTABLE",
                "missing_context": [],
            }
        ],
        "missing_claims": [],
        "conflict_proposals": [],
    }
    result["evidence_refs"] = ["ev-stale-other-scope"]

    validation = validate_agent_boundary(
        task=task,
        result=result,
        current_head=fixture_head(task),
    )

    assert "EVIDENCE_SCOPE_OR_DATE_INVALID" in {error.code for error in validation.errors}
