import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError
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
from app.workflows.evidence_retrieval import EvidenceRetrievalStageHandler
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext


def action(action_id: str, polarity: str, *, source_id: str = "area-profile") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "claim_id": "claim:AREA_PROFILE",
        "polarity": polarity,
        "tool_name": "get_source_health",
        "tool_version": "1.0.0",
        "typed_arguments": {
            "source_ids": [source_id],
            "as_of": "2026-08-21",
        },
        "required_authority": ["PRIMARY_DATA"],
        "date_constraints": {"as_of": "2026-08-21", "max_age_days": 365},
        "scope_constraints": {
            "scope_type": "ADMINISTRATIVE_AREA",
            "scope_id": "41117550",
            "boundary_version": "2026-01",
        },
    }


def rag_action(action_id: str, polarity: str) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "claim_id": "claim:AREA_PROFILE",
        "polarity": polarity,
        "tool_name": "retrieve_official_documents",
        "tool_version": "1.0.0",
        "typed_arguments": {
            "query": "카페 영업 신고 공식 절차",
            "source_families": ["GOVERNMENT_GUIDE"],
            "as_of": "2026-08-21",
            "limit": 5,
        },
        "required_authority": ["PRIMARY_OFFICIAL"],
        "date_constraints": {"as_of": "2026-08-21", "max_age_days": 365},
        "scope_constraints": {
            "scope_type": "ADMINISTRATIVE_AREA",
            "scope_id": "41117550",
            "boundary_version": "2026-01",
        },
    }


def metric_action(action_id: str, polarity: str) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "claim_id": "claim:AREA_PROFILE",
        "polarity": polarity,
        "tool_name": "get_area_profile",
        "tool_version": "1.0.0",
        "typed_arguments": {
            "administrative_code": "41117550",
            "boundary_version": "2026-01",
            "as_of": "2026-08-21",
        },
        "required_authority": ["PRIMARY_DATA"],
        "date_constraints": {"as_of": "2026-08-21", "max_age_days": 365},
        "scope_constraints": {
            "scope_type": "ADMINISTRATIVE_AREA",
            "scope_id": "41117550",
            "boundary_version": "2026-01",
        },
    }


def context(*, support: list[dict[str, Any]], counter: list[dict[str, Any]]) -> StageContext:
    now = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    head = HeadFence(
        workflow_generation=1,
        state_version=1,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id=None,
        policy_snapshot_id="policy-1",
        index_generation_id=None,
        seed_registry_id=None,
    )
    claim = {
        "claim_id": "claim:AREA_PROFILE",
        "claim_type": "AREA_PROFILE",
        "materiality": "HIGH",
        "geographic_scope": {
            "scope_type": "ADMINISTRATIVE_AREA",
            "scope_id": "41117550",
            "boundary_version": "2026-01",
        },
        "required_freshness": "P365D",
    }
    return StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="retrieval-1",
            stage_code="EVIDENCE_RETRIEVAL",
            input_digest="a" * 64,
            lease_token="lease-token",
            lease_expires_at=now + timedelta(seconds=45),
            attempt=1,
            head=head,
        ),
        project_id="project-1",
        state=VentureState(
            project_id="project-1",
            user_id="user-1",
            state_version=1,
            status=VentureStatus.ANALYZING,
            founder=FounderState(
                target_area_input="수원 아주대 부근",
                own_funds_krw=50_000_000,
                borrowing_intent=BorrowingIntent.UNDECIDED,
                cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
                operation_mode=OperationMode.DIRECT_FULL_TIME,
            ),
            area=AreaState(
                resolution_status=AreaResolutionStatus.RESOLVED,
                administrative_code="41117550",
                display_name="경기도 수원시 영통구 원천동",
                boundary_version="2026-01",
                coverage_profile=CoverageProfile.N1_NATIONWIDE_CONDITIONAL,
            ),
            updated_at=now,
        ),
        dependency_results={
            "EVIDENCE_PLAN": {
                "evidence_plan": {
                    "status": "COMPLETE",
                    "claims": [claim],
                    "planning_constraints": {
                        "as_of": "2026-08-21",
                        "max_actions_per_claim": 2,
                        "max_total_actions": 4,
                        "allowed_tools": sorted(
                            {value["tool_name"] for value in [*support, *counter]}
                        ),
                    },
                    "claim_plans": [
                        {
                            "claim_id": "claim:AREA_PROFILE",
                            "route": "MCP_STRUCTURED",
                            "support_actions": support,
                            "counter_actions": counter,
                            "stop_condition": "자료 확보",
                            "abstain_condition": "자료 없음",
                        }
                    ],
                }
            }
        },
    )


