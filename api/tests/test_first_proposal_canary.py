"""운영 검증은 단일 제안 실행과 현재 결과를 한 번 읽어 확인해야 한다."""

from datetime import UTC, datetime

import pytest

from app.domain.models import (
    AreaMappingStatus,
    AreaResolutionStatus,
    AreaScopeType,
    AreaState,
    BorrowingIntent,
    CafeTypePreference,
    CandidateSetCompleteness,
    CoverageProfile,
    FounderState,
    OperationMode,
    Project,
)
from app.results.models import AuditStatus, ResultFreshness, ResultView
from app.verification.first_proposal import FirstProposalCanary, FirstProposalCanaryError
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import (
    HeadFence,
    StageStatus,
    WorkflowCode,
    WorkflowProgress,
    WorkflowRun,
    WorkflowStageProgress,
    WorkflowStatus,
)

INSTANT = datetime(2026, 8, 22, tzinfo=UTC)
HEAD = HeadFence(
    workflow_generation=1,
    state_version=1,
    policy_snapshot_id="policy-v1",
    seed_registry_id="seed-v1",
)


class FakeProjects:
    def __init__(self) -> None:
        self.founder = None
        self.area = None

    def create_project(self, *, user_id: str, idempotency_key: str) -> Project:
        assert idempotency_key.endswith(":create")
        return Project(
            project_id="canary-project",
            user_id=user_id,
            created_at=INSTANT,
            state=None,
        )

    def confirm_onboarding(self, **kwargs: object) -> Project:
        self.founder = kwargs["founder"]
        self.area = kwargs["area"]
        return Project(
            project_id=str(kwargs["project_id"]),
            user_id=str(kwargs["user_id"]),
            created_at=INSTANT,
            state=None,
        )


class FakeWorkflows:
    def __init__(self, progress: WorkflowProgress) -> None:
        self.progress = progress

    def start(self, **kwargs: object) -> WorkflowRun:
        assert kwargs["workflow_code"] == WorkflowCode.FIRST_PROPOSAL
        return workflow_run(WorkflowStatus.QUEUED)

    def get_progress(self, **kwargs: object) -> WorkflowProgress:
        del kwargs
        return self.progress


class FakeResults:
    def __init__(self, result: ResultView) -> None:
        self.result = result

    def get_current(self, **kwargs: object) -> ResultView:
        del kwargs
        return self.result


class FakeCleaner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def cleanup(self, *, project_id: str, user_id: str) -> None:
        self.calls.append((project_id, user_id))


def workflow_run(status: WorkflowStatus) -> WorkflowRun:
    return WorkflowRun(
        workflow_run_id="canary-workflow",
        project_id="canary-project",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        status=status,
        head=HEAD,
        input_digest="a" * 64,
        created_at=INSTANT,
        updated_at=INSTANT,
    )


def progress(
    status: WorkflowStatus,
    *,
    cafe_type_preference: CafeTypePreference = CafeTypePreference.OPEN_TO_BOTH,
) -> WorkflowProgress:
    del cafe_type_preference
    stages = [
        WorkflowStageProgress(
            stage_run_id="stage-1",
            stage_code=FirstProposalStage.RUN_PROPOSAL.value,
            status=(
                StageStatus.SUCCEEDED if status == WorkflowStatus.SUCCEEDED else StageStatus.READY
            ),
            attempt=1 if status == WorkflowStatus.SUCCEEDED else 0,
            updated_at=INSTANT,
            completed_at=INSTANT if status == WorkflowStatus.SUCCEEDED else None,
        )
    ]
    return WorkflowProgress(
        **workflow_run(status).model_dump(mode="python"),
        stages=stages,
        completed_stage_count=(len(stages) if status == WorkflowStatus.SUCCEEDED else 0),
        total_stage_count=len(stages),
        current_stage_codes=([] if status == WorkflowStatus.SUCCEEDED else [stages[0].stage_code]),
        human_review_requests=[],
        terminal_reason_codes=([] if status == WorkflowStatus.SUCCEEDED else ["NOT_READY"]),
        poll_after_ms=None if status == WorkflowStatus.SUCCEEDED else 1500,
    )


