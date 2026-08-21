import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import rfc8785
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.contracts.schema_registry import CandidateContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError
from app.results.models import ResultBundlePayload
from app.workflows.first_proposal import FirstProposalStage, stage_input_digest
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    HeadFence,
    StageFailure,
    StageLease,
)

LEASE_SECONDS = 45
MAX_STAGE_ATTEMPTS = 3


class PostgresStageExecutionRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_token: Callable[[], str] | None = None,
        new_result_id: Callable[[], str] | None = None,
        contracts: CandidateContractValidator | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_token = new_token or (lambda: secrets.token_urlsafe(32))
        self._new_result_id = new_result_id or (lambda: str(uuid4()))
        self._contracts = contracts or ContractRegistry()

    def authorize(self, lease: StageLease) -> bool:
        now = self._now()
        with self._engine.connect() as connection:
            row = self._load_stage(
                connection,
                stage_run_id=lease.stage_run_id,
                for_update=False,
            )
            return bool(
                row is not None
                and row["workflow_run_id"] == lease.workflow_run_id
                and row["stage_code"] == lease.stage_code
                and row["input_digest"] == lease.input_digest
                and self._lease_is_current(row, lease_token=lease.lease_token, now=now)
                and self._stored_head(row) == lease.head
                and self._stored_head(row) == self._current_head(row)
            )

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

            bundle = self._parse_result_bundle(result)
            is_commit_stage = row["stage_code"] == FirstProposalStage.COMMIT_RESULT.value
            if bundle is not None and not is_commit_stage:
                raise ContractValidationError("Only COMMIT_RESULT may persist a ResultBundle")
            if bundle is None and is_commit_stage:
                raise ContractValidationError("COMMIT_RESULT requires a ResultBundle")
            result_bundle_id: str | None = None
            if bundle is not None:
                try:
                    bundle.validate_contracts(
                        project_id=row["project_id"],
                        state_version=row["state_version"],
                        contracts=self._contracts,
                    )
                except ValueError as error:
                    raise ContractValidationError(str(error)) from error
                result_bundle_id = self._persist_result_bundle(
                    connection,
                    row=row,
                    bundle=bundle,
                    created_at=now,
                )

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
                    "event": json.dumps(
                        {
                            "stage_run_id": stage_run_id,
                            "stage_code": row["stage_code"],
                            "result_bundle_id": result_bundle_id,
                        }
                    ),
                    "now": now,
                },
            )
            ready_stages = connection.execute(
                text(
                    """
                    SELECT candidate.stage_run_id, candidate.stage_code,
                           candidate.input_digest
                    FROM stage_runs candidate
                    WHERE candidate.workflow_run_id=:workflow_run_id
                      AND candidate.status='PENDING'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM stage_dependencies dependency
                          JOIN stage_runs prerequisite
                            ON prerequisite.stage_run_id=dependency.depends_on_stage_run_id
                          WHERE dependency.stage_run_id=candidate.stage_run_id
                            AND prerequisite.status <> 'SUCCEEDED'
                      )
                    ORDER BY candidate.created_at, candidate.stage_code
                    FOR UPDATE OF candidate
                    """
                ),
                {"workflow_run_id": row["workflow_run_id"]},
            ).mappings().all()
            for ready in ready_stages:
                dependencies = connection.execute(
                    text(
                        """
                        SELECT prerequisite.stage_code, prerequisite.input_digest,
                               prerequisite.result_json
                        FROM stage_dependencies dependency
                        JOIN stage_runs prerequisite
                          ON prerequisite.stage_run_id=dependency.depends_on_stage_run_id
                        WHERE dependency.stage_run_id=:stage_run_id
                        ORDER BY prerequisite.stage_code
                        """
                    ),
                    {"stage_run_id": ready["stage_run_id"]},
                ).mappings().all()
                ready_digest = stage_input_digest(
                    workflow_run_id=row["workflow_run_id"],
                    stage_code=FirstProposalStage(ready["stage_code"]),
                    head=self._stored_head(row),
                    dependencies=tuple(
                        {
                            "stage_code": dependency["stage_code"],
                            "input_digest": dependency["input_digest"],
                            "result": dependency["result_json"],
                        }
                        for dependency in dependencies
                    ),
                )
                connection.execute(
                    text(
                        "UPDATE stage_runs SET status='READY', input_digest=:input_digest, "
                        "updated_at=:now "
                        "WHERE stage_run_id=:stage_run_id AND status='PENDING'"
                    ),
                    {
                        "input_digest": ready_digest,
                        "now": now,
                        "stage_run_id": ready["stage_run_id"],
                    },
                )
                self._insert_stage_ready_event(
                    connection,
                    workflow_run_id=row["workflow_run_id"],
                    stage_run_id=ready["stage_run_id"],
                    stage_code=ready["stage_code"],
                    input_digest=ready_digest,
                    occurred_at=now,
                )
            incomplete = connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM stage_runs "
                    "WHERE workflow_run_id=:workflow_run_id AND status <> 'SUCCEEDED')"
                ),
                {"workflow_run_id": row["workflow_run_id"]},
            ).scalar_one()
            if not incomplete:
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
                            :workflow_run_id, 'WORKFLOW_SUCCEEDED', '{}'::JSONB, :now
                        )
                        """
                    ),
                    {"workflow_run_id": row["workflow_run_id"], "now": now},
                )
            return CheckpointOutcome.APPLIED

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
            if row is None:
                return FailureOutcome.LEASE_REJECTED
            if row["stage_status"] in {"SUCCEEDED", "FAILED", "TIMED_OUT"}:
                return FailureOutcome.DUPLICATE_DISCARDED
            if row["workflow_status"] == "CANCELLED" or row["stage_status"] == "CANCELLED":
                return FailureOutcome.CANCELLED_DISCARDED
            if row["input_digest"] != input_digest:
                return FailureOutcome.LEASE_REJECTED
            if not self._lease_is_current(row, lease_token=lease_token, now=now):
                return FailureOutcome.LEASE_REJECTED
            if self._stored_head(row) != self._current_head(row):
                return FailureOutcome.STALE_DISCARDED

            failure_json = json.dumps(failure.model_dump(mode="json"), separators=(",", ":"))
            retryable = failure.retryable and int(row["attempt"]) < MAX_STAGE_ATTEMPTS
            if retryable:
                connection.execute(
                    text(
                        """
                        UPDATE stage_runs SET status='READY', failure_json=CAST(:failure AS JSONB),
                            lease_token_digest=NULL, lease_owner=NULL, lease_expires_at=NULL,
                            heartbeat_at=NULL, updated_at=:now
                        WHERE stage_run_id=:stage_run_id
                        """
                    ),
                    {"failure": failure_json, "now": now, "stage_run_id": stage_run_id},
                )
                self._insert_failure_event(
                    connection,
                    workflow_run_id=row["workflow_run_id"],
                    stage_run_id=stage_run_id,
                    event_type="STAGE_RETRY_SCHEDULED",
                    attempt=int(row["attempt"]),
                    failure=failure,
                    occurred_at=now,
                )
                return FailureOutcome.RETRY_SCHEDULED

            stage_status = "TIMED_OUT" if failure.code == "STAGE_TIMEOUT" else "FAILED"
            connection.execute(
                text(
                    """
                    UPDATE stage_runs SET status=:stage_status,
                        failure_json=CAST(:failure AS JSONB), completed_at=:now, updated_at=:now,
                        lease_token_digest=NULL, lease_owner=NULL, lease_expires_at=NULL
                    WHERE stage_run_id=:stage_run_id
                    """
                ),
                {
                    "stage_status": stage_status,
                    "failure": failure_json,
                    "now": now,
                    "stage_run_id": stage_run_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workflow_runs SET status='FAILED', updated_at=:now "
                    "WHERE workflow_run_id=:workflow_run_id"
                ),
                {"now": now, "workflow_run_id": row["workflow_run_id"]},
            )
            connection.execute(
                text(
                    """
                    UPDATE stage_runs SET status='CANCELLED', completed_at=:now, updated_at=:now,
                        lease_token_digest=NULL, lease_owner=NULL, lease_expires_at=NULL
                    WHERE workflow_run_id=:workflow_run_id
                      AND stage_run_id <> :stage_run_id
                      AND status IN ('PENDING', 'READY', 'RUNNING', 'CHECKPOINTED')
                    """
                ),
                {
                    "now": now,
                    "workflow_run_id": row["workflow_run_id"],
                    "stage_run_id": stage_run_id,
                },
            )
            self._insert_failure_event(
                connection,
                workflow_run_id=row["workflow_run_id"],
                stage_run_id=stage_run_id,
                event_type="STAGE_FAILED",
                attempt=int(row["attempt"]),
                failure=failure,
                occurred_at=now,
            )
            return FailureOutcome.TERMINAL_FAILED

    @staticmethod
    def _insert_stage_ready_event(
        connection: Connection,
        *,
        workflow_run_id: str,
        stage_run_id: str,
        stage_code: str,
        input_digest: str,
        occurred_at: datetime,
    ) -> None:
        payload = {
            "workflow_run_id": workflow_run_id,
            "stage_run_id": stage_run_id,
            "input_digest": input_digest,
        }
        payload_bytes = rfc8785.dumps(payload)
        connection.execute(
            text(
                """
                INSERT INTO workflow_events(
                    workflow_run_id, event_type, event_json, occurred_at
                ) VALUES (
                    :workflow_run_id, 'STAGE_READY', CAST(:event_json AS JSONB), :occurred_at
                )
                """
            ),
            {
                "workflow_run_id": workflow_run_id,
                "event_json": json.dumps(
                    {"stage_run_id": stage_run_id, "stage_code": stage_code},
                    separators=(",", ":"),
                ),
                "occurred_at": occurred_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO workflow_outbox(
                    topic, aggregate_id, payload_json, payload_digest,
                    available_at, created_at
                ) VALUES (
                    'WORKFLOW_STAGE_READY', :aggregate_id, CAST(:payload_json AS JSONB),
                    :payload_digest, :occurred_at, :occurred_at
                )
                """
            ),
            {
                "aggregate_id": stage_run_id,
                "payload_json": payload_bytes.decode(),
                "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                "occurred_at": occurred_at,
            },
        )

    @staticmethod
    def _insert_failure_event(
        connection: Connection,
        *,
        workflow_run_id: str,
        stage_run_id: str,
        event_type: str,
        attempt: int,
        failure: StageFailure,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workflow_events(
                    workflow_run_id, event_type, event_json, occurred_at
                ) VALUES (
                    :workflow_run_id, :event_type, CAST(:event AS JSONB), :occurred_at
                )
                """
            ),
            {
                "workflow_run_id": workflow_run_id,
                "event_type": event_type,
                "event": json.dumps(
                    {
                        "stage_run_id": stage_run_id,
                        "attempt": attempt,
                        "failure": failure.model_dump(mode="json"),
                    },
                    separators=(",", ":"),
                ),
                "occurred_at": occurred_at,
            },
        )

    def _persist_result_bundle(
        self,
        connection: Connection,
        *,
        row: RowMapping,
        bundle: ResultBundlePayload,
        created_at: datetime,
    ) -> str:
        result_bundle_id = self._new_result_id()
        connection.execute(
            text(
                """
                INSERT INTO result_bundles(
                    result_bundle_id, project_id, workflow_run_id,
                    workflow_generation, state_version, founder_snapshot_id,
                    area_snapshot_id, evidence_snapshot_id, policy_snapshot_id,
                    index_generation_id, seed_registry_id, bundle_json, created_at
                ) VALUES (
                    :result_bundle_id, :project_id, :workflow_run_id,
                    :workflow_generation, :state_version, :founder_snapshot_id,
                    :area_snapshot_id, :evidence_snapshot_id, :policy_snapshot_id,
                    :index_generation_id, :seed_registry_id, CAST(:bundle_json AS JSONB),
                    :created_at
                )
                """
            ),
            {
                "result_bundle_id": result_bundle_id,
                "project_id": row["project_id"],
                "workflow_run_id": row["workflow_run_id"],
                **self._stored_head(row).model_dump(mode="python"),
                "bundle_json": json.dumps(bundle.model_dump(mode="json")),
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "UPDATE venture_projects SET current_result_bundle_id=:result_bundle_id "
                "WHERE project_id=:project_id"
            ),
            {
                "result_bundle_id": result_bundle_id,
                "project_id": row["project_id"],
            },
        )
        return result_bundle_id

    @staticmethod
    def _parse_result_bundle(result: dict[str, object]) -> ResultBundlePayload | None:
        value = result.get("result_bundle")
        if value is None:
            return None
        try:
            return ResultBundlePayload.model_validate(value)
        except ValidationError as error:
            raise ContractValidationError("Result bundle shape is invalid") from error

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
                       s.lease_expires_at, w.workflow_run_id, w.project_id,
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
