from collections.abc import Collection
from typing import Protocol

from app.domain.errors import FirstProposalConfigurationUnavailableError
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode


class WorkflowStartGuard(Protocol):
    def validate(self, workflow_code: WorkflowCode) -> None: ...


class FirstProposalStartGuard:
    def __init__(self, available_stages: Collection[FirstProposalStage]) -> None:
        available = set(available_stages)
        self._missing_stages = sorted(
            stage.value for stage in FirstProposalStage if stage not in available
        )

    @property
    def missing_stages(self) -> list[str]:
        return list(self._missing_stages)

    def validate(self, workflow_code: WorkflowCode) -> None:
        if workflow_code == WorkflowCode.FIRST_PROPOSAL and self._missing_stages:
            raise FirstProposalConfigurationUnavailableError(self._missing_stages)
