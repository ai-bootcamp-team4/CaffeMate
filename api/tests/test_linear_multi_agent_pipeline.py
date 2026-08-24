"""사용자는 단순한 한 요청 안에서도 실제 세 Agent 역할의 결과를 받아야 한다."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.agents.task_factory import AgentTaskFactory
from app.candidates.seed_registry import IndependentSeedRegistry
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
from app.mcp.client import McpCallOutcome
from app.workflows.linear_agent_pipeline import LinearMultiAgentProposalPipeline
from app.workflows.models import HeadFence
from app.workflows.simple_proposal import SimpleProposalBuilder

NOW = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)


def _state(
    preference: CafeTypePreference = CafeTypePreference.INDEPENDENT_ONLY,
) -> VentureState:
    return VentureState(
        project_id="project-1",
        user_id="user-1",
        state_version=2,
        status=VentureStatus.ANALYZING,
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=preference,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
        area=AreaState(
            resolution_status=AreaResolutionStatus.RESOLVED,
            area_id="area-1",
            administrative_code="11440565",
            display_name="서울특별시 마포구 공덕동",
            boundary_version="2026-01",
            coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
        ),
        updated_at=NOW,
    )


def _head() -> HeadFence:
    return HeadFence(
        workflow_generation=3,
        state_version=2,
        founder_snapshot_id="founder-2",
        area_snapshot_id="area-2",
        evidence_snapshot_id="evidence-2",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )


class FakeMcp:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        tool_name = kwargs["tool_name"]
        self.tool_names.append(tool_name)
        request_id = f"request-{tool_name}"
        content = {
            "schema_version": "1.0.0",
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_version": "1.0.0",
            "status": "NOT_FOUND",
            "project_id": "project-1",
            "evidence_records": [],
            "missing_fields": [f"{tool_name}_data"],
            "conflicts": [],
            "source_trace": [],
            "error_codes": [],
            "observed_at": NOW.isoformat().replace("+00:00", "Z"),
            "data": [],
        }
        return McpCallOutcome(
            request_id=request_id,
            tool_name=tool_name,
            tool_version="1.0.0",
            status="NOT_FOUND",
            is_complete=False,
            structured_content=content,
        )


class FakeRuntime:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.task_types: list[str] = []
        self.tasks: list[dict[str, Any]] = []
        self.fail_on = fail_on

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        task_type = task["task_type"]
        self.task_types.append(task_type)
        self.tasks.append(deepcopy(task))
        if task_type == self.fail_on:
            raise RuntimeError(f"{task_type} failed")
        if task_type == "EVIDENCE_ASSESS":
            claim_ids = [claim["claim_id"] for claim in task["payload"]["claims"]]
            return self._result(
                task,
                payload={
                    "assessments": [],
                    "missing_claims": claim_ids,
                    "conflict_proposals": [],
                },
                missing_claim_ids=claim_ids,
            )
        if task_type == "PROPOSE_INDEPENDENT":
            seed = task["payload"]["model_seeds"][0]
            return self._result(
                task,
                payload={
                    "candidate_proposals": [
                        {
                            "proposal_id": seed["proposal_id"],
                            "case_type": "INDEPENDENT",
                            "display_name": seed["display_name"],
                            "seed_or_brand_id": seed["model_id"],
                            "adjusted_parameters": [],
                            "claim_refs": [],
                            "evidence_refs": [],
                            "assumption_refs": deepcopy(seed["support_refs"]),
                            "missing_fields": [],
                            "warnings": [],
                        }
                    ]
                },
            )
        if task_type == "PROPOSE_FRANCHISE":
            brand = task["payload"]["franchise_universe"][0]
            return self._result(
                task,
                payload={
                    "candidate_proposals": [
                        {
                            "proposal_id": brand["proposal_id"],
                            "case_type": "FRANCHISE",
                            "display_name": brand["display_name"],
                            "seed_or_brand_id": brand["brand_id"],
                            "adjusted_parameters": [],
                            "claim_refs": [],
                            "evidence_refs": deepcopy(brand["evidence_refs"]),
                            "assumption_refs": [],
                            "missing_fields": [],
                            "warnings": [],
                        }
                    ]
                },
            )
        if task_type == "CANDIDATE_AUDIT":
            candidates = task["payload"]["candidates"]
            return self._result(
                task,
                payload={
                    "candidate_audits": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "status": "PASS",
                            "findings": [],
                        }
                        for candidate in candidates
                    ],
                    "global_findings": [],
                },
            )
        raise AssertionError(f"Unexpected task: {task_type}")

    @staticmethod
    def _result(
        task: dict[str, Any],
        *,
        payload: dict[str, Any],
        missing_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
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
            "payload": payload,
            "evidence_refs": [],
            "missing_claim_ids": missing_claim_ids or [],
            "reason_codes": [],
            "warnings": [],
        }


def _pipeline(runtime: FakeRuntime, mcp: FakeMcp) -> LinearMultiAgentProposalPipeline:
    registry = IndependentSeedRegistry.load_default()
    return LinearMultiAgentProposalPipeline(
        runtime=runtime,
        mcp=mcp,
        seed_registry=registry,
        builder=SimpleProposalBuilder(registry),
        task_factory=AgentTaskFactory(now=lambda: NOW),
        now=lambda: NOW,
    )


def test_pipeline_calls_research_proposals_and_auditor_in_one_linear_flow() -> None:
    runtime = FakeRuntime()
    mcp = FakeMcp()

    bundle = _pipeline(runtime, mcp).run(
        state=_state(),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    assert set(mcp.tool_names) == {"get_area_profile", "retrieve_official_documents"}
    assert runtime.task_types[0] == "EVIDENCE_ASSESS"
    assert runtime.task_types[-1] == "CANDIDATE_AUDIT"
    assert runtime.task_types[1:-1] == ["PROPOSE_INDEPENDENT"] * 3
    assert 1 <= len(bundle.candidates) <= 3
    assert {candidate["case_type"] for candidate in bundle.candidates} == {"INDEPENDENT"}


def test_pipeline_does_not_hide_agent_runtime_failure_with_static_result() -> None:
    runtime = FakeRuntime(fail_on="PROPOSE_INDEPENDENT")

    with pytest.raises(RuntimeError, match="PROPOSE_INDEPENDENT failed"):
        _pipeline(runtime, FakeMcp()).run(
            state=_state(),
            head=_head(),
            workflow_run_id="workflow-1",
            evidence_records=[],
        )

    assert "CANDIDATE_AUDIT" not in runtime.task_types


class FranchiseMcp(FakeMcp):
    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        tool_name = kwargs["tool_name"]
        if tool_name != "list_franchise_universe":
            return await super().call_tool(**kwargs)
        self.tool_names.append(tool_name)
        brands = [
            {
                "brand_id": "kr-mega-mgc-coffee",
                "display_name": "메가MGC커피",
                "individual_franchise_eligibility": "VERIFIED",
                "eligibility_evidence_id": "eligibility:mega",
                "disclosure_status": "MISSING",
                "finance_profile": {
                    "currency": "KRW",
                    "coverage": "PARTIAL",
                    "value_kind": "EVIDENCED_FACT",
                    "known_initial_cost_range_krw": {
                        "low": 74_230_000,
                        "base": 74_230_000,
                        "high": 74_230_000,
                    },
                    "reference_area_sqm": 33,
                    "monthly_royalty_krw": None,
                    "evidence_refs": ["franchise-cost:mega"],
                    "source_refs": ["https://example.com/mega-official"],
                    "scope_note": "10평 기준 공식 창업비용",
                    "missing_costs": [
                        "DEPOSIT",
                        "ACQUISITION_OR_PREMIUM",
                        "SITE_SPECIFIC_WORKS",
                        "OPERATING_RESERVE",
                        "ROYALTY",
                    ],
                },
            },
            {
                "brand_id": "kr-starbucks-korea",
                "display_name": "스타벅스",
                "individual_franchise_eligibility": "INELIGIBLE",
                "eligibility_evidence_id": None,
                "disclosure_status": "MISSING",
                "finance_profile": {
                    "currency": "KRW",
                    "coverage": "NOT_APPLICABLE",
                    "value_kind": "UNKNOWN",
                    "known_initial_cost_range_krw": None,
                    "reference_area_sqm": None,
                    "monthly_royalty_krw": None,
                    "evidence_refs": [],
                    "source_refs": [],
                    "scope_note": "개인 가맹 대상 아님",
                    "missing_costs": [],
                },
            },
        ]
        records = [
            self._evidence("eligibility:mega", "STRING"),
            self._evidence("franchise-cost:mega", "MONEY_RANGE"),
        ]
        content = {
            "schema_version": "1.0.0",
            "request_id": "request-list_franchise_universe",
            "tool_name": tool_name,
            "tool_version": "1.0.0",
            "status": "PARTIAL",
            "project_id": "project-1",
            "evidence_records": records,
            "missing_fields": ["franchise_disclosure"],
            "conflicts": [],
            "source_trace": [],
            "error_codes": [],
            "observed_at": NOW.isoformat().replace("+00:00", "Z"),
            "data": brands,
        }
        return McpCallOutcome(
            request_id=content["request_id"],
            tool_name=tool_name,
            tool_version="1.0.0",
            status="PARTIAL",
            is_complete=False,
            structured_content=content,
        )

    @staticmethod
    def _evidence(evidence_id: str, value_kind: str) -> dict[str, Any]:
        value = (
            {"kind": "STRING", "value": "개인 가맹 모집 확인"}
            if value_kind == "STRING"
            else {
                "kind": "MONEY_RANGE",
                "currency": "KRW",
                "low": 74_230_000,
                "base": 74_230_000,
                "high": 74_230_000,
            }
        )
        return {
            "schema_version": "2.0.0",
            "evidence_id": evidence_id,
            "project_id": "project-1",
            "claim_type": "FRANCHISE_OFFICIAL_PROFILE",
            "metric": "INDIVIDUAL_FRANCHISE_PROFILE",
            "value": value,
            "value_kind": "EVIDENCED_FACT",
            "unit": "KRW" if value_kind == "MONEY_RANGE" else None,
            "geographic_scope": {
                "scope_type": "NATIONAL",
                "scope_id": "KR",
                "boundary_version": None,
            },
            "source": {
                "title": "메가MGC 공식 가맹 안내",
                "source_ref": "https://example.com/mega-official",
                "authority": "COMPANY_OFFICIAL",
                "source_type": "WEB",
                "published_or_data_date": "2026-08-24",
                "source_observed_at": NOW.isoformat().replace("+00:00", "Z"),
                "document_version": "2026-08-24",
                "checksum": "sha256:" + "0" * 64,
            },
            "original_anchor": {
                "anchor_type": "SECTION",
                "locator": "가맹 비용",
                "excerpt_hash": "sha256:" + "1" * 64,
            },
            "freshness_status": "FRESH",
            "conflict_status": "NONE",
            "retrieved_at": NOW.isoformat().replace("+00:00", "Z"),
            "missing_context": ["실제 점포 비용 확인 필요"],
            "durable_evidence_refs": ["https://example.com/mega-official"],
        }


class FranchiseRuntime(FakeRuntime):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["task_type"] != "EVIDENCE_ASSESS":
            return super().invoke(task)
        self.task_types.append("EVIDENCE_ASSESS")
        self.tasks.append(deepcopy(task))
        refs = ["eligibility:mega", "franchise-cost:mega"]
        return self._result(
            task,
            payload={
                "assessments": [
                    {
                        "candidate_ref": ref,
                        "claim_id": "claim:list_franchise_universe",
                        "relation": "SUPPORTS",
                        "scope_status": "MATCH",
                        "date_status": "MATCH",
                        "freshness_status": "FRESH",
                        "anchor_status": "VALID",
                        "authority_status": "ACCEPTABLE",
                        "missing_context": [],
                    }
                    for ref in refs
                ],
                "missing_claims": [],
                "conflict_proposals": [],
            },
        )


def test_franchise_profile_reaches_agent_and_grounded_calculation_without_static_brand() -> None:
    runtime = FranchiseRuntime()
    mcp = FranchiseMcp()

    bundle = _pipeline(runtime, mcp).run(
        state=_state(CafeTypePreference.FRANCHISE_ONLY),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    proposal_task = next(
        task for task in runtime.tasks if task["task_type"] == "PROPOSE_FRANCHISE"
    )
    supplied_brand = proposal_task["payload"]["franchise_universe"][0]
    assert supplied_brand["brand_id"] == "kr-mega-mgc-coffee"
    assert supplied_brand["finance_profile"]["known_initial_cost_range_krw"]["base"] == 74_230_000
    assert [candidate["franchise"]["brand_id"] for candidate in bundle.candidates] == [
        "kr-mega-mgc-coffee"
    ]
    assert bundle.candidates[0]["financial_summary"]["initial_cash"]["base"] > 74_230_000
    assert "kr-starbucks-korea" not in {
        candidate["franchise"]["brand_id"] for candidate in bundle.candidates
    }
