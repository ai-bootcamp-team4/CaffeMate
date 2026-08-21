import pytest

from app.domain.models import CafeTypePreference
from app.workflows.first_proposal import FirstProposalStage, compile_first_proposal_plan


@pytest.mark.parametrize(
    ("preference", "included", "excluded"),
    [
        (
            CafeTypePreference.OPEN_TO_BOTH,
            {
                FirstProposalStage.PROPOSE_INDEPENDENT,
                FirstProposalStage.PROPOSE_FRANCHISE,
            },
            set(),
        ),
        (
            CafeTypePreference.INDEPENDENT_ONLY,
            {FirstProposalStage.PROPOSE_INDEPENDENT},
            {FirstProposalStage.PROPOSE_FRANCHISE},
        ),
        (
            CafeTypePreference.FRANCHISE_ONLY,
            {FirstProposalStage.PROPOSE_FRANCHISE},
            {FirstProposalStage.PROPOSE_INDEPENDENT},
        ),
    ],
)
def test_plan_selects_only_requested_proposal_branches(
    preference: CafeTypePreference,
    included: set[FirstProposalStage],
    excluded: set[FirstProposalStage],
) -> None:
    plan = compile_first_proposal_plan(preference)
    codes = {stage.code for stage in plan}

    assert included <= codes
    assert not (excluded & codes)
    assert plan[0].code == FirstProposalStage.AREA_RESOLUTION
    assert plan[-1].code == FirstProposalStage.COMMIT_RESULT


@pytest.mark.parametrize("preference", list(CafeTypePreference))
def test_plan_is_topological_and_join_depends_on_every_included_proposal(
    preference: CafeTypePreference,
) -> None:
    plan = compile_first_proposal_plan(preference)
    seen: set[FirstProposalStage] = set()

    for stage in plan:
        assert stage.code not in seen
        assert set(stage.dependencies) <= seen
        seen.add(stage.code)

    join = next(
        stage for stage in plan if stage.code == FirstProposalStage.CALCULATE_GATE_RANK
    )
    expected = {
        stage
        for stage in (
            FirstProposalStage.PROPOSE_INDEPENDENT,
            FirstProposalStage.PROPOSE_FRANCHISE,
        )
        if stage in seen
    }
    assert set(join.dependencies) == expected


@pytest.mark.parametrize(
    "stage_code",
    [
        FirstProposalStage.INDEPENDENT_SEED,
        FirstProposalStage.FRANCHISE_ELIGIBILITY,
    ],
)
def test_candidate_input_branches_pin_area_and_frozen_evidence(
    stage_code: FirstProposalStage,
) -> None:
    plan = compile_first_proposal_plan(CafeTypePreference.OPEN_TO_BOTH)
    stage = next(value for value in plan if value.code == stage_code)

    assert set(stage.dependencies) == {
        FirstProposalStage.AREA_RESOLUTION,
        FirstProposalStage.EVIDENCE_FREEZE,
    }
