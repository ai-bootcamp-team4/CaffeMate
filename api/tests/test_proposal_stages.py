from datetime import UTC, datetime
from typing import Any

import pytest

from app.agents.task_factory import AgentTaskFactory, compute_agent_input_digest
from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import CafeTypePreference
from app.workflows.candidate_inputs import (
    FranchiseEligibilityStageHandler,
    IndependentSeedStageHandler,
)
from app.workflows.proposal import ProposalStageHandler
from app.workflows.stage_context import StageContext
from tests.test_candidate_input_stages import candidate_context


class FakeRuntime:
    def __init__(self, result_factory: Any) -> None:
        self._result_factory = result_factory
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        return self._result_factory(task)


def proposal_context(task_type: str, *, empty: bool = False) -> StageContext:
    selected_registry = IndependentSeedRegistry.load_default()
    context = candidate_context(seed_registry_id=selected_registry.registry_id)
    if task_type == "PROPOSE_INDEPENDENT":
        prepared = IndependentSeedStageHandler(selected_registry).execute(context)
        if empty:
            source = prepared["independent_seed"]
            assert isinstance(source, dict)
            proposal_input = source["proposal_input"]
            assert isinstance(proposal_input, dict)
            proposal_input["model_seeds"] = []
            proposal_input["requested_candidate_count"] = 0
            source["reason_codes"] = ["NO_ELIGIBLE_INDEPENDENT_SEED"]
        dependency_results = {"INDEPENDENT_SEED": prepared}
    else:
        freeze = context.dependency_results["EVIDENCE_FREEZE"]["evidence_freeze"]
        freeze["franchise_universe"] = [
            {
                "brand_id": "brand-1",
                "display_name": "검증 브랜드",
                "individual_franchise_eligibility": "VERIFIED",
                "eligibility_evidence_id": "ev-franchise-verified",
                "disclosure_status": "MISSING",
            }
        ]
        prepared = FranchiseEligibilityStageHandler().execute(context)
        if empty:
            source = prepared["franchise_eligibility"]
            assert isinstance(source, dict)
            proposal_input = source["proposal_input"]
            assert isinstance(proposal_input, dict)
            proposal_input["franchise_universe"] = []
            proposal_input["requested_candidate_count"] = 0
            source["reason_codes"] = ["NO_VERIFIED_FRANCHISE_CANDIDATE"]
        dependency_results = {"FRANCHISE_ELIGIBILITY": prepared}
    return StageContext(
        lease=context.lease.model_copy(
            update={
                "stage_run_id": f"stage-{task_type.lower()}",
                "stage_code": task_type,
            }
        ),
        project_id=context.project_id,
        state=context.state,
        dependency_results=dependency_results,
    )


def proposal_result(
    task: dict[str, Any],
    *,
    status: str = "COMPLETE",
) -> dict[str, Any]:
    task_payload = task["payload"]
    if task["task_type"] == "PROPOSE_INDEPENDENT":
        source = task_payload["model_seeds"][0]
        proposal = {
            "proposal_id": source["proposal_id"],
            "case_type": "INDEPENDENT",
            "display_name": source["display_name"],
            "seed_or_brand_id": source["model_id"],
            "adjusted_parameters": [],
            "claim_refs": [],
            "evidence_refs": [],
            "assumption_refs": source["support_refs"],
            "missing_fields": ["rent"],
            "warnings": ["임대 조건 확인 필요"],
        }
        evidence_refs: list[str] = []
    else:
        source = task_payload["franchise_universe"][0]
        proposal = {
            "proposal_id": source["proposal_id"],
            "case_type": "FRANCHISE",
            "display_name": source["display_name"],
            "seed_or_brand_id": source["brand_id"],
            "adjusted_parameters": [],
            "claim_refs": [],
            "evidence_refs": source["evidence_refs"],
            "assumption_refs": [],
            "missing_fields": source["missing_fields"],
            "warnings": ["본사 출점 가능 여부 확인 필요"],
        }
        evidence_refs = source["evidence_refs"]
    payload = {"candidate_proposals": [proposal]} if status != "ABSTAIN" else None
    reason_codes = [] if status == "COMPLETE" else [f"PROPOSAL_{status}"]
    missing_claim_ids = (
        [task_payload["claim_id_pool"][0]] if status == "NEEDS_EVIDENCE" else []
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
        "head_fence_seen": task["head_fence"],
        "input_digest": task["input_digest"],
        "output_schema_id": task["output_schema_id"],
        "status": status,
        "payload": payload,
        "evidence_refs": evidence_refs,
        "missing_claim_ids": missing_claim_ids,
        "reason_codes": reason_codes,
        "warnings": [],
    }


