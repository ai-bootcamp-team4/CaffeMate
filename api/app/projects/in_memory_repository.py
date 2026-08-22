import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

import rfc8785

from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import IdempotencyKeyReusedError, ProjectNotFoundError
from app.domain.events import ConfirmOnboardingCommand, OnboardingConfirmed, ProjectCreated
from app.domain.models import Project
from app.domain.reducer import reduce_venture_state


class InMemoryProjectRepository:
    """Test adapter. Production must bind the same interface to PostgreSQL transactions."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
        contracts: ContractRegistry | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))
        self._contracts = contracts or ContractRegistry()
        self._projects: dict[str, Project] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[bytes, str]] = {}
        self._events: list[ProjectCreated | OnboardingConfirmed] = []
        self._lock = RLock()

    @property
    def events(self) -> tuple[ProjectCreated | OnboardingConfirmed, ...]:
        return tuple(self._events)

    def create(self, *, user_id: str, idempotency_key: str) -> Project:
        scope = (user_id, "CREATE_PROJECT", idempotency_key)
        digest = hashlib.sha256(rfc8785.dumps({"user_id": user_id})).digest()
        with self._lock:
            replay_id = self._replay_or_reject(scope, digest)
            if replay_id is not None:
                return self._projects[replay_id].model_copy(deep=True)

            project_id = self._new_id()
            occurred_at = self._now()
            event = ProjectCreated(
                event_id=self._new_id(),
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
            state = reduce_venture_state(None, event)
            project = Project(
                project_id=project_id,
                user_id=user_id,
                created_at=occurred_at,
                state=state,
            )
            self._projects[project_id] = project
            self._events.append(event)
            self._idempotency[scope] = (digest, project_id)
            return project.model_copy(deep=True)

    def get(self, *, project_id: str, user_id: str) -> Project:
        with self._lock:
            project = self._owned_project(project_id=project_id, user_id=user_id)
            return project.model_copy(deep=True)

    def list_for_user(self, *, user_id: str) -> list[Project]:
        with self._lock:
            return [
                project.model_copy(deep=True)
                for project in self._projects.values()
                if project.user_id == user_id
            ]

    def confirm_onboarding(self, command: ConfirmOnboardingCommand) -> Project:
        scope = (
            command.user_id,
            f"CONFIRM_ONBOARDING:{command.project_id}",
            command.idempotency_key,
        )
        digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "founder": command.founder.model_dump(mode="json"),
                    "area": command.area.model_dump(mode="json") if command.area else None,
                }
            )
        ).digest()
        with self._lock:
            project = self._owned_project(
                project_id=command.project_id,
                user_id=command.user_id,
            )
            replay_id = self._replay_or_reject(scope, digest)
            if replay_id is not None:
                return self._projects[replay_id].model_copy(deep=True)

            event = OnboardingConfirmed(
                event_id=self._new_id(),
                project_id=project.project_id,
                user_id=project.user_id,
                occurred_at=self._now(),
                founder=command.founder,
                area=command.area,
            )
            state = reduce_venture_state(project.state, event)
            assert state is not None
            self._contracts.validate_venture_state(state.model_dump(mode="json"))
            updated = project.model_copy(update={"state": state}, deep=True)
            self._projects[project.project_id] = updated
            self._events.append(event)
            self._idempotency[scope] = (digest, project.project_id)
            return updated.model_copy(deep=True)

    def _owned_project(self, *, project_id: str, user_id: str) -> Project:
        project = self._projects.get(project_id)
        if project is None or project.user_id != user_id:
            raise ProjectNotFoundError("Project does not exist")
        return project

    def _replay_or_reject(
        self,
        scope: tuple[str, str, str],
        digest: bytes,
    ) -> str | None:
        existing = self._idempotency.get(scope)
        if existing is None:
            return None
        existing_digest, result_id = existing
        if existing_digest != digest:
            raise IdempotencyKeyReusedError("Idempotency key was used with another payload")
        return result_id
