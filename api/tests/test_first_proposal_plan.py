"""사용자는 한 번의 분석 요청으로 결과를 받아야 하며 13단계 제어부를 보지 않는다."""

from inspect import signature

from app.workflows.first_proposal import (
    FirstProposalStage,
    stage_input_digest,
)
from app.workflows.models import HeadFence
from app.workflows.selective_start import start_selective_first_proposal


def test_first_proposal_has_one_execution_unit() -> None:
    assert list(FirstProposalStage) == [FirstProposalStage.RUN_PROPOSAL]


def test_single_execution_contract_has_no_stage_selection_or_dependencies() -> None:
    assert "dependencies" not in signature(stage_input_digest).parameters
    assert (
        "affected_stage_codes"
        not in signature(start_selective_first_proposal).parameters
    )


def test_single_stage_digest_is_stable() -> None:
    head = HeadFence(
        workflow_generation=2,
        state_version=3,
        founder_snapshot_id="founder-3",
        area_snapshot_id="area-3",
        evidence_snapshot_id="evidence-2",
        policy_snapshot_id="policy-1",
        index_generation_id=None,
        seed_registry_id="seeds-1",
    )

    first = stage_input_digest(
        workflow_run_id="workflow-2",
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )
    second = stage_input_digest(
        workflow_run_id="workflow-2",
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )

    assert first == second
