import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.contracts.schema_registry import ContractRegistry, VentureStateValidator
from app.domain.errors import (
    ContractValidationError,
    FeedbackPreconditionError,
    FeedbackPreviewNotFoundError,
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
)
from app.domain.events import FeedbackChangeConfirmed
from app.domain.models import VentureState
from app.domain.reducer import reduce_venture_state
from app.feedback.models import (
    FeedbackPreviewRecord,
    FeedbackPreviewStatus,
)
from app.workflows.models import HeadFence, WorkflowCode, WorkflowRun, WorkflowStatus
from app.workflows.selective_start import start_selective_first_proposal


class PostgresFeedbackRepository:
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

    def begin_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        result_bundle_id: str,
        source_workflow_run_id: str,
        base_state_version: int,
        head_json: dict[str, object],
        idempotency_key: str,
        request_digest: bytes,
        user_input: str,
        task: dict[str, object],
    ) -> FeedbackPreviewRecord:
        with self._engine.begin() as connection:
            project = connection.execute(
                text(
                    """
                    SELECT p.current_result_bundle_id,
                           h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id
                    FROM venture_projects p
                    JOIN project_heads h ON h.project_id=p.project_id
                    WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
            if project is None:
                raise ProjectNotFoundError("Project does not exist")
            current_head = self._head_from_row(project)
            expected_head = HeadFence.model_validate(head_json)
            if (
                project["current_result_bundle_id"] != result_bundle_id
                or current_head != expected_head
                or current_head.state_version != base_state_version
            ):
                raise FeedbackPreconditionError(
                    "Feedback requires the current result and full head"
                )
            existing = connection.execute(
                text(
                    """
                    SELECT * FROM feedback_previews
                    WHERE owner_user_id=:user_id AND project_id=:project_id
                      AND idempotency_key=:idempotency_key
                    """
                ),
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "idempotency_key": idempotency_key,
                },
            ).mappings().one_or_none()
            if existing is not None:
                if bytes(existing["request_digest"]) != request_digest:
                    raise IdempotencyKeyReusedError(
                        "Idempotency key was used with another feedback input"
                    )
                return self._from_row(existing)
            occurred_at = self._now()
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_previews(
                        preview_id, project_id, owner_user_id, result_bundle_id,
                        source_workflow_run_id, base_state_version, head_json,
                        idempotency_key, request_digest, user_input, task_json,
                        status, created_at, updated_at
                    ) VALUES (
                        :preview_id, :project_id, :owner_user_id, :result_bundle_id,
                        :source_workflow_run_id, :base_state_version,
                        CAST(:head_json AS JSONB), :idempotency_key, :request_digest,
                        :user_input, CAST(:task_json AS JSONB), 'PROCESSING',
                        :created_at, :updated_at
                    )
                    """
                ),
                {
                    "preview_id": preview_id,
                    "project_id": project_id,
                    "owner_user_id": user_id,
                    "result_bundle_id": result_bundle_id,
                    "source_workflow_run_id": source_workflow_run_id,
                    "base_state_version": base_state_version,
                    "head_json": json.dumps(head_json, separators=(",", ":")),
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "user_input": user_input,
                    "task_json": json.dumps(task, separators=(",", ":")),
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                },
            )
            return self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
            )

    def complete_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        expected_head_json: dict[str, object],
        agent_result: dict[str, object],
        proposal: dict[str, object] | None,
        status: FeedbackPreviewStatus,
    ) -> FeedbackPreviewRecord:
        with self._engine.begin() as connection:
            preview = self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
                for_update=True,
            )
            if preview.status != FeedbackPreviewStatus.PROCESSING:
                return preview
            current = connection.execute(
                text(
                    """
                    SELECT h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id
                    FROM project_heads h
                    JOIN venture_projects p ON p.project_id=h.project_id
                    WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
            if current is None:
                raise ProjectNotFoundError("Project does not exist")
            final_status = status
            if self._head_from_row(current) != HeadFence.model_validate(
                expected_head_json
            ):
                final_status = FeedbackPreviewStatus.EXPIRED
            connection.execute(
                text(
                    """
                    UPDATE feedback_previews
                    SET agent_result_json=CAST(:agent_result AS JSONB),
                        proposal_json=CAST(:proposal AS JSONB),
                        proposal_digest=:proposal_digest, status=:status,
                        updated_at=:updated_at
                    WHERE preview_id=:preview_id
                    """
                ),
                {
                    "agent_result": json.dumps(agent_result, separators=(",", ":")),
                    "proposal": (
                        json.dumps(proposal, separators=(",", ":"))
                        if proposal is not None
                        else None
                    ),
                    "proposal_digest": (
                        f"sha256:{hashlib.sha256(rfc8785.dumps(cast(Any, proposal))).hexdigest()}"
                        if proposal is not None
                        else None
                    ),
                    "status": final_status.value,
                    "updated_at": self._now(),
                    "preview_id": preview_id,
                },
            )
            return self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
            )

    def get_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
    ) -> FeedbackPreviewRecord:
        with self._engine.connect() as connection:
            return self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
            )

    def confirm_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
        expected_head: HeadFence,
        proposal_digest: str,
    ) -> tuple[FeedbackPreviewRecord, VentureState, WorkflowRun]:
        request_digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "command": "CONFIRM",
                    "preview_id": preview_id,
                    "expected_head": expected_head.model_dump(mode="json"),
                    "proposal_digest": proposal_digest,
                }
            )
        ).digest()
        with self._engine.begin() as connection:
            preview = self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
                for_update=True,
            )
            if self._is_resolution_replay(
                preview,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                expected_status=FeedbackPreviewStatus.CONFIRMED,
            ):
                return self._load_confirmed_resolution(connection, preview)
            if preview.status != FeedbackPreviewStatus.REVIEW_REQUIRED:
                raise FeedbackPreconditionError("Only a reviewable preview can be confirmed")
            if preview.head != expected_head or preview.proposal_digest != proposal_digest:
                raise FeedbackPreconditionError("Feedback proposal or full head changed")
            project = connection.execute(
                text(
                    """
                    SELECT p.current_state_version, p.current_result_bundle_id,
                           s.state_json, h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id
                    FROM venture_projects p
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
            if project is None:
                raise ProjectNotFoundError("Project does not exist")
            if (
                project["current_state_version"] != preview.base_state_version
                or project["current_result_bundle_id"] != preview.result_bundle_id
                or self._head_from_row(project) != preview.head
            ):
                raise FeedbackPreconditionError("Feedback preview expired before confirmation")
            state_json = project["state_json"]
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            current_state = VentureState.model_validate(state_json)
            proposal = preview.proposal
            if proposal is None or proposal.get("decision") != "PROPOSE_DELTA":
                raise ContractValidationError("Feedback preview has no applicable proposal")
            operations = proposal.get("operations")
            if not isinstance(operations, list) or not operations:
                raise ContractValidationError("Feedback preview operations are invalid")
            occurred_at = self._now()
            event = FeedbackChangeConfirmed(
                event_id=self._new_id(),
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
                preview_id=preview_id,
                expected_state_version=preview.base_state_version,
                proposal_digest=proposal_digest,
                operations=operations,
            )
            next_state = reduce_venture_state(current_state, event)
            assert next_state is not None
            state_value = next_state.model_dump(mode="json")
            self._contracts.validate_venture_state(state_value)
            connection.execute(
                text(
                    """
                    INSERT INTO venture_states(project_id, state_version, state_json, created_at)
                    VALUES (:project_id, :state_version, CAST(:state_json AS JSONB), :created_at)
                    """
                ),
                {
                    "project_id": project_id,
                    "state_version": next_state.state_version,
                    "state_json": json.dumps(state_value, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    "UPDATE venture_projects SET current_state_version=:state_version "
                    "WHERE project_id=:project_id"
                ),
                {"state_version": next_state.state_version, "project_id": project_id},
            )
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
                    "project_id": project_id,
                    "event_type": event.event_type,
                    "event_json": event.model_dump_json(),
                    "occurred_at": occurred_at,
                },
            )
            workflow = start_selective_first_proposal(
                connection,
                project_id=project_id,
                user_id=user_id,
                state=next_state,
                source_workflow_run_id=preview.source_workflow_run_id,
                previous_head=preview.head,
                now=occurred_at,
                new_id=self._new_id,
            )
            connection.execute(
                text(
                    """
                    UPDATE feedback_previews
                    SET status='CONFIRMED', resolution_idempotency_key=:idempotency_key,
                        resolution_request_digest=:request_digest,
                        confirmed_event_id=:event_id,
                        confirmed_state_version=:state_version,
                        recompute_workflow_run_id=:workflow_run_id,
                        resolved_at=:resolved_at, updated_at=:resolved_at
                    WHERE preview_id=:preview_id
                    """
                ),
                {
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "event_id": event.event_id,
                    "state_version": next_state.state_version,
                    "workflow_run_id": workflow.workflow_run_id,
                    "resolved_at": occurred_at,
                    "preview_id": preview_id,
                },
            )
            return (
                self._load(
                    connection,
                    preview_id=preview_id,
                    project_id=project_id,
                    user_id=user_id,
                ),
                next_state,
                workflow,
            )

    def cancel_preview(
        self,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> FeedbackPreviewRecord:
        request_digest = hashlib.sha256(
            rfc8785.dumps({"command": "CANCEL", "preview_id": preview_id})
        ).digest()
        with self._engine.begin() as connection:
            preview = self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
                for_update=True,
            )
            if self._is_resolution_replay(
                preview,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                expected_status=FeedbackPreviewStatus.CANCELLED,
            ):
                return preview
            if preview.status != FeedbackPreviewStatus.REVIEW_REQUIRED:
                raise FeedbackPreconditionError("Only a reviewable preview can be cancelled")
            occurred_at = self._now()
            connection.execute(
                text(
                    """
                    UPDATE feedback_previews
                    SET status='CANCELLED', resolution_idempotency_key=:idempotency_key,
                        resolution_request_digest=:request_digest,
                        resolved_at=:resolved_at, updated_at=:resolved_at
                    WHERE preview_id=:preview_id
                    """
                ),
                {
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "resolved_at": occurred_at,
                    "preview_id": preview_id,
                },
            )
            return self._load(
                connection,
                preview_id=preview_id,
                project_id=project_id,
                user_id=user_id,
            )

    @staticmethod
    def _is_resolution_replay(
        preview: FeedbackPreviewRecord,
        *,
        idempotency_key: str,
        request_digest: bytes,
        expected_status: FeedbackPreviewStatus,
    ) -> bool:
        if preview.status != expected_status:
            return False
        if (
            preview.resolution_idempotency_key != idempotency_key
            or preview.resolution_request_digest != request_digest
        ):
            raise FeedbackPreconditionError("Feedback preview was already resolved")
        return True

    def _load_confirmed_resolution(
        self,
        connection: Any,
        preview: FeedbackPreviewRecord,
    ) -> tuple[FeedbackPreviewRecord, VentureState, WorkflowRun]:
        state_json = connection.execute(
            text(
                """
                SELECT state_json FROM venture_states
                WHERE project_id=:project_id AND state_version=:state_version
                """
            ),
            {
                "project_id": preview.project_id,
                "state_version": preview.confirmed_state_version,
            },
        ).scalar_one()
        row = connection.execute(
            text("SELECT * FROM workflow_runs WHERE workflow_run_id=:workflow_run_id"),
            {"workflow_run_id": preview.recompute_workflow_run_id},
        ).mappings().one()
        return preview, VentureState.model_validate(state_json), self._workflow_from_row(row)

    def _load(
        self,
        connection: Any,
        *,
        preview_id: str,
        project_id: str,
        user_id: str,
        for_update: bool = False,
    ) -> FeedbackPreviewRecord:
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute(
            text(
                """
                SELECT * FROM feedback_previews
                WHERE preview_id=:preview_id AND project_id=:project_id
                  AND owner_user_id=:user_id
                """
                + suffix
            ),
            {
                "preview_id": preview_id,
                "project_id": project_id,
                "user_id": user_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise FeedbackPreviewNotFoundError("Feedback preview does not exist")
        return self._from_row(row)

    @staticmethod
    def _from_row(row: RowMapping) -> FeedbackPreviewRecord:
        return FeedbackPreviewRecord(
            preview_id=row["preview_id"],
            project_id=row["project_id"],
            owner_user_id=row["owner_user_id"],
            result_bundle_id=row["result_bundle_id"],
            source_workflow_run_id=row["source_workflow_run_id"],
            base_state_version=row["base_state_version"],
            head=HeadFence.model_validate(row["head_json"]),
            idempotency_key=row["idempotency_key"],
            user_input=row["user_input"],
            task=dict(row["task_json"]),
            agent_result=(
                dict(row["agent_result_json"])
                if isinstance(row["agent_result_json"], dict)
                else None
            ),
            proposal=(
                dict(row["proposal_json"])
                if isinstance(row["proposal_json"], dict)
                else None
            ),
            proposal_digest=row.get("proposal_digest"),
            status=row["status"],
            resolution_idempotency_key=row.get("resolution_idempotency_key"),
            resolution_request_digest=(
                bytes(row["resolution_request_digest"])
                if row.get("resolution_request_digest") is not None
                else None
            ),
            confirmed_event_id=row.get("confirmed_event_id"),
            confirmed_state_version=row.get("confirmed_state_version"),
            recompute_workflow_run_id=row.get("recompute_workflow_run_id"),
            resolved_at=row.get("resolved_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _head_from_row(row: RowMapping) -> HeadFence:
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

    @classmethod
    def _workflow_from_row(cls, row: RowMapping) -> WorkflowRun:
        return WorkflowRun(
            workflow_run_id=row["workflow_run_id"],
            project_id=row["project_id"],
            workflow_code=WorkflowCode(row["workflow_code"]),
            status=WorkflowStatus(row["status"]),
            head=cls._head_from_row(row),
            input_digest=row["input_digest"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cancelled_at=row["cancelled_at"],
        )
