from typing import cast

import pytest
from fastapi.testclient import TestClient
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


def test_valid_pubsub_push_is_acked_after_worker_applies() -> None:
    worker = FakeWorker()
    app = create_worker_app(
        worker=worker,
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


def test_terminal_and_duplicate_delivery_are_acked_without_reprocessing_signal() -> None:
    for outcome in (
        DeliveryOutcome.TERMINAL_FAILED,
        DeliveryOutcome.DUPLICATE_OR_INELIGIBLE,
    ):
        app = create_worker_app(
            worker=FakeWorker(outcome=outcome),
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
        health = client.get("/healthz")
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
