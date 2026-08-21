import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine, text

OUTBOX_CLAIM_SECONDS = 45


@dataclass(frozen=True)
class ClaimedOutboxMessage:
    outbox_id: int
    topic: str
    aggregate_id: str
    payload: Mapping[str, object]
    payload_digest: str
    claim_token: str


class MessagePublisher(Protocol):
    def publish(
        self,
        *,
        topic: str,
        payload: Mapping[str, object],
        attributes: Mapping[str, str],
    ) -> str: ...


class PostgresOutboxRepository:
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

    def claim_next(self, *, publisher_id: str) -> ClaimedOutboxMessage | None:
        now = self._now()
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT outbox_id, topic, aggregate_id, payload_json, payload_digest
                    FROM workflow_outbox
                    WHERE status='PENDING'
                       OR (status='PUBLISHING' AND claim_expires_at <= :now)
                    ORDER BY available_at, outbox_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {"now": now},
            ).mappings().one_or_none()
            if row is None:
                return None
            token = self._new_token()
            connection.execute(
                text(
                    """
                    UPDATE workflow_outbox SET status='PUBLISHING', attempts=attempts+1,
                        claim_token_digest=:digest, claim_expires_at=:expires,
                        publisher_id=:publisher_id
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {
                    "digest": self._digest(token),
                    "expires": now + timedelta(seconds=OUTBOX_CLAIM_SECONDS),
                    "publisher_id": publisher_id,
                    "outbox_id": row["outbox_id"],
                },
            )
            return ClaimedOutboxMessage(
                outbox_id=row["outbox_id"],
                topic=row["topic"],
                aggregate_id=row["aggregate_id"],
                payload=row["payload_json"],
                payload_digest=row["payload_digest"],
                claim_token=token,
            )

    def mark_published(
        self,
        *,
        outbox_id: int,
        claim_token: str,
        pubsub_message_id: str,
    ) -> bool:
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    """
                    UPDATE workflow_outbox SET status='PUBLISHED', published_at=:now,
                        pubsub_message_id=:message_id, claim_token_digest=NULL,
                        claim_expires_at=NULL
                    WHERE outbox_id=:outbox_id AND status='PUBLISHING'
                      AND claim_token_digest=:digest
                    """
                ),
                {
                    "now": self._now(),
                    "message_id": pubsub_message_id,
                    "outbox_id": outbox_id,
                    "digest": self._digest(claim_token),
                },
            ).rowcount
            return changed == 1

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


class OutboxPublisher:
    def __init__(
        self,
        repository: PostgresOutboxRepository,
        publisher: MessagePublisher,
        *,
        publisher_id: str,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._publisher_id = publisher_id

    def publish_one(self) -> bool:
        message = self._repository.claim_next(publisher_id=self._publisher_id)
        if message is None:
            return False
        message_id = self._publisher.publish(
            topic=message.topic,
            payload=message.payload,
            attributes={
                "outbox_id": str(message.outbox_id),
                "aggregate_id": message.aggregate_id,
                "payload_digest": message.payload_digest,
            },
        )
        if not self._repository.mark_published(
            outbox_id=message.outbox_id,
            claim_token=message.claim_token,
            pubsub_message_id=message_id,
        ):
            raise RuntimeError("Outbox claim was lost after publish; message may be redelivered")
        return True
