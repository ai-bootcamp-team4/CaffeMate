from app.domain.events import ConfirmOnboardingCommand
from app.domain.models import FounderState, Project
from app.projects.repository import ProjectRepository


class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create_project(self, *, user_id: str, idempotency_key: str) -> Project:
        return self._repository.create(user_id=user_id, idempotency_key=idempotency_key)

    def get_project(self, *, project_id: str, user_id: str) -> Project:
        return self._repository.get(project_id=project_id, user_id=user_id)

    def list_projects(self, *, user_id: str) -> list[Project]:
        return self._repository.list_for_user(user_id=user_id)

    def confirm_onboarding(
        self,
        *,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        founder: FounderState,
    ) -> Project:
        return self._repository.confirm_onboarding(
            ConfirmOnboardingCommand(
                project_id=project_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                founder=founder,
            )
        )
