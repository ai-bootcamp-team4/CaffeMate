from typing import cast

import pytest
from fastapi.testclient import TestClient
from worker.agent_cleanup import CleanupOutcome
from worker.dead_letter import (
    DeadLetterOperationError,
    DeadLetterPage,
    DeadLetterRecord,
    DeadLetterReprocessResult,
    DeadLetterStatus,
)
from worker.main import create_worker_app
from worker.pubsub import PubSubDelivery
from worker.runtime import DeliveryOutcome, WorkerRetryRequiredError

from tests.test_worker_runtime import envelope


class FakeWorker:
    def __init__(
        self,
        *,
        outcome: DeliveryOutcome = DeliveryOutcome.APPLIED,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.error = error
        self.deliveries: list[PubSubDelivery] = []

    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        self.deliveries.append(delivery)
        if self.error is not None:
            raise self.error
        return self.outcome


class FakeOutboxDispatcher:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0

    def publish_one(self) -> bool:
        self.calls += 1
        return next(self._outcomes, False)


class FakeCleanupConsumer:
    def __init__(self, outcomes: list[CleanupOutcome]) -> None:
        self._outcomes = iter(outcomes)

    def cleanup_one(self) -> CleanupOutcome:
        return next(self._outcomes, CleanupOutcome.EMPTY)


class FakeDeadLetterOperations:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.reprocess_calls: list[tuple[int, object]] = []

    def list(self, *, limit: int, after_outbox_id: int | None = None) -> DeadLetterPage:
        assert limit == 20
        assert after_outbox_id == 10
        return DeadLetterPage(
            items=[
                DeadLetterRecord(
                    outbox_id=11,
                    topic="AGENT_SESSION_CLEANUP",
                    aggregate_id="session-1",
                    attempts=5,
                    failure_code="AGENT_CLEANUP_RETRY_EXHAUSTED",
                    failed_at="2026-08-21T10:00:00Z",
                    payload_digest="a" * 64,
                    reprocessable=True,
                )
            ],
            next_cursor=None,
        )

    def reprocess(self, *, outbox_id: int, request: object) -> DeadLetterReprocessResult:
        self.reprocess_calls.append((outbox_id, request))
        if self.error is not None:
            raise DeadLetterOperationError(self.error)
        return DeadLetterReprocessResult(
            reprocess_event_id="event-1",
            request_id="request-1",
            outbox_id=outbox_id,
            status=DeadLetterStatus.REQUEUED,
            previous_failure_code="AGENT_CLEANUP_RETRY_EXHAUSTED",
            previous_attempts=5,
            requested_at="2026-08-21T10:05:00Z",
        )


def test_valid_pubsub_push_is_acked_after_worker_applies() -> None:
    worker = FakeWorker()
    dispatcher = FakeOutboxDispatcher([True])
    app = create_worker_app(
        worker=worker,
        outbox_dispatcher=dispatcher,
        expected_subscription="projects/test/subscriptions/worker",
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/pubsub/workflow-stages",
            json=envelope({"stage_run_id": "stage-1", "input_digest": "a" * 64}),
        )

    assert response.status_code == 204
    assert len(worker.deliveries) == 1
    assert worker.deliveries[0].message_id == "message-1"
    assert dispatcher.calls == 1


def test_stage_push_is_retried_when_immediate_next_stage_publish_fails() -> None:
    class FailingDispatcher:
        def publish_one(self) -> bool:
            raise RuntimeError("provider detail must stay private")

    app = create_worker_app(
        worker=FakeWorker(),
        outbox_dispatcher=FailingDispatcher(),
        expected_subscription="projects/test/subscriptions/worker",
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/pubsub/workflow-stages",
            json=envelope({"stage_run_id": "stage-1", "input_digest": "a" * 64}),
        )

    assert response.status_code == 503
    assert response.json() == {"code": "OUTBOX_PUBLISH_RETRY_REQUIRED"}
    assert "provider detail" not in response.text


def test_terminal_and_duplicate_delivery_are_acked_without_reprocessing_signal() -> None:
    for outcome in (
        DeliveryOutcome.TERMINAL_FAILED,
        DeliveryOutcome.DUPLICATE_OR_INELIGIBLE,
    ):
        app = create_worker_app(
            worker=FakeWorker(outcome=outcome),
            outbox_dispatcher=FakeOutboxDispatcher([False]),
            expected_subscription="projects/test/subscriptions/worker",
        )
        with TestClient(app) as client:
            response = client.post(
                "/internal/v1/pubsub/workflow-stages",
                json=envelope({"stage_run_id": "stage-1", "input_digest": "a" * 64}),
            )
        assert response.status_code == 204


def test_retryable_worker_failure_returns_safe_non_success_response() -> None:
    app = create_worker_app(
        worker=FakeWorker(error=WorkerRetryRequiredError("secret provider response")),
        outbox_dispatcher=FakeOutboxDispatcher([False]),
        expected_subscription="projects/test/subscriptions/worker",
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/pubsub/workflow-stages",
            json=envelope({"stage_run_id": "stage-1", "input_digest": "a" * 64}),
        )

    assert response.status_code == 503
    assert response.json() == {"code": "WORKER_RETRY_REQUIRED"}
    assert "secret" not in response.text


def test_wrong_subscription_or_digest_is_rejected_before_worker() -> None:
    worker = FakeWorker()
    app = create_worker_app(
        worker=worker,
        outbox_dispatcher=FakeOutboxDispatcher([False]),
        expected_subscription="projects/expected/subscriptions/worker",
    )
    body = envelope({"stage_run_id": "stage-1", "input_digest": "a" * 64})

    with TestClient(app) as client:
        wrong_subscription = client.post("/internal/v1/pubsub/workflow-stages", json=body)
        tampered = cast(dict[str, object], body.copy())
        message = cast(dict[str, object], cast(dict[str, object], body["message"]).copy())
        attributes = cast(dict[str, str], cast(dict[str, str], message["attributes"]).copy())
        attributes["payload_digest"] = "0" * 64
        message["attributes"] = attributes
        tampered["message"] = message
        wrong_digest = client.post("/internal/v1/pubsub/workflow-stages", json=tampered)

    assert wrong_subscription.status_code == 400
    assert wrong_digest.status_code == 400
    assert worker.deliveries == []


def test_missing_subscription_fails_closed_while_liveness_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBSUB_SUBSCRIPTION", raising=False)
    app = create_worker_app(worker=FakeWorker(), expected_subscription=None)

    with TestClient(app) as client:
        health = client.get("/health")
        response = client.post("/internal/v1/pubsub/workflow-stages", json={})

    assert health.status_code == 200
    assert response.status_code == 503
    assert response.json() == {"code": "WORKER_CONFIGURATION_UNAVAILABLE"}


def test_outbox_dispatch_drains_until_empty_or_bounded_limit() -> None:
    drained_dispatcher = FakeOutboxDispatcher([True, True, False])
    bounded_dispatcher = FakeOutboxDispatcher([True, True, True])
    drained_app = create_worker_app(
        worker=FakeWorker(),
        outbox_dispatcher=drained_dispatcher,
    )
    bounded_app = create_worker_app(
        worker=FakeWorker(),
        outbox_dispatcher=bounded_dispatcher,
    )

    with TestClient(drained_app) as client:
        drained = client.post("/internal/v1/outbox:publish", json={"limit": 10})
    with TestClient(bounded_app) as client:
        bounded = client.post("/internal/v1/outbox:publish", json={"limit": 2})

    assert drained.status_code == 200
    assert drained.json() == {"published": 2, "drained": True}
    assert drained_dispatcher.calls == 3
    assert bounded.status_code == 200
    assert bounded.json() == {"published": 2, "drained": False}
    assert bounded_dispatcher.calls == 2


def test_outbox_dispatch_fails_closed_without_publisher_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKFLOW_STAGE_TOPIC_RESOURCE", raising=False)
    app = create_worker_app(worker=FakeWorker())

    with TestClient(app) as client:
        response = client.post("/internal/v1/outbox:publish", json={})

    assert response.status_code == 503
    assert response.json() == {"code": "OUTBOX_CONFIGURATION_UNAVAILABLE"}


def test_agent_cleanup_endpoint_reports_success_retry_and_dead_letter() -> None:
    app = create_worker_app(
        worker=FakeWorker(),
        cleanup_consumer=FakeCleanupConsumer(
            [
                CleanupOutcome.DELETED,
                CleanupOutcome.RETRY_SCHEDULED,
                CleanupOutcome.DEAD_LETTERED,
                CleanupOutcome.EMPTY,
            ]
        ),
    )

    with TestClient(app) as client:
        response = client.post("/internal/v1/agent-sessions:cleanup", json={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {
        "deleted": 1,
        "retry_scheduled": 1,
        "dead_lettered": 1,
        "drained": True,
    }


def test_agent_cleanup_endpoint_fails_closed_without_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_RUNTIME_RESOURCE_ID", raising=False)
    app = create_worker_app(worker=FakeWorker())

    with TestClient(app) as client:
        response = client.post("/internal/v1/agent-sessions:cleanup", json={})

    assert response.status_code == 503
    assert response.json() == {"code": "AGENT_CLEANUP_CONFIGURATION_UNAVAILABLE"}


def test_dead_letter_endpoints_hide_payload_and_require_fenced_reprocess_input() -> None:
    operations = FakeDeadLetterOperations()
    app = create_worker_app(
        worker=FakeWorker(),
        dead_letter_operations=operations,
    )
    with TestClient(app) as client:
        listed = client.get("/internal/v1/dead-letters?limit=20&after_outbox_id=10")
        reprocessed = client.post(
            "/internal/v1/dead-letters/11:reprocess",
            json={
                "request_id": "request-1",
                "expected_failure_code": "AGENT_CLEANUP_RETRY_EXHAUSTED",
                "remediation_code": "RUNTIME_RECOVERED",
                "change_reference": "INC-42",
            },
        )

    assert listed.status_code == 200
    listed_item = listed.json()["items"][0]
    assert "payload" not in listed_item
    assert listed_item["reprocessable"] is True
    assert reprocessed.status_code == 200
    assert reprocessed.json()["status"] == "REQUEUED"
    assert operations.reprocess_calls[0][0] == 11


def test_nonreprocessable_dead_letter_returns_safe_error_code() -> None:
    app = create_worker_app(
        worker=FakeWorker(),
        dead_letter_operations=FakeDeadLetterOperations(
            error="DEAD_LETTER_NOT_REPROCESSABLE"
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/dead-letters/11:reprocess",
            json={
                "request_id": "request-1",
                "expected_failure_code": "AGENT_CLEANUP_PAYLOAD_INVALID",
                "remediation_code": "PAYLOAD_REVIEWED",
                "change_reference": "INC-43",
            },
        )
    assert response.status_code == 422
    assert response.json() == {"code": "DEAD_LETTER_NOT_REPROCESSABLE"}
