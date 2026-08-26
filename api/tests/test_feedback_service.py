"""결과를 선택한 뒤에도 현재 프로젝트 상태를 기준으로 조건 변경안을 만든다."""

from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    BorrowingIntent,
    CafeTypePreference,
    CoverageProfile,
    FounderState,
    OperationMode,
    Project,
    VentureState,
    VentureStatus,
)
from app.feedback.models import FeedbackPreviewRecord, FeedbackPreviewStatus
from app.feedback.service import FeedbackService
from app.results.models import AuditStatus, ResultFreshness, ResultView
from app.workflows.models import HeadFence

NOW = datetime(2026, 8, 26, tzinfo=UTC)
RESULT_HEAD = HeadFence(
    workflow_generation=1,
    state_version=1,
    founder_snapshot_id="project-1:state:1:founder",
    area_snapshot_id="project-1:state:1:area",
    policy_snapshot_id="policy-1",
)
CURRENT_HEAD = HeadFence(
    workflow_generation=1,
    state_version=2,
    founder_snapshot_id="project-1:state:2:founder",
    area_snapshot_id="project-1:state:2:area",
    policy_snapshot_id="policy-1",
)
STATE = VentureState(
    project_id="project-1",
    user_id="user-1",
    state_version=2,
    status=VentureStatus.RESULT_READY,
    founder=FounderState(
        target_area_input="서울특별시 성동구 성수동2가",
        own_funds_krw=200_000_000,
        borrowing_intent=BorrowingIntent.NO,
        cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
        operation_mode=OperationMode.DIRECT_FULL_TIME,
    ),
    area=AreaState(
        resolution_status=AreaResolutionStatus.RESOLVED,
        coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
    ),
    updated_at=NOW,
)
RESULT = ResultView(
    result_bundle_id="result-1",
    project_id="project-1",
    workflow_run_id="workflow-1",
    head=RESULT_HEAD,
    candidates=[{"candidate_id": "candidate-1"}],
    primary_candidate_id="candidate-1",
    audit_status=AuditStatus.PASSED,
    created_at=NOW,
    freshness=ResultFreshness.STALE,
    stale_head_dimensions=["state_version", "founder_snapshot_id", "area_snapshot_id"],
    current_head=CURRENT_HEAD,
)


class Projects:
    def get_project(self, **_: str) -> Project:
        return Project(project_id="project-1", user_id="user-1", created_at=NOW, state=STATE)


class Results:
    def get_current(self, **_: str) -> ResultView:
        return RESULT


class Repository:
    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    def begin_preview(self, **values: Any) -> FeedbackPreviewRecord:
        self.received = values
        return FeedbackPreviewRecord(
            preview_id=values["preview_id"],
            project_id=values["project_id"],
            owner_user_id=values["user_id"],
            result_bundle_id=values["result_bundle_id"],
            source_workflow_run_id=values["source_workflow_run_id"],
            base_state_version=values["base_state_version"],
            head=HeadFence.model_validate(values["head_json"]),
            idempotency_key=values["idempotency_key"],
            user_input=values["user_input"],
            task=values["task"],
            status=FeedbackPreviewStatus.NOOP,
            created_at=NOW,
            updated_at=NOW,
        )


class Runtime:
    def invoke(self, _: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("replayed preview must not call the runtime")


def test_feedback_after_candidate_selection_uses_current_project_head() -> None:
    repository = Repository()
    service = FeedbackService(
        repository=repository,  # type: ignore[arg-type]
        projects=Projects(),  # type: ignore[arg-type]
        results=Results(),  # type: ignore[arg-type]
        runtime=Runtime(),
        new_id=lambda: "preview-1",
    )

    preview = service.create_preview(
        project_id="project-1",
        user_id="user-1",
        idempotency_key="feedback-1",
        user_input="내 예산을 3억 원으로 바꿔줘",
    )

    assert preview.head == CURRENT_HEAD
    assert preview.base_state_version == 2
    assert repository.received is not None
    assert repository.received["head_json"] == CURRENT_HEAD.model_dump(mode="json")
    assert repository.received["task"]["head_fence"] == CURRENT_HEAD.model_dump(mode="json")
    assert repository.received["task"]["payload"]["current_state_projection"]["state_version"] == 2
