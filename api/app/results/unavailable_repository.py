from app.domain.errors import PersistenceUnavailableError
from app.results.models import ResultBundle


class UnavailableResultRepository:
    def get_current(self, *, project_id: str, user_id: str) -> ResultBundle:
        del project_id, user_id
        raise PersistenceUnavailableError("PostgreSQL repository is not configured")
