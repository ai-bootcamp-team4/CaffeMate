from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError
from app.workflows.evidence_assess import EvidenceAssessStageHandler
from tests.test_evidence_retrieval_stage import action, context


def assess_context(*, failed: bool = False):
    value = context(support=[action("action-01", "SUPPORT")], counter=[])
    value.lease = value.lease.model_copy(
        update={"stage_run_id": "assess-1", "stage_code": "EVIDENCE_ASSESS"}
    )
    claims = value.dependency_results["EVIDENCE_PLAN"]["evidence_plan"]["claims"]
    failed_actions = (
        [
            {
                "action_id": "action-01",
                "claim_id": "claim:AREA_PROFILE",
                "polarity": "SUPPORT",
                "tool_name": "get_source_health",
                "error_code": "MCP_TRANSPORT_ERROR",
            }
        ]
        if failed
        else []
    )
    value.dependency_results = {
        "EVIDENCE_RETRIEVAL": {
            "evidence_retrieval": {
                "claims": claims,
                "planned_action_count": 1,
                "physical_call_count": 1,
                "completeness": "UNAVAILABLE" if failed else "COMPLETE",
                "executed_actions": [],
                "failed_actions": failed_actions,
            }
        }
    }
    return value


def result_for(task: dict[str, Any], *, status: str = "COMPLETE") -> dict[str, Any]:
    payload = (
        {"assessments": [], "missing_claims": [], "conflict_proposals": []}
        if status == "COMPLETE"
        else None
    )
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
        "status": status,
        "payload": payload,
        "evidence_refs": [],
        "missing_claim_ids": [],
        "reason_codes": [] if status == "COMPLETE" else ["INSUFFICIENT_CONTEXT"],
        "warnings": [],
    }


class FakeRuntime:
    def __init__(self, status: str = "COMPLETE", *, wrong_project: bool = False) -> None:
        self.status = status
        self.wrong_project = wrong_project
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        result = result_for(task, status=self.status)
        if self.wrong_project:
            result["venture_project_id"] = "another-project"
        return result


def handler(runtime: FakeRuntime) -> EvidenceAssessStageHandler:
    return EvidenceAssessStageHandler(
        runtime,
        task_factory=AgentTaskFactory(
            now=lambda: datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
            new_invocation_id=lambda: "invocation-assess",
        ),
    )


def test_complete_assessment_is_normalized_for_evidence_freeze() -> None:
    runtime = FakeRuntime()

    result = handler(runtime).execute(assess_context())

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    assessment = result["evidence_assessment"]
    assert isinstance(assessment, dict)
    assert assessment["status"] == "COMPLETE"
    assert assessment["assessments"] == []
    assert assessment["missing_claim_ids"] == ["claim:AREA_PROFILE"]
    assert assessment["reason_codes"] == ["ASSESSMENT_COVERAGE_MISSING"]
    assert assessment["agent_trace"]["invocation_id"] == "invocation-assess"
    assert runtime.tasks[0]["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"


def test_failed_mcp_actions_become_missing_claims_without_becoming_empty_success() -> None:
    result = handler(FakeRuntime()).execute(assess_context(failed=True))

    assessment = result["evidence_assessment"]
    assert isinstance(assessment, dict)
    assert assessment["missing_claim_ids"] == ["claim:AREA_PROFILE"]
    assert assessment["reason_codes"] == ["MCP_ACTION_FAILED"]
    assert assessment["retrieval_completeness"] == "UNAVAILABLE"


def test_agent_abstention_preserves_missing_state_and_continues_to_freeze() -> None:
    result = handler(FakeRuntime("ABSTAIN")).execute(assess_context())

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    assessment = result["evidence_assessment"]
    assert isinstance(assessment, dict)
    assert assessment["status"] == "ABSTAIN"
    assert assessment["reason_codes"] == ["INSUFFICIENT_CONTEXT"]
    assert assessment["missing_claim_ids"] == ["claim:AREA_PROFILE"]


def test_human_required_status_pauses_workflow() -> None:
    result = handler(FakeRuntime("NEEDS_HUMAN")).execute(assess_context())

    assert result["stage_control"] == {
        "disposition": "WAITING_FOR_HUMAN",
        "reason_codes": ["INSUFFICIENT_CONTEXT"],
    }


def test_cross_project_echo_is_rejected_before_freeze() -> None:
    with pytest.raises(ContractValidationError, match="TASK_ECHO_MISMATCH"):
        handler(FakeRuntime(wrong_project=True)).execute(assess_context())
