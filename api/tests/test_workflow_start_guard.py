import pytest

from app.domain.errors import FirstProposalConfigurationUnavailableError
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode
from app.workflows.start_guard import FirstProposalStartGuard


def test_complete_first_proposal_composition_is_accepted() -> None:
    guard = FirstProposalStartGuard(list(FirstProposalStage))

    guard.validate(WorkflowCode.FIRST_PROPOSAL)


def test_incomplete_first_proposal_composition_reports_every_missing_stage() -> None:
    guard = FirstProposalStartGuard(
        [FirstProposalStage.AREA_RESOLUTION, FirstProposalStage.CLAIM_PLAN]
    )

    with pytest.raises(FirstProposalConfigurationUnavailableError) as caught:
        guard.validate(WorkflowCode.FIRST_PROPOSAL)

    assert caught.value.missing_stage_codes == sorted(
        stage.value
        for stage in FirstProposalStage
        if stage
        not in {FirstProposalStage.AREA_RESOLUTION, FirstProposalStage.CLAIM_PLAN}
    )
