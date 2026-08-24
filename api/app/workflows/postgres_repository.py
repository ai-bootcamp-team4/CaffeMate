import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import (
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
    WorkflowNotFoundError,
    WorkflowPreconditionError,
)
from app.domain.models import VentureState
from app.workflows.models import (
    HeadFence,
    StartWorkflowCommand,
    WorkflowProgress,
    WorkflowRun,
    WorkflowStageProgress,
)
from app.workflows.persistence import persist_completed_first_proposal
from app.workflows.simple_proposal import SimpleProposalBuilder


class PostgresWorkflowRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        policy_snapshot_id: str,
        seed_registry_id: str,
        seed_registry: IndependentSeedRegistry | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        if not policy_snapshot_id or len(policy_snapshot_id) > 128:
            raise ValueError("policy_snapshot_id must contain 1..128 characters")
        if not seed_registry_id or len(seed_registry_id) > 128:
            raise ValueError("seed_registry_id must contain 1..128 characters")
        registry = seed_registry or IndependentSeedRegistry.load_default()
        if registry.registry_id != seed_registry_id:
            raise ValueError("seed_registry_id does not match the registered models")
        self._engine = engine
        self._policy_snapshot_id = policy_snapshot_id
        self._seed_registry_id = seed_registry_id
        self._builder = SimpleProposalBuilder(registry)
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def start(self, command: StartWorkflowCommand) -> WorkflowRun:
        operation = (
            f"START_WORKFLOW:{command.project_id}:{command.workflow_code.value}"
        )
        request_digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "project_id": command.project_id,
                    "workflow_code": command.workflow_code.value,
                }
            )
        ).digest()
        with self._engine.begin() as connection:
            state = self._load_current_state(
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
            run = persist_completed_first_proposal(
                connection,
                project_id=command.project_id,
                user_id=command.user_id,
                state=state,
                policy_snapshot_id=self._policy_snapshot_id,
                seed_registry_id=self._seed_registry_id,
                builder=self._builder,
                now=self._now(),
                new_id=self._new_id,
            )
            self._complete_idempotency(
                connection,
                user_id=command.user_id,
                operation=operation,
                idempotency_key=command.idempotency_key,
                workflow_run_id=run.workflow_run_id,
            )
            return run

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        with self._engine.connect() as connection:
            run = self._load_owned_workflow(
                connection,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
            )
            rows = connection.execute(
                text(
                    """
                    SELECT stage_run_id, stage_code, status, attempt,
                           result_json, failure_json, updated_at, completed_at
                    FROM stage_runs
                    WHERE workflow_run_id=:workflow_run_id
                    ORDER BY created_at, stage_code
                    """
                ),
                {"workflow_run_id": workflow_run_id},
            ).mappings().all()
        stages = [self._stage_from_row(row) for row in rows]
        terminal_reasons = sorted(
            {
                stage.failure_code
                for stage in stages
                if isinstance(stage.failure_code, str)
            }
        )
        return WorkflowProgress(
            **run.model_dump(),
            stages=stages,
            completed_stage_count=sum(
                stage.status.value in {"SUCCEEDED", "SKIPPED"} for stage in stages
            ),
            total_stage_count=max(1, len(stages)),
            current_stage_codes=[
                stage.stage_code
                for stage in stages
                if stage.status.value in {"READY", "RUNNING"}
            ],
            human_review_requests=[],
            terminal_reason_codes=terminal_reasons,
            poll_after_ms=None,
        )

    @staticmethod
    def _load_current_state(
        connection: Connection,
        *,
        project_id: str,
        user_id: str,
    ) -> VentureState:
        row = connection.execute(
            text(
                """
                SELECT state.state_json
                FROM venture_projects project
                JOIN venture_states state
                  ON state.project_id=project.project_id
                 AND state.state_version=project.current_state_version
                WHERE project.project_id=:project_id
                  AND project.owner_user_id=:user_id
                FOR UPDATE OF project
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        ).scalar_one_or_none()
        if row is None:
            owned = connection.execute(
                text(
                    "SELECT 1 FROM venture_projects "
                    "WHERE project_id=:project_id AND owner_user_id=:user_id"
                ),
                {"project_id": project_id, "user_id": user_id},
            ).scalar_one_or_none()
            if owned is None:
                raise ProjectNotFoundError("Project does not exist")
            raise WorkflowPreconditionError("Onboarding State must exist before workflow start")
        return VentureState.model_validate(row)

    @staticmethod
    def _load_owned_workflow(
        connection: Connection,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowRun:
        row = connection.execute(
            text(
                """
                SELECT workflow.*
                FROM workflow_runs workflow
                JOIN venture_projects project ON project.project_id=workflow.project_id
                WHERE workflow.project_id=:project_id
                  AND workflow.workflow_run_id=:workflow_run_id
                  AND project.owner_user_id=:user_id
                """
            ),
            {
                "project_id": project_id,
                "workflow_run_id": workflow_run_id,
                "user_id": user_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise WorkflowNotFoundError("Workflow does not exist")
        return PostgresWorkflowRepository._workflow_from_row(row)

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
    def _stage_from_row(row: RowMapping) -> WorkflowStageProgress:
        failure = row["failure_json"] if isinstance(row["failure_json"], dict) else {}
        result = row["result_json"] if isinstance(row["result_json"], dict) else {}
        reasons = result.get("reason_codes", [])
        return WorkflowStageProgress(
            stage_run_id=row["stage_run_id"],
            stage_code=row["stage_code"],
            status=row["status"],
            attempt=row["attempt"],
            reason_codes=(
                sorted({value for value in reasons if isinstance(value, str)})
                if isinstance(reasons, list)
                else []
            ),
            failure_code=(
                failure.get("code") if isinstance(failure.get("code"), str) else None
            ),
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
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

    @staticmethod
    def _replay_idempotency(
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
                WHERE user_id=:user_id AND operation=:operation
                  AND idempotency_key=:idempotency_key
                """
            ),
            {
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one()
        if bytes(row["request_digest"]) != digest:
            raise IdempotencyKeyReusedError(
                "Idempotency key was used with another payload"
            )
        workflow_run_id = row["response_workflow_run_id"]
        if not isinstance(workflow_run_id, str):
            raise RuntimeError("Committed idempotency record has no response")
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
                SET response_workflow_run_id=:workflow_run_id
                WHERE user_id=:user_id AND operation=:operation
                  AND idempotency_key=:idempotency_key
                """
            ),
            {
                "workflow_run_id": workflow_run_id,
                "user_id": user_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        )