@pytest.mark.parametrize(
    ("task_type", "builder_name", "source_key"),
    [
        ("PROPOSE_INDEPENDENT", "build_independent_proposal", "model_seeds"),
        ("PROPOSE_FRANCHISE", "build_franchise_proposal", "franchise_universe"),
    ],
)
def test_proposal_task_is_pinned_schema_valid_and_has_no_tools(
    task_type: str,
    builder_name: str,
    source_key: str,
) -> None:
    context = proposal_context(task_type)
    factory = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-proposal",
    )

    task = getattr(factory, builder_name)(context)

    assert task["task_type"] == task_type
    assert task["deadline_at"] == "2026-08-21T10:01:00Z"
    assert task["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"
    assert task["available_tool_catalog"] == []
    assert task["tool_manifest_digest"] is None
    assert task["payload"][source_key]
    assert task["input_digest"] == compute_agent_input_digest(task)


@pytest.mark.parametrize("task_type", ["PROPOSE_INDEPENDENT", "PROPOSE_FRANCHISE"])
def test_complete_proposal_is_boundary_validated_and_normalized(task_type: str) -> None:
    runtime = FakeRuntime(proposal_result)
    handler = (
        ProposalStageHandler.independent(runtime)
        if task_type == "PROPOSE_INDEPENDENT"
        else ProposalStageHandler.franchise(runtime)
    )

    result = handler.execute(proposal_context(task_type))

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    output_key = (
        "independent_proposal"
        if task_type == "PROPOSE_INDEPENDENT"
        else "franchise_proposal"
    )
    output = result[output_key]
    assert isinstance(output, dict)
    assert output["status"] == "COMPLETE"
    assert len(output["candidate_proposals"]) == 1
    assert output["agent_trace"]["input_digest"] == runtime.tasks[0]["input_digest"]


@pytest.mark.parametrize("task_type", ["PROPOSE_INDEPENDENT", "PROPOSE_FRANCHISE"])
def test_empty_branch_abstains_without_calling_runtime(task_type: str) -> None:
    runtime = FakeRuntime(proposal_result)
    handler = (
        ProposalStageHandler.independent(runtime)
        if task_type == "PROPOSE_INDEPENDENT"
        else ProposalStageHandler.franchise(runtime)
    )

    result = handler.execute(proposal_context(task_type, empty=True))

    assert runtime.tasks == []
    output_key = (
        "independent_proposal"
        if task_type == "PROPOSE_INDEPENDENT"
        else "franchise_proposal"
    )
    output = result[output_key]
    assert isinstance(output, dict)
    assert output["status"] == "ABSTAIN"
    assert output["candidate_proposals"] == []
    assert result["stage_control"]["disposition"] == "CONTINUE"


def test_needs_evidence_preserves_supported_conditional_proposal() -> None:
    runtime = FakeRuntime(lambda task: proposal_result(task, status="NEEDS_EVIDENCE"))

    result = ProposalStageHandler.franchise(runtime).execute(
        proposal_context("PROPOSE_FRANCHISE")
    )

    output = result["franchise_proposal"]
    assert isinstance(output, dict)
    assert output["status"] == "NEEDS_EVIDENCE"
    assert len(output["candidate_proposals"]) == 1
    assert output["missing_claim_ids"] == ["claim:MORE_EVIDENCE"]


def test_needs_human_waits_only_when_no_other_branch_can_continue() -> None:
    runtime = FakeRuntime(lambda task: proposal_result(task, status="NEEDS_HUMAN"))
    both_context = proposal_context("PROPOSE_INDEPENDENT")
    only_context = proposal_context("PROPOSE_INDEPENDENT")
    only_context.state.founder.cafe_type_preference = CafeTypePreference.INDEPENDENT_ONLY
    handler = ProposalStageHandler.independent(runtime)

    both = handler.execute(both_context)
    only = handler.execute(only_context)

    assert both["stage_control"]["disposition"] == "CONTINUE"
    assert both["independent_proposal"]["candidate_proposals"] == []
    assert only["stage_control"]["disposition"] == "WAITING_FOR_HUMAN"


def test_boundary_rejection_stops_proposal_before_stage_output() -> None:
    def invented_brand(task: dict[str, Any]) -> dict[str, Any]:
        result = proposal_result(task)
        result["payload"]["candidate_proposals"][0]["seed_or_brand_id"] = "invented-brand"
        return result

    runtime = FakeRuntime(invented_brand)

    with pytest.raises(ContractValidationError, match="BRAND_REFERENCE_MISMATCH"):
        ProposalStageHandler.franchise(runtime).execute(
            proposal_context("PROPOSE_FRANCHISE")
        )


def test_one_branch_runtime_failure_becomes_local_abstention() -> None:
    def unavailable(_task: dict[str, Any]) -> dict[str, Any]:
        raise ExternalExecutionUnavailableError("runtime unavailable")

    runtime = FakeRuntime(unavailable)
    context = proposal_context("PROPOSE_INDEPENDENT")
    context.lease = context.lease.model_copy(update={"attempt": 3})

    result = ProposalStageHandler.independent(runtime).execute(context)

    assert result["stage_control"] == {
        "disposition": "CONTINUE",
        "reason_codes": ["PROPOSAL_RUNTIME_UNAVAILABLE"],
    }
    assert result["independent_proposal"]["candidate_proposals"] == []


def test_runtime_failure_is_retried_before_branch_abstains() -> None:
    def unavailable(_task: dict[str, Any]) -> dict[str, Any]:
        raise ExternalExecutionUnavailableError("runtime unavailable")

    runtime = FakeRuntime(unavailable)

    with pytest.raises(ExternalExecutionUnavailableError):
        ProposalStageHandler.independent(runtime).execute(
            proposal_context("PROPOSE_INDEPENDENT")
        )


def test_agent_cannot_return_rank_or_gate_fields() -> None:
    def authoritative_decision(task: dict[str, Any]) -> dict[str, Any]:
        result = proposal_result(task)
        result["payload"]["candidate_proposals"][0]["rank"] = 1
        return result

    runtime = FakeRuntime(authoritative_decision)

    with pytest.raises(ContractValidationError, match="CONTRACT_SCHEMA_INVALID"):
        ProposalStageHandler.independent(runtime).execute(
            proposal_context("PROPOSE_INDEPENDENT")
        )
