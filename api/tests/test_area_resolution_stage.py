from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

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
from app.workflows.area_resolution import AreaResolutionStageHandler
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext


def stage_context() -> StageContext:
    now = datetime(2026, 8, 21, tzinfo=UTC)
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
    return StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="stage-1",
            stage_code="AREA_RESOLUTION",
            input_digest="a" * 64,
            lease_token="token",
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
                target_area_input=" 수원 아주대 부근 ",
                own_funds_krw=50_000_000,
                borrowing_intent=BorrowingIntent.UNDECIDED,
                cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
                operation_mode=OperationMode.DIRECT_FULL_TIME,
            ),
            area=AreaState(
                resolution_status=AreaResolutionStatus.UNRESOLVED,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
            ),
            updated_at=now,
        ),
        dependency_results={},
    )


def candidate(code: str, name: str, match_kind: str) -> dict[str, str]:
    return {
        "administrative_code": code,
        "display_name": name,
        "boundary_version": "2026-01",
        "match_kind": match_kind,
    }


class FakeMcpClient:
    def __init__(self, *, status: str, data: list[dict[str, str]]) -> None:
        self.status = status
        self.data = data
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        self.calls.append(kwargs)
        return McpCallOutcome(
            request_id="request-1",
            tool_name="resolve_area",
            tool_version="1.0.0",
            status=self.status,
            is_complete=self.status == "OK",
            structured_content={
                "data": self.data,
                "evidence_records": [],
                "missing_fields": [] if self.status == "OK" else ["area"],
                "conflicts": [],
                "source_trace": [],
                "observed_at": "2026-08-21T10:00:00Z",
            },
        )


def test_exact_candidate_continues_with_deterministic_selection() -> None:
    client = FakeMcpClient(
        status="OK",
        data=[
            candidate("4111755000", "매탄1동", "CONTAINS"),
            candidate("4111756000", "원천동", "EXACT"),
        ],
    )

    result = AreaResolutionStageHandler(client).execute(stage_context())

    assert result["stage_control"] == {"disposition": "CONTINUE", "reason_codes": []}
    assert result["area_resolution"]["selected"]["administrative_code"] == "4111756000"
    assert client.calls[0]["tool_name"] == "resolve_area"
    assert client.calls[0]["arguments"] == {
        "query": "수원 아주대 부근",
        "country_code": "KR",
        "limit": 10,
    }


def test_multiple_nonexact_candidates_wait_for_human_selection() -> None:
    client = FakeMcpClient(
        status="OK",
        data=[
            candidate("4111755000", "매탄1동", "CONTAINS"),
            candidate("4111756000", "원천동", "CONTAINS"),
        ],
    )

    result = AreaResolutionStageHandler(client).execute(stage_context())

    assert result["stage_control"] == {
        "disposition": "WAITING_FOR_HUMAN",
        "reason_codes": ["AREA_SELECTION_REQUIRED"],
    }
    assert result["area_resolution"]["resolution_status"] == "AMBIGUOUS"
    assert result["area_resolution"]["selected"] is None


@pytest.mark.parametrize("status", ["PARTIAL", "STALE", "NOT_FOUND"])
def test_incomplete_source_status_abstains_even_when_candidate_exists(status: str) -> None:
    client = FakeMcpClient(
        status=status,
        data=[candidate("4111756000", "원천동", "EXACT")],
    )

    result = AreaResolutionStageHandler(client).execute(stage_context())

    assert result["stage_control"] == {
        "disposition": "ABSTAIN",
        "reason_codes": [f"AREA_SOURCE_{status}"],
    }
    assert result["area_resolution"]["selected"] is None


def test_empty_ok_result_abstains_instead_of_inventing_an_area() -> None:
    result = AreaResolutionStageHandler(FakeMcpClient(status="OK", data=[])).execute(
        stage_context()
    )

    assert result["stage_control"] == {
        "disposition": "ABSTAIN",
        "reason_codes": ["AREA_NOT_FOUND"],
    }