class FakeMcpClient:
    def __init__(self, *, failing_source: str | None = None) -> None:
        self.failing_source = failing_source
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.max_active = 0

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        self.calls.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        source_id = kwargs["arguments"]["source_ids"][0]
        if source_id == self.failing_source:
            raise McpClientError("MCP_TRANSPORT_ERROR")
        request_id = f"request-{len(self.calls)}"
        content = {
            "schema_version": "1.0.0",
            "request_id": request_id,
            "tool_name": "get_source_health",
            "tool_version": "1.0.0",
            "status": "OK",
            "project_id": "project-1",
            "evidence_records": [],
            "missing_fields": [],
            "conflicts": [],
            "source_trace": [],
            "error_codes": [],
            "observed_at": "2026-08-21T09:00:00Z",
            "data": [],
        }
        return McpCallOutcome(
            request_id=request_id,
            tool_name="get_source_health",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content=content,
        )


class FakeRagMcpClient:
    def __init__(self, *, source_trace: bool = True) -> None:
        self.source_trace = source_trace

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        request_id = "request-rag"
        source_ref = "https://example.go.kr/cafe-guide"
        content = {
            "schema_version": "1.0.0",
            "request_id": request_id,
            "tool_name": "retrieve_official_documents",
            "tool_version": "1.0.0",
            "status": "OK",
            "project_id": "project-1",
            "evidence_records": [],
            "missing_fields": [],
            "conflicts": [],
            "source_trace": (
                [
                    {
                        "source_id": "official-cafe-guide",
                        "source_ref": source_ref,
                        "data_date": "2026-08-01",
                        "retrieved_at": "2026-08-21T09:00:00Z",
                        "content_digest": "sha256:" + "a" * 64,
                    }
                ]
                if self.source_trace
                else []
            ),
            "error_codes": [],
            "observed_at": "2026-08-21T09:00:00Z",
            "data": [
                {
                    "document_revision_id": "guide@2026-08-01",
                    "title": "카페 영업 신고 안내",
                    "anchor": f"{source_ref}#section=registration",
                    "excerpt": "휴게음식점 영업 신고 후 사업자등록을 진행합니다.",
                    "source_date": "2026-08-01",
                    "evidence_id": "rag:file-1:chunk-1",
                }
            ],
        }
        return McpCallOutcome(
            request_id=request_id,
            tool_name="retrieve_official_documents",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content=content,
        )


class FakeMetricMcpClient:
    def __init__(self, *, source_trace: bool = True) -> None:
        self.source_trace = source_trace

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        request_id = "request-metric"
        source_id = "seoul-resident-population-quarterly"
        ingestion_id = "5b206b15c98303940cebfbfa"
        content = {
            "schema_version": "1.0.0",
            "request_id": request_id,
            "tool_name": "get_area_profile",
            "tool_version": "1.0.0",
            "status": "OK",
            "project_id": "project-1",
            "evidence_records": [],
            "missing_fields": [],
            "conflicts": [],
            "source_trace": (
                [
                    {
                        "source_id": source_id,
                        "source_ref": "https://data.seoul.go.kr/resident",
                        "data_date": "2026-03-31",
                        "retrieved_at": "2026-08-21T09:00:00Z",
                        "content_digest": "sha256:" + "a" * 64,
                    }
                ]
                if self.source_trace
                else []
            ),
            "error_codes": [],
            "observed_at": "2026-08-21T09:00:00Z",
            "data": [
                {
                    "metric": "RESIDENT_POPULATION",
                    "value": {"kind": "INTEGER", "value": 40000},
                    "unit": "PERSONS",
                    "as_of": "2026-03-31",
                    "evidence_id": f"{source_id}:{ingestion_id}:" + "b" * 64,
                }
            ],
        }
        return McpCallOutcome(
            request_id=request_id,
            tool_name="get_area_profile",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content=content,
        )


