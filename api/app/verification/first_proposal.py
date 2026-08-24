"""운영 검증은 동기식 단일 제안 실행과 저장 결과를 즉시 대조한다."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, text

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
from app.results.models import ResultFreshness, ResultOutcomeStatus, ResultView
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode, WorkflowProgress, WorkflowRun, WorkflowStatus


class ProjectOperations(Protocol):
    def create_project(self, *, user_id: str, idempotency_key: str) -> Project: ...

    def confirm_onboarding(
        self,
        *,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        founder: FounderState,
        area: AreaState | None = None,
    ) -> Project: ...


class WorkflowOperations(Protocol):
    def start(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun: ...

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress: ...


class ResultOperations(Protocol):
    def get_current(self, *, project_id: str, user_id: str) -> ResultView: ...


class CanaryCleaner(Protocol):
    def cleanup(self, *, project_id: str, user_id: str) -> None: ...


class FirstProposalCanaryError(RuntimeError):
    def __init__(self, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class FirstProposalCanaryReport:
    status: str
    requested_cafe_type_preference: str
    workflow_status: str
    stage_count: int
    max_stage_attempt: int
    elapsed_ms: int
    candidate_count: int
    candidate_case_types: tuple[str, ...]
    franchise_candidate_brand_ids: tuple[str, ...]
    market_signals: tuple[dict[str, object], ...]
    result_freshness: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requested_cafe_type_preference": self.requested_cafe_type_preference,
            "workflow_status": self.workflow_status,
            "stage_count": self.stage_count,
            "max_stage_attempt": self.max_stage_attempt,
            "elapsed_ms": self.elapsed_ms,
            "candidate_count": self.candidate_count,
            "candidate_case_types": list(self.candidate_case_types),
            "franchise_candidate_brand_ids": list(self.franchise_candidate_brand_ids),
            "market_signals": list(self.market_signals),
            "result_freshness": self.result_freshness,
        }


class PostgresFirstProposalCanaryCleaner:
    """Delete only resources owned by one generated canary identity."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def cleanup(self, *, project_id: str, user_id: str) -> None:
        with self._engine.begin() as connection:
            owned = connection.execute(
                text(
                    "SELECT project_id FROM venture_projects "
                    "WHERE project_id=:project_id AND owner_user_id=:user_id FOR UPDATE"
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one_or_none()
            if owned is not None:
                connection.execute(
                    text(
                        """
                        DELETE FROM workflow_outbox queued
                        WHERE queued.aggregate_id IN (
                            SELECT run.workflow_run_id
                            FROM workflow_runs run
                            WHERE run.project_id=:project_id
                            UNION
                            SELECT stage.stage_run_id
                            FROM stage_runs stage
                            JOIN workflow_runs run
                              ON run.workflow_run_id=stage.workflow_run_id
                            WHERE run.project_id=:project_id
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM workflow_runs run
                            WHERE run.project_id=:project_id
                              AND queued.payload_json->>'workflow_run_id'=run.workflow_run_id
                        )
                        """
                    ),
                    {"project_id": project_id},
                )
                # Evidence snapshots point to both stage runs and evidence records with
                # RESTRICT semantics. Remove the canary-owned snapshots first so the
                # project cascade cannot encounter those cross-branch references.
                connection.execute(
                    text("DELETE FROM evidence_snapshots WHERE project_id=:project_id"),
                    {"project_id": project_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM venture_projects "
                        "WHERE project_id=:project_id AND owner_user_id=:user_id"
                    ),
                    {"project_id": project_id, "user_id": user_id},
                )
            connection.execute(
                text("DELETE FROM workflow_idempotency_records WHERE user_id=:user_id"),
                {"user_id": user_id},
            )
            connection.execute(
                text("DELETE FROM idempotency_records WHERE user_id=:user_id"),
                {"user_id": user_id},
            )

        with self._engine.connect() as connection:
            remaining = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM venture_projects
                       WHERE project_id=:project_id OR owner_user_id=:user_id)
                    + (SELECT COUNT(*) FROM idempotency_records WHERE user_id=:user_id)
                    + (SELECT COUNT(*) FROM workflow_idempotency_records WHERE user_id=:user_id)
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one()
        if remaining != 0:
            raise FirstProposalCanaryError("CANARY_CLEANUP_INCOMPLETE")


class FirstProposalCanary:
    def __init__(
        self,
        *,
        projects: ProjectOperations,
        workflows: WorkflowOperations,
        results: ResultOperations,
        cleaner: CanaryCleaner,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._projects = projects
        self._workflows = workflows
        self._results = results
        self._cleaner = cleaner
        self._new_id = new_id or (lambda: uuid4().hex)

    def run(
        self,
        *,
        cafe_type_preference: CafeTypePreference = CafeTypePreference.OPEN_TO_BOTH,
        founder: FounderState | None = None,
        area: AreaState | None = None,
    ) -> FirstProposalCanaryReport:
        # 사용자 의도: 운영 평가는 고정된 한 입력이 아니라 합성 창업자 조건을 실제
        # 온보딩 경로에 넣어야 하며, 입력하지 않은 기존 카나리 동작은 그대로 유지한다.
        selected_founder = founder or FounderState(
            target_area_input="서울특별시 마포구 망원동",
            own_funds_krw=70_000_000,
            borrowing_intent=BorrowingIntent.UNDECIDED,
            cafe_type_preference=cafe_type_preference,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
            preferences=["대학가 생활권", "개인카페와 프랜차이즈 비교"],
        )
        selected_area = area or AreaState(
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
        selected_preference = selected_founder.cafe_type_preference
        canary_id = self._new_id()
        user_id = f"first-proposal-canary-{canary_id}"
        project: Project | None = None
        workflow: WorkflowRun | None = None
        try:
            project = self._projects.create_project(
                user_id=user_id,
                idempotency_key=f"{canary_id}:create",
            )
            self._projects.confirm_onboarding(
                project_id=project.project_id,
                user_id=user_id,
                idempotency_key=f"{canary_id}:onboarding",
                founder=selected_founder,
                area=selected_area,
            )
            workflow = self._workflows.start(
                project_id=project.project_id,
                user_id=user_id,
                workflow_code=WorkflowCode.FIRST_PROPOSAL,
                idempotency_key=f"{canary_id}:workflow",
            )
            progress = self._workflows.get_progress(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=workflow.workflow_run_id,
            )
            return self._validate_result(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=workflow.workflow_run_id,
                progress=progress,
                cafe_type_preference=selected_preference,
            )
        finally:
            if project is not None:
                self._cleaner.cleanup(project_id=project.project_id, user_id=user_id)

    def _validate_result(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        progress: WorkflowProgress,
        cafe_type_preference: CafeTypePreference,
    ) -> FirstProposalCanaryReport:
        if progress.status != WorkflowStatus.SUCCEEDED:
            raise FirstProposalCanaryError(
                "CANARY_WORKFLOW_NOT_SUCCEEDED",
                {
                    "workflow_status": progress.status.value,
                    "completed_stage_count": progress.completed_stage_count,
                    "terminal_reason_codes": progress.terminal_reason_codes,
                    "human_review_requests": [
                        request.model_dump(mode="json")
                        for request in progress.human_review_requests
                    ],
                },
            )
        expected_stages = {FirstProposalStage.RUN_PROPOSAL.value}
        observed_stages = {stage.stage_code for stage in progress.stages}
        unsuccessful = [
            stage.stage_code for stage in progress.stages if stage.status.value != "SUCCEEDED"
        ]
        if (
            len(progress.stages) != len(expected_stages)
            or observed_stages != expected_stages
            or unsuccessful
        ):
            raise FirstProposalCanaryError(
                "CANARY_STAGE_SET_INVALID",
                {
                    "expected_stage_count": len(expected_stages),
                    "observed_stage_count": len(progress.stages),
                    "missing_stage_codes": sorted(expected_stages - observed_stages),
                    "unexpected_stage_codes": sorted(observed_stages - expected_stages),
                    "unsuccessful_stage_codes": unsuccessful,
                },
            )
        retried = sorted(stage.stage_code for stage in progress.stages if stage.attempt != 1)
        if retried:
            raise FirstProposalCanaryError(
                "CANARY_STAGE_RETRIED",
                {
                    "retried_stage_codes": retried,
                    "stage_attempts": {
                        stage.stage_code: stage.attempt for stage in progress.stages
                    },
                },
            )
        result = self._results.get_current(project_id=project_id, user_id=user_id)
        candidate_ids = {candidate.get("candidate_id") for candidate in result.candidates}
        primary_is_valid = (
            result.primary_candidate_id is None
            and result.outcome_status == ResultOutcomeStatus.NO_REVIEWABLE_CANDIDATES
            and all(
                candidate.get("review_status") == "EXCLUDED"
                and candidate.get("rank") is None
                and candidate.get("is_primary_next_review") is False
                for candidate in result.candidates
            )
        ) or (
            result.primary_candidate_id is not None
            and result.outcome_status == ResultOutcomeStatus.REVIEWABLE_CANDIDATES
            and result.primary_candidate_id in candidate_ids
        )
        if (
            result.workflow_run_id != workflow_run_id
            or result.freshness != ResultFreshness.CURRENT
            or not result.candidates
            or not primary_is_valid
        ):
            raise FirstProposalCanaryError(
                "CANARY_RESULT_INVALID",
                {
                    "result_workflow_matches": result.workflow_run_id == workflow_run_id,
                    "result_freshness": result.freshness.value,
                    "candidate_count": len(result.candidates),
                },
            )
        case_types = sorted(
            {
                str(candidate["case_type"])
                for candidate in result.candidates
                if candidate.get("case_type") in {"INDEPENDENT", "FRANCHISE"}
            }
        )
        expected_case_types = {
            CafeTypePreference.OPEN_TO_BOTH: {"INDEPENDENT", "FRANCHISE"},
            CafeTypePreference.INDEPENDENT_ONLY: {"INDEPENDENT"},
            CafeTypePreference.FRANCHISE_ONLY: {"FRANCHISE"},
        }[cafe_type_preference]
        if set(case_types) != expected_case_types:
            raise FirstProposalCanaryError(
                "CANARY_CANDIDATE_TYPE_INVALID",
                {
                    "requested_cafe_type_preference": cafe_type_preference.value,
                    "expected_candidate_case_types": sorted(expected_case_types),
                    "observed_candidate_case_types": case_types,
                },
            )
        franchise_brand_ids = sorted(
            {
                str(franchise["brand_id"])
                for candidate in result.candidates
                if candidate.get("case_type") == "FRANCHISE"
                and candidate.get("review_status") in {"REVIEW_RECOMMENDED", "CONDITIONAL_REVIEW"}
                and isinstance(candidate.get("rank"), int)
                and isinstance((franchise := candidate.get("franchise")), dict)
                and franchise.get("eligibility") == "VERIFIED"
                and isinstance(franchise.get("brand_id"), str)
                and franchise["brand_id"]
            }
        )
        if (
            result.outcome_status == ResultOutcomeStatus.REVIEWABLE_CANDIDATES
            and "FRANCHISE" in expected_case_types
            and not franchise_brand_ids
        ):
            raise FirstProposalCanaryError(
                "CANARY_VERIFIED_FRANCHISE_CANDIDATE_MISSING",
                {
                    "requested_cafe_type_preference": cafe_type_preference.value,
                    "candidate_count": len(result.candidates),
                },
            )
        ungrounded_recommendations = [
            str(candidate.get("candidate_id"))
            for candidate in result.candidates
            if candidate.get("review_status") == "REVIEW_RECOMMENDED"
            and not candidate.get("evidence_refs")
            and not candidate.get("market_signals")
            and not candidate.get("official_documents")
        ]
        if ungrounded_recommendations:
            raise FirstProposalCanaryError(
                "CANARY_UNGROUNDED_RECOMMENDATION",
                {"candidate_ids": ungrounded_recommendations},
            )
        market_signals = sorted(
            (
                {
                    key: signal[key]
                    for key in (
                        "signal_type",
                        "value",
                        "unit",
                        "data_date",
                        "source_ref",
                    )
                }
                for candidate in result.candidates
                for signal in candidate.get("market_signals", [])
                if isinstance(signal, dict)
                and all(
                    key in signal
                    for key in (
                        "signal_type",
                        "value",
                        "unit",
                        "data_date",
                        "source_ref",
                    )
                )
            ),
            key=lambda signal: str(signal["signal_type"]),
        )
        return FirstProposalCanaryReport(
            status="verified",
            requested_cafe_type_preference=cafe_type_preference.value,
            workflow_status=progress.status.value,
            stage_count=len(progress.stages),
            max_stage_attempt=max(stage.attempt for stage in progress.stages),
            elapsed_ms=max(
                0,
                round((progress.updated_at - progress.created_at).total_seconds() * 1000),
            ),
            candidate_count=len(result.candidates),
            candidate_case_types=tuple(case_types),
            franchise_candidate_brand_ids=tuple(franchise_brand_ids),
            market_signals=tuple(market_signals),
            result_freshness=result.freshness.value,
        )
