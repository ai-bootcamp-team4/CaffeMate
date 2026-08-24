"""사용자는 복잡한 단계 제어 없이 실제 Researcher, Proposal, Auditor 결과를 받는다."""

import asyncio
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import rfc8785

from app.agents.protocols import AgentRuntime
from app.agents.task_factory import AgentTaskFactory
from app.candidates.seed_registry import IndependentSeedDefinition, IndependentSeedRegistry
from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import CafeTypePreference, VentureState
from app.finance.calculator import calculate_finance
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)
from app.mcp.client import McpCallOutcome
from app.results.models import AuditStatus, ResultBundlePayload
from app.workflows.models import HeadFence, StageLease
from app.workflows.simple_proposal import (
    PropertyCostOverride,
    SimpleProposalBuilder,
)
from app.workflows.stage_context import StageContext


class ProposalMcp(Protocol):
    async def call_tool(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        tool_name: str,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> McpCallOutcome: ...


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

    def run(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        evidence_records: list[dict[str, Any]],
        property_cost_override: PropertyCostOverride | None = None,
    ) -> ResultBundlePayload:
        outcomes = asyncio.run(
            self._retrieve_evidence(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
            )
        )
        newly_retrieved_records = self._deduplicate_evidence(
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
        evidence_result = self._invoke_complete(evidence_task)
        retrieved_records = self._freeze_assessed_evidence(
            existing=evidence_records,
            retrieved=newly_retrieved_records,
            assessment_payload=evidence_result["payload"],
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
            proposal_results = list(executor.map(self._invoke_complete, proposal_tasks))
        proposals = self._validated_proposals(proposal_tasks, proposal_results)

        bundle = self._builder.build(
            state=state,
            evidence_records=retrieved_records,
            property_cost_override=property_cost_override,
            agent_proposals=proposals,
            franchise_universe=self._franchise_universe(
                outcomes,
                evidence_records=retrieved_records,
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
        audit_result = self._invoke_complete(audit_task)
        return self._apply_audit(bundle, audit_result)

    async def _retrieve_evidence(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
    ) -> list[McpCallOutcome]:
        as_of = self._now().date().isoformat()
        calls: list[tuple[str, dict[str, Any]]] = []
        if state.area.administrative_code and state.area.boundary_version:
            calls.append(
                (
                    "get_area_profile",
                    {
                        "administrative_code": state.area.administrative_code,
                        "boundary_version": state.area.boundary_version,
                        "as_of": as_of,
                    },
                )
            )
            calls.append(
                (
                    "search_cafe_observations",
                    {
                        "administrative_code": state.area.administrative_code,
                        "boundary_version": state.area.boundary_version,
                        "as_of": as_of,
                        "metrics": [
                            "CAFE_COUNT",
                            "OPEN_COUNT",
                            "CLOSE_COUNT",
                            "CLOSURE_RATE",
                            "ESTIMATED_SALES",
                            "FOOT_TRAFFIC",
                            "RESIDENT_POPULATION",
                            "WORKER_POPULATION",
                            "AGE_DISTRIBUTION",
                        ],
                    },
                )
            )
        if state.founder.cafe_type_preference != CafeTypePreference.INDEPENDENT_ONLY:
            calls.append(
                (
                    "list_franchise_universe",
                    {"business_category": "CAFE", "as_of": as_of},
                )
            )
        calls.append(
            (
                "retrieve_official_documents",
                {
                    "query": (
                        f"{state.founder.target_area_input} 카페 창업 비용 인허가 "
                        "프랜차이즈 정보공개서"
                    ),
                    # 공식 RAG에는 현재 정부 안내 자료만 적재되어 있습니다.
                    # 상권 수치와 가맹 후보는 각각의 구조화 MCP 도구에서 조회합니다.
                    "source_families": ["GOVERNMENT_GUIDE"],
                    "as_of": as_of,
                    "limit": 10,
                },
            )
        )
        return list(
            await asyncio.gather(
                *[
                    self._mcp.call_tool(
                        venture_project_id=state.project_id,
                        workflow_run_id=workflow_run_id,
                        head=head,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                    for tool_name, arguments in calls
                ]
            )
        )

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
                                        "finance_snapshot": self._finance_snapshot(seed),
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
        universe = self._franchise_universe(
            outcomes,
            evidence_records=evidence_records,
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

    @staticmethod
    def _finance_snapshot(seed: IndependentSeedDefinition) -> dict[str, Any]:
        """Agent가 등록 비용을 읽되 권위 계산을 다시 만들지 않게 한다."""

        profile = seed.finance_profile
        if profile is None:
            raise ContractValidationError(
                f"Independent model has no registered finance profile: {seed.model_id}"
            )
        initial_lines = [
            CostLine(
                field_id=category.value,
                category=category,
                amount=profile.cost_ranges[category],
                provenance=ValueProvenance.ASSUMPTION,
            )
            for category in sorted(INITIAL_COST_CATEGORIES, key=lambda value: value.value)
            if category != CostCategory.FRANCHISE_INITIAL_FEES
        ]
        initial_lines.append(
            CostLine(
                field_id=CostCategory.FRANCHISE_INITIAL_FEES.value,
                category=CostCategory.FRANCHISE_INITIAL_FEES,
                amount=MoneyRange(low=0, base=0, high=0),
                provenance=ValueProvenance.DERIVED,
            )
        )
        monthly_lines = [
            CostLine(
                field_id=category.value,
                category=category,
                amount=profile.cost_ranges[category],
                provenance=ValueProvenance.ASSUMPTION,
            )
            for category in sorted(
                MONTHLY_FIXED_COST_CATEGORIES,
                key=lambda value: value.value,
            )
        ]
        finance = calculate_finance(
            FinanceInput(
                initial_cost_lines=initial_lines,
                monthly_fixed_cost_lines=monthly_lines,
                contribution_margin_bps=profile.contribution_margin_bps,
                operating_days_per_month=profile.operating_days_per_month,
                average_ticket_krw=profile.average_ticket_krw,
            )
        )
        return {
            "initial_cash_krw": finance.initial_cash.model_dump(mode="json"),
            "monthly_fixed_cost_krw": finance.monthly_fixed_cost.model_dump(mode="json"),
            "contribution_margin_bps": profile.contribution_margin_bps,
            "operating_days_per_month": profile.operating_days_per_month,
            "average_ticket_krw": profile.average_ticket_krw,
            "break_even_monthly_sales_krw": finance.break_even_monthly_sales_krw,
            "required_daily_orders": (
                float(finance.required_daily_orders)
                if finance.required_daily_orders is not None
                else None
            ),
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

    def _invoke_complete(self, task: dict[str, Any]) -> dict[str, Any]:
        result = self._runtime.invoke(task)
        self._contracts.validate_agent_task_result(result)
        for field in (
            "task_id",
            "agent_name",
            "task_type",
            "workflow_run_id",
            "stage_run_id",
            "venture_project_id",
            "input_digest",
            "output_schema_id",
        ):
            if result.get(field) != task.get(field):
                raise ContractValidationError(f"Agent result changed {field}")
        if result.get("head_fence_seen") != task.get("head_fence"):
            raise ContractValidationError("Agent result changed the State head")
        if result.get("status") != "COMPLETE" or not isinstance(
            result.get("payload"), dict
        ):
            raise ContractValidationError("Agent did not complete the requested role")
        return result

    @staticmethod
    def _validated_proposals(
        tasks: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for task, result in zip(tasks, results, strict=True):
            values = result["payload"].get("candidate_proposals")
            if not isinstance(values, list) or len(values) != 1:
                raise ContractValidationError("Proposal Agent must return one candidate")
            proposal = values[0]
            if not isinstance(proposal, dict):
                raise ContractValidationError("Proposal Agent candidate is invalid")
            collection = (
                task["payload"].get("model_seeds")
                or task["payload"].get("franchise_universe")
            )
            allowed = {
                value.get("model_id") or value.get("brand_id")
                for value in collection
                if isinstance(value, dict)
            }
            if proposal.get("seed_or_brand_id") not in allowed:
                raise ContractValidationError("Proposal Agent invented a candidate")
            proposals.append(deepcopy(proposal))
        return proposals

    @staticmethod
    def _franchise_universe(
        outcomes: list[McpCallOutcome],
        *,
        evidence_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        universe: list[dict[str, Any]] = []
        accepted_evidence_ids = {
            record.get("evidence_id")
            for record in evidence_records
            if isinstance(record.get("evidence_id"), str)
        }
        for outcome in outcomes:
            if outcome.tool_name != "list_franchise_universe":
                continue
            missing = [
                value
                for value in outcome.structured_content.get("missing_fields", [])
                if isinstance(value, str)
            ]
            for item in outcome.structured_content.get("data", []):
                if not isinstance(item, dict) or item.get(
                    "individual_franchise_eligibility"
                ) != "VERIFIED":
                    continue
                evidence_id = item.get("eligibility_evidence_id")
                if (
                    not isinstance(evidence_id, str)
                    or evidence_id not in accepted_evidence_ids
                ):
                    continue
                finance_profile = deepcopy(item.get("finance_profile"))
                if not isinstance(finance_profile, dict):
                    continue
                finance_refs = {
                    ref
                    for ref in finance_profile.get("evidence_refs", [])
                    if isinstance(ref, str)
                }
                if not finance_refs.issubset(accepted_evidence_ids):
                    finance_profile.update(
                        {
                            "coverage": "UNKNOWN",
                            "value_kind": "UNKNOWN",
                            "known_initial_cost_range_krw": None,
                            "evidence_refs": [],
                            "missing_costs": sorted(
                                set(finance_profile.get("missing_costs", []))
                                | {"TOTAL_INITIAL_COST"}
                            ),
                        }
                    )
                universe.append(
                    {
                        "proposal_id": f"proposal:{item['brand_id']}",
                        "brand_id": item["brand_id"],
                        "display_name": item["display_name"],
                        "individual_franchise_eligibility": "VERIFIED",
                        "evidence_refs": [evidence_id],
                        "finance_profile": finance_profile,
                        "missing_fields": sorted(set(missing)),
                    }
                )
        return sorted(
            universe,
            key=lambda item: (
                item.get("finance_profile", {}).get("known_initial_cost_range_krw")
                is None,
                item.get("finance_profile", {})
                .get("known_initial_cost_range_krw", {})
                .get("base", 0),
                item["brand_id"],
            ),
        )

    def _claims(
        self,
        state: VentureState,
        outcomes: list[McpCallOutcome],
    ) -> list[dict[str, Any]]:
        return [
            {
                "claim_id": f"claim:{outcome.tool_name}",
                "claim_type": {
                    "get_area_profile": "AREA_DEMAND_SIGNALS",
                    "search_cafe_observations": "AREA_DEMAND_SIGNALS",
                    "list_franchise_universe": "FRANCHISE_UNIVERSE_ELIGIBILITY",
                    "retrieve_official_documents": "OFFICIAL_STARTUP_GUIDANCE",
                }[outcome.tool_name],
                "materiality": "HIGH",
                "geographic_scope": (
                    {
                        "scope_type": "ADMINISTRATIVE_AREA",
                        "scope_id": state.area.administrative_code,
                        "boundary_version": state.area.boundary_version,
                    }
                    if outcome.tool_name
                    in {"get_area_profile", "search_cafe_observations"}
                    else {
                        "scope_type": "NATIONAL",
                        "scope_id": "KR",
                        "boundary_version": None,
                    }
                ),
                "required_freshness": "P365D",
            }
            for outcome in outcomes
        ]

    @staticmethod
    def _executed_actions(
        outcomes: list[McpCallOutcome],
    ) -> list[dict[str, Any]]:
        return [
            {
                "action_id": f"action:{outcome.tool_name}",
                "claim_id": f"claim:{outcome.tool_name}",
                "polarity": "SUPPORT",
                "tool_name": outcome.tool_name,
                "request_id": outcome.request_id,
                "structured_result": outcome.structured_content,
            }
            for outcome in outcomes
        ]

    @staticmethod
    def _deduplicate_evidence(
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            evidence_id = record.get("evidence_id")
            if isinstance(evidence_id, str):
                by_id[evidence_id] = record
        return [by_id[key] for key in sorted(by_id)]

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
        return self._deduplicate_evidence(existing + frozen_retrieved)

    @staticmethod
    def _apply_audit(
        bundle: ResultBundlePayload,
        result: dict[str, Any],
    ) -> ResultBundlePayload:
        payload = result["payload"]
        audits = payload.get("candidate_audits")
        expected_ids = {candidate["candidate_id"] for candidate in bundle.candidates}
        actual_ids = {
            audit.get("candidate_id")
            for audit in audits
            if isinstance(audit, dict)
        } if isinstance(audits, list) else set()
        if actual_ids != expected_ids:
            raise ContractValidationError("Candidate Auditor coverage is incomplete")
        needs_human = bool(payload.get("global_findings")) or any(
            audit.get("status") != "PASS" for audit in audits
        )
        return bundle.model_copy(
            update={
                "audit_status": (
                    AuditStatus.REQUIRES_HUMAN if needs_human else AuditStatus.PASSED
                )
            }
        )


def content_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"