def test_identical_support_and_counter_requests_execute_once_but_keep_both_actions() -> None:
    client = FakeMcpClient()
    value = context(
        support=[action("action-01", "SUPPORT")],
        counter=[action("action-02", "COUNTER")],
    )

    result = EvidenceRetrievalStageHandler(client).execute(value)

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    assert retrieval["planned_action_count"] == 2
    assert retrieval["physical_call_count"] == 1
    assert retrieval["completeness"] == "COMPLETE"
    assert len(client.calls) == 1
    assert [item["polarity"] for item in retrieval["executed_actions"]] == [
        "SUPPORT",
        "COUNTER",
    ]
    assert len({item["request_id"] for item in retrieval["executed_actions"]}) == 1


def test_unique_requests_run_concurrently_and_failure_remains_explicit() -> None:
    client = FakeMcpClient(failing_source="counter-source")
    value = context(
        support=[action("action-01", "SUPPORT")],
        counter=[action("action-02", "COUNTER", source_id="counter-source")],
    )

    result = EvidenceRetrievalStageHandler(client, max_concurrency=2).execute(value)

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    assert retrieval["physical_call_count"] == 2
    assert retrieval["completeness"] == "PARTIAL"
    assert client.max_active == 2
    assert len(retrieval["executed_actions"]) == 1
    assert retrieval["failed_actions"] == [
        {
            "action_id": "action-02",
            "claim_id": "claim:AREA_PROFILE",
            "polarity": "COUNTER",
            "tool_name": "get_source_health",
            "error_code": "MCP_TRANSPORT_ERROR",
        }
    ]


def test_tampered_tool_arguments_are_rejected_before_any_call() -> None:
    client = FakeMcpClient()
    invalid = action("action-01", "SUPPORT")
    invalid["typed_arguments"] = {"source_ids": []}

    with pytest.raises(ContractValidationError):
        EvidenceRetrievalStageHandler(client).execute(context(support=[invalid], counter=[]))
    assert client.calls == []


def test_duplicate_action_ids_are_rejected_before_any_call() -> None:
    client = FakeMcpClient()

    with pytest.raises(ContractValidationError, match="duplicated"):
        EvidenceRetrievalStageHandler(client).execute(
            context(
                support=[action("action-01", "SUPPORT")],
                counter=[action("action-01", "COUNTER", source_id="counter")],
            )
        )
    assert client.calls == []


def test_rag_hits_become_claim_scoped_evidence_candidates_before_assessment() -> None:
    value = context(
        support=[rag_action("action-01", "SUPPORT")],
        counter=[rag_action("action-02", "COUNTER")],
    )

    result = EvidenceRetrievalStageHandler(FakeRagMcpClient()).execute(value)

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    assert retrieval["physical_call_count"] == 1
    assert retrieval["completeness"] == "COMPLETE"
    executed = retrieval["executed_actions"]
    assert isinstance(executed, list)
    assert len(executed) == 2
    support_record = executed[0]["structured_result"]["evidence_records"][0]
    counter_record = executed[1]["structured_result"]["evidence_records"][0]
    assert support_record == counter_record
    assert support_record["evidence_id"].startswith("rag-evidence:")
    assert support_record["claim_type"] == "AREA_PROFILE"
    assert support_record["project_id"] == "project-1"
    assert support_record["value"] == {
        "kind": "STRING",
        "value": "휴게음식점 영업 신고 후 사업자등록을 진행합니다.",
    }
    assert support_record["source"]["authority"] == "PRIMARY_OFFICIAL"
    assert support_record["source"]["checksum"] == "sha256:" + "a" * 64
    assert support_record["freshness_status"] == "FRESH"
    assert support_record["durable_evidence_refs"] == [
        "rag:file-1:chunk-1",
        "guide@2026-08-01",
    ]

    value.lease = value.lease.model_copy(
        update={"stage_run_id": "assess-1", "stage_code": "EVIDENCE_ASSESS"}
    )
    value.dependency_results = {"EVIDENCE_RETRIEVAL": result}
    task = AgentTaskFactory(
        now=lambda: datetime(2026, 8, 21, 9, 1, tzinfo=UTC),
        new_invocation_id=lambda: "invocation-assess",
    ).build_evidence_assess(value)
    projected_result = task["payload"]["executed_actions"][0]["structured_result"]
    assert projected_result["data"] == []
    assert projected_result["evidence_records"] == [support_record]


