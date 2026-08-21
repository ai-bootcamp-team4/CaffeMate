import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.workflows.models import CheckpointOutcome, HeadFence, StageLease

LEASE_SECONDS = 45


class PostgresStageExecutionRepository:
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
            if row is None or row["workflow_status"] in self._terminal_workflow_statuses():
                return None
            if row["stage_status"] == "RUNNING" and row["lease_expires_at"] > now:
                return None
            if row["stage_status"] not in {"READY", "RUNNING"}:
                return None
            if row["input_digest"] != expected_input_digest:
                return None
            if self._stored_head(row) != self._current_head(row):
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
                        lease_expires_at=:expires_at, heartbeat_at=:now, updated_at=:now
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

    def heartbeat(self, *, stage_run_id: str, lease_token: str) -> bool:
        now = self._now()
        with self._engine.begin() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=True)
            if row is None or not self._lease_is_current(row, lease_token=lease_token, now=now):
                return False
            if self._stored_head(row) != self._current_head(row):
                return False
            expires_at = now + timedelta(seconds=LEASE_SECONDS)
            connection.execute(
                text(
                    """
                    UPDATE stage_runs SET heartbeat_at=:now, lease_expires_at=:expires_at,
                        updated_at=:now
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {"now": now, "expires_at": expires_at, "stage_run_id": stage_run_id},
            )
            return True

    def checkpoint(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        result: dict[str, object],
    ) -> CheckpointOutcome:
        now = self._now()
        with self._engine.begin() as connection:
            row = self._load_stage(connection, stage_run_id=stage_run_id, for_update=True)
            if row is None:
                return CheckpointOutcome.LEASE_REJECTED
            if row["stage_status"] == "SUCCEEDED":
                return CheckpointOutcome.DUPLICATE_DISCARDED
            if row["workflow_status"] == "CANCELLED" or row["stage_status"] == "CANCELLED":
                return CheckpointOutcome.CANCELLED_DISCARDED
            if row["input_digest"] != input_digest:
                return CheckpointOutcome.LEASE_REJECTED
            if not self._lease_is_current(row, lease_token=lease_token, now=now):
                if row["lease_expires_at"] is not None and row["lease_expires_at"] <= now:
                    return CheckpointOutcome.LATE_DISCARDED
                return CheckpointOutcome.LEASE_REJECTED
            if self._stored_head(row) != self._current_head(row):
                return CheckpointOutcome.STALE_DISCARDED

            connection.execute(
                text(
                    """
                    UPDATE stage_runs SET status='SUCCEEDED', result_json=CAST(:result AS JSONB),
                        completed_at=:now, updated_at=:now, lease_token_digest=NULL,
                        lease_owner=NULL, lease_expires_at=NULL
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {
                    "result": json.dumps(result, separators=(",", ":")),
                    "now": now,
                    "stage_run_id": stage_run_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workflow_runs SET status='SUCCEEDED', updated_at=:now "
                    "WHERE workflow_run_id=:workflow_run_id"
                ),
                {"now": now, "workflow_run_id": row["workflow_run_id"]},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO workflow_events(
                        workflow_run_id, event_type, event_json, occurred_at
                    ) VALUES (
                        :workflow_run_id, 'STAGE_SUCCEEDED', CAST(:event AS JSONB), :now
                    )
                    """
                ),
                {
                    "workflow_run_id": row["workflow_run_id"],
                    "event": json.dumps({"stage_run_id": stage_run_id}),
                    "now": now,
                },
            )
            return CheckpointOutcome.APPLIED

    @staticmethod
    def _load_stage(
        connection: Connection,
        *,
        stage_run_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        suffix = " FOR UPDATE OF s, w" if for_update else ""
        return connection.execute(
            text(
                """
                SELECT s.stage_run_id, s.stage_code, s.status AS stage_status,
                       s.input_digest, s.attempt, s.lease_token_digest,
                       s.lease_expires_at, w.workflow_run_id,
                       w.status AS workflow_status,
                       w.workflow_generation, w.state_version,
                       w.founder_snapshot_id, w.area_snapshot_id,
                       w.evidence_snapshot_id, w.policy_snapshot_id,
                       w.index_generation_id, w.seed_registry_id,
                       h.workflow_generation AS current_workflow_generation,
                       h.state_version AS current_state_version,
                       h.founder_snapshot_id AS current_founder_snapshot_id,
                       h.area_snapshot_id AS current_area_snapshot_id,
                       h.evidence_snapshot_id AS current_evidence_snapshot_id,
                       h.policy_snapshot_id AS current_policy_snapshot_id,
                       h.index_generation_id AS current_index_generation_id,
                       h.seed_registry_id AS current_seed_registry_id
                FROM stage_runs s
                JOIN workflow_runs w ON w.workflow_run_id=s.workflow_run_id
                JOIN project_heads h ON h.project_id=w.project_id
                WHERE s.stage_run_id=:stage_run_id
                """
                + suffix
            ),
            {"stage_run_id": stage_run_id},
        ).mappings().one_or_none()

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
            and row["workflow_status"] == "RUNNING"
            and row["lease_token_digest"] == cls._digest_token(lease_token)
            and row["lease_expires_at"] is not None
            and row["lease_expires_at"] > now
        )

    @staticmethod
    def _digest_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _terminal_workflow_statuses() -> set[str]:
        return {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}
