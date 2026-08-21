import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import rfc8785
from worker.pubsub import (
    GooglePubSubPublisher,
    InvalidPubSubEnvelopeError,
    PubSubDelivery,
    decode_push_envelope,
)
from worker.runtime import DeliveryOutcome, DurableWorker

from app.domain.errors import ContractValidationError
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    HeadFence,
    StageFailure,
    StageLease,
)


class FakeFuture:
    def result(self, timeout: float | None = None) -> str:
        assert timeout == 10
        return "message-1"


class FakePublisherClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def publish(self, topic: str, data: bytes, **attributes: str) -> FakeFuture:
        self.calls.append((topic, data, attributes))
        return FakeFuture()


def envelope(payload: dict[str, str], *, digest: str | None = None) -> dict[str, object]:
    canonical = rfc8785.dumps(payload)
    return {
        "subscription": "projects/test/subscriptions/worker",
        "message": {
            "messageId": "message-1",
            "data": base64.b64encode(canonical).decode(),
            "attributes": {
                "logical_topic": "WORKFLOW_STAGE_READY",
                "payload_digest": digest or hashlib.sha256(canonical).hexdigest(),
            },
        },
    }


def test_google_publisher_uses_only_allowlisted_topic_resource() -> None:
    client = FakePublisherClient()
    publisher = GooglePubSubPublisher(
        topic_resources={"WORKFLOW_STAGE_READY": "projects/test/topics/workflow"},
        client=client,
    )

    message_id = publisher.publish(
        topic="WORKFLOW_STAGE_READY",
        payload={"stage_run_id": "stage-1"},
        attributes={"payload_digest": "digest"},
    )

    assert message_id == "message-1"
    assert client.calls[0][0] == "projects/test/topics/workflow"
    assert client.calls[0][2]["logical_topic"] == "WORKFLOW_STAGE_READY"
    with pytest.raises(ValueError):
        publisher.publish(topic="ATTACKER_TOPIC", payload={}, attributes={})


def test_push_envelope_requires_subscription_and_payload_digest() -> None:
    payload = {"stage_run_id": "stage-1", "input_digest": "a" * 64}
    decoded = decode_push_envelope(
        envelope(payload),
        expected_subscription="projects/test/subscriptions/worker",
    )
    assert decoded.payload == payload

    with pytest.raises(InvalidPubSubEnvelopeError):
        decode_push_envelope(
            envelope(payload, digest="0" * 64),
            expected_subscription="projects/test/subscriptions/worker",
        )
    with pytest.raises(InvalidPubSubEnvelopeError):
        decode_push_envelope(
            envelope(payload),
            expected_subscription="projects/other/subscriptions/worker",
        )


class FakeExecution:
    def __init__(
        self,
        lease: StageLease | None,
        *,
        failure_outcome: FailureOutcome = FailureOutcome.RETRY_SCHEDULED,
        checkpoint_error: Exception | None = None,
    ) -> None:
        self.lease = lease
        self.checkpoints = 0
        self.failure_outcome = failure_outcome
        self.checkpoint_error = checkpoint_error
        self.failures: list[StageFailure] = []

    def claim(
        self,
        *,
        stage_run_id: str,
        worker_id: str,
        expected_input_digest: str,
    ) -> StageLease | None:
        assert stage_run_id == "stage-1"
        assert worker_id == "worker-1"
        assert expected_input_digest == "a" * 64
        claimed, self.lease = self.lease, None
        return claimed

    def checkpoint(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        result: dict[str, object],
    ) -> CheckpointOutcome:
        del stage_run_id, lease_token, input_digest, result
        if self.checkpoint_error is not None:
            raise self.checkpoint_error
        self.checkpoints += 1
        return CheckpointOutcome.APPLIED

    def record_failure(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        failure: StageFailure,
    ) -> FailureOutcome:
        del stage_run_id, lease_token, input_digest
        self.failures.append(failure)
        return self.failure_outcome


class FakeProcessor:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, lease: StageLease) -> dict[str, object]:
        self.calls += 1
        return {"attempt": lease.attempt}


def stage_lease() -> StageLease:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return StageLease(
        workflow_run_id="workflow-1",
        stage_run_id="stage-1",
        stage_code="FIRST_PROPOSAL",
        input_digest="a" * 64,
        lease_token="lease-token",
        lease_expires_at=now + timedelta(seconds=45),
        attempt=1,
        head=HeadFence(
            workflow_generation=1,
            state_version=1,
            founder_snapshot_id="founder-1",
            area_snapshot_id="area-1",
            evidence_snapshot_id=None,
            policy_snapshot_id="policy-v1",
            index_generation_id=None,
            seed_registry_id=None,
        ),
    )


def test_worker_absorbs_pubsub_redelivery_before_side_effect() -> None:
    execution = FakeExecution(stage_lease())
    processor = FakeProcessor()
    worker = DurableWorker(execution, processor, worker_id="worker-1")
    delivery = PubSubDelivery(
        message_id="message-1",
        logical_topic="WORKFLOW_STAGE_READY",
        payload={"stage_run_id": "stage-1", "input_digest": "a" * 64},
        attributes={},
    )

    assert worker.handle(delivery) == DeliveryOutcome.APPLIED
    assert worker.handle(delivery) == DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
    assert processor.calls == 1
    assert execution.checkpoints == 1


class FailingProcessor:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def process(self, lease: StageLease) -> dict[str, object]:
        del lease
        raise self._error


def delivery() -> PubSubDelivery:
    return PubSubDelivery(
        message_id="message-1",
        logical_topic="WORKFLOW_STAGE_READY",
        payload={"stage_run_id": "stage-1", "input_digest": "a" * 64},
        attributes={},
    )


def test_worker_nacks_retryable_failure_after_releasing_lease() -> None:
    execution = FakeExecution(stage_lease())
    worker = DurableWorker(
        execution,
        FailingProcessor(RuntimeError("secret provider response")),
        worker_id="worker-1",
    )

    with pytest.raises(RuntimeError, match="secret provider response"):
        worker.handle(delivery())

    assert execution.failures == [
        StageFailure(code="STAGE_PROCESSING_ERROR", retryable=True)
    ]
    assert execution.checkpoints == 0


def test_worker_acks_terminal_failure_and_classifies_timeout() -> None:
    execution = FakeExecution(
        stage_lease(),
        failure_outcome=FailureOutcome.TERMINAL_FAILED,
    )
    worker = DurableWorker(
        execution,
        FailingProcessor(TimeoutError("do not persist this message")),
        worker_id="worker-1",
    )

    assert worker.handle(delivery()) == DeliveryOutcome.TERMINAL_FAILED
    assert execution.failures == [StageFailure(code="STAGE_TIMEOUT", retryable=True)]


def test_checkpoint_contract_rejection_is_terminal_without_retry() -> None:
    execution = FakeExecution(
        stage_lease(),
        failure_outcome=FailureOutcome.TERMINAL_FAILED,
        checkpoint_error=ContractValidationError("sensitive validation detail"),
    )
    worker = DurableWorker(execution, FakeProcessor(), worker_id="worker-1")

    assert worker.handle(delivery()) == DeliveryOutcome.TERMINAL_FAILED
    assert execution.failures == [StageFailure(code="CONTRACT_REJECTED", retryable=False)]
