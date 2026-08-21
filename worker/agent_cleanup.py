from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

import httpx
from app.agents.runtime import AccessTokenProvider, AgentRuntimeError

from worker.outbox import ClaimedOutboxMessage, PostgresOutboxRepository

MAX_CLEANUP_ATTEMPTS = 5


class CleanupOutcome(StrEnum):
    EMPTY = "EMPTY"
    DELETED = "DELETED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DEAD_LETTERED = "DEAD_LETTERED"


class AgentSessionDeleter(Protocol):
    def delete(self, payload: Mapping[str, object]) -> None: ...


class AgentRuntimeSessionDeleter:
    def __init__(
        self,
        *,
        gcp_project_id: str,
        resource_id: str,
        access_tokens: AccessTokenProvider,
        location: str = "asia-northeast3",
        client: httpx.Client | None = None,
    ) -> None:
        if location != "asia-northeast3":
            raise ValueError("Agent Runtime cleanup location must be asia-northeast3")
        self._resource = (
            f"projects/{gcp_project_id}/locations/{location}/reasoningEngines/{resource_id}"
        )
        self._url = (
            f"https://{location}-aiplatform.googleapis.com/v1/{self._resource}:query"
        )
        self._tokens = access_tokens
        self._client = client or httpx.Client(timeout=15.0, follow_redirects=False)

    def delete(self, payload: Mapping[str, object]) -> None:
        if payload.get("runtime_resource") != self._resource:
            raise ValueError("Cleanup payload crossed the configured Runtime resource")
        user_id = payload.get("user_id")
        session_id = payload.get("session_id")
        if not isinstance(user_id, str) or not user_id or not isinstance(session_id, str) or not session_id:
            raise ValueError("Cleanup payload identity is invalid")
        try:
            response = self._client.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._tokens.token()}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "class_method": "async_delete_session",
                    "input": {"user_id": user_id, "session_id": session_id},
                },
            )
            if response.status_code == 404:
                return
            if 400 <= response.status_code < 500 and response.status_code != 429:
                raise ValueError("Agent cleanup request was permanently rejected")
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise AgentRuntimeError("RUNTIME_SESSION_CLEANUP_FAILED") from error


class AgentSessionCleanupConsumer:
    def __init__(
        self,
        repository: PostgresOutboxRepository,
        deleter: AgentSessionDeleter,
        *,
        consumer_id: str,
    ) -> None:
        self._repository = repository
        self._deleter = deleter
        self._consumer_id = consumer_id

    def cleanup_one(self) -> CleanupOutcome:
        message = self._repository.claim_next(
            publisher_id=self._consumer_id,
            logical_topic="AGENT_SESSION_CLEANUP",
        )
        if message is None:
            return CleanupOutcome.EMPTY
        if not self._valid_payload(message):
            self._dead_letter(message, "AGENT_CLEANUP_PAYLOAD_INVALID")
            return CleanupOutcome.DEAD_LETTERED
        try:
            self._deleter.delete(message.payload)
        except ValueError:
            self._dead_letter(message, "AGENT_CLEANUP_SCOPE_INVALID")
            return CleanupOutcome.DEAD_LETTERED
        except AgentRuntimeError:
            if message.attempts >= MAX_CLEANUP_ATTEMPTS:
                self._dead_letter(message, "AGENT_CLEANUP_RETRY_EXHAUSTED")
                return CleanupOutcome.DEAD_LETTERED
            delay = min(300, 2 ** message.attempts)
            if not self._repository.release_for_retry(
                outbox_id=message.outbox_id,
                claim_token=message.claim_token,
                delay_seconds=delay,
            ):
                raise RuntimeError("Agent cleanup retry claim was lost")
            return CleanupOutcome.RETRY_SCHEDULED
        if not self._repository.mark_published(
            outbox_id=message.outbox_id,
            claim_token=message.claim_token,
            pubsub_message_id=f"direct-cleanup:{message.aggregate_id}",
        ):
            raise RuntimeError("Agent cleanup completion claim was lost")
        return CleanupOutcome.DELETED

    def _dead_letter(self, message: ClaimedOutboxMessage, code: str) -> None:
        if not self._repository.mark_dead_letter(
            outbox_id=message.outbox_id,
            claim_token=message.claim_token,
            failure_code=code,
        ):
            raise RuntimeError("Agent cleanup dead-letter claim was lost")

    @staticmethod
    def _valid_payload(message: ClaimedOutboxMessage) -> bool:
        return (
            message.topic == "AGENT_SESSION_CLEANUP"
            and set(message.payload) == {"runtime_resource", "user_id", "session_id"}
            and all(isinstance(value, str) and value for value in message.payload.values())
        )
