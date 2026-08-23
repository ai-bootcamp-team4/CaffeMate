import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Protocol
from uuid import uuid4

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
from app.results.models import ResultFreshness, ResultView
from app.selections.models import (
    CandidateSelection,
    PropertyTermsApplication,
    PropertyTermsInput,
)
from app.selections.preparation import PreparationGuide
from app.verification.first_proposal import CanaryCleaner
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import (
    HeadFence,
    WorkflowCode,
    WorkflowProgress,
    WorkflowRun,
    WorkflowStatus,
)


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

    def cancel(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> WorkflowRun: ...


class ResultOperations(Protocol):
    def get_current(self, *, project_id: str, user_id: str) -> ResultView: ...


class CandidateSelectionOperations(Protocol):
    def select(
        self,
        *,
        project_id: str,
        user_id: str,
        result_bundle_id: str,
        candidate_id: str,
        expected_head: HeadFence,
        idempotency_key: str,
    ) -> CandidateSelection: ...


class PropertyTermsOperations(Protocol):
    def apply(
        self,
        *,
        project_id: str,
        selection_id: str,
        user_id: str,
        expected_state_version: int,
        terms: PropertyTermsInput,
        idempotency_key: str,
    ) -> PropertyTermsApplication: ...


class PreparationGuideOperations(Protocol):
    async def get(
        self,
        *,
        project_id: str,
        selection_id: str,
        user_id: str,
    ) -> PreparationGuide: ...


class SelectedCandidateCanaryError(RuntimeError):
    def __init__(self, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class SelectedCandidateCanaryReport:
    status: str
    selected_candidate_id: str
    selected_case_type: str
    property_input_id: str
    source_workflow_run_id: str
    recompute_workflow_run_id: str
    recomputed_stage_codes: tuple[str, ...]
    reused_stage_count: int
    result_freshness: str
    decision_delta_present: bool
    changed_cost_fields: tuple[str, ...]
    rag_source_count: int
    rag_evidence_count: int
    rag_procedure_step_count: int
    rag_source_refs: tuple[str, ...]
    elapsed_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_case_type": self.selected_case_type,
            "property_input_id": self.property_input_id,
            "source_workflow_run_id": self.source_workflow_run_id,
            "recompute_workflow_run_id": self.recompute_workflow_run_id,
            "recomputed_stage_codes": list(self.recomputed_stage_codes),
            "reused_stage_count": self.reused_stage_count,
            "result_freshness": self.result_freshness,
            "decision_delta_present": self.decision_delta_present,
            "changed_cost_fields": list(self.changed_cost_fields),
            "rag_source_count": self.rag_source_count,
            "rag_evidence_count": self.rag_evidence_count,
            "rag_procedure_step_count": self.rag_procedure_step_count,
            "rag_source_refs": list(self.rag_source_refs),
            "elapsed_ms": self.elapsed_ms,
        }


class SelectedCandidateCanary:
    """Exercise selection -> real property terms -> selective recompute end to end."""

    _terminal_statuses = {
        WorkflowStatus.WAITING_FOR_HUMAN,
        WorkflowStatus.SUCCEEDED,
        WorkflowStatus.PARTIAL,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.STALE,
    }

    def __init__(
        self,
        *,
        projects: ProjectOperations,
        workflows: WorkflowOperations,
        results: ResultOperations,
        selections: CandidateSelectionOperations,
        preparation_guides: PreparationGuideOperations,
        property_terms: PropertyTermsOperations,
        cleaner: CanaryCleaner,
        now: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._projects = projects
        self._workflows = workflows
        self._results = results
        self._selections = selections
        self._preparation_guides = preparation_guides
        self._property_terms = property_terms
        self._cleaner = cleaner
        self._now = now
        self._wait = wait
        self._new_id = new_id or (lambda: uuid4().hex)

    def run(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> SelectedCandidateCanaryReport:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Canary timeout and poll interval must be positive")
        canary_id = self._new_id()
        user_id = f"selected-candidate-canary-{canary_id}"
        project: Project | None = None
        active_workflow: WorkflowRun | None = None
        started_at = self._now()
        try:
            project = self._projects.create_project(
                user_id=user_id,
                idempotency_key=f"{canary_id}:create",
            )
            self._projects.confirm_onboarding(
                project_id=project.project_id,
                user_id=user_id,
                idempotency_key=f"{canary_id}:onboarding",
                founder=FounderState(
                    target_area_input="서울특별시 마포구 망원동",
                    own_funds_krw=70_000_000,
                    borrowing_intent=BorrowingIntent.UNDECIDED,
                    cafe_type_preference=CafeTypePreference.INDEPENDENT_ONLY,
                    operation_mode=OperationMode.DIRECT_FULL_TIME,
                    preferences=["실제 점포 조건 검증"],
                ),
                area=AreaState(
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
                ),
            )
            active_workflow = self._workflows.start(
                project_id=project.project_id,
                user_id=user_id,
                workflow_code=WorkflowCode.FIRST_PROPOSAL,
                idempotency_key=f"{canary_id}:first-proposal",
            )
            first_progress = self._await_succeeded(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=active_workflow.workflow_run_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            first_result = self._results.get_current(
                project_id=project.project_id,
                user_id=user_id,
            )
            if (
                first_result.workflow_run_id != active_workflow.workflow_run_id
                or first_result.freshness != ResultFreshness.CURRENT
                or not first_result.candidates
                or first_result.primary_candidate_id is None
            ):
                raise SelectedCandidateCanaryError("SOURCE_RESULT_INVALID")
            selection = self._selections.select(
                project_id=project.project_id,
                user_id=user_id,
                result_bundle_id=first_result.result_bundle_id,
                candidate_id=first_result.primary_candidate_id,
                expected_head=first_result.current_head,
                idempotency_key=f"{canary_id}:select",
            )
            preparation_guide = asyncio.run(
                self._preparation_guides.get(
                    project_id=project.project_id,
                    selection_id=selection.selection_id,
                    user_id=user_id,
                )
            )
            self._validate_rag_grounding(preparation_guide)
            application = self._property_terms.apply(
                project_id=project.project_id,
                selection_id=selection.selection_id,
                user_id=user_id,
                expected_state_version=selection.selected_state_version,
                terms=PropertyTermsInput(
                    address="서울특별시 마포구 망원동 데모 점포 · 실매물 아님",
                    area_sqm=33,
                    floor="1층",
                    deposit_krw=30_000_000,
                    monthly_rent_krw=2_200_000,
                    management_fee_krw=200_000,
                    key_money_krw=10_000_000,
                ),
                idempotency_key=f"{canary_id}:property-terms",
            )
            active_workflow = application.recompute_workflow
            recompute_progress = self._await_succeeded(
                project_id=project.project_id,
                user_id=user_id,
                workflow_run_id=active_workflow.workflow_run_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            return self._validate_recompute(
                first_result=first_result,
                selection=selection,
                application=application,
                progress=recompute_progress,
                current_result=self._results.get_current(
                    project_id=project.project_id,
                    user_id=user_id,
                ),
                source_stage_count=len(first_progress.stages),
                preparation_guide=preparation_guide,
                elapsed_ms=max(0, round((self._now() - started_at) * 1000)),
            )
        except Exception:
            if project is not None and active_workflow is not None:
                self._cancel_if_active(
                    project_id=project.project_id,
                    user_id=user_id,
                    workflow_run_id=active_workflow.workflow_run_id,
                    idempotency_key=f"{canary_id}:cancel",
                )
            raise
        finally:
            if project is not None:
                self._cleaner.cleanup(project_id=project.project_id, user_id=user_id)

    def _await_succeeded(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> WorkflowProgress:
        deadline = self._now() + timeout_seconds
        while True:
            progress = self._workflows.get_progress(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            if progress.status in self._terminal_statuses:
                if progress.status != WorkflowStatus.SUCCEEDED:
                    raise SelectedCandidateCanaryError(
                        "CANARY_WORKFLOW_NOT_SUCCEEDED",
                        {
                            "workflow_run_id": workflow_run_id,
                            "workflow_status": progress.status.value,
                            "terminal_reason_codes": progress.terminal_reason_codes,
                            "stages": [
                                stage.model_dump(mode="json") for stage in progress.stages
                            ],
                        },
                    )
                return progress
            if self._now() >= deadline:
                raise SelectedCandidateCanaryError(
                    "CANARY_TIMED_OUT",
                    {"workflow_run_id": workflow_run_id},
                )
            self._wait(min(poll_interval_seconds, max(0.0, deadline - self._now())))

    def _validate_recompute(
        self,
        *,
        first_result: ResultView,
        selection: CandidateSelection,
        application: PropertyTermsApplication,
        progress: WorkflowProgress,
        current_result: ResultView,
        source_stage_count: int,
        preparation_guide: PreparationGuide,
        elapsed_ms: int,
    ) -> SelectedCandidateCanaryReport:
        stages = {stage.stage_code: stage for stage in progress.stages}
        recomputed = tuple(
            sorted(stage.stage_code for stage in progress.stages if stage.attempt > 0)
        )
        expected_recomputed = {
            FirstProposalStage.CALCULATE_GATE_RANK.value,
            FirstProposalStage.CANDIDATE_AUDIT.value,
            FirstProposalStage.COMMIT_RESULT.value,
        }
        if not expected_recomputed <= set(recomputed):
            raise SelectedCandidateCanaryError(
                "SELECTIVE_STAGE_SET_INVALID",
                {"recomputed_stage_codes": list(recomputed)},
            )
        if any(stage.status.value != "SUCCEEDED" for stage in stages.values()):
            raise SelectedCandidateCanaryError("SELECTIVE_STAGE_NOT_SUCCEEDED")
        if (
            current_result.workflow_run_id != application.recompute_workflow.workflow_run_id
            or current_result.freshness != ResultFreshness.CURRENT
            or current_result.decision_delta is None
        ):
            raise SelectedCandidateCanaryError("RECOMPUTED_RESULT_INVALID")
        delta = current_result.decision_delta
        if (
            delta.previous_result_bundle_id != first_result.result_bundle_id
            or delta.current_result_bundle_id != current_result.result_bundle_id
        ):
            raise SelectedCandidateCanaryError("DECISION_DELTA_LINEAGE_INVALID")
        cost_fields = {
            "initial_cash_base_delta_krw": any(
                change.initial_cash_base_delta_krw not in {None, 0}
                for change in delta.candidate_changes
            ),
            "monthly_fixed_cost_base_delta_krw": any(
                change.monthly_fixed_cost_base_delta_krw not in {None, 0}
                for change in delta.candidate_changes
            ),
            "break_even_monthly_sales_delta_krw": any(
                change.break_even_monthly_sales_delta_krw not in {None, 0}
                for change in delta.candidate_changes
            ),
        }
        changed_cost_fields = tuple(sorted(key for key, changed in cost_fields.items() if changed))
        if not changed_cost_fields:
            raise SelectedCandidateCanaryError("PROPERTY_TERMS_DID_NOT_CHANGE_COSTS")
        return SelectedCandidateCanaryReport(
            status="verified",
            selected_candidate_id=selection.candidate_id,
            selected_case_type=str(selection.candidate.get("case_type")),
            property_input_id=application.property_input_id,
            source_workflow_run_id=first_result.workflow_run_id,
            recompute_workflow_run_id=current_result.workflow_run_id,
            recomputed_stage_codes=recomputed,
            reused_stage_count=max(0, source_stage_count - len(recomputed)),
            result_freshness=current_result.freshness.value,
            decision_delta_present=True,
            changed_cost_fields=changed_cost_fields,
            rag_source_count=len(preparation_guide.source_trace),
            rag_evidence_count=len(preparation_guide.evidence_records),
            rag_procedure_step_count=sum(
                len(procedure.steps) for procedure in preparation_guide.procedures
            ),
            rag_source_refs=tuple(
                sorted({source.source_ref for source in preparation_guide.source_trace})
            ),
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _validate_rag_grounding(guide: PreparationGuide) -> None:
        evidence_ids = {
            evidence.get("evidence_id")
            for evidence in guide.evidence_records
            if isinstance(evidence, dict) and isinstance(evidence.get("evidence_id"), str)
        }
        grounded_steps = [
            step
            for procedure in guide.procedures
            for step in procedure.steps
            if step.evidence_id in evidence_ids
        ]
        if not guide.source_trace or not evidence_ids or not grounded_steps:
            raise SelectedCandidateCanaryError(
                "ADVANCED_RAG_GROUNDING_MISSING",
                {
                    "source_trace_count": len(guide.source_trace),
                    "evidence_record_count": len(evidence_ids),
                    "grounded_step_count": len(grounded_steps),
                },
            )

    def _cancel_if_active(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_run_id: str,
        idempotency_key: str,
    ) -> None:
        try:
            progress = self._workflows.get_progress(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            if progress.status not in self._terminal_statuses:
                self._workflows.cancel(
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            return
