import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.contracts.schema_registry import ContractRegistry, VentureStateValidator
from app.domain.errors import IdempotencyKeyReusedError, ProjectNotFoundError
from app.domain.events import ConfirmOnboardingCommand, OnboardingConfirmed, ProjectCreated
from app.domain.models import Project, VentureState
from app.domain.reducer import reduce_venture_state


class PostgresProjectRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
        contracts: VentureStateValidator | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))
        self._contracts = contracts or ContractRegistry()

    def create(self, *, user_id: str, idempotency_key: str) -> Project:
        operation = "CREATE_PROJECT"
        digest = rfc8785.dumps({"user_id": user_id})
        with self._engine.begin() as connection:
            if not self._claim_idempotency(
                connection,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                digest=digest,
            ):
                project_id = self._replay_idempotency(
                    connection,
                    user_id=user_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    digest=digest,
                )
                return self._load_owned_project(connection, project_id=project_id, user_id=user_id)

            project_id = self._new_id()
            occurred_at = self._now()
            event = ProjectCreated(
                event_id=self._new_id(),
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
            state = reduce_venture_state(None, event)
            assert state is None
            connection.execute(
                text(
                    "INSERT INTO venture_projects"
                    "(project_id, owner_user_id, created_at) "
                    "VALUES (:project_id, :owner_user_id, :created_at)"
                ),
                {
                    "project_id": project_id,
                    "owner_user_id": user_id,
                    "created_at": occurred_at,
                },
            )
            self._insert_event(connection, event)
            self._complete_idempotency(
                connection,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                project_id=project_id,
                state_version=None,
            )
            return Project(
                project_id=project_id,
                user_id=user_id,
                created_at=occurred_at,
                state=None,
            )

    def get(self, *, project_id: str, user_id: str) -> Project:
        with self._engine.connect() as connection:
            return self._load_owned_project(connection, project_id=project_id, user_id=user_id)

    def list_for_user(self, *, user_id: str) -> list[Project]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT p.project_id, p.owner_user_id, p.created_at, s.state_json
                    FROM venture_projects p
                    LEFT JOIN venture_states s
                      ON s.project_id = p.project_id
                     AND s.state_version = p.current_state_version
                    WHERE p.owner_user_id = :user_id
                    ORDER BY p.created_at, p.project_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
            return [self._project_from_row(row) for row in rows]

    def confirm_onboarding(self, command: ConfirmOnboardingCommand) -> Project:
        operation = f"CONFIRM_ONBOARDING:{command.project_id}"
        digest = rfc8785.dumps(command.founder.model_dump(mode="json"))
        with self._engine.begin() as connection:
            locked = connection.execute(
                text(
                    """
                    SELECT project_id
                    FROM venture_projects
                    WHERE project_id = :project_id AND owner_user_id = :user_id
                    FOR UPDATE
                    """
                ),
                {"project_id": command.project_id, "user_id": command.user_id},
            ).scalar_one_or_none()
            if locked is None:
                raise ProjectNotFoundError("Project does not exist")

            if not self._claim_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                digest=digest,
            ):
                project_id = self._replay_idempotency(
                    connection,
                    user_id=command.user_id,
                    operation=operation,
                    idempotency_key=command.idempotency_key,
                    digest=digest,
                )
                return self._load_owned_project(
                    connection,
                    project_id=project_id,
                    user_id=command.user_id,
                )

            project = self._load_owned_project(
                connection,
                project_id=command.project_id,
                user_id=command.user_id,
            )
            event = OnboardingConfirmed(
                event_id=self._new_id(),
                project_id=project.project_id,
                user_id=project.user_id,
                occurred_at=self._now(),
                founder=command.founder,
            )
            state = reduce_venture_state(project.state, event)
            assert state is not None
            state_value = state.model_dump(mode="json")
            self._contracts.validate_venture_state(state_value)
            connection.execute(
                text(
                    """
                    INSERT INTO venture_states(project_id, state_version, state_json, created_at)
                    VALUES (:project_id, :state_version, CAST(:state_json AS JSONB), :created_at)
                    """
                ),
                {
                    "project_id": state.project_id,
                    "state_version": state.state_version,
                    "state_json": json.dumps(state_value, separators=(",", ":")),
                    "created_at": state.updated_at,
                },
            )
            connection.execute(
                text(
                    "UPDATE venture_projects SET current_state_version = :state_version "
                    "WHERE project_id = :project_id"
                ),
                {"state_version": state.state_version, "project_id": state.project_id},
            )
            self._insert_event(connection, event)
            self._complete_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                project_id=state.project_id,
                state_version=state.state_version,
            )
            return project.model_copy(update={"state": state}, deep=True)

    def _claim_idempotency(
        self,
        connection: Connection,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        digest: bytes,
    ) -> bool:
        claimed = connection.execute(
            text(
                """
                INSERT INTO idempotency_records(
                    user_id, operation, idempotency_key, request_digest, created_at
                ) VALUES (
                    :user_id, :operation, :idempotency_key, :request_digest, :created_at
                )
                ON CONFLICT (user_id, operation, idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """
            ),
            {
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
                "request_digest": digest,
                "created_at": self._now(),
            },
        ).scalar_one_or_none()
        return claimed is not None

    def _replay_idempotency(
        self,
        connection: Connection,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        digest: bytes,
    ) -> str:
        row = connection.execute(
            text(
                """
                SELECT request_digest, response_project_id
                FROM idempotency_records
                WHERE user_id = :user_id
                  AND operation = :operation
                  AND idempotency_key = :idempotency_key
                """
            ),
            {
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one()
        if bytes(row["request_digest"]) != digest:
            raise IdempotencyKeyReusedError("Idempotency key was used with another payload")
        project_id = row["response_project_id"]
        if not isinstance(project_id, str):
            raise RuntimeError("Committed idempotency record has no response")
        return project_id

    def _complete_idempotency(
        self,
        connection: Connection,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        project_id: str,
        state_version: int | None,
    ) -> None:
        connection.execute(
            text(
                """
                UPDATE idempotency_records
                SET response_project_id = :project_id,
                    response_state_version = :state_version
                WHERE user_id = :user_id
                  AND operation = :operation
                  AND idempotency_key = :idempotency_key
                """
            ),
            {
                "project_id": project_id,
                "state_version": state_version,
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        )

    def _load_owned_project(
        self,
        connection: Connection,
        *,
        project_id: str,
        user_id: str,
    ) -> Project:
        row = connection.execute(
            text(
                """
                SELECT p.project_id, p.owner_user_id, p.created_at, s.state_json
                FROM venture_projects p
                LEFT JOIN venture_states s
                  ON s.project_id = p.project_id
                 AND s.state_version = p.current_state_version
                WHERE p.project_id = :project_id AND p.owner_user_id = :user_id
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            raise ProjectNotFoundError("Project does not exist")
        return self._project_from_row(row)

    @staticmethod
    def _project_from_row(row: RowMapping) -> Project:
        state_value = row["state_json"]
        if isinstance(state_value, str):
            state_value = json.loads(state_value)
        state = VentureState.model_validate(state_value) if state_value is not None else None
        return Project(
            project_id=row["project_id"],
            user_id=row["owner_user_id"],
            created_at=row["created_at"],
            state=state,
        )

    @staticmethod
    def _insert_event(
        connection: Connection,
        event: ProjectCreated | OnboardingConfirmed,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO project_events(
                    event_id, project_id, event_type, event_json, occurred_at
                ) VALUES (
                    :event_id, :project_id, :event_type,
                    CAST(:event_json AS JSONB), :occurred_at
                )
                """
            ),
            {
                "event_id": event.event_id,
                "project_id": event.project_id,
                "event_type": event.event_type,
                "event_json": event.model_dump_json(),
                "occurred_at": event.occurred_at,
            },
        )
