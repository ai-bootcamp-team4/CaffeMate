import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

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
                        "allowed_tools": ["get_source_health"],
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
        EvidenceRetrievalStageHandler(client).execute(
            context(support=[invalid], counter=[])
        )
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
