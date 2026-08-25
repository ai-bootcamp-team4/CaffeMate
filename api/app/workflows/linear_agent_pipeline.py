"""사용자는 복잡한 단계 제어 없이 실제 Researcher, Proposal, Auditor 결과를 받는다."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.agents.boundary import validate_agent_boundary
from app.agents.protocols import AgentRuntime
from app.agents.runtime import AgentRuntimeError
from app.agents.task_factory import AgentTaskFactory
from app.candidates.seed_registry import IndependentSeedRegistry
from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import CafeTypePreference, VentureState
from app.finance.case_facts import CaseFactResolution, PropertyContext
from app.finance.labor_benchmark import minimum_wage_references_from_mcp_results
from app.finance.labor_oncost import employer_social_insurance_references_from_mcp_results
from app.finance.property_benchmark import property_rent_benchmarks_from_mcp_results
from app.mcp.client import McpCallOutcome
from app.observability import tracer
from app.results.models import AuditStatus, ResultBundlePayload
from app.workflows.franchise_grounding import (
    franchise_disclosure_resolution,
    franchise_universe,
)
from app.workflows.independent_finance_snapshot import independent_finance_snapshot
from app.workflows.models import HeadFence, StageLease
from app.workflows.proposal_evidence_retrieval import (
    ProposalEvidenceRetriever,
    ProposalMcp,
    deduplicate_evidence,
)
from app.workflows.simple_proposal import SimpleProposalBuilder
from app.workflows.stage_context import StageContext

FATAL_AGENT_BOUNDARY_CODES = {
    "TASK_ECHO_MISMATCH",
    "FENCE_ECHO_MISMATCH",
    "CURRENT_HEAD_MISMATCH",
    "UNALLOCATED_OUTPUT_ID",
    "UNSUPPORTED_REFERENCE",
    "SEED_REFERENCE_MISMATCH",
    "BRAND_REFERENCE_MISMATCH",
}


class LinearMultiAgentProposalPipeline:
    """Run the three cognitive roles as one explicit, observable call chain."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        mcp: ProposalMcp,
        seed_registry: IndependentSeedRegistry,
        builder: SimpleProposalBuilder,
        task_factory: AgentTaskFactory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._mcp = mcp
        self._seeds = seed_registry
        self._builder = builder
        self._tasks = task_factory or AgentTaskFactory()
        self._contracts = ContractRegistry()
        self._now = now or (lambda: datetime.now(UTC))
        self._evidence_retriever = ProposalEvidenceRetriever(
            mcp=mcp,
            contracts=self._contracts,
            now=self._now,
        )

    def run(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        evidence_records: list[dict[str, Any]],
        property_context: PropertyContext | None = None,
        case_fact_resolution: CaseFactResolution | None = None,
    ) -> ResultBundlePayload:
        with tracer().start_as_current_span("caffemate.pipeline.first_proposal"):
            return self._run_traced(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
                evidence_records=evidence_records,
                property_context=property_context,
                case_fact_resolution=case_fact_resolution,
            )

    def _run_traced(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        evidence_records: list[dict[str, Any]],
        property_context: PropertyContext | None,
        case_fact_resolution: CaseFactResolution | None,
    ) -> ResultBundlePayload:
        outcomes = asyncio.run(
            self._evidence_retriever.retrieve(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
            )
        )
        # 공식 RAG 검색 결과도 다른 MCP 근거와 같은 EvidenceRecord 계약으로 전달한다.
        outcomes = [
            self._evidence_retriever.with_official_rag_evidence(outcome)
            for outcome in outcomes
        ]
        newly_retrieved_records = deduplicate_evidence(
            [
                record
                for outcome in outcomes
                for record in outcome.structured_content.get("evidence_records", [])
                if isinstance(record, dict)
            ]
        )
        evidence_task = self._tasks.build_evidence_assess(
            self._context(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
                stage_code="EVIDENCE_ASSESS",
                dependencies={
                    "EVIDENCE_RETRIEVAL": {
                        "evidence_retrieval": {
                            "claims": self._claims(state, outcomes),
                            "executed_actions": self._executed_actions(outcomes),
                        }
                    }
                },
            )
        )
        # 사용자 의도: 한 Agent의 불완전한 응답 때문에 이미 확보한 근거와
        # 다른 Agent의 실행을 모두 폐기하지 않는다. 변조만 즉시 중단한다.
        try:
            evidence_result = self._invoke_accepted(evidence_task, head)
        except ExternalExecutionUnavailableError:
            evidence_result = None
        evidence_payload = self._usable_payload(evidence_result)
        if evidence_payload is not None:
            retrieved_records = self._freeze_assessed_evidence(
                existing=evidence_records,
                retrieved=newly_retrieved_records,
                assessment_payload=evidence_payload,
            )
        else:
            retrieved_records = deduplicate_evidence(evidence_records)

        disclosure_resolution = franchise_disclosure_resolution(
            outcomes=outcomes,
            evidence_records=retrieved_records,
        )

        proposal_tasks = self._proposal_tasks(
            state=state,
            head=head,
            workflow_run_id=workflow_run_id,
            evidence_records=retrieved_records,
            outcomes=outcomes,
        )
        if not proposal_tasks:
            raise ContractValidationError("No eligible proposal input is available")
        with ThreadPoolExecutor(max_workers=len(proposal_tasks)) as executor:
            futures = [
                executor.submit(copy_context().run, self._invoke_accepted, task, head)
                for task in proposal_tasks
            ]
            proposal_results: list[dict[str, Any] | None] = []
            for future in futures:
                try:
                    proposal_results.append(future.result())
                except ExternalExecutionUnavailableError:
                    proposal_results.append(None)
        proposals = self._validated_proposals(proposal_tasks, proposal_results)
        if not proposals:
            raise ContractValidationError("No usable Agent proposal is available")

        bundle = self._builder.build(
            state=state,
            evidence_records=retrieved_records,
            property_context=property_context,
            case_fact_resolution=case_fact_resolution,
            franchise_disclosure_resolution=disclosure_resolution,
            property_rent_benchmarks=property_rent_benchmarks_from_mcp_results(
                [outcome.structured_content for outcome in outcomes]
            ),
            minimum_wage_references=minimum_wage_references_from_mcp_results(
                [outcome.structured_content for outcome in outcomes]
            ),
            employer_social_insurance_references=(
                employer_social_insurance_references_from_mcp_results(
                    [outcome.structured_content for outcome in outcomes]
                )
            ),
            agent_proposals=proposals,
            franchise_universe=franchise_universe(
                outcomes,
                evidence_records=retrieved_records,
                disclosure_resolution=disclosure_resolution,
            ),
        )
        audit_task = self._tasks.build_result_bundle_audit(
            project_id=state.project_id,
            workflow_run_id=workflow_run_id,
            stage_run_id=f"{workflow_run_id}:candidate-audit",
            head=head,
            candidates=bundle.candidates,
            evidence_records=retrieved_records,
        )
        try:
            audit_result = self._invoke_accepted(audit_task, head)
        except ExternalExecutionUnavailableError:
            audit_result = None
        return self._apply_audit(bundle, audit_result)

    def _proposal_tasks(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        evidence_records: list[dict[str, Any]],
        outcomes: list[McpCallOutcome],
    ) -> list[dict[str, Any]]:
        independent_tasks: list[dict[str, Any]] = []
        franchise_tasks: list[dict[str, Any]] = []
        preference = state.founder.cafe_type_preference
        minimum_wage_references = minimum_wage_references_from_mcp_results(
            [outcome.structured_content for outcome in outcomes]
        )
        employer_social_insurance_references = (
            employer_social_insurance_references_from_mcp_results(
                [outcome.structured_content for outcome in outcomes]
            )
        )
        if preference != CafeTypePreference.FRANCHISE_ONLY:
            seeds = self._seeds.select(state.founder)[:3]
            context = self._context(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
                stage_code="PROPOSE_INDEPENDENT",
                dependencies={
                    "INDEPENDENT_SEED": {
                        "independent_seed": {
                            "proposal_input": {
                                **self._common_proposal_input(state, evidence_records),
                                "model_seeds": [
                                    {
                                        "proposal_id": f"proposal:{seed.model_id}",
                                        "model_id": seed.model_id,
                                        "display_name": seed.display_name,
                                        "allowed_operation_modes": [
                                            value.value for value in seed.allowed_operation_modes
                                        ],
                                        "allowed_parameters": [
                                            value.model_dump(mode="json")
                                            for value in seed.allowed_parameters
                                        ],
                                        "finance_snapshot": independent_finance_snapshot(
                                            seed,
                                            minimum_wage_references,
                                            employer_social_insurance_references,
                                        ),
                                        "support_refs": seed.support_refs,
                                    }
                                    for seed in seeds
                                ],
                                "requested_candidate_count": len(seeds),
                            }
                        }
                    }
                },
            )
            independent_tasks = self._tasks.build_independent_proposal_tasks(context)
        universe = franchise_universe(
            outcomes,
            evidence_records=evidence_records,
            disclosure_resolution=franchise_disclosure_resolution(
                outcomes=outcomes,
                evidence_records=evidence_records,
            ),
        )
        if preference != CafeTypePreference.INDEPENDENT_ONLY and universe:
            context = self._context(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
                stage_code="PROPOSE_FRANCHISE",
                dependencies={
                    "FRANCHISE_ELIGIBILITY": {
                        "franchise_eligibility": {
                            "proposal_input": {
                                **self._common_proposal_input(state, evidence_records),
                                "franchise_universe": universe[:3],
                                "requested_candidate_count": min(3, len(universe)),
                            }
                        }
                    }
                },
            )
            franchise_tasks = self._tasks.build_franchise_proposal_tasks(context)

        if preference == CafeTypePreference.INDEPENDENT_ONLY:
            return independent_tasks[:3]
        if preference == CafeTypePreference.FRANCHISE_ONLY:
            return franchise_tasks[:3]
        if independent_tasks and franchise_tasks:
            return (independent_tasks[:1] + franchise_tasks[:2])[:3]
        return (independent_tasks + franchise_tasks)[:3]

    def _common_proposal_input(
        self,
        state: VentureState,
        evidence_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        projection = AgentTaskFactory._state_projection(state)
        return {
            "founder": projection["founder"],
            "area": projection["area"],
            "evidence_records": evidence_records,
        }

    def _context(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        stage_code: str,
        dependencies: dict[str, dict[str, Any]],
    ) -> StageContext:
        return StageContext(
            lease=StageLease(
                workflow_run_id=workflow_run_id,
                stage_run_id=f"{workflow_run_id}:{stage_code.lower()}",
                stage_code=stage_code,
                input_digest="0" * 64,
                lease_token="linear-call",
                lease_expires_at=self._now() + timedelta(minutes=5),
                attempt=1,
                head=head,
            ),
            project_id=state.project_id,
            state=state,
            dependency_results=dependencies,
        )

    def _invoke_accepted(
        self,
        task: dict[str, Any],
        current_head: HeadFence,
    ) -> dict[str, Any]:
        result = self._runtime.invoke(task)
        validation = validate_agent_boundary(
            task=task,
            result=result,
            current_head=current_head,
            contracts=self._contracts,
        )
        if validation.accepted:
            return result
        codes = {error.code for error in validation.errors}
        if codes & FATAL_AGENT_BOUNDARY_CODES:
            raise ContractValidationError(
                f"Agent crossed the authority boundary: {', '.join(sorted(codes))}"
            )
        raise AgentRuntimeError("AGENT_RESULT_REJECTED")

    @staticmethod
    def _usable_payload(
        result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if (
            result is not None
            and result.get("status") in {"COMPLETE", "NEEDS_EVIDENCE"}
            and isinstance(result.get("payload"), dict)
        ):
            return cast(dict[str, Any], result["payload"])
        return None

    @staticmethod
    def _validated_proposals(
        tasks: list[dict[str, Any]],
        results: list[dict[str, Any] | None],
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for _task, result in zip(tasks, results, strict=True):
            payload = LinearMultiAgentProposalPipeline._usable_payload(result)
            if payload is None:
                continue
            values = payload.get("candidate_proposals")
            if not isinstance(values, list):
                continue
            proposals.extend(deepcopy(value) for value in values if isinstance(value, dict))
        return proposals

    def _claims(
        self,
        state: VentureState,
        outcomes: list[McpCallOutcome],
    ) -> list[dict[str, Any]]:
        claim_ids = self._claim_ids(outcomes)
        return [
            {
                "claim_id": claim_id,
                "claim_type": {
                    "get_area_profile": "AREA_DEMAND_SIGNALS",
                    "get_property_reference": "PROPERTY_RENT_REFERENCE",
                    "get_cost_reference": "LABOR_COST_REFERENCE",
                    "search_cafe_observations": "AREA_DEMAND_SIGNALS",
                    "list_franchise_universe": "FRANCHISE_UNIVERSE_ELIGIBILITY",
                    "get_franchise_disclosure": "FRANCHISE_DISCLOSURE_FACT",
                    "retrieve_official_documents": "OFFICIAL_STARTUP_GUIDANCE",
                }[outcome.tool_name],
                "materiality": "HIGH",
                "geographic_scope": self._claim_geographic_scope(
                    state,
                    outcome.tool_name,
                ),
                "required_freshness": "P365D",
            }
            for outcome, claim_id in zip(outcomes, claim_ids, strict=True)
        ]

    @staticmethod
    def _claim_geographic_scope(state: VentureState, tool_name: str) -> dict[str, Any]:
        if tool_name in {"get_area_profile", "search_cafe_observations"}:
            return {
                "scope_type": "ADMINISTRATIVE_AREA",
                "scope_id": state.area.administrative_code,
                "boundary_version": state.area.boundary_version,
            }
        if tool_name == "get_property_reference" and state.area.administrative_code:
            return {
                "scope_type": "REGION",
                "scope_id": state.area.administrative_code[:2],
                "boundary_version": None,
            }
        return {
            "scope_type": "NATIONAL",
            "scope_id": "KR",
            "boundary_version": None,
        }

    @staticmethod
    def _executed_actions(
        outcomes: list[McpCallOutcome],
    ) -> list[dict[str, Any]]:
        claim_ids = LinearMultiAgentProposalPipeline._claim_ids(outcomes)
        return [
            {
                "action_id": claim_id.replace("claim:", "action:", 1),
                "claim_id": claim_id,
                "polarity": "SUPPORT",
                "tool_name": outcome.tool_name,
                "request_id": outcome.request_id,
                "structured_result": outcome.structured_content,
            }
            for outcome, claim_id in zip(outcomes, claim_ids, strict=True)
        ]

    @staticmethod
    def _claim_ids(outcomes: list[McpCallOutcome]) -> list[str]:
        totals: dict[str, int] = {}
        for outcome in outcomes:
            totals[outcome.tool_name] = totals.get(outcome.tool_name, 0) + 1
        observed: dict[str, int] = {}
        claim_ids: list[str] = []
        for outcome in outcomes:
            tool_name = outcome.tool_name
            observed[tool_name] = observed.get(tool_name, 0) + 1
            suffix = f":{observed[tool_name]}" if totals[tool_name] > 1 else ""
            claim_ids.append(f"claim:{tool_name}{suffix}")
        return claim_ids

    def _freeze_assessed_evidence(
        self,
        *,
        existing: list[dict[str, Any]],
        retrieved: list[dict[str, Any]],
        assessment_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        assessments = assessment_payload.get("assessments")
        if not isinstance(assessments, list):
            raise ContractValidationError("Evidence Researcher assessments are invalid")
        accepted_refs = {
            assessment.get("candidate_ref")
            for assessment in assessments
            if isinstance(assessment, dict)
            and assessment.get("relation") in {"SUPPORTS", "CONTRADICTS", "AMBIGUOUS"}
            and assessment.get("scope_status") != "MISMATCH"
            and assessment.get("date_status") != "MISMATCH"
            and assessment.get("anchor_status") != "INVALID"
            and assessment.get("authority_status") != "INSUFFICIENT"
        }
        frozen_retrieved = [
            record for record in retrieved if record.get("evidence_id") in accepted_refs
        ]
        return deduplicate_evidence(existing + frozen_retrieved)

    @staticmethod
    def _apply_audit(
        bundle: ResultBundlePayload,
        result: dict[str, Any] | None,
    ) -> ResultBundlePayload:
        if result is None or result.get("status") in {"ABSTAIN", "INVALID"}:
            return bundle.model_copy(update={"audit_status": AuditStatus.UNAVAILABLE})
        if result.get("status") in {"NEEDS_EVIDENCE", "NEEDS_HUMAN"}:
            return bundle.model_copy(update={"audit_status": AuditStatus.REQUIRES_HUMAN})
        if result.get("status") != "COMPLETE" or not isinstance(
            result.get("payload"), dict
        ):
            return bundle.model_copy(update={"audit_status": AuditStatus.UNAVAILABLE})
        payload = result["payload"]
        audits = payload.get("candidate_audits")
        expected_ids = {candidate["candidate_id"] for candidate in bundle.candidates}
        actual_ids = {
            audit.get("candidate_id")
            for audit in audits
            if isinstance(audit, dict)
        } if isinstance(audits, list) else set()
        needs_human = (
            actual_ids != expected_ids
            or bool(payload.get("global_findings"))
            or any(audit.get("status") != "PASS" for audit in audits)
        )
        return bundle.model_copy(
            update={
                "audit_status": (
                    AuditStatus.REQUIRES_HUMAN if needs_human else AuditStatus.PASSED
                )
            }
        )