def test_rag_hit_without_source_trace_is_not_promoted_to_evidence() -> None:
    value = context(
        support=[rag_action("action-01", "SUPPORT")],
        counter=[],
    )

    result = EvidenceRetrievalStageHandler(FakeRagMcpClient(source_trace=False)).execute(value)

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    assert retrieval["completeness"] == "PARTIAL"
    structured = retrieval["executed_actions"][0]["structured_result"]
    assert structured["status"] == "PARTIAL"
    assert structured["evidence_records"] == []
    assert structured["missing_fields"] == ["rag_hit_source_trace"]


def test_structured_metrics_become_claim_scoped_evidence_candidates() -> None:
    value = context(
        support=[metric_action("action-01", "SUPPORT")],
        counter=[metric_action("action-02", "COUNTER")],
    )

    result = EvidenceRetrievalStageHandler(FakeMetricMcpClient()).execute(value)

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    assert retrieval["physical_call_count"] == 1
    assert retrieval["completeness"] == "COMPLETE"
    record = retrieval["executed_actions"][0]["structured_result"]["evidence_records"][0]
    assert record["claim_type"] == "AREA_PROFILE"
    assert record["metric"] == "RESIDENT_POPULATION"
    assert record["value"] == {"kind": "INTEGER", "value": 40000}
    assert record["unit"] == "PERSONS"
    assert record["source"]["authority"] == "PRIMARY_DATA"
    assert record["source"]["document_version"] == "5b206b15c98303940cebfbfa"
    assert record["freshness_status"] == "FRESH"
    assert record["original_anchor"]["anchor_type"] == "DATASET_ROW"
    assert record["missing_context"] == ["QUARTERLY_ADMIN_DONG_AGGREGATE"]


def test_structured_metric_without_matching_trace_is_not_promoted() -> None:
    result = EvidenceRetrievalStageHandler(FakeMetricMcpClient(source_trace=False)).execute(
        context(support=[metric_action("action-01", "SUPPORT")], counter=[])
    )

    retrieval = result["evidence_retrieval"]
    assert isinstance(retrieval, dict)
    structured = retrieval["executed_actions"][0]["structured_result"]
    assert structured["status"] == "PARTIAL"
    assert structured["evidence_records"] == []
    assert structured["missing_fields"] == ["metric_source_trace"]


@pytest.mark.parametrize(
    ("source_date", "max_age_days", "expected"),
    [
        ("2026-08-01", 365, "FRESH"),
        ("2024-08-01", 365, "STALE"),
        ("2026-09-01", 365, "UNKNOWN"),
        (None, 365, "UNKNOWN"),
        ("2026-08-01", None, "NOT_APPLICABLE"),
    ],
)
def test_rag_candidate_freshness_is_deterministic(
    source_date: str | None,
    max_age_days: int | None,
    expected: str,
) -> None:
    assert (
        EvidenceRetrievalStageHandler._freshness_status(
            source_date,
            as_of="2026-08-21",
            max_age_days=max_age_days,
        )
        == expected
    )
