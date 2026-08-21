from typing import Protocol

from app.domain.events import ConfirmOnboardingCommand
from app.domain.models import Project


class ProjectRepository(Protocol):
    def create(self, *, user_id: str, idempotency_key: str) -> Project: ...

    def get(self, *, project_id: str, user_id: str) -> Project: ...

    def confirm_onboarding(self, command: ConfirmOnboardingCommand) -> Project: ...
