from app.domain.errors import PersistenceUnavailableError
from app.domain.events import ConfirmOnboardingCommand
from app.domain.models import Project


class UnavailableProjectRepository:
    """Fail-closed adapter used until the PostgreSQL repository is configured."""

    def create(self, *, user_id: str, idempotency_key: str) -> Project:
        del user_id, idempotency_key
        raise PersistenceUnavailableError("PostgreSQL repository is not configured")

    def get(self, *, project_id: str, user_id: str) -> Project:
        del project_id, user_id
        raise PersistenceUnavailableError("PostgreSQL repository is not configured")

    def list_for_user(self, *, user_id: str) -> list[Project]:
        del user_id
        raise PersistenceUnavailableError("PostgreSQL repository is not configured")

    def confirm_onboarding(self, command: ConfirmOnboardingCommand) -> Project:
        del command
        raise PersistenceUnavailableError("PostgreSQL repository is not configured")
