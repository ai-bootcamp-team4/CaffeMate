import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.domain.errors import (
    FeedbackPreconditionError,
    FeedbackPreviewNotFoundError,
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
)
from app.feedback.models import (
    FeedbackPreviewRecord,
    FeedbackPreviewStatus,
)
from app.workflows.models import HeadFence


class PostgresFeedbackRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))

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
                        proposal_json=CAST(:proposal AS JSONB), status=:status,
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
            status=row["status"],
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
