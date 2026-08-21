import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.domain.errors import (
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
    WorkflowNotFoundError,
    WorkflowPreconditionError,
)
from app.workflows.models import (
    CancelWorkflowCommand,
    HeadFence,
    StartWorkflowCommand,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
)

TERMINAL_WORKFLOW_STATUSES = {
    WorkflowStatus.SUCCEEDED,
    WorkflowStatus.PARTIAL,
    WorkflowStatus.FAILED,
    WorkflowStatus.CANCELLED,
    WorkflowStatus.STALE,
}


class PostgresWorkflowRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        policy_snapshot_id: str,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        if not policy_snapshot_id or len(policy_snapshot_id) > 128:
            raise ValueError("policy_snapshot_id must contain 1..128 characters")
        self._engine = engine
        self._policy_snapshot_id = policy_snapshot_id
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def start(self, command: StartWorkflowCommand) -> WorkflowRun:
        operation = f"START_WORKFLOW:{command.project_id}:{command.workflow_code.value}"
        request_digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "project_id": command.project_id,
                    "workflow_code": command.workflow_code.value,
                }
            )
        ).digest()
        with self._engine.begin() as connection:
            project = self._lock_project(
                connection,
                project_id=command.project_id,
                user_id=command.user_id,
            )
            if not self._claim_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                digest=request_digest,
            ):
                workflow_run_id = self._replay_idempotency(
                    connection,
                    user_id=command.user_id,
                    operation=operation,
                    idempotency_key=command.idempotency_key,
                    digest=request_digest,
                )
                return self._load_owned_workflow(
                    connection,
                    project_id=command.project_id,
                    workflow_run_id=workflow_run_id,
                    user_id=command.user_id,
                )

            state_version = project["current_state_version"]
            if not isinstance(state_version, int):
                raise WorkflowPreconditionError("Onboarding State must exist before workflow start")

            generation = int(project["workflow_generation"]) + 1
            connection.execute(
                text(
                    "UPDATE venture_projects SET workflow_generation = :generation "
                    "WHERE project_id = :project_id"
                ),
                {"generation": generation, "project_id": command.project_id},
            )
            state_json = project["state_json"]
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            evidence_snapshot_id = (
                state_json.get("evidence_snapshot_id") if isinstance(state_json, dict) else None
            )
            head = HeadFence(
                workflow_generation=generation,
                state_version=state_version,
                founder_snapshot_id=project["founder_snapshot_id"],
                area_snapshot_id=project["area_snapshot_id"],
                evidence_snapshot_id=evidence_snapshot_id,
                policy_snapshot_id=self._policy_snapshot_id,
                index_generation_id=None,
                seed_registry_id=None,
            )
            input_digest = hashlib.sha256(
                rfc8785.dumps(
                    {
                        "command": {
                            "project_id": command.project_id,
                            "workflow_code": command.workflow_code.value,
                        },
                        "head": head.model_dump(mode="json"),
                        "contract_version": "1.0.0",
                    }
                )
            ).hexdigest()
            workflow_run_id = self._new_id()
            stage_run_id = self._new_id()
            occurred_at = self._now()
            connection.execute(
                text(
                    """
                    INSERT INTO workflow_runs(
                        workflow_run_id, project_id, owner_user_id, workflow_code, status,
                        workflow_generation, state_version, founder_snapshot_id,
                        area_snapshot_id, evidence_snapshot_id, policy_snapshot_id,
                        index_generation_id, seed_registry_id, input_digest,
                        created_at, updated_at
                    ) VALUES (
                        :workflow_run_id, :project_id, :owner_user_id, :workflow_code, 'QUEUED',
                        :workflow_generation, :state_version, :founder_snapshot_id,
                        :area_snapshot_id, :evidence_snapshot_id, :policy_snapshot_id,
                        :index_generation_id, :seed_registry_id, :input_digest,
                        :created_at, :updated_at
                    )
                    """
                ),
                {
                    "workflow_run_id": workflow_run_id,
                    "project_id": command.project_id,
                    "owner_user_id": command.user_id,
                    "workflow_code": command.workflow_code.value,
                    **head.model_dump(mode="python"),
                    "input_digest": input_digest,
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO stage_runs(
                        stage_run_id, workflow_run_id, stage_code, status,
                        input_digest, created_at, updated_at
                    ) VALUES (
                        :stage_run_id, :workflow_run_id, :stage_code, 'READY',
                        :input_digest, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "stage_run_id": stage_run_id,
                    "workflow_run_id": workflow_run_id,
                    "stage_code": command.workflow_code.value,
                    "input_digest": input_digest,
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                },
            )
            self._insert_event(
                connection,
                workflow_run_id=workflow_run_id,
                event_type="WORKFLOW_QUEUED",
                data={
                    "stage_run_id": stage_run_id,
                    "input_digest": input_digest,
                    "head": head.model_dump(mode="json"),
                },
                occurred_at=occurred_at,
            )
            self._insert_outbox(
                connection,
                topic="WORKFLOW_STAGE_READY",
                aggregate_id=stage_run_id,
                payload={
                    "workflow_run_id": workflow_run_id,
                    "stage_run_id": stage_run_id,
                    "input_digest": input_digest,
                },
                occurred_at=occurred_at,
            )
            self._complete_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                workflow_run_id=workflow_run_id,
            )
            return WorkflowRun(
                workflow_run_id=workflow_run_id,
                project_id=command.project_id,
                workflow_code=command.workflow_code,
                status=WorkflowStatus.QUEUED,
                head=head,
                input_digest=input_digest,
                created_at=occurred_at,
                updated_at=occurred_at,
            )

    def get(self, *, project_id: str, workflow_run_id: str, user_id: str) -> WorkflowRun:
        with self._engine.connect() as connection:
            return self._load_owned_workflow(
                connection,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )

    def list_events(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> list[WorkflowEvent]:
        with self._engine.connect() as connection:
            self._load_owned_workflow(
                connection,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            rows = connection.execute(
                text(
                    """
                    SELECT sequence_id, workflow_run_id, event_type, event_json, occurred_at
                    FROM workflow_events
                    WHERE workflow_run_id = :workflow_run_id
                    ORDER BY sequence_id
                    """
                ),
                {"workflow_run_id": workflow_run_id},
            ).mappings()
            return [self._event_from_row(row) for row in rows]

    def cancel(self, command: CancelWorkflowCommand) -> WorkflowRun:
        operation = f"CANCEL_WORKFLOW:{command.project_id}:{command.workflow_run_id}"
        request_digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "project_id": command.project_id,
                    "workflow_run_id": command.workflow_run_id,
                }
            )
        ).digest()
        with self._engine.begin() as connection:
            self._lock_project(
                connection,
                project_id=command.project_id,
                user_id=command.user_id,
            )
            current = self._load_owned_workflow(
                connection,
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                user_id=command.user_id,
                for_update=True,
            )
            if not self._claim_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                digest=request_digest,
            ):
                workflow_run_id = self._replay_idempotency(
                    connection,
                    user_id=command.user_id,
                    operation=operation,
                    idempotency_key=command.idempotency_key,
                    digest=request_digest,
                )
                return self._load_owned_workflow(
                    connection,
                    project_id=command.project_id,
                    workflow_run_id=workflow_run_id,
                    user_id=command.user_id,
                )

            if current.status not in TERMINAL_WORKFLOW_STATUSES:
                occurred_at = self._now()
                connection.execute(
                    text(
                        "UPDATE venture_projects "
                        "SET workflow_generation = workflow_generation + 1 "
                        "WHERE project_id = :project_id"
                    ),
                    {"project_id": command.project_id},
                )
                connection.execute(
                    text(
                        """
                        UPDATE workflow_runs
                        SET status = 'CANCELLED', updated_at = :occurred_at,
                            cancelled_at = :occurred_at
                        WHERE workflow_run_id = :workflow_run_id
                        """
                    ),
                    {
                        "occurred_at": occurred_at,
                        "workflow_run_id": command.workflow_run_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        UPDATE stage_runs
                        SET status = 'CANCELLED', updated_at = :occurred_at,
                            completed_at = :occurred_at, lease_token = NULL,
                            lease_owner = NULL, lease_expires_at = NULL
                        WHERE workflow_run_id = :workflow_run_id
                          AND status IN ('PENDING', 'READY', 'RUNNING', 'CHECKPOINTED')
                        """
                    ),
                    {
                        "occurred_at": occurred_at,
                        "workflow_run_id": command.workflow_run_id,
                    },
                )
                self._insert_event(
                    connection,
                    workflow_run_id=command.workflow_run_id,
                    event_type="WORKFLOW_CANCELLED",
                    data={},
                    occurred_at=occurred_at,
                )
                self._insert_outbox(
                    connection,
                    topic="WORKFLOW_CLEANUP",
                    aggregate_id=command.workflow_run_id,
                    payload={"workflow_run_id": command.workflow_run_id},
                    occurred_at=occurred_at,
                )

            self._complete_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                workflow_run_id=command.workflow_run_id,
            )
            return self._load_owned_workflow(
                connection,
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                user_id=command.user_id,
            )

    def _lock_project(
        self,
        connection: Connection,
        *,
        project_id: str,
        user_id: str,
    ) -> RowMapping:
        row = connection.execute(
            text(
                """
                SELECT p.project_id, p.current_state_version, p.workflow_generation,
                       s.founder_snapshot_id, s.area_snapshot_id, s.state_json
                FROM venture_projects p
                LEFT JOIN venture_states s
                  ON s.project_id = p.project_id
                 AND s.state_version = p.current_state_version
                WHERE p.project_id = :project_id AND p.owner_user_id = :user_id
                FOR UPDATE OF p
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            raise ProjectNotFoundError("Project does not exist")
        return row

    def _load_owned_workflow(
        self,
        connection: Connection,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
        for_update: bool = False,
    ) -> WorkflowRun:
        suffix = " FOR UPDATE OF w" if for_update else ""
        row = connection.execute(
            text(
                """
                SELECT w.*
                FROM workflow_runs w
                JOIN venture_projects p ON p.project_id = w.project_id
                WHERE w.project_id = :project_id
                  AND w.workflow_run_id = :workflow_run_id
                  AND p.owner_user_id = :user_id
                """
                + suffix
            ),
            {
                "project_id": project_id,
                "workflow_run_id": workflow_run_id,
                "user_id": user_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise WorkflowNotFoundError("Workflow does not exist")
        return self._workflow_from_row(row)

    @staticmethod
    def _workflow_from_row(row: RowMapping) -> WorkflowRun:
        return WorkflowRun(
            workflow_run_id=row["workflow_run_id"],
            project_id=row["project_id"],
            workflow_code=row["workflow_code"],
            status=row["status"],
            head=HeadFence(
                workflow_generation=row["workflow_generation"],
                state_version=row["state_version"],
                founder_snapshot_id=row["founder_snapshot_id"],
                area_snapshot_id=row["area_snapshot_id"],
                evidence_snapshot_id=row["evidence_snapshot_id"],
                policy_snapshot_id=row["policy_snapshot_id"],
                index_generation_id=row["index_generation_id"],
                seed_registry_id=row["seed_registry_id"],
            ),
            input_digest=row["input_digest"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cancelled_at=row["cancelled_at"],
        )

    @staticmethod
    def _event_from_row(row: RowMapping) -> WorkflowEvent:
        data = row["event_json"]
        if isinstance(data, str):
            data = json.loads(data)
        return WorkflowEvent(
            sequence_id=row["sequence_id"],
            workflow_run_id=row["workflow_run_id"],
            event_type=row["event_type"],
            data=data,
            occurred_at=row["occurred_at"],
        )

    def _claim_idempotency(
        self,
        connection: Connection,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        digest: bytes,
    ) -> bool:
        return (
            connection.execute(
                text(
                    """
                    INSERT INTO workflow_idempotency_records(
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
            is not None
        )

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
                SELECT request_digest, response_workflow_run_id
                FROM workflow_idempotency_records
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
        workflow_run_id = row["response_workflow_run_id"]
        if not isinstance(workflow_run_id, str):
            raise RuntimeError("Committed workflow idempotency record has no response")
        return workflow_run_id

    @staticmethod
    def _complete_idempotency(
        connection: Connection,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        workflow_run_id: str,
    ) -> None:
        connection.execute(
            text(
                """
                UPDATE workflow_idempotency_records
                SET response_workflow_run_id = :workflow_run_id
                WHERE user_id = :user_id
                  AND operation = :operation
                  AND idempotency_key = :idempotency_key
                """
            ),
            {
                "workflow_run_id": workflow_run_id,
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        )

    @staticmethod
    def _insert_event(
        connection: Connection,
        *,
        workflow_run_id: str,
        event_type: str,
        data: dict[str, object],
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workflow_events(
                    workflow_run_id, event_type, event_json, occurred_at
                ) VALUES (
                    :workflow_run_id, :event_type, CAST(:event_json AS JSONB), :occurred_at
                )
                """
            ),
            {
                "workflow_run_id": workflow_run_id,
                "event_type": event_type,
                "event_json": json.dumps(data, separators=(",", ":")),
                "occurred_at": occurred_at,
            },
        )

    @staticmethod
    def _insert_outbox(
        connection: Connection,
        *,
        topic: str,
        aggregate_id: str,
        payload: dict[str, str],
        occurred_at: datetime,
    ) -> None:
        payload_bytes = rfc8785.dumps(payload)
        connection.execute(
            text(
                """
                INSERT INTO workflow_outbox(
                    topic, aggregate_id, payload_json, payload_digest,
                    available_at, created_at
                ) VALUES (
                    :topic, :aggregate_id, CAST(:payload_json AS JSONB), :payload_digest,
                    :available_at, :created_at
                )
                """
            ),
            {
                "topic": topic,
                "aggregate_id": aggregate_id,
                "payload_json": payload_bytes.decode(),
                "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                "available_at": occurred_at,
                "created_at": occurred_at,
            },
        )
