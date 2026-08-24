from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.agents.runtime import AgentRuntimeError
from app.agents.task_factory import AgentTaskFactory, compute_agent_input_digest
from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.workflows.calculate_gate_rank import CalculateGateRankStageHandler
from app.workflows.candidate_audit import CandidateAuditStageHandler
from app.workflows.stage_context import StageContext
from tests.test_calculate_gate_rank_stage import (
    calculation_context,
    complete_independent_finance,
)
from tests.test_proposal_stages import FakeRuntime


def audit_context(
    *,
    complete_finance: bool = True,
    include_franchise: bool = False,
    attempt: int = 1,
) -> StageContext:
    records = complete_independent_finance() if complete_finance else []
    source = calculation_context(
        evidence_records=records,
        include_franchise=include_franchise,
    )
    calculated = CalculateGateRankStageHandler().execute(source)
    return StageContext(
        lease=source.lease.model_copy(
            update={
                "stage_run_id": "stage-candidate-audit",
                "stage_code": "CANDIDATE_AUDIT",
                "attempt": attempt,
            }
        ),
        project_id=source.project_id,
        state=source.state,
        dependency_results={"CALCULATE_GATE_RANK": calculated},
    )


def audit_result(
    task: dict[str, Any],
    *,
    status: str = "COMPLETE",
    audit_status: str = "PASS",
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = None
    if status == "COMPLETE":
        payload = {
            "candidate_audits": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "status": audit_status,
                    "findings": findings or [],
                }
                for candidate in task["payload"]["candidates"]
            ],
            "global_findings": [],
        }
    return {
        "schema_version": "1.0.0",
        "task_id": task["task_id"],
        "invocation_id": task["invocation_id"],
        "agent_name": task["agent_name"],
        "task_type": task["task_type"],
        "workflow_run_id": task["workflow_run_id"],
        "stage_run_id": task["stage_run_id"],
        "venture_project_id": task["venture_project_id"],
        "head_fence_seen": task["head_fence"],
        "input_digest": task["input_digest"],
        "output_schema_id": task["output_schema_id"],
        "status": status,
        "payload": payload,
        "evidence_refs": [],
        "missing_claim_ids": [],
        "reason_codes": [] if status == "COMPLETE" else ["INSUFFICIENT_CONTEXT"],
        "warnings": [],
    }


