"""사용자는 복잡한 단계 제어 없이 실제 Researcher, Proposal, Auditor 결과를 받는다."""

import asyncio
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import rfc8785

from app.agents.boundary import validate_agent_boundary
from app.agents.protocols import AgentRuntime
from app.agents.runtime import AgentRuntimeError
from app.agents.task_factory import AgentTaskFactory
from app.candidates.seed_registry import IndependentSeedDefinition, IndependentSeedRegistry
from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import CafeTypePreference, VentureState
from app.finance.calculator import calculate_finance
from app.finance.case_facts import CaseFactResolution, PropertyContext
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)
from app.finance.property_benchmark import property_rent_benchmarks_from_mcp_results
from app.mcp.client import McpCallOutcome, McpClientError
from app.observability import tracer
from app.results.models import AuditStatus, ResultBundlePayload
from app.workflows.models import HeadFence, StageLease
from app.workflows.simple_proposal import SimpleProposalBuilder
from app.workflows.stage_context import StageContext

FRANCHISE_RAG_QUERIES: tuple[str, ...] = (
    "컴포즈커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "컴포즈커피 공식 창업 비용 10평 15평 포함 제외 항목",
    "메가MGC커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "메가MGC커피 공식 창업 비용 10평 포함 제외 항목",
    "이디야커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "이디야커피 공식 창업 비용 가맹비 월 로열티 포함 제외 항목",
)
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
FATAL_AGENT_BOUNDARY_CODES = {
    "TASK_ECHO_MISMATCH",
    "FENCE_ECHO_MISMATCH",
    "CURRENT_HEAD_MISMATCH",
    "UNALLOCATED_OUTPUT_ID",
    "UNSUPPORTED_REFERENCE",
    "SEED_REFERENCE_MISMATCH",
    "BRAND_REFERENCE_MISMATCH",
}


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
            self._retrieve_evidence(
                state=state,
                head=head,
                workflow_run_id=workflow_run_id,
            )
        )
        # 공식 RAG 검색 결과도 다른 MCP 근거와 같은 EvidenceRecord 계약으로 전달한다.
        outcomes = [self._with_official_rag_evidence(outcome) for outcome in outcomes]
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
            retrieved_records = self._deduplicate_evidence(evidence_records)

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
            property_rent_benchmarks=property_rent_benchmarks_from_mcp_results(
                [outcome.structured_content for outcome in outcomes]
            ),
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
        try:
            audit_result = self._invoke_accepted(audit_task, head)
        except ExternalExecutionUnavailableError:
            audit_result = None
        return self._apply_audit(bundle, audit_result)

    async def _retrieve_evidence(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
    ) -> list[McpCallOutcome]:
        # 사용자 의도: 국내 창업 자료의 기준일은 서버의 UTC 날짜가 아니라
        # 실제 서비스 지역인 서울의 달력 날짜와 일치해야 한다.
        as_of = self._now().astimezone(SEOUL_TIMEZONE).date().isoformat()
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
                    "get_property_reference",
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
        if state.founder.cafe_type_preference != CafeTypePreference.INDEPENDENT_ONLY:
            calls.extend(
                (
                    "retrieve_official_documents",
                    {
                        "query": query,
                        "source_families": ["COMPANY_OFFICIAL_FRANCHISE"],
                        "as_of": as_of,
                        "limit": 3,
                    },
                )
                for query in FRANCHISE_RAG_QUERIES
            )
        # 사용자 의도: 한 자료원의 MCP 오류가 성공한 조회와 세 Agent 역할까지
        # 함께 취소해서는 안 된다. 오류는 근거 없는 ERROR action으로 보존한다.
        return await asyncio.gather(
            *[
                self._retrieve_one_evidence_call(
                    call_index=index,
                    state=state,
                    head=head,
                    workflow_run_id=workflow_run_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
                for index, (tool_name, arguments) in enumerate(calls)
            ]
        )

    async def _retrieve_one_evidence_call(
        self,
        *,
        call_index: int,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpCallOutcome:
        try:
            return await self._mcp.call_tool(
                venture_project_id=state.project_id,
                workflow_run_id=workflow_run_id,
                head=head,
                tool_name=tool_name,
                arguments=arguments,
            )
        except McpClientError as error:
            request_digest = hashlib.sha256(
                f"{workflow_run_id}:{call_index}:{tool_name}".encode()
            ).hexdigest()[:20]
            request_id = f"failed-mcp-{request_digest}"
            tool_version = self._contracts.mcp_tool_version(tool_name)
            content = {
                "schema_version": "1.0.0",
                "request_id": request_id,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "status": "ERROR",
                "project_id": state.project_id,
                "evidence_records": [],
                "missing_fields": [f"{tool_name}_result"],
                "conflicts": [],
                "source_trace": [],
                "error_codes": [error.mcp_code],
                "observed_at": self._now().isoformat().replace("+00:00", "Z"),
                "data": [],
            }
            self._contracts.validate_mcp_tool_result(tool_name, content)
            return McpCallOutcome(
                request_id=request_id,
                tool_name=tool_name,
                tool_version=tool_version,
                status="ERROR",
                is_complete=False,
                structured_content=content,
            )

    def _with_official_rag_evidence(
        self,
        outcome: McpCallOutcome,
    ) -> McpCallOutcome:
        """출처와 원문 위치가 확인된 공식 RAG hit만 표준 근거로 연결한다."""

        if outcome.tool_name != "retrieve_official_documents":
            return outcome
        content = outcome.structured_content
        existing = content.get("evidence_records")
        if isinstance(existing, list) and existing:
            return outcome

        traces = [
            trace
            for trace in content.get("source_trace", [])
            if isinstance(trace, dict)
            and isinstance(trace.get("source_ref"), str)
            and isinstance(trace.get("data_date"), str)
            and isinstance(trace.get("content_digest"), str)
        ]
        observed_at = content.get("observed_at")
        project_id = content.get("project_id")
        if not isinstance(observed_at, str) or not isinstance(project_id, str):
            return outcome

        records: list[dict[str, Any]] = []
        for hit in content.get("data", []):
            if not isinstance(hit, dict):
                continue
            required = {
                key: hit.get(key)
                for key in (
                    "document_revision_id",
                    "title",
                    "anchor",
                    "excerpt",
                    "source_date",
                    "evidence_id",
                )
            }
            if not all(isinstance(value, str) and value for value in required.values()):
                continue
            anchor = cast(str, required["anchor"])
            source_date = cast(str, required["source_date"])
            matching_traces = [
                trace
                for trace in traces
                if anchor.startswith(trace["source_ref"])
                and source_date == trace["data_date"]
            ]
            if len(matching_traces) != 1:
                continue
            trace = matching_traces[0]
            excerpt = cast(str, required["excerpt"])
            source_family = hit.get("source_family")
            claim_type = hit.get("claim_type")
            brand_id = hit.get("brand_id")
            is_company_franchise = source_family == "COMPANY_OFFICIAL_FRANCHISE"
            record = {
                "schema_version": "2.0.0",
                "evidence_id": f"official-rag:{required['evidence_id']}",
                "project_id": project_id,
                "claim_type": (
                    claim_type
                    if isinstance(claim_type, str) and claim_type
                    else "OFFICIAL_STARTUP_GUIDANCE"
                ),
                "metric": (
                    brand_id if isinstance(brand_id, str) and brand_id else None
                ),
                "value": {"kind": "STRING", "value": excerpt},
                "value_kind": "EVIDENCED_FACT",
                "unit": None,
                "geographic_scope": {
                    "scope_type": "NATIONAL",
                    "scope_id": "KR",
                    "boundary_version": None,
                },
                "source": {
                    "title": required["title"],
                    "source_ref": trace["source_ref"],
                    "authority": (
                        "COMPANY_OFFICIAL"
                        if is_company_franchise
                        else "PRIMARY_OFFICIAL"
                    ),
                    "source_type": "WEB",
                    **(
                        {"source_family": source_family}
                        if isinstance(source_family, str) and source_family
                        else {}
                    ),
                    "published_or_data_date": source_date,
                    "source_observed_at": observed_at,
                    "document_version": required["document_revision_id"],
                    "checksum": trace["content_digest"],
                },
                "original_anchor": {
                    "anchor_type": "SECTION",
                    "locator": anchor,
                    "excerpt_hash": content_digest(excerpt),
                },
                "freshness_status": self._freshness_status(source_date),
                "conflict_status": "NONE",
                "retrieved_at": observed_at,
                "missing_context": (
                    ["AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION"]
                    if claim_type == "FRANCHISE_INDIVIDUAL_ELIGIBILITY"
                    else []
                ),
                "durable_evidence_refs": [
                    trace["source_ref"],
                    required["document_revision_id"],
                    required["evidence_id"],
                ],
            }
            self._contracts.validate_evidence_record(record)
            records.append(record)

        if not records:
            return outcome
        enriched = deepcopy(content)
        enriched["evidence_records"] = self._deduplicate_evidence(records)
        return outcome.model_copy(update={"structured_content": enriched})

    def _freshness_status(self, source_date: str) -> str:
        try:
            age = self._now().date() - date.fromisoformat(source_date)
        except ValueError:
            return "UNKNOWN"
        if age.days < 0:
            return "UNKNOWN"
        return "FRESH" if age.days <= 365 else "STALE"

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
        def sort_key(item: dict[str, Any]) -> tuple[bool, int, str]:
            # 비용 근거가 부족한 조건부 브랜드도 유지하되, 확인된 비용 후보 뒤에 둔다.
            finance_profile = item["finance_profile"]
            cost_range = finance_profile.get("known_initial_cost_range_krw")
            base_cost = cost_range.get("base", 0) if isinstance(cost_range, dict) else 0
            return cost_range is None, base_cost, item["brand_id"]

        return sorted(universe, key=sort_key)


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
                    "search_cafe_observations": "AREA_DEMAND_SIGNALS",
                    "list_franchise_universe": "FRANCHISE_UNIVERSE_ELIGIBILITY",
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


def content_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"
