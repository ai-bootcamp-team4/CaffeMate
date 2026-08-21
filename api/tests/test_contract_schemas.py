import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError


def test_all_shared_contracts_are_valid_draft_2020_12_schemas() -> None:
    contract_directory = Path(__file__).resolve().parents[2] / "docs" / "contracts"
    schemas = sorted(contract_directory.glob("*.schema.json"))

    assert schemas
    for schema_path in schemas:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def head_fence() -> dict[str, object]:
    return {
        "workflow_generation": 1,
        "state_version": 1,
        "founder_snapshot_id": "founder-1",
        "area_snapshot_id": "area-1",
        "evidence_snapshot_id": "evidence-1",
        "policy_snapshot_id": "policy-1",
        "index_generation_id": "index-1",
        "seed_registry_id": "seed-1",
    }


def intent_payload() -> dict[str, object]:
    return {
        "current_state_projection": {
            "state_version": 1,
            "founder": {
                "target_area_input": "수원 아주대 부근",
                "own_funds_krw": 50_000_000,
                "borrowing_intent": "UNDECIDED",
                "cafe_type_preference": "OPEN_TO_BOTH",
                "operation_mode": "DIRECT_FULL_TIME",
                "preferences": [],
                "avoidances": [],
            },
            "area": {
                "resolution_status": "RESOLVED",
                "administrative_code": "4111755000",
                "display_name": "원천동",
                "boundary_version": "2026-01",
                "coverage_profile": "N1_NATIONWIDE_CONDITIONAL",
                "evidence_ids": [],
                "unavailable_fields": [],
            },
            "active_case_id": None,
            "venture_cases": [],
        },
        "latest_user_input": "규모를 줄여줘",
        "allowed_field_paths": ["venture.preferences.scale"],
        "current_candidate_refs": [],
        "operation_id_pool": ["op-1"],
    }


def agent_task() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "task_id": "task-1",
        "invocation_id": "invocation-1",
        "agent_name": "INTENT_INTERPRETER",
        "task_type": "INTENT_DELTA",
        "workflow_run_id": "workflow-1",
        "stage_run_id": "stage-1",
        "transport_attempt": 1,
        "repair_attempt": 0,
        "venture_project_id": "project-1",
        "head_fence": head_fence(),
        "prompt_version": "intent-v1",
        "input_schema_id": "caffemate.agent.intent-input.v1",
        "output_schema_id": "caffemate.agent.intent-result.v1",
        "input_artifacts": [],
        "input_digest": f"sha256:{'a' * 64}",
        "deadline_at": "2026-08-21T10:00:00Z",
        "runtime_tool_policy": "NO_DIRECT_TOOL_CALLS",
        "tool_manifest_digest": None,
        "available_tool_catalog": [],
        "payload": intent_payload(),
    }


def agent_task_result() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "task_id": "task-1",
        "invocation_id": "invocation-1",
        "agent_name": "INTENT_INTERPRETER",
        "task_type": "INTENT_DELTA",
        "workflow_run_id": "workflow-1",
        "stage_run_id": "stage-1",
        "venture_project_id": "project-1",
        "head_fence_seen": head_fence(),
        "input_digest": f"sha256:{'a' * 64}",
        "output_schema_id": "caffemate.agent.intent-result.v1",
        "status": "COMPLETE",
        "payload": {
            "decision": "NOOP",
            "operations": [],
            "clarifying_questions": [],
            "affected_workflow_codes": [],
            "risk_flags": [],
        },
        "evidence_refs": [],
        "missing_claim_ids": [],
        "reason_codes": [],
        "warnings": [],
    }


def test_contract_registry_resolves_and_validates_agent_contract_graph() -> None:
    registry = ContractRegistry()

    registry.validate_agent_task(agent_task())
    registry.validate_agent_task_result(agent_task_result())


def test_agent_task_schema_rejects_role_and_task_mismatch() -> None:
    registry = ContractRegistry()
    task = agent_task()
    task["agent_name"] = "PROPOSAL_AGENT"

    with pytest.raises(ContractValidationError, match="agent-task.schema.json"):
        registry.validate_agent_task(task)


def test_complete_agent_result_requires_typed_payload() -> None:
    registry = ContractRegistry()
    result = agent_task_result()
    result["payload"] = None

    with pytest.raises(ContractValidationError, match="payload"):
        registry.validate_agent_task_result(result)