def result() -> ResultView:
    return ResultView(
        result_bundle_id="result-1",
        project_id="canary-project",
        workflow_run_id="canary-workflow",
        head=HEAD,
        candidates=[
            {
                "candidate_id": "candidate-1",
                "case_type": "INDEPENDENT",
                "rank": 1,
                "market_signals": [
                    {
                        "signal_type": "CAFE_COUNT",
                        "value": 208,
                        "unit": "STORES",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/store",
                    },
                    {
                        "signal_type": "OPEN_COUNT",
                        "value": 9,
                        "unit": "STORES_PER_QUARTER",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/store",
                    },
                    {
                        "signal_type": "CLOSE_COUNT",
                        "value": 6,
                        "unit": "STORES_PER_QUARTER",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/store",
                    },
                    {
                        "signal_type": "CLOSURE_RATE",
                        "value": 2.88,
                        "unit": "PERCENT_DERIVED",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/store",
                    },
                    {
                        "signal_type": "ESTIMATED_SALES",
                        "value": 2_596_733_728,
                        "unit": "KRW_PER_QUARTER_ESTIMATE",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/sales",
                    },
                    {
                        "signal_type": "FOOT_TRAFFIC",
                        "value": 12_465_323,
                        "unit": "PERSON_VISITS_PER_QUARTER_ESTIMATE",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/foot",
                    },
                    {
                        "signal_type": "RESIDENT_POPULATION",
                        "value": 37_068,
                        "unit": "PERSONS",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/resident",
                    },
                    {
                        "signal_type": "WORKER_POPULATION",
                        "value": 7_365,
                        "unit": "PERSONS",
                        "data_date": "2026-03-31",
                        "source_ref": "https://data.seoul.go.kr/worker",
                    },
                ],
            },
            {
                "candidate_id": "candidate-2",
                "case_type": "FRANCHISE",
                "display_name": "이디야커피",
                "review_status": "CONDITIONAL_REVIEW",
                "rank": 2,
                "franchise": {
                    "brand_id": "kr-ediya-coffee",
                    "eligibility": "VERIFIED",
                },
                "market_signals": [],
            },
        ],
        primary_candidate_id="candidate-1",
        audit_status=AuditStatus.PASSED,
        created_at=INSTANT,
        freshness=ResultFreshness.CURRENT,
        stale_head_dimensions=[],
        current_head=HEAD,
    )


def test_canary_requires_single_execution_and_current_result_then_cleans() -> None:
    projects = FakeProjects()
    workflows = FakeWorkflows(progress(WorkflowStatus.SUCCEEDED))
    cleaner = FakeCleaner()

    report = FirstProposalCanary(
        projects=projects,
        workflows=workflows,
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "probe",
    ).run()

    assert report.as_dict() == {
        "status": "verified",
        "requested_cafe_type_preference": "OPEN_TO_BOTH",
        "workflow_status": "SUCCEEDED",
        "stage_count": 1,
        "max_stage_attempt": 1,
        "elapsed_ms": 0,
        "candidate_count": 2,
        "candidate_case_types": ["FRANCHISE", "INDEPENDENT"],
        "franchise_candidate_brand_ids": ["kr-ediya-coffee"],
        "market_signals": [
            {
                "signal_type": "CAFE_COUNT",
                "value": 208,
                "unit": "STORES",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/store",
            },
            {
                "signal_type": "CLOSE_COUNT",
                "value": 6,
                "unit": "STORES_PER_QUARTER",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/store",
            },
            {
                "signal_type": "CLOSURE_RATE",
                "value": 2.88,
                "unit": "PERCENT_DERIVED",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/store",
            },
            {
                "signal_type": "ESTIMATED_SALES",
                "value": 2_596_733_728,
                "unit": "KRW_PER_QUARTER_ESTIMATE",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/sales",
            },
            {
                "signal_type": "FOOT_TRAFFIC",
                "value": 12_465_323,
                "unit": "PERSON_VISITS_PER_QUARTER_ESTIMATE",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/foot",
            },
            {
                "signal_type": "OPEN_COUNT",
                "value": 9,
                "unit": "STORES_PER_QUARTER",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/store",
            },
            {
                "signal_type": "RESIDENT_POPULATION",
                "value": 37_068,
                "unit": "PERSONS",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/resident",
            },
            {
                "signal_type": "WORKER_POPULATION",
                "value": 7_365,
                "unit": "PERSONS",
                "data_date": "2026-03-31",
                "source_ref": "https://data.seoul.go.kr/worker",
            },
        ],
        "result_freshness": "CURRENT",
    }
    assert projects.founder is not None
    assert projects.founder.target_area_input == "서울특별시 마포구 망원동"
    assert projects.area is not None
    assert projects.area.administrative_code == "1144012300"
    assert projects.area.coverage_profile == CoverageProfile.R2_REGIONAL_CONNECTOR
    assert projects.area.source_revision == "MOIS_LEGAL_DONG_20260301"
    assert cleaner.calls == [("canary-project", "first-proposal-canary-probe")]


