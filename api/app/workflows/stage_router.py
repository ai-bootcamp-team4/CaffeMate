from collections.abc import Mapping
from typing import Protocol

from app.domain.errors import ExternalExecutionUnavailableError
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import StageLease
from app.workflows.stage_context import StageContext


class StageContextRepository(Protocol):
    def load(self, lease: StageLease) -> StageContext: ...


class FirstProposalStageHandler(Protocol):
    def execute(self, context: StageContext) -> dict[str, object]: ...


class FirstProposalStageRouter:
    def __init__(
        self,
        context_repository: StageContextRepository,
        handlers: Mapping[FirstProposalStage, FirstProposalStageHandler],
    ) -> None:
        self._context_repository = context_repository
        self._handlers = dict(handlers)

    def execute(self, lease: StageLease) -> dict[str, object]:
        try:
            stage = FirstProposalStage(lease.stage_code)
        except ValueError as error:
            raise ExternalExecutionUnavailableError(
                f"Unknown FIRST_PROPOSAL stage: {lease.stage_code}"
            ) from error
        handler = self._handlers.get(stage)
        if handler is None:
            raise ExternalExecutionUnavailableError(
                f"FIRST_PROPOSAL stage handler is not configured: {stage.value}"
            )
        context = self._context_repository.load(lease)
        return handler.execute(context)
