from datetime import UTC, datetime

import pytest

from app.domain.models import CafeTypePreference, CoverageProfile, Project
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
        self.cancelled = False

    def start(self, **kwargs: object) -> WorkflowRun:
        assert kwargs["workflow_code"] == WorkflowCode.FIRST_PROPOSAL
        return workflow_run(WorkflowStatus.QUEUED)

    def get_progress(self, **kwargs: object) -> WorkflowProgress:
        del kwargs
        return self.progress

    def cancel(self, **kwargs: object) -> WorkflowRun:
        del kwargs
        self.cancelled = True
        return workflow_run(WorkflowStatus.CANCELLED)


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


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def wait(self, seconds: float) -> None:
        self.value += seconds


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


def progress(status: WorkflowStatus) -> WorkflowProgress:
    stages = [
        WorkflowStageProgress(
            stage_run_id=f"stage-{index}",
            stage_code=stage.value,
            status=(
                StageStatus.SUCCEEDED
                if status == WorkflowStatus.SUCCEEDED
                else StageStatus.READY
            ),
            attempt=1 if status == WorkflowStatus.SUCCEEDED else 0,
            updated_at=INSTANT,
            completed_at=INSTANT if status == WorkflowStatus.SUCCEEDED else None,
        )
        for index, stage in enumerate(FirstProposalStage, start=1)
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


def test_canary_requires_all_thirteen_stages_and_current_result_then_cleans() -> None:
    projects = FakeProjects()
    workflows = FakeWorkflows(progress(WorkflowStatus.SUCCEEDED))
    cleaner = FakeCleaner()

    report = FirstProposalCanary(
        projects=projects,
        workflows=workflows,
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "probe",
    ).run(timeout_seconds=10, poll_interval_seconds=1)

    assert report.as_dict() == {
        "status": "verified",
        "requested_cafe_type_preference": "OPEN_TO_BOTH",
        "workflow_status": "SUCCEEDED",
        "stage_count": 13,
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
    assert not workflows.cancelled


def test_franchise_only_canary_requires_a_ranked_verified_real_brand() -> None:
    franchise_only = result()
    franchise_only.candidates = franchise_only.candidates[1:]
    franchise_only.primary_candidate_id = "candidate-2"
    franchise_only.candidates[0]["market_signals"] = result().candidates[0][
        "market_signals"
    ]
    projects = FakeProjects()

    report = FirstProposalCanary(
        projects=projects,
        workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
        results=FakeResults(franchise_only),
        cleaner=FakeCleaner(),
        new_id=lambda: "franchise-only",
    ).run(
        timeout_seconds=10,
        poll_interval_seconds=1,
        cafe_type_preference=CafeTypePreference.FRANCHISE_ONLY,
    )

    assert projects.founder.cafe_type_preference == CafeTypePreference.FRANCHISE_ONLY
    assert report.candidate_case_types == ("FRANCHISE",)
    assert report.franchise_candidate_brand_ids == ("kr-ediya-coffee",)


def test_franchise_only_canary_rejects_unverified_brand() -> None:
    franchise_only = result()
    franchise_only.candidates = franchise_only.candidates[1:]
    franchise_only.primary_candidate_id = "candidate-2"
    franchise_only.candidates[0]["market_signals"] = result().candidates[0][
        "market_signals"
    ]
    franchise_only.candidates[0]["franchise"]["eligibility"] = "UNVERIFIED"

    with pytest.raises(
        FirstProposalCanaryError,
        match="CANARY_VERIFIED_FRANCHISE_CANDIDATE_MISSING",
    ):
        FirstProposalCanary(
            projects=FakeProjects(),
            workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
            results=FakeResults(franchise_only),
            cleaner=FakeCleaner(),
            new_id=lambda: "unverified-franchise",
        ).run(
            timeout_seconds=10,
            poll_interval_seconds=1,
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
        canary.run(timeout_seconds=10, poll_interval_seconds=1)

    assert cleaner.calls == [
        ("canary-project", "first-proposal-canary-missing-franchise")
    ]


def test_canary_rejects_result_without_required_grounded_market_signals() -> None:
    ungrounded = result()
    ungrounded.candidates[0]["market_signals"] = []
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=FakeWorkflows(progress(WorkflowStatus.SUCCEEDED)),
        results=FakeResults(ungrounded),
        cleaner=cleaner,
        new_id=lambda: "ungrounded",
    )

    with pytest.raises(
        FirstProposalCanaryError,
        match="CANARY_GROUNDED_MARKET_SIGNALS_MISSING",
    ):
        canary.run(timeout_seconds=10, poll_interval_seconds=1)

    assert cleaner.calls == [("canary-project", "first-proposal-canary-ungrounded")]


def test_canary_rejects_hidden_stage_retry_and_still_cleans() -> None:
    retried = progress(WorkflowStatus.SUCCEEDED)
    retried.stages[2] = retried.stages[2].model_copy(update={"attempt": 2})
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=FakeWorkflows(retried),
        results=FakeResults(result()),
        cleaner=cleaner,
        new_id=lambda: "retried",
    )

    with pytest.raises(FirstProposalCanaryError, match="CANARY_STAGE_RETRIED"):
        canary.run(timeout_seconds=10, poll_interval_seconds=1)

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
        canary.run(timeout_seconds=10, poll_interval_seconds=1)

    assert cleaner.calls == [("canary-project", "first-proposal-canary-partial")]
    assert not workflows.cancelled


def test_canary_cancels_active_workflow_before_timeout_cleanup() -> None:
    clock = Clock()
    workflows = FakeWorkflows(progress(WorkflowStatus.RUNNING))
    cleaner = FakeCleaner()
    canary = FirstProposalCanary(
        projects=FakeProjects(),
        workflows=workflows,
        results=FakeResults(result()),
        cleaner=cleaner,
        now=clock.now,
        wait=clock.wait,
        new_id=lambda: "timeout",
    )

    with pytest.raises(FirstProposalCanaryError, match="CANARY_TIMED_OUT"):
        canary.run(timeout_seconds=1, poll_interval_seconds=1)

    assert workflows.cancelled
    assert cleaner.calls == [("canary-project", "first-proposal-canary-timeout")]