def test_candidate_audit_task_projects_schema_valid_deterministic_inputs() -> None:
    context = audit_context()
    factory = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-audit",
    )

    task = factory.build_candidate_audit(context)

    candidate = task["payload"]["candidates"][0]
    assert task["task_type"] == "CANDIDATE_AUDIT"
    assert task["deadline_at"] == "2026-08-21T10:01:00Z"
    assert task["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"
    assert task["available_tool_catalog"] == []
    assert task["input_digest"] == compute_agent_input_digest(task)
    assert candidate["review_status"] == "REVIEW_RECOMMENDED"
    assert candidate["rank"] == 1
    assert candidate["is_primary_next_review"] is True
    assert candidate["financial_summary"]["required_daily_orders"] == 40.0
    assert candidate["financial_summary"]["initial_cash"]["provenance_refs"]
    candidate_gates = task["payload"]["gate_snapshot"]["candidate_gates"]
    assert len(candidate_gates) == 3
    assert [gate["candidate_id"] for gate in candidate_gates] == [
        value["candidate_id"] for value in task["payload"]["candidates"]
    ]
    assert all(
        gate
        == {
            "candidate_id": value["candidate_id"],
            "hard_constraint": "PASS",
            "economic_viability": "PASS",
            "founder_fit": "PASS",
            "risk_adjusted_status": "REVIEW_RECOMMENDED",
        }
        for gate, value in zip(
            candidate_gates, task["payload"]["candidates"], strict=True
        )
    )


def test_franchise_natural_language_warning_does_not_enter_reason_code_field() -> None:
    task = AgentTaskFactory().build_candidate_audit(
        audit_context(include_franchise=True)
    )

    warning_codes = task["payload"]["calculation_snapshot"]["warning_codes"]
    assert warning_codes
    assert all(
        code.replace("_", "").isalnum() and code == code.upper()
        for code in warning_codes
    )
    assert "본사 출점 가능 여부 확인 필요" not in warning_codes


def test_complete_audit_preserves_deterministic_candidate_and_passes() -> None:
    runtime = FakeRuntime(audit_result)

    result = CandidateAuditStageHandler(runtime).execute(audit_context())

    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert output["status"] == "PASSED"
    assert output["agent_status"] == "COMPLETE"
    assert output["candidates"] == runtime.tasks[0]["payload"]["candidates"]
    assert output["candidates"][0]["rank"] == 1
    assert output["candidate_audits"][0]["status"] == "PASS"
    stage_control = result["stage_control"]
    assert isinstance(stage_control, dict)
    assert stage_control["disposition"] == "CONTINUE"


def test_serious_advisory_finding_requires_human_without_mutating_rank() -> None:
    def finding_result(task: dict[str, Any]) -> dict[str, Any]:
        candidate_id = task["payload"]["candidates"][0]["candidate_id"]
        finding = {
            "code": "UNSUPPORTED_FINANCIAL_OUTPUT",
            "severity": "HIGH",
            "field_path": "/financial_summary/break_even_monthly_sales_krw",
            "claim_refs": [],
            "evidence_refs": [],
            "calculation_refs": [candidate_id],
            "disposition": "REQUIRE_HUMAN",
        }
        return audit_result(
            task,
            audit_status="REQUIRES_HUMAN",
            findings=[finding],
        )

    runtime = FakeRuntime(finding_result)

    result = CandidateAuditStageHandler(runtime).execute(audit_context())

    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert output["status"] == "REQUIRES_HUMAN"
    assert output["candidates"][0]["rank"] == 1
    assert output["candidates"][0]["review_status"] == "REVIEW_RECOMMENDED"
    assert "CANDIDATE_AUDIT_REQUIRES_HUMAN" in output["reason_codes"]


def test_missing_candidate_coverage_preserves_candidates_as_unavailable() -> None:
    def missing_audit(task: dict[str, Any]) -> dict[str, Any]:
        result = audit_result(task)
        result["payload"]["candidate_audits"] = []
        return result

    result = CandidateAuditStageHandler(FakeRuntime(missing_audit)).execute(
        audit_context()
    )
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"
    assert result["candidate_audit"]["candidates"]


def test_unallocated_calculation_reference_preserves_candidates() -> None:
    def forged_reference(task: dict[str, Any]) -> dict[str, Any]:
        finding = {
            "code": "FORGED_CALCULATION",
            "severity": "HIGH",
            "field_path": "/financial_summary",
            "claim_refs": [],
            "evidence_refs": [],
            "calculation_refs": ["calculation-not-in-input"],
            "disposition": "REQUIRE_HUMAN",
        }
        return audit_result(
            task,
            audit_status="REQUIRES_HUMAN",
            findings=[finding],
        )

    result = CandidateAuditStageHandler(FakeRuntime(forged_reference)).execute(
        audit_context()
    )
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"
    assert result["candidate_audit"]["candidates"]


def test_agent_replacement_rank_preserves_backend_candidates() -> None:
    def replacement_rank(task: dict[str, Any]) -> dict[str, Any]:
        result = audit_result(task)
        result["payload"]["candidate_audits"][0]["rank"] = 2
        return result

    result = CandidateAuditStageHandler(FakeRuntime(replacement_rank)).execute(
        audit_context()
    )
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"
    assert result["candidate_audit"]["candidates"][0]["rank"] == 1


def test_tampered_finance_output_abstains_before_runtime() -> None:
    context = audit_context()
    dependency = context.dependency_results["CALCULATE_GATE_RANK"]
    candidate = dependency["calculate_gate_rank"]["candidates"][0]
    candidate["finance"]["break_even_monthly_sales_krw"] += 1
    runtime = FakeRuntime(audit_result)

    result = CandidateAuditStageHandler(runtime).execute(context)

    assert runtime.tasks == []
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"


def test_missing_franchise_eligibility_evidence_abstains() -> None:
    context = audit_context(include_franchise=True)
    dependency = context.dependency_results["CALCULATE_GATE_RANK"]
    franchise = next(
        value
        for value in dependency["calculate_gate_rank"]["candidates"]
        if value["case_type"] == "FRANCHISE"
    )
    franchise["franchise_eligibility_evidence_refs"] = ["missing-evidence"]
    runtime = FakeRuntime(audit_result)

    result = CandidateAuditStageHandler(runtime).execute(context)

    assert runtime.tasks == []
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"


def test_pass_status_with_findings_preserves_candidates() -> None:
    def incoherent(task: dict[str, Any]) -> dict[str, Any]:
        finding = {
            "code": "INCOHERENT",
            "severity": "LOW",
            "field_path": "/summary",
            "claim_refs": [],
            "evidence_refs": [],
            "calculation_refs": [],
            "disposition": "REQUIRE_EVIDENCE",
        }
        return audit_result(task, findings=[finding])

    result = CandidateAuditStageHandler(FakeRuntime(incoherent)).execute(
        audit_context()
    )
    assert result["candidate_audit"]["status"] == "UNAVAILABLE"
    assert result["candidate_audit"]["candidates"]


def test_runtime_failure_preserves_candidates_without_duplicate_stage_retry() -> None:
    def unavailable(_task: dict[str, Any]) -> dict[str, Any]:
        raise ExternalExecutionUnavailableError("runtime unavailable")

    handler = CandidateAuditStageHandler(FakeRuntime(unavailable))
    result = handler.execute(audit_context(attempt=1))

    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert output["status"] == "UNAVAILABLE"
    assert output["candidates"][0]["rank"] == 1
    assert output["reason_codes"] == ["CANDIDATE_AUDIT_RUNTIME_UNAVAILABLE"]


def test_repaired_agent_output_rejection_preserves_candidates_without_stage_retry() -> None:
    def invalid_output(_task: dict[str, Any]) -> dict[str, Any]:
        raise AgentRuntimeError("RUNTIME_AGENT_OUTPUT_INVALID")

    runtime = FakeRuntime(invalid_output)
    result = CandidateAuditStageHandler(runtime).execute(audit_context(attempt=1))

    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert output["status"] == "UNAVAILABLE"
    assert output["agent_status"] == "ABSTAIN"
    assert output["candidates"][0]["rank"] == 1
    assert output["reason_codes"] == ["CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID"]
    assert result["stage_control"]["disposition"] == "CONTINUE"
    assert len(runtime.tasks) == 1


def test_no_calculated_candidates_abstains_without_runtime_call() -> None:
    context = audit_context()
    dependency = deepcopy(context.dependency_results["CALCULATE_GATE_RANK"])
    dependency["calculate_gate_rank"]["candidates"] = []
    context.dependency_results = {"CALCULATE_GATE_RANK": dependency}
    runtime = FakeRuntime(audit_result)

    result = CandidateAuditStageHandler(runtime).execute(context)

    assert runtime.tasks == []
    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert output["status"] == "UNAVAILABLE"
    assert output["candidates"] == []


def test_task_construction_failure_never_leaks_raw_calculation_candidates() -> None:
    class RejectingTaskFactory:
        def build_candidate_audit(self, _context: StageContext) -> dict[str, Any]:
            raise ContractValidationError("synthetic task construction failure")

    runtime = FakeRuntime(audit_result)
    result = CandidateAuditStageHandler(
        runtime,
        task_factory=RejectingTaskFactory(),  # type: ignore[arg-type]
    ).execute(audit_context())

    output = result["candidate_audit"]
    assert isinstance(output, dict)
    assert runtime.tasks == []
    assert output["status"] == "UNAVAILABLE"
    assert output["reason_codes"] == ["CANDIDATE_AUDIT_INPUT_UNAVAILABLE"]
    assert "finance" not in output["candidates"][0]
    ContractRegistry().validate_candidate_result(output["candidates"][0])