def test_canary_uses_the_supplied_synthetic_founder_profile() -> None:
    """운영 평가는 서로 다른 창업자 조건을 실제 온보딩 입력으로 전달해야 한다."""

    projects = FakeProjects()
    founder = FounderState(
        target_area_input="서울특별시 마포구 망원동",
        own_funds_krw=150_000_000,
        borrowing_intent=BorrowingIntent.YES,
        cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
        operation_mode=OperationMode.EMPLOYEE_LED,
        preferences=["직원 중심 운영"],
    )
    area = AreaState(
        resolution_status=AreaResolutionStatus.RESOLVED,
        area_id="legal-dong:1144012300",
        scope_type=AreaScopeType.LEGAL_DONG,
        administrative_code="1144012300",
        legal_dong_code="1144012300",
        administrative_dong_codes=[],
        mapping_status=AreaMappingStatus.UNVERIFIED,
        candidate_set_completeness=CandidateSetCompleteness.UNVERIFIED,
        source_revision="MOIS_LEGAL_DONG_20260301",
        display_name="서울특별시 마포구 망원동",
        boundary_version=None,
        coverage_profile=CoverageProfile.R2_REGIONAL_CONNECTOR,
        unavailable_fields=[],
    )
    franchise_result = result().model_copy(
        update={
            "candidates": [result().candidates[1]],
            "primary_candidate_id": "candidate-2",
        }
    )

    report = FirstProposalCanary(
        projects=projects,
        workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
        results=FakeResults(franchise_result),
        cleaner=FakeCleaner(),
        new_id=lambda: "synthetic-profile",
    ).run(founder=founder, area=area)

    assert projects.founder == founder
    assert projects.area == area
    assert report.requested_cafe_type_preference == "FRANCHISE_ONLY"


def test_franchise_only_canary_requires_a_ranked_verified_real_brand() -> None:
    franchise_only = result()
    franchise_only.candidates = franchise_only.candidates[1:]
    franchise_only.primary_candidate_id = "candidate-2"
    franchise_only.candidates[0]["market_signals"] = result().candidates[0]["market_signals"]
    projects = FakeProjects()

    report = FirstProposalCanary(
        projects=projects,
        workflows=FakeWorkflows(
            progress(
                WorkflowStatus.SUCCEEDED,
                cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
            )
        ),
        results=FakeResults(franchise_only),
        cleaner=FakeCleaner(),
        new_id=lambda: "franchise-only",
    ).run(
        cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
    )

    assert projects.founder.cafe_type_preference == CafeTypePreference.FRANCHISE_ONLY
    assert report.stage_count == 1
    assert report.candidate_case_types == ("FRANCHISE",)
    assert report.franchise_candidate_brand_ids == ("kr-ediya-coffee",)


