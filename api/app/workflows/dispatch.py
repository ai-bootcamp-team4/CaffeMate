import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import rfc8785
from google.cloud import pubsub_v1  # type: ignore[attr-defined]
from sqlalchemy import Engine, text

OUTBOX_CLAIM_SECONDS = 45


class PublishFuture(Protocol):
    def result(self, timeout: float | None = None) -> str: ...


class PublisherClient(Protocol):
    def publish(self, topic: str, data: bytes, **attributes: str) -> PublishFuture: ...


@dataclass(frozen=True)
class ClaimedWorkflowMessage:
    outbox_id: int
    aggregate_id: str
    payload: Mapping[str, object]
    payload_digest: str
    claim_token: str


class WorkflowDispatcher(Protocol):
    def dispatch(self, workflow_run_id: str | None = None) -> bool: ...


class PostgresPubSubWorkflowDispatcher:
    """Publish durable workflow outbox rows immediately, with Scheduler retry support."""

    def __init__(
        self,
        engine: Engine,
        *,
        topic_resource: str,
        publisher_id: str,
        client: PublisherClient | None = None,
        now: Callable[[], datetime] | None = None,
        new_token: Callable[[], str] | None = None,
    ) -> None:
        if not topic_resource:
            raise ValueError("Workflow stage topic resource is required")
        self._engine = engine
        self._topic = topic_resource
        self._publisher_id = publisher_id
        self._client = client or cast(PublisherClient, pubsub_v1.PublisherClient())
        self._now = now or (lambda: datetime.now(UTC))
        self._new_token = new_token or (lambda: secrets.token_urlsafe(32))

    def dispatch(self, workflow_run_id: str | None = None) -> bool:
        message = self._claim(workflow_run_id=workflow_run_id)
        if message is None:
            return False
        try:
            message_id = self._client.publish(
                self._topic,
                rfc8785.dumps(cast(Any, dict(message.payload))),
                logical_topic="WORKFLOW_STAGE_READY",
                outbox_id=str(message.outbox_id),
                aggregate_id=message.aggregate_id,
                payload_digest=message.payload_digest,
            ).result(timeout=10.0)
        except Exception:
            self._release(message)
            raise
        if not self._mark_published(message, message_id=message_id):
            raise RuntimeError("Workflow outbox claim was lost after publish")
        return True

    def _claim(self, *, workflow_run_id: str | None) -> ClaimedWorkflowMessage | None:
        now = self._now()
        aggregate_clause = " AND aggregate_id=:aggregate_id" if workflow_run_id else ""
        parameters: dict[str, object] = {"now": now}
        if workflow_run_id:
            parameters["aggregate_id"] = workflow_run_id
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT outbox_id, aggregate_id, payload_json, payload_digest
                    FROM workflow_outbox
                    WHERE topic='WORKFLOW_STAGE_READY'
                      AND ((status='PENDING' AND available_at <= :now)
                        OR (status='PUBLISHING' AND claim_expires_at <= :now))
                    """
                    + aggregate_clause
                    + """
                    ORDER BY available_at, outbox_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                parameters,
            ).mappings().one_or_none()
            if row is None:
                return None
            token = self._new_token()
            connection.execute(
                text(
                    """
                    UPDATE workflow_outbox
                    SET status='PUBLISHING', attempts=attempts+1,
                        claim_token_digest=:digest, claim_expires_at=:expires,
                        publisher_id=:publisher_id
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {
                    "digest": self._digest(token),
                    "expires": now + timedelta(seconds=OUTBOX_CLAIM_SECONDS),
                    "publisher_id": self._publisher_id,
                    "outbox_id": row["outbox_id"],
                },
            )
            return ClaimedWorkflowMessage(
                outbox_id=int(row["outbox_id"]),
                aggregate_id=str(row["aggregate_id"]),
                payload=row["payload_json"],
                payload_digest=str(row["payload_digest"]),
                claim_token=token,
            )

    def _release(self, message: ClaimedWorkflowMessage) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE workflow_outbox
                    SET status='PENDING', claim_token_digest=NULL,
                        claim_expires_at=NULL, publisher_id=NULL,
                        available_at=:available_at
                    WHERE outbox_id=:outbox_id AND status='PUBLISHING'
                      AND claim_token_digest=:digest
                    """
                ),
                {
                    "available_at": self._now() + timedelta(seconds=5),
                    "outbox_id": message.outbox_id,
                    "digest": self._digest(message.claim_token),
                },
            )

    def _mark_published(self, message: ClaimedWorkflowMessage, *, message_id: str) -> bool:
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    """
                    UPDATE workflow_outbox
                    SET status='PUBLISHED', published_at=:now,
                        pubsub_message_id=:message_id, claim_token_digest=NULL,
                        claim_expires_at=NULL
                    WHERE outbox_id=:outbox_id AND status='PUBLISHING'
                      AND claim_token_digest=:digest
                    """
                ),
                {
                    "now": self._now(),
                    "message_id": message_id,
                    "outbox_id": message.outbox_id,
                    "digest": self._digest(message.claim_token),
                },
            ).rowcount
            return changed == 1

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()