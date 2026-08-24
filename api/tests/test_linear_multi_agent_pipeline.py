"""사용자는 단순한 한 요청 안에서도 실제 세 Agent 역할의 결과를 받아야 한다."""

import asyncio
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
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.linear_agent_pipeline import LinearMultiAgentProposalPipeline
from app.workflows.models import HeadFence
from app.workflows.simple_proposal import SimpleProposalBuilder

NOW = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)


def _state(
    preference: CafeTypePreference = CafeTypePreference.INDEPENDENT_ONLY,
    *,
    founder_preferences: list[str] | None = None,
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
            preferences=founder_preferences or [],
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
            support_ref = seed["support_refs"][0]
            return self._result(
                task,
                payload={
                    "candidate_proposals": [
                        {
                            "proposal_id": seed["proposal_id"],
                            "case_type": "INDEPENDENT",
                            "display_name": seed["display_name"],
                            "seed_or_brand_id": seed["model_id"],
                            "adjusted_parameters": [
                                {
                                    "field_path": "operations.seats",
                                    "value": {"kind": "INTEGER", "value": 6},
                                    "unit": "seat",
                                    "support_refs": [support_ref],
                                }
                            ],
                            "claim_refs": [],
                            "evidence_refs": [],
                            "assumption_refs": deepcopy(seed["support_refs"]),
                            "fit_assessments": self._fit_assessments(support_ref),
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
    def _fit_assessments(support_ref: str) -> list[dict[str, Any]]:
        return [
            {
                "axis": "CAPITAL_FIT",
                "signal": "POSITIVE",
                "summary": "등록 비용 범위가 현재 자기자금 안에 있습니다.",
                "input_field_refs": ["/founder/own_funds_krw"],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": [support_ref],
                "missing_context": [],
            },
            {
                "axis": "OPERATING_FIT",
                "signal": "POSITIVE",
                "summary": "직접 전업 운영 조건과 맞습니다.",
                "input_field_refs": ["/founder/operation_mode"],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": [support_ref],
                "missing_context": [],
            },
            {
                "axis": "USER_PREFERENCE_FIT",
                "signal": "POSITIVE",
                "summary": "개인카페 선호 조건과 맞습니다.",
                "input_field_refs": ["/founder/cafe_type_preference"],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": [support_ref],
                "missing_context": [],
            },
            {
                "axis": "AREA_FIT",
                "signal": "UNKNOWN",
                "summary": "상권 자료가 부족해 방향을 확정할 수 없습니다.",
                "input_field_refs": ["/area/display_name"],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": [],
                "missing_context": ["상권 자료"],
            },
            {
                "axis": "EVIDENCE_COMPLETENESS",
                "signal": "UNKNOWN",
                "summary": "등록 가정 외 실제 점포 자료가 필요합니다.",
                "input_field_refs": ["/area/evidence_ids"],
                "claim_refs": [],
                "evidence_refs": [],
                "assumption_refs": [support_ref],
                "missing_context": ["실제 점포 자료"],
            },
        ]

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


def _pipeline(
    runtime: FakeRuntime,
    mcp: FakeMcp,
    *,
    now: datetime = NOW,
) -> LinearMultiAgentProposalPipeline:
    registry = IndependentSeedRegistry.load_default()
    return LinearMultiAgentProposalPipeline(
        runtime=runtime,
        mcp=mcp,
        seed_registry=registry,
        builder=SimpleProposalBuilder(registry),
        task_factory=AgentTaskFactory(now=lambda: now),
        now=lambda: now,
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

    assert set(mcp.tool_names) == {
        "get_area_profile",
        "search_cafe_observations",
        "retrieve_official_documents",
    }
    assert runtime.task_types[0] == "EVIDENCE_ASSESS"
    assert runtime.task_types[-1] == "CANDIDATE_AUDIT"
    assert runtime.task_types[1:-1] == ["PROPOSE_INDEPENDENT"] * 3
    assert 1 <= len(bundle.candidates) <= 3
    assert {candidate["case_type"] for candidate in bundle.candidates} == {"INDEPENDENT"}


def test_pipeline_keeps_running_when_one_mcp_read_fails() -> None:
    """한 자료원이 실패해도 확보한 근거로 세 Agent 역할을 끝까지 실행한다."""

    class OneFailedReadMcp(FakeMcp):
        async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
            if kwargs["tool_name"] == "retrieve_official_documents":
                self.tool_names.append(kwargs["tool_name"])
                raise McpClientError("MCP_STRUCTURED_CONTENT_MISSING")
            return await super().call_tool(**kwargs)

    runtime = FakeRuntime()
    mcp = OneFailedReadMcp()

    bundle = _pipeline(runtime, mcp).run(
        state=_state(),
        head=_head(),
        workflow_run_id="workflow-partial-retrieval",
        evidence_records=[],
    )

    assert bundle.candidates
    assert runtime.task_types[0] == "EVIDENCE_ASSESS"
    assert runtime.task_types[-1] == "CANDIDATE_AUDIT"
    researcher_task = runtime.tasks[0]
    failed_action = next(
        action
        for action in researcher_task["payload"]["executed_actions"]
        if action["tool_name"] == "retrieve_official_documents"
    )
    assert failed_action["structured_result"]["status"] == "ERROR"
    assert failed_action["structured_result"]["error_codes"] == [
        "MCP_STRUCTURED_CONTENT_MISSING"
    ]
    assert failed_action["structured_result"]["evidence_records"] == []


def test_pipeline_builds_government_and_claim_specific_franchise_rag_queries() -> None:
    class InspectingMcp(FakeMcp):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict[str, Any]] = []

        async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
            self.calls.append(deepcopy(kwargs))
            return await super().call_tool(**kwargs)

    mcp = InspectingMcp()

    _pipeline(FakeRuntime(), mcp).run(
        state=_state(CafeTypePreference.OPEN_TO_BOTH),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    official_calls = [
        call for call in mcp.calls if call["tool_name"] == "retrieve_official_documents"
    ]
    assert len(official_calls) == 7
    assert official_calls[0]["arguments"]["source_families"] == ["GOVERNMENT_GUIDE"]
    franchise_calls = official_calls[1:]
    assert all(
        call["arguments"]["source_families"] == ["COMPANY_OFFICIAL_FRANCHISE"]
        for call in franchise_calls
    )
    assert {call["arguments"]["query"] for call in franchise_calls} == {
        "컴포즈커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
        "컴포즈커피 공식 창업 비용 10평 15평 포함 제외 항목",
        "메가MGC커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
        "메가MGC커피 공식 창업 비용 10평 포함 제외 항목",
        "이디야커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
        "이디야커피 공식 창업 비용 가맹비 월 로열티 포함 제외 항목",
    }


def test_pipeline_uses_seoul_business_date_for_official_evidence_queries() -> None:
    class InspectingMcp(FakeMcp):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict[str, Any]] = []

        async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
            self.calls.append(deepcopy(kwargs))
            return await super().call_tool(**kwargs)

    mcp = InspectingMcp()
    utc_late_evening = datetime(2026, 8, 24, 22, 0, tzinfo=UTC)

    _pipeline(FakeRuntime(), mcp, now=utc_late_evening).run(
        state=_state(CafeTypePreference.OPEN_TO_BOTH),
        head=_head(),
        workflow_run_id="workflow-seoul-date",
        evidence_records=[],
    )

    assert {call["arguments"]["as_of"] for call in mcp.calls} == {"2026-08-25"}


def test_pipeline_maps_official_rag_hits_before_the_researcher_projection() -> None:
    class OfficialRagMcp(FakeMcp):
        async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
            if kwargs["tool_name"] != "retrieve_official_documents":
                return await super().call_tool(**kwargs)
            self.tool_names.append("retrieve_official_documents")
            source_ref = "https://easylaw.go.kr/coffee-registration"
            content = {
                "schema_version": "1.0.0",
                "request_id": "request-retrieve_official_documents",
                "tool_name": "retrieve_official_documents",
                "tool_version": "1.0.0",
                "status": "OK",
                "project_id": "project-1",
                "evidence_records": [],
                "missing_fields": [],
                "conflicts": [],
                "source_trace": [
                    {
                        "source_id": "easylaw-csmSeq-706",
                        "source_ref": source_ref,
                        "data_date": "2026-07-15",
                        "retrieved_at": NOW.isoformat().replace("+00:00", "Z"),
                        "content_digest": "sha256:" + "0" * 64,
                    }
                ],
                "error_codes": [],
                "observed_at": NOW.isoformat().replace("+00:00", "Z"),
                "data": [
                    {
                        "document_revision_id": "easylaw-csmSeq-706@2026-07-15",
                        "title": "커피전문점 영업신고 및 사업자등록",
                        "anchor": f"{source_ref}#chunk-1",
                        "excerpt": "커피전문점은 휴게음식점 영업신고를 해야 합니다.",
                        "source_date": "2026-07-15",
                        "evidence_id": "rag:file-1:chunk-1",
                    }
                ],
            }
            return McpCallOutcome(
                request_id=content["request_id"],
                tool_name="retrieve_official_documents",
                tool_version="1.0.0",
                status="OK",
                is_complete=True,
                structured_content=content,
            )

    runtime = FakeRuntime()

    _pipeline(runtime, OfficialRagMcp()).run(
        state=_state(),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    researcher_task = next(
        task for task in runtime.tasks if task["task_type"] == "EVIDENCE_ASSESS"
    )
    action = next(
        action
        for action in researcher_task["payload"]["executed_actions"]
        if action["tool_name"] == "retrieve_official_documents"
    )
    records = action["structured_result"]["evidence_records"]
    assert len(records) == 1
    assert records[0]["claim_type"] == "OFFICIAL_STARTUP_GUIDANCE"
    assert records[0]["value"]["value"].startswith("커피전문점은")
    assert records[0]["source"]["authority"] == "PRIMARY_OFFICIAL"


def test_pipeline_preserves_franchise_rag_metadata_in_evidence_record() -> None:
    class FranchiseRagMcp(FakeMcp):
        async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
            if (
                kwargs["tool_name"] != "retrieve_official_documents"
                or kwargs["arguments"]["source_families"]
                != ["COMPANY_OFFICIAL_FRANCHISE"]
            ):
                return await super().call_tool(**kwargs)
            source_ref = "https://composecoffee.com/composefranchise"
            content = {
                "schema_version": "1.0.0",
                "request_id": f"request-{len(self.tool_names)}",
                "tool_name": "retrieve_official_documents",
                "tool_version": "1.0.0",
                "status": "OK",
                "project_id": "project-1",
                "evidence_records": [],
                "missing_fields": [],
                "conflicts": [],
                "source_trace": [
                    {
                        "source_id": "compose-official-opening-cost",
                        "source_ref": source_ref,
                        "data_date": "2026-08-25",
                        "retrieved_at": NOW.isoformat().replace("+00:00", "Z"),
                        "content_digest": "sha256:" + "2" * 64,
                    }
                ],
                "error_codes": [],
                "observed_at": NOW.isoformat().replace("+00:00", "Z"),
                "data": [
                    {
                        "document_revision_id": "compose-official-opening-cost@2026-08-25",
                        "title": "컴포즈커피 공식 창업비 안내",
                        "anchor": f"{source_ref}#chunk-cost",
                        "excerpt": "10평 기준 공식 창업비 안내입니다.",
                        "source_date": "2026-08-25",
                        "evidence_id": "rag:compose:cost",
                        "source_family": "COMPANY_OFFICIAL_FRANCHISE",
                        "claim_type": "FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE",
                        "brand_id": "kr-compose-coffee",
                        "source_id": "compose-official-opening-cost",
                    }
                ],
            }
            return McpCallOutcome(
                request_id=content["request_id"],
                tool_name="retrieve_official_documents",
                tool_version="1.0.0",
                status="OK",
                is_complete=True,
                structured_content=content,
            )

    runtime = FakeRuntime()
    _pipeline(runtime, FranchiseRagMcp()).run(
        state=_state(CafeTypePreference.OPEN_TO_BOTH),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    researcher_task = next(
        task for task in runtime.tasks if task["task_type"] == "EVIDENCE_ASSESS"
    )
    records = [
        record
        for action in researcher_task["payload"]["executed_actions"]
        for record in action["structured_result"]["evidence_records"]
        if record.get("metric") == "kr-compose-coffee"
    ]
    assert records
    assert records[0]["claim_type"] == "FRANCHISE_OFFICIAL_OPENING_COST_GUIDANCE"
    assert records[0]["source"]["authority"] == "COMPANY_OFFICIAL"
    assert records[0]["source"]["source_family"] == "COMPANY_OFFICIAL_FRANCHISE"
    assert records[0]["source"]["source_ref"] == (
        "https://composecoffee.com/composefranchise"
    )
    assert records[0]["source"]["published_or_data_date"] == "2026-08-25"


def test_independent_proposal_receives_finance_snapshot_and_keeps_agent_advice() -> None:
    runtime = FakeRuntime()

    bundle = _pipeline(runtime, FakeMcp()).run(
        state=_state(),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    proposal_task = next(
        task for task in runtime.tasks if task["task_type"] == "PROPOSE_INDEPENDENT"
    )
    seed = proposal_task["payload"]["model_seeds"][0]
    assert seed["finance_snapshot"] == {
        "initial_cash_krw": {"low": 79_500_000, "base": 139_500_000, "high": 232_000_000},
        "monthly_fixed_cost_krw": {"low": 4_200_000, "base": 7_600_000, "high": 13_300_000},
        "contribution_margin_bps": 6_800,
        "operating_days_per_month": 26,
        "average_ticket_krw": 6_500,
        "break_even_monthly_sales_krw": 11_176_471,
        "required_daily_orders": 66.14,
    }

    candidate = next(
        value
        for value in bundle.candidates
        if value["independent_model"]["model_id"] == seed["model_id"]
    )
    assert [
        assessment["axis"] for assessment in candidate["agent_advisory"]["fit_assessments"]
    ] == [
        "CAPITAL_FIT",
        "OPERATING_FIT",
        "USER_PREFERENCE_FIT",
        "AREA_FIT",
        "EVIDENCE_COMPLETENESS",
    ]
    assert candidate["agent_advisory"]["adjusted_parameters"][0]["field_path"] == (
        "operations.seats"
    )
    assert candidate["independent_model"]["adjusted_fields"] == ["operations.seats"]


def test_pipeline_prioritizes_the_explicit_independent_model_preference() -> None:
    """사용자가 원하는 유형은 세 개의 병렬 Proposal Agent 입력에 먼저 포함한다."""

    runtime = FakeRuntime()
    _pipeline(runtime, FakeMcp()).run(
        state=_state(founder_preferences=["스페셜티 원두와 핸드드립 중심"]),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    proposal_tasks = [
        task for task in runtime.tasks if task["task_type"] == "PROPOSE_INDEPENDENT"
    ]
    assert proposal_tasks[0]["payload"]["model_seeds"][0]["model_id"] == (
        "independent-specialty-v1"
    )
    assert len(proposal_tasks) == 3


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


class AreaMcp(FakeMcp):
    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        tool_name = kwargs["tool_name"]
        if tool_name != "search_cafe_observations":
            return await super().call_tool(**kwargs)
        self.tool_names.append(tool_name)
        evidence = {
            "schema_version": "2.0.0",
            "evidence_id": "seoul-store:approved:CAFE_COUNT",
            "project_id": "project-1",
            "claim_type": "AREA_CAFE_COMPETITION",
            "metric": "CAFE_COUNT",
            "value": {"kind": "INTEGER", "value": 139},
            "value_kind": "EVIDENCED_FACT",
            "unit": "STORES",
            "geographic_scope": {
                "scope_type": "ADMINISTRATIVE_AREA",
                "scope_id": "11440565",
                "boundary_version": "2026-01",
            },
            "source": {
                "title": "서울시 상권분석서비스 카페 업소 현황",
                "source_ref": "https://data.seoul.go.kr/store",
                "authority": "PRIMARY_DATA",
                "source_type": "DATASET",
                "published_or_data_date": "2026-03-31",
                "source_observed_at": NOW.isoformat().replace("+00:00", "Z"),
                "document_version": "approved",
                "checksum": "sha256:" + "0" * 64,
            },
            "original_anchor": {
                "anchor_type": "DATASET_ROW",
                "locator": "approved:11440565:CAFE_COUNT",
                "excerpt_hash": "sha256:" + "1" * 64,
            },
            "freshness_status": "FRESH",
            "conflict_status": "NONE",
            "retrieved_at": NOW.isoformat().replace("+00:00", "Z"),
            "missing_context": [],
            "durable_evidence_refs": ["https://data.seoul.go.kr/store"],
        }
        content = {
            "schema_version": "1.0.0",
            "request_id": "request-search_cafe_observations",
            "tool_name": tool_name,
            "tool_version": "1.0.0",
            "status": "OK",
            "project_id": "project-1",
            "evidence_records": [evidence],
            "missing_fields": [],
            "conflicts": [],
            "source_trace": [],
            "error_codes": [],
            "observed_at": NOW.isoformat().replace("+00:00", "Z"),
            "data": [
                {
                    "metric": "CAFE_COUNT",
                    "value": {"kind": "INTEGER", "value": 139},
                    "unit": "STORES",
                    "as_of": "2026-03-31",
                    "evidence_id": evidence["evidence_id"],
                }
            ],
        }
        return McpCallOutcome(
            request_id=content["request_id"],
            tool_name=tool_name,
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content=content,
        )


class AreaRuntime(FakeRuntime):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["task_type"] != "EVIDENCE_ASSESS":
            return super().invoke(task)
        self.task_types.append("EVIDENCE_ASSESS")
        self.tasks.append(deepcopy(task))
        return self._result(
            task,
            payload={
                "assessments": [
                    {
                        "candidate_ref": "seoul-store:approved:CAFE_COUNT",
                        "claim_id": "claim:search_cafe_observations",
                        "relation": "SUPPORTS",
                        "scope_status": "MATCH",
                        "date_status": "MATCH",
                        "freshness_status": "FRESH",
                        "anchor_status": "VALID",
                        "authority_status": "ACCEPTABLE",
                        "missing_context": [],
                    }
                ],
                "missing_claims": [
                    "claim:get_area_profile",
                    "claim:retrieve_official_documents",
                ],
                "conflict_proposals": [],
            },
            missing_claim_ids=[
                "claim:get_area_profile",
                "claim:retrieve_official_documents",
            ],
        )


def test_area_observation_reaches_proposal_agent_and_result_card() -> None:
    runtime = AreaRuntime()

    bundle = _pipeline(runtime, AreaMcp()).run(
        state=_state(),
        head=_head(),
        workflow_run_id="workflow-1",
        evidence_records=[],
    )

    proposal_task = next(
        task for task in runtime.tasks if task["task_type"] == "PROPOSE_INDEPENDENT"
    )
    assert proposal_task["payload"]["evidence_records"][0]["metric"] == "CAFE_COUNT"
    assert bundle.candidates[0]["market_signals"] == [
        {
            "signal_type": "CAFE_COUNT",
            "value": 139,
            "unit": "STORES",
            "data_date": "2026-03-31",
            "freshness_status": "FRESH",
            "source_title": "서울시 상권분석서비스 카페 업소 현황",
            "source_ref": "https://data.seoul.go.kr/store",
            "evidence_id": "seoul-store:approved:CAFE_COUNT",
            "caveat": (
                "선택 지역에 연결된 행정동의 카페 업종 집계이며 "
                "개별 점포의 경쟁력을 뜻하지 않습니다."
            ),
        }
    ]


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


class ProductionSizedFranchiseMcp(FranchiseMcp):
    """운영 카탈로그와 비슷한 후보·근거 수로 LLM 없는 경로를 검증한다."""

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        if kwargs["tool_name"] != "list_franchise_universe":
            return await super().call_tool(**kwargs)
        self.tool_names.append("list_franchise_universe")
        brands: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        for index in range(9):
            brand_id = f"kr-test-cafe-{index + 1}"
            eligibility_id = f"eligibility:{brand_id}"
            cost_id = f"franchise-cost:{brand_id}"
            eligibility = self._evidence(eligibility_id, "STRING")
            eligibility["source"]["title"] = f"테스트 카페 {index + 1} 공식 가맹 안내"
            eligibility["source"]["source_ref"] = (
                f"https://example.com/{brand_id}/franchise"
            )
            eligibility["durable_evidence_refs"] = [
                eligibility["source"]["source_ref"]
            ]
            records.append(eligibility)

            has_cost_evidence = index < 6
            if has_cost_evidence:
                cost = self._evidence(cost_id, "MONEY_RANGE")
                cost["source"]["title"] = f"테스트 카페 {index + 1} 공식 창업 비용"
                cost["source"]["source_ref"] = (
                    f"https://example.com/{brand_id}/cost"
                )
                cost["durable_evidence_refs"] = [cost["source"]["source_ref"]]
                records.append(cost)

            base_cost = 70_000_000 + index * 5_000_000
            brands.append(
                {
                    "brand_id": brand_id,
                    "display_name": f"테스트 카페 {index + 1}",
                    "individual_franchise_eligibility": "VERIFIED",
                    "eligibility_evidence_id": eligibility_id,
                    "disclosure_status": "MISSING",
                    "finance_profile": {
                        "currency": "KRW",
                        "coverage": "PARTIAL",
                        "value_kind": "EVIDENCED_FACT",
                        "known_initial_cost_range_krw": {
                            "low": base_cost,
                            "base": base_cost,
                            "high": base_cost,
                        },
                        "reference_area_sqm": 33,
                        "monthly_royalty_krw": None,
                        "evidence_refs": [cost_id] if has_cost_evidence else [],
                        "source_refs": [
                            f"https://example.com/{brand_id}/cost"
                        ]
                        if has_cost_evidence
                        else [],
                        "scope_note": "10평 기준 공식 창업비용",
                        "missing_costs": ["DEPOSIT", "OPERATING_RESERVE"],
                    },
                }
            )

        content = {
            "schema_version": "1.0.0",
            "request_id": "request-list_franchise_universe",
            "tool_name": "list_franchise_universe",
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
            tool_name="list_franchise_universe",
            tool_version="1.0.0",
            status="PARTIAL",
            is_complete=False,
            structured_content=content,
        )


class OpenBothNoLlmRuntime(FakeRuntime):
    """고정 JSON만 반환해 Vertex 호출 없이 Control API 연결만 검사한다."""

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["task_type"] != "EVIDENCE_ASSESS":
            return super().invoke(task)
        self.task_types.append("EVIDENCE_ASSESS")
        self.tasks.append(deepcopy(task))
        assessments: list[dict[str, Any]] = []
        missing_claims: list[str] = []
        for action in task["payload"]["executed_actions"]:
            records = action["structured_result"]["evidence_records"]
            if not records:
                missing_claims.append(action["claim_id"])
                continue
            assessments.extend(
                {
                    "candidate_ref": record["evidence_id"],
                    "claim_id": action["claim_id"],
                    "relation": "SUPPORTS",
                    "scope_status": "MATCH",
                    "date_status": "MATCH",
                    "freshness_status": record["freshness_status"],
                    "anchor_status": "VALID",
                    "authority_status": "ACCEPTABLE",
                    "missing_context": [],
                }
                for record in records
            )
        return self._result(
            task,
            payload={
                "assessments": assessments,
                "missing_claims": missing_claims,
                "conflict_proposals": [],
            },
            missing_claim_ids=missing_claims,
        )


def test_open_to_both_completes_with_production_sized_input_without_llm() -> None:
    runtime = OpenBothNoLlmRuntime()

    bundle = _pipeline(runtime, ProductionSizedFranchiseMcp()).run(
        state=_state(CafeTypePreference.OPEN_TO_BOTH),
        head=_head(),
        workflow_run_id="workflow-open-both-no-llm",
        evidence_records=[],
    )

    evidence_task = runtime.tasks[0]
    franchise_action = next(
        action
        for action in evidence_task["payload"]["executed_actions"]
        if action["tool_name"] == "list_franchise_universe"
    )
    # Vertex의 구조화 출력 스키마는 이 역할에서 후보 15개부터 요청을 거절한다.
    assert len(franchise_action["structured_result"]["evidence_records"]) == 14
    assert runtime.task_types == [
        "EVIDENCE_ASSESS",
        "PROPOSE_INDEPENDENT",
        "PROPOSE_FRANCHISE",
        "PROPOSE_FRANCHISE",
        "CANDIDATE_AUDIT",
    ]
    assert {candidate["case_type"] for candidate in bundle.candidates} == {
        "INDEPENDENT",
        "FRANCHISE",
    }


def test_franchise_universe_keeps_unknown_cost_without_crashing() -> None:
    outcome = asyncio.run(
        FranchiseMcp().call_tool(tool_name="list_franchise_universe")
    )

    universe = LinearMultiAgentProposalPipeline._franchise_universe(
        [outcome],
        evidence_records=[{"evidence_id": "eligibility:mega"}],
    )

    assert [candidate["brand_id"] for candidate in universe] == [
        "kr-mega-mgc-coffee"
    ]
    assert universe[0]["finance_profile"]["known_initial_cost_range_krw"] is None


def test_evidence_projection_distributes_vertex_capacity_across_sources() -> None:
    actions = [
        {
            "action_id": f"action:{source}",
            "claim_id": f"claim:{source}",
            "tool_name": source,
            "request_id": f"request:{source}",
            "structured_result": {
                "evidence_records": [
                    {"evidence_id": f"{source}:{index}"} for index in range(8)
                ],
                "source_trace": [],
                "data": [],
            },
        }
        for source in ("area", "franchise", "official")
    ]

    projected = AgentTaskFactory._project_evidence_assess_actions(actions)

    counts = [
        len(action["structured_result"]["evidence_records"])
        for action in projected
    ]
    assert counts == [5, 5, 4]
    assert sum(counts) == 14


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
