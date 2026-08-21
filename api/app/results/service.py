from app.results.models import ResultBundle
from app.results.repository import ResultRepository


class ResultService:
    def __init__(self, repository: ResultRepository) -> None:
        self._repository = repository

    def get_current(self, *, project_id: str, user_id: str) -> ResultBundle:
        return self._repository.get_current(project_id=project_id, user_id=user_id)
