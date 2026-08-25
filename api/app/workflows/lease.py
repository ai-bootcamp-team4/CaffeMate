import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.workflows.models import FailureOutcome, HeadFence, StageFailure, StageLease

LEASE_SECONDS = 90
MAX_STAGE_ATTEMPTS = 3


class PostgresWorkflowLeaseRepository:
    """Own the single orchestration lease while the API records user-visible phases."""

    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_token: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_token = new_token or (lambda: secrets.token_urlsafe(32))

    def claim(
        self,
        *,
        stage_run_id: str,
        worker_id: str,
        expected_input_digest: str,
    ) -> StageLease | None:
        now = self._now()
        with self._engine.begin() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=True)
            if row is None or row["stage_code"] != "RUN_PROPOSAL":
                return None
            if row["workflow_status"] in self._terminal_workflow_statuses():
                return None
            if row["input_digest"] != expected_input_digest:
                return None
            if row["stage_status"] == "RUNNING" and row["lease_expires_at"] > now:
                return None
            if row["stage_status"] not in {"READY", "RUNNING"}:
                return None
            if self._stored_head(row) != self._current_head(row):
                self._mark_stale(connection, row=row, now=now)
                return None

            token = self._new_token()
            expires_at = now + timedelta(seconds=LEASE_SECONDS)
            attempt = int(row["attempt"]) + 1
            connection.execute(
                text(
                    """
                    UPDATE stage_runs SET
                        status='RUNNING', attempt=:attempt,
                        lease_token_digest=:token_digest, lease_owner=:worker_id,
                        lease_expires_at=:expires_at, heartbeat_at=:now, updated_at=:now,
                        failure_json=NULL
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {
                    "attempt": attempt,
                    "token_digest": self._digest_token(token),
                    "worker_id": worker_id,
                    "expires_at": expires_at,
                    "now": now,
                    "stage_run_id": stage_run_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workflow_runs SET status='RUNNING', updated_at=:now "
                    "WHERE workflow_run_id=:workflow_run_id AND status='QUEUED'"
                ),
                {"now": now, "workflow_run_id": row["workflow_run_id"]},
            )
            return StageLease(
                workflow_run_id=row["workflow_run_id"],
                stage_run_id=stage_run_id,
                stage_code=row["stage_code"],
                input_digest=row["input_digest"],
                lease_token=token,
                lease_expires_at=expires_at,
                attempt=attempt,
                head=self._stored_head(row),
            )

    def authorize(self, lease: StageLease) -> bool:
        now = self._now()
        with self._engine.connect() as connection:
            row = self._load_stage(connection, stage_run_id=lease.stage_run_id, for_update=False)
            return bool(
                row is not None
                and row["workflow_run_id"] == lease.workflow_run_id
                and row["stage_code"] == lease.stage_code == "RUN_PROPOSAL"
                and row["input_digest"] == lease.input_digest
                and self._lease_is_current(row, lease_token=lease.lease_token, now=now)
                and self._stored_head(row) == lease.head
                and self._stored_head(row) == self._current_head(row)
            )

    def authorize_mutation(
        self,
        connection: Connection,
        *,
        lease: StageLease,
        now: datetime,
    ) -> bool:
        """Fence a state mutation with project/head and orchestration-stage locks."""

        authoritative = (
            connection.execute(
                text(
                    """
                    SELECT project.current_state_version, project.workflow_generation,
                           head.workflow_generation AS head_workflow_generation,
                           head.state_version AS head_state_version,
                           head.founder_snapshot_id, head.area_snapshot_id,
                           head.evidence_snapshot_id, head.policy_snapshot_id,
                           head.index_generation_id, head.seed_registry_id
                    FROM workflow_runs workflow
                    JOIN venture_projects project ON project.project_id=workflow.project_id
                    JOIN project_heads head ON head.project_id=workflow.project_id
                    WHERE workflow.workflow_run_id=:workflow_run_id
                    FOR UPDATE OF project, head
                    """
                ),
                {"workflow_run_id": lease.workflow_run_id},
            )
            .mappings()
            .one_or_none()
        )
        if authoritative is None:
            return False
        current_head = HeadFence(
            workflow_generation=authoritative["head_workflow_generation"],
            state_version=authoritative["head_state_version"],
            founder_snapshot_id=authoritative["founder_snapshot_id"],
            area_snapshot_id=authoritative["area_snapshot_id"],
            evidence_snapshot_id=authoritative["evidence_snapshot_id"],
            policy_snapshot_id=authoritative["policy_snapshot_id"],
            index_generation_id=authoritative["index_generation_id"],
            seed_registry_id=authoritative["seed_registry_id"],
        )
        if (
            int(authoritative["current_state_version"]) != lease.head.state_version
            or int(authoritative["workflow_generation"]) != lease.head.workflow_generation
            or current_head != lease.head
        ):
            return False
        row = self._load_stage(connection, stage_run_id=lease.stage_run_id, for_update=True)
        return bool(
            row is not None
            and row["workflow_run_id"] == lease.workflow_run_id
            and row["stage_code"] == lease.stage_code == "RUN_PROPOSAL"
            and row["input_digest"] == lease.input_digest
            and self._lease_is_current(row, lease_token=lease.lease_token, now=now)
            and self._stored_head(row) == lease.head
            and self._stored_head(row) == self._current_head(row)
        )

    def heartbeat(self, *, stage_run_id: str, lease_token: str) -> bool:
        now = self._now()
        with self._engine.begin() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=True)
            if row is None or not self._lease_is_current(row, lease_token=lease_token, now=now):
                return False
            if self._stored_head(row) != self._current_head(row):
                self._mark_stale(connection, row=row, now=now)
                return False
            connection.execute(
                text(
                    """
                    UPDATE stage_runs
                    SET heartbeat_at=:now, lease_expires_at=:expires_at, updated_at=:now
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {
                    "now": now,
                    "expires_at": now + timedelta(seconds=LEASE_SECONDS),
                    "stage_run_id": stage_run_id,
                },
            )
            return True

    def is_pending_delivery(
        self,
        *,
        stage_run_id: str,
        expected_input_digest: str,
    ) -> bool:
        """Return whether a rejected delivery still represents unfinished work."""

        with self._engine.connect() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=False)
        return bool(
            row is not None
            and row["stage_code"] == "RUN_PROPOSAL"
            and row["input_digest"] == expected_input_digest
            and row["workflow_status"] not in self._terminal_workflow_statuses()
            and row["stage_status"] in {"READY", "RUNNING"}
        )

    def record_failure(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        failure: StageFailure,
    ) -> FailureOutcome:
        now = self._now()
        with self._engine.begin() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=True)
            if row is None or row["input_digest"] != input_digest:
                return FailureOutcome.LEASE_REJECTED
            if row["workflow_status"] in self._terminal_workflow_statuses():
                return FailureOutcome.DUPLICATE_DISCARDED
            if not self._lease_is_current(row, lease_token=lease_token, now=now):
                return FailureOutcome.LEASE_REJECTED
            if self._stored_head(row) != self._current_head(row):
                self._mark_stale(connection, row=row, now=now)
                return FailureOutcome.STALE_DISCARDED

            failure_json = json.dumps(failure.model_dump(mode="json"), separators=(",", ":"))
            if failure.retryable and int(row["attempt"]) < MAX_STAGE_ATTEMPTS:
                connection.execute(
                    text(
                        """
                        UPDATE stage_runs
                        SET status='READY', lease_token_digest=NULL, lease_owner=NULL,
                            lease_expires_at=NULL, heartbeat_at=NULL,
                            failure_json=CAST(:failure_json AS JSONB), updated_at=:now
                        WHERE stage_run_id=:stage_run_id
                        """
                    ),
                    {"failure_json": failure_json, "now": now, "stage_run_id": stage_run_id},
                )
                connection.execute(
                    text(
                        """
                        UPDATE stage_runs
                        SET status='PENDING', attempt=0, result_json=NULL, failure_json=NULL,
                            updated_at=:now, completed_at=NULL
                        WHERE workflow_run_id=:workflow_run_id
                          AND stage_code <> 'RUN_PROPOSAL'
                        """
                    ),
                    {"now": now, "workflow_run_id": row["workflow_run_id"]},
                )
                connection.execute(
                    text(
                        "UPDATE workflow_runs SET status='QUEUED', updated_at=:now "
                        "WHERE workflow_run_id=:workflow_run_id"
                    ),
                    {"now": now, "workflow_run_id": row["workflow_run_id"]},
                )
                return FailureOutcome.RETRY_SCHEDULED

            connection.execute(
                text(
                    """
                    UPDATE stage_runs
                    SET status='FAILED', failure_json=CAST(:failure_json AS JSONB),
                        lease_token_digest=NULL, lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=:now, completed_at=:now
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {"failure_json": failure_json, "now": now, "stage_run_id": stage_run_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE stage_runs
                    SET status='FAILED', failure_json=CAST(:failure_json AS JSONB),
                        updated_at=:now, completed_at=:now
                    WHERE workflow_run_id=:workflow_run_id AND status='RUNNING'
                      AND stage_code <> 'RUN_PROPOSAL'
                    """
                ),
                {
                    "failure_json": failure_json,
                    "now": now,
                    "workflow_run_id": row["workflow_run_id"],
                },
            )
            connection.execute(
                text(
                    "UPDATE workflow_runs SET status='FAILED', updated_at=:now "
                    "WHERE workflow_run_id=:workflow_run_id"
                ),
                {"now": now, "workflow_run_id": row["workflow_run_id"]},
            )
            return FailureOutcome.TERMINAL_FAILED

    def _load_stage(
        self,
        connection: Connection,
        *,
        stage_run_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        suffix = " FOR UPDATE OF stage" if for_update else ""
        return (
            connection.execute(
                text(
                    """
                    SELECT stage.stage_run_id, stage.workflow_run_id,
                           stage.stage_code, stage.status AS stage_status,
                           stage.input_digest, stage.attempt,
                           stage.lease_token_digest, stage.lease_expires_at,
                           workflow.status AS workflow_status,
                           workflow.project_id, workflow.workflow_generation,
                           workflow.state_version, workflow.founder_snapshot_id,
                           workflow.area_snapshot_id, workflow.evidence_snapshot_id,
                           workflow.policy_snapshot_id, workflow.index_generation_id,
                           workflow.seed_registry_id,
                           head.workflow_generation AS current_workflow_generation,
                           head.state_version AS current_state_version,
                           head.founder_snapshot_id AS current_founder_snapshot_id,
                           head.area_snapshot_id AS current_area_snapshot_id,
                           head.evidence_snapshot_id AS current_evidence_snapshot_id,
                           head.policy_snapshot_id AS current_policy_snapshot_id,
                           head.index_generation_id AS current_index_generation_id,
                           head.seed_registry_id AS current_seed_registry_id
                    FROM stage_runs stage
                    JOIN workflow_runs workflow
                      ON workflow.workflow_run_id=stage.workflow_run_id
                    JOIN project_heads head ON head.project_id=workflow.project_id
                    WHERE stage.stage_run_id=:stage_run_id
                    """
                    + suffix
                ),
                {"stage_run_id": stage_run_id},
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    def _stored_head(row: RowMapping) -> HeadFence:
        return HeadFence(
            workflow_generation=row["workflow_generation"],
            state_version=row["state_version"],
            founder_snapshot_id=row["founder_snapshot_id"],
            area_snapshot_id=row["area_snapshot_id"],
            evidence_snapshot_id=row["evidence_snapshot_id"],
            policy_snapshot_id=row["policy_snapshot_id"],
            index_generation_id=row["index_generation_id"],
            seed_registry_id=row["seed_registry_id"],
        )

    @staticmethod
    def _current_head(row: RowMapping) -> HeadFence:
        return HeadFence(
            workflow_generation=row["current_workflow_generation"],
            state_version=row["current_state_version"],
            founder_snapshot_id=row["current_founder_snapshot_id"],
            area_snapshot_id=row["current_area_snapshot_id"],
            evidence_snapshot_id=row["current_evidence_snapshot_id"],
            policy_snapshot_id=row["current_policy_snapshot_id"],
            index_generation_id=row["current_index_generation_id"],
            seed_registry_id=row["current_seed_registry_id"],
        )

    @classmethod
    def _lease_is_current(cls, row: RowMapping, *, lease_token: str, now: datetime) -> bool:
        return bool(
            row["stage_status"] == "RUNNING"
            and row["lease_token_digest"] == cls._digest_token(lease_token)
            and row["lease_expires_at"] is not None
            and row["lease_expires_at"] > now
        )

    @staticmethod
    def _terminal_workflow_statuses() -> set[str]:
        return {"WAITING_FOR_HUMAN", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}

    @staticmethod
    def _digest_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _mark_stale(connection: Connection, *, row: RowMapping, now: datetime) -> None:
        connection.execute(
            text(
                "UPDATE workflow_runs SET status='STALE', updated_at=:now "
                "WHERE workflow_run_id=:workflow_run_id"
            ),
            {"now": now, "workflow_run_id": row["workflow_run_id"]},
        )
        connection.execute(
            text(
                """
                UPDATE stage_runs SET status='CANCELLED', updated_at=:now, completed_at=:now
                WHERE workflow_run_id=:workflow_run_id
                  AND status IN ('PENDING', 'READY', 'RUNNING', 'CHECKPOINTED')
                """
            ),
            {"now": now, "workflow_run_id": row["workflow_run_id"]},
        )
