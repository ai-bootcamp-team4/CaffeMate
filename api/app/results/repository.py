from typing import Protocol

from app.results.models import ResultBundle


class ResultRepository(Protocol):
    def get_current(self, *, project_id: str, user_id: str) -> ResultBundle: ...
