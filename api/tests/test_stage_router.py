from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import ExternalExecutionUnavailableError
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
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext
from app.workflows.stage_router import FirstProposalStageRouter


def lease(stage: FirstProposalStage = FirstProposalStage.AREA_RESOLUTION) -> StageLease:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return StageLease(
        workflow_run_id="workflow-1",
        stage_run_id="stage-1",
        stage_code=stage.value,
        input_digest="a" * 64,
        lease_token="lease-token",
        lease_expires_at=now + timedelta(seconds=45),
        attempt=1,
        head=HeadFence(
            workflow_generation=1,
            state_version=1,
            founder_snapshot_id="founder-1",
            area_snapshot_id="area-1",
            evidence_snapshot_id=None,
            policy_snapshot_id="policy-1",
            index_generation_id=None,
            seed_registry_id=None,
        ),
    )


def context(stage_lease: StageLease) -> StageContext:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return StageContext(
        lease=stage_lease,
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
                resolution_status=AreaResolutionStatus.UNRESOLVED,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
            ),
            updated_at=now,
        ),
        dependency_results={},
    )


class FakeContextRepository:
    def __init__(self, value: StageContext) -> None:
        self.value = value
        self.calls = 0

    def load(self, stage_lease: StageLease) -> StageContext:
        assert stage_lease == self.value.lease
        self.calls += 1
        return self.value


class FakeHandler:
    def __init__(self) -> None:
        self.contexts: list[StageContext] = []

    def execute(self, value: StageContext) -> dict[str, object]:
        self.contexts.append(value)
        return {"stage": value.lease.stage_code}


def test_router_loads_context_and_calls_only_registered_stage_handler() -> None:
    stage_lease = lease()
    stage_context = context(stage_lease)
    repository = FakeContextRepository(stage_context)
    handler = FakeHandler()
    router = FirstProposalStageRouter(
        repository,
        {FirstProposalStage.AREA_RESOLUTION: handler},
    )

    assert router.execute(stage_lease) == {"stage": "AREA_RESOLUTION"}
    assert repository.calls == 1
    assert handler.contexts == [stage_context]


def test_router_rejects_unconfigured_stage_before_loading_context() -> None:
    stage_lease = lease(FirstProposalStage.CLAIM_PLAN)
    repository = FakeContextRepository(context(stage_lease))
    router = FirstProposalStageRouter(repository, {})

    with pytest.raises(ExternalExecutionUnavailableError, match="not configured"):
        router.execute(stage_lease)
    assert repository.calls == 0
