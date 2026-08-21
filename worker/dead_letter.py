from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.domain.models import StrictModel
from pydantic import Field
from sqlalchemy import Engine, text

REPROCESSABLE_FAILURE_CODES = frozenset({"AGENT_CLEANUP_RETRY_EXHAUSTED"})


class DeadLetterStatus(StrEnum):
    DEAD_LETTER = "DEAD_LETTER"
    REQUEUED = "REQUEUED"


class DeadLetterRecord(StrictModel):
    outbox_id: int = Field(ge=1)
    topic: str = Field(min_length=1)
    aggregate_id: str = Field(min_length=1)
    attempts: int = Field(ge=0)
    failure_code: str = Field(min_length=1)
    failed_at: datetime
    payload_digest: str = Field(min_length=64, max_length=64)
    reprocessable: bool


class DeadLetterPage(StrictModel):
    items: list[DeadLetterRecord]
    next_cursor: int | None


class ReprocessDeadLetterRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    expected_failure_code: str = Field(min_length=1, max_length=128)
    remediation_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    change_reference: str = Field(pattern=r"^(PR|INC|CHG)-[A-Za-z0-9._-]{1,120}$")


class DeadLetterReprocessResult(StrictModel):
    reprocess_event_id: str
    request_id: str
    outbox_id: int
    status: DeadLetterStatus
    previous_failure_code: str
    previous_attempts: int
    requested_at: datetime


class DeadLetterOperationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DeadLetterOperations:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def list(self, *, limit: int, after_outbox_id: int | None = None) -> DeadLetterPage:
        cursor_clause = " AND outbox_id > :cursor" if after_outbox_id is not None else ""
        parameters: dict[str, object] = {"limit": limit + 1}
        if after_outbox_id is not None:
            parameters["cursor"] = after_outbox_id
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT outbox_id, topic, aggregate_id, attempts, failure_code,
                           failed_at, payload_digest
                    FROM workflow_outbox
                    WHERE status='DEAD_LETTER'
                    """
                    + cursor_clause
                    + " ORDER BY outbox_id LIMIT :limit"
                ),
                parameters,
            ).mappings().all()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [
            DeadLetterRecord(
                **dict(row),
                reprocessable=row["failure_code"] in REPROCESSABLE_FAILURE_CODES,
            )
            for row in visible
        ]
        return DeadLetterPage(
            items=items,
            next_cursor=items[-1].outbox_id if has_more and items else None,
        )

    def reprocess(
        self,
        *,
        outbox_id: int,
        request: ReprocessDeadLetterRequest,
    ) -> DeadLetterReprocessResult:
        now = self._now()
        with self._engine.begin() as connection:
            replay = connection.execute(
                text(
                    """
                    SELECT reprocess_event_id, request_id, outbox_id,
                           previous_failure_code, previous_attempts, remediation_code,
                           change_reference,
                           requested_at
                    FROM outbox_reprocess_events WHERE request_id=:request_id
                    """
                ),
                {"request_id": request.request_id},
            ).mappings().one_or_none()
            if replay is not None:
                if (
                    replay["outbox_id"] != outbox_id
                    or replay["previous_failure_code"] != request.expected_failure_code
                    or replay["remediation_code"] != request.remediation_code
                    or replay["change_reference"] != request.change_reference
                ):
                    raise DeadLetterOperationError("DEAD_LETTER_REQUEST_ID_REUSED")
                return self._result_from_replay(replay)
            row = connection.execute(
                text(
                    """
                    SELECT outbox_id, topic, aggregate_id, attempts, failure_code, status
                    FROM workflow_outbox WHERE outbox_id=:outbox_id FOR UPDATE
                    """
                ),
                {"outbox_id": outbox_id},
            ).mappings().one_or_none()
            if row is None:
                raise DeadLetterOperationError("DEAD_LETTER_NOT_FOUND")
            if row["failure_code"] != request.expected_failure_code:
                raise DeadLetterOperationError("DEAD_LETTER_FAILURE_CODE_CHANGED")
            if row["status"] != "DEAD_LETTER":
                raise DeadLetterOperationError("DEAD_LETTER_STATE_CHANGED")
            if row["failure_code"] not in REPROCESSABLE_FAILURE_CODES:
                raise DeadLetterOperationError("DEAD_LETTER_NOT_REPROCESSABLE")
            event_id = self._new_id()
            connection.execute(
                text(
                    """
                    INSERT INTO outbox_reprocess_events(
                        reprocess_event_id, request_id, outbox_id, topic, aggregate_id,
                        previous_failure_code, previous_attempts, remediation_code,
                        change_reference, requested_at
                    ) VALUES (
                        :event_id, :request_id, :outbox_id, :topic, :aggregate_id,
                        :failure_code, :attempts, :remediation_code,
                        :change_reference, :requested_at
                    )
                    """
                ),
                {
                    "event_id": event_id,
                    "request_id": request.request_id,
                    "outbox_id": outbox_id,
                    "topic": row["topic"],
                    "aggregate_id": row["aggregate_id"],
                    "failure_code": row["failure_code"],
                    "attempts": row["attempts"],
                    "remediation_code": request.remediation_code,
                    "change_reference": request.change_reference,
                    "requested_at": now,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE workflow_outbox SET status='PENDING', attempts=0,
                        available_at=:now, failure_code=NULL, failed_at=NULL,
                        claim_token_digest=NULL, claim_expires_at=NULL, publisher_id=NULL
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {"now": now, "outbox_id": outbox_id},
            )
            return DeadLetterReprocessResult(
                reprocess_event_id=event_id,
                request_id=request.request_id,
                outbox_id=outbox_id,
                status=DeadLetterStatus.REQUEUED,
                previous_failure_code=row["failure_code"],
                previous_attempts=row["attempts"],
                requested_at=now,
            )

    @staticmethod
    def _result_from_replay(row: Any) -> DeadLetterReprocessResult:
        return DeadLetterReprocessResult(
            reprocess_event_id=row["reprocess_event_id"],
            request_id=row["request_id"],
            outbox_id=row["outbox_id"],
            status=DeadLetterStatus.REQUEUED,
            previous_failure_code=row["previous_failure_code"],
            previous_attempts=row["previous_attempts"],
            requested_at=row["requested_at"],
        )


class UnavailableDeadLetterOperations:
    def list(self, **_: Any) -> DeadLetterPage:
        raise DeadLetterOperationError("DEAD_LETTER_CONFIGURATION_UNAVAILABLE")

    def reprocess(self, **_: Any) -> DeadLetterReprocessResult:
        raise DeadLetterOperationError("DEAD_LETTER_CONFIGURATION_UNAVAILABLE")