def test_franchise_only_canary_rejects_unverified_brand() -> None:
    franchise_only = result()
    franchise_only.candidates = franchise_only.candidates[1:]
    franchise_only.primary_candidate_id = "candidate-2"
    franchise_only.candidates[0]["market_signals"] = result().candidates[0]["market_signals"]
    franchise_only.candidates[0]["franchise"]["eligibility"] = "UNVERIFIED"

    with pytest.raises(
        FirstProposalCanaryError,
        match="CANARY_VERIFIED_FRANCHISE_CANDIDATE_MISSING",
    ):
        FirstProposalCanary(
            projects=FakeProjects(),
            workflows=FakeWorkflows(
                progress(
                    WorkflowStatus.SUCCEEDED,
                    cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
                )
            ),
            results=FakeResults(franchise_only),
            cleaner=FakeCleaner(),
            new_id=lambda: "unverified-franchise",
        ).run(
            cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
        )


def test_canary_rejects_open_to_both_result_without_franchise_candidate() -> None:
    independent_only = result()
    independent_only.candidates = independent_only.candidates[:1]
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
        results=FakeResults(independent_only),
        cleaner=cleaner,
        new_id=lambda: "missing-franchise",
    )

    with pytest.raises(
        FirstProposalCanaryError,
        match="CANARY_CANDIDATE_TYPE_INVALID",
    ):
        canary.run()

    assert cleaner.calls == [("canary-project", "first-proposal-canary-missing-franchise")]


def test_canary_allows_conditional_result_while_market_evidence_is_pending() -> None:
    ungrounded = result()
    ungrounded.candidates[0]["market_signals"] = []
    ungrounded.candidates[0]["review_status"] = "CONDITIONAL_REVIEW"
    ungrounded.candidates[1]["market_signals"] = []
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
        results=FakeResults(ungrounded),
        cleaner=cleaner,
        new_id=lambda: "ungrounded",
    )

    report = canary.run()

    assert report.market_signals == ()
    assert cleaner.calls == [("canary-project", "first-proposal-canary-ungrounded")]


def test_canary_rejects_recommended_candidate_without_grounded_evidence() -> None:
    ungrounded = result()
    ungrounded.candidates[0]["market_signals"] = []
    ungrounded.candidates[0]["evidence_refs"] = []
    ungrounded.candidates[0]["review_status"] = "REVIEW_RECOMMENDED"

    with pytest.raises(
        FirstProposalCanaryError,
        match="CANARY_UNGROUNDED_RECOMMENDATION",
    ):
        FirstProposalCanary(
            projects=FakeProjects(),
            workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
            results=FakeResults(ungrounded),
            cleaner=FakeCleaner(),
            new_id=lambda: "unsafe-recommendation",
        ).run()


def test_canary_rejects_hidden_stage_retry_and_still_cleans() -> None:
    retried = progress(WorkflowStatus.SUCCEEDED)
    retried.stages[0] = retried.stages[0].model_copy(update={"attempt": 2})
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=FakeWorkflows(retried),
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "retried",
    )

    with pytest.raises(FirstProposalCanaryError, match="CANARY_STAGE_RETRIED"):
        canary.run()

    assert cleaner.calls == [("canary-project", "first-proposal-canary-retried")]


def test_canary_fails_closed_on_partial_terminal_and_still_cleans() -> None:
    workflows = FakeWorkflows(progress(WorkflowStatus.PARTIAL))
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=workflows,
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "partial",
    )

    with pytest.raises(FirstProposalCanaryError, match="CANARY_WORKFLOW_NOT_SUCCEEDED"):
        canary.run()

    assert cleaner.calls == [("canary-project", "first-proposal-canary-partial")]


def test_canary_rejects_active_workflow_immediately_and_cleans() -> None:
    workflows = FakeWorkflows(progress(WorkflowStatus.RUNNING))
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=workflows,
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "still-running",
    )

    with pytest.raises(FirstProposalCanaryError, match="CANARY_WORKFLOW_NOT_SUCCEEDED"):
        canary.run()

    assert cleaner.calls == [("canary-project", "first-proposal-canary-still-running")]
