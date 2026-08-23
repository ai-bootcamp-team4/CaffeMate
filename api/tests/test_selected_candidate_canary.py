from datetime import UTC, datetime

import pytest

from app.results.models import (
    AuditStatus,
    CandidateDecisionDelta,
    ResultDecisionDelta,
    ResultFreshness,
    ResultView,
)
from app.selections.models import (
    CandidateSelection,
    PropertyTermsApplication,
    PropertyTermsInput,
)
from app.selections.preparation import (
    OfficialSourceTrace,
    PreparationGuide,
    PreparationGuideStatus,
    ProcedureCoverage,
    ProcedureRetrievalStatus,
    ProcedureStep,
    ProcedureType,
)
from app.verification.selected_candidate import (
    SelectedCandidateCanary,
    SelectedCandidateCanaryError,
)
from app.workflows.models import (
    HeadFence,
    StageStatus,
    WorkflowCode,
    WorkflowProgress,
    WorkflowRun,
    WorkflowStageProgress,
    WorkflowStatus,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _head(*, generation: int, state_version: int) -> HeadFence:
    return HeadFence(
        workflow_generation=generation,
        state_version=state_version,
        founder_snapshot_id=f"founder-{state_version}",
        area_snapshot_id=f"area-{state_version}",
        evidence_snapshot_id="evidence-1",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )


def _candidate(*, state_version: int, initial_cash: int) -> dict[str, object]:
    return {
        "candidate_id": f"candidate-{state_version}",
        "project_id": "project-1",
        "state_version": state_version,
        "case_type": "INDEPENDENT",
        "display_name": "소형 포장 중심 개인카페",
        "rank": 1,
        "review_status": "CONDITIONAL_REVIEW",
        "is_primary_next_review": True,
        "independent_model": {"model_id": "small-takeout-v1"},
        "financial_summary": {
            "initial_cash": {"base": initial_cash},
            "monthly_fixed_cost": {"base": 3_000_000},
            "break_even_monthly_sales": {"base": 5_000_000},
        },
    }


def _result(
    *,
    bundle_id: str,
    workflow_id: str,
    state_version: int,
    initial_cash: int,
    delta: ResultDecisionDelta | None = None,
) -> ResultView:
    candidate = _candidate(state_version=state_version, initial_cash=initial_cash)
    head = _head(generation=state_version, state_version=state_version)
    return ResultView(
        result_bundle_id=bundle_id,
        project_id="project-1",
        workflow_run_id=workflow_id,
        head=head,
        candidates=[candidate],
        primary_candidate_id=str(candidate["candidate_id"]),
        audit_status=AuditStatus.PASSED,
        created_at=NOW,
        freshness=ResultFreshness.CURRENT,
        stale_head_dimensions=[],
        current_head=head,
        decision_delta=delta,
    )


def _progress() -> WorkflowProgress:
    head = _head(generation=3, state_version=3)
    stages = [
        WorkflowStageProgress(
            stage_run_id=f"stage-{code}",
            stage_code=code,
            status=StageStatus.SUCCEEDED,
            attempt=1
            if code
            in {
                "CALCULATE_GATE_RANK",
                "CANDIDATE_AUDIT",
                "COMMIT_RESULT",
            }
            else 0,
            updated_at=NOW,
            completed_at=NOW,
        )
        for code in (
            "AREA_RESOLUTION",
            "CLAIM_PLAN",
            "EVIDENCE_PLAN",
            "EVIDENCE_RETRIEVAL",
            "EVIDENCE_ASSESS",
            "EVIDENCE_FREEZE",
            "INDEPENDENT_SEED",
            "PROPOSE_INDEPENDENT",
            "CALCULATE_GATE_RANK",
            "CANDIDATE_AUDIT",
            "COMMIT_RESULT",
        )
    ]
    return WorkflowProgress(
        workflow_run_id="workflow-recompute",
        project_id="project-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        status=WorkflowStatus.SUCCEEDED,
        head=head,
        input_digest="a" * 64,
        created_at=NOW,
        updated_at=NOW,
        stages=stages,
        completed_stage_count=len(stages),
        total_stage_count=len(stages),
        current_stage_codes=[],
        human_review_requests=[],
        terminal_reason_codes=[],
    )


def _selection() -> CandidateSelection:
    candidate = _candidate(state_version=2, initial_cash=50_000_000)
    return CandidateSelection(
        selection_id="selection-1",
        project_id="project-1",
        result_bundle_id="result-before",
        candidate_id=str(candidate["candidate_id"]),
        selected_state_version=2,
        candidate=candidate,
        required_evidence=[],
        created_at=NOW,
    )


def _application() -> PropertyTermsApplication:
    head = _head(generation=3, state_version=3)
    return PropertyTermsApplication(
        property_input_id="property-1",
        project_id="project-1",
        selection_id="selection-1",
        candidate_id="candidate-2",
        applied_state_version=3,
        terms=PropertyTermsInput(
            address="서울특별시 마포구 망원동 데모 점포",
            area_sqm=33,
            floor="1층",
            deposit_krw=30_000_000,
            monthly_rent_krw=2_200_000,
            management_fee_krw=200_000,
            key_money_krw=10_000_000,
        ),
        previous_financial_summary={},
        recompute_workflow=WorkflowRun(
            workflow_run_id="workflow-recompute",
            project_id="project-1",
            workflow_code=WorkflowCode.FIRST_PROPOSAL,
            status=WorkflowStatus.QUEUED,
            head=head,
            input_digest="b" * 64,
            created_at=NOW,
            updated_at=NOW,
        ),
        created_at=NOW,
    )


def _preparation_guide() -> PreparationGuide:
    source = OfficialSourceTrace(
        source_id="rag-file-1",
        source_ref="https://www.easylaw.go.kr/example",
        data_date=NOW.date(),
        retrieved_at=NOW,
        content_digest=f"sha256:{'c' * 64}",
    )
    evidence = {"evidence_id": "procedure:BUSINESS_REGISTRATION:record-1"}
    return PreparationGuide(
        project_id="project-1",
        selection_id="selection-1",
        candidate_id="candidate-2",
        candidate_type="INDEPENDENT",
        jurisdiction_code="1144012300",
        jurisdiction_display_name="서울특별시 마포구 망원동",
        as_of=NOW.date(),
        status=PreparationGuideStatus.REVIEW_REQUIRED,
        procedures=[
            ProcedureCoverage(
                procedure_type=ProcedureType.BUSINESS_REGISTRATION,
                status=ProcedureRetrievalStatus.OK,
                steps=[
                    ProcedureStep(
                        procedure_type=ProcedureType.BUSINESS_REGISTRATION,
                        step_order=1,
                        title="사업자등록 신청",
                        required=True,
                        authority="찾기쉬운 생활법령정보",
                        source_date=NOW.date(),
                        evidence_id=str(evidence["evidence_id"]),
                    )
                ],
                missing_fields=[],
                conflicts=[],
                error_codes=[],
                source_trace=[source],
                evidence_records=[evidence],
            )
        ],
        source_trace=[source],
        evidence_records=[evidence],
        generated_at=NOW,
    )


def _canary() -> SelectedCandidateCanary:
    return SelectedCandidateCanary(
        projects=object(),  # type: ignore[arg-type]
        workflows=object(),  # type: ignore[arg-type]
        results=object(),  # type: ignore[arg-type]
        selections=object(),  # type: ignore[arg-type]
        preparation_guides=object(),  # type: ignore[arg-type]
        property_terms=object(),  # type: ignore[arg-type]
        cleaner=object(),  # type: ignore[arg-type]
    )


def test_selected_candidate_canary_accepts_selective_recompute_with_cost_delta() -> None:
    before = _result(
        bundle_id="result-before",
        workflow_id="workflow-first",
        state_version=1,
        initial_cash=50_000_000,
    )
    delta = ResultDecisionDelta(
        previous_result_bundle_id="result-before",
        current_result_bundle_id="result-after",
        primary_candidate_changed=False,
        candidate_changes=[
            CandidateDecisionDelta(
                candidate_key="INDEPENDENT:small-takeout-v1",
                display_name="소형 포장 중심 개인카페",
                change_type="UPDATED",
                previous_rank=1,
                current_rank=1,
                previous_review_status="CONDITIONAL_REVIEW",
                current_review_status="CONDITIONAL_REVIEW",
                initial_cash_base_delta_krw=40_000_000,
                monthly_fixed_cost_base_delta_krw=400_000,
                break_even_monthly_sales_delta_krw=600_000,
            )
        ],
    )
    after = _result(
        bundle_id="result-after",
        workflow_id="workflow-recompute",
        state_version=3,
        initial_cash=90_000_000,
        delta=delta,
    )

    report = _canary()._validate_recompute(
        first_result=before,
        selection=_selection(),
        application=_application(),
        progress=_progress(),
        current_result=after,
        source_stage_count=11,
        preparation_guide=_preparation_guide(),
        elapsed_ms=1200,
    )

    assert report.status == "verified"
    assert report.recomputed_stage_codes == (
        "CALCULATE_GATE_RANK",
        "CANDIDATE_AUDIT",
        "COMMIT_RESULT",
    )
    assert report.reused_stage_count == 8
    assert report.changed_cost_fields == (
        "break_even_monthly_sales_delta_krw",
        "initial_cash_base_delta_krw",
        "monthly_fixed_cost_base_delta_krw",
    )
    assert report.rag_source_count == 1
    assert report.rag_evidence_count == 1
    assert report.rag_procedure_step_count == 1


def test_selected_candidate_canary_rejects_result_without_cost_change() -> None:
    before = _result(
        bundle_id="result-before",
        workflow_id="workflow-first",
        state_version=1,
        initial_cash=50_000_000,
    )
    delta = ResultDecisionDelta(
        previous_result_bundle_id="result-before",
        current_result_bundle_id="result-after",
        primary_candidate_changed=False,
        candidate_changes=[
            CandidateDecisionDelta(
                candidate_key="INDEPENDENT:small-takeout-v1",
                display_name="소형 포장 중심 개인카페",
                change_type="UPDATED",
                previous_rank=1,
                current_rank=1,
                previous_review_status="CONDITIONAL_REVIEW",
                current_review_status="CONDITIONAL_REVIEW",
                initial_cash_base_delta_krw=0,
                monthly_fixed_cost_base_delta_krw=0,
                break_even_monthly_sales_delta_krw=0,
            )
        ],
    )
    after = _result(
        bundle_id="result-after",
        workflow_id="workflow-recompute",
        state_version=3,
        initial_cash=50_000_000,
        delta=delta,
    )

    with pytest.raises(SelectedCandidateCanaryError) as error:
        _canary()._validate_recompute(
            first_result=before,
            selection=_selection(),
            application=_application(),
            progress=_progress(),
            current_result=after,
            source_stage_count=11,
            preparation_guide=_preparation_guide(),
            elapsed_ms=1200,
        )

    assert error.value.code == "PROPERTY_TERMS_DID_NOT_CHANGE_COSTS"
