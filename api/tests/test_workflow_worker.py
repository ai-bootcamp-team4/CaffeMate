from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Any

import pytest
from worker.pubsub import PubSubDelivery
from worker.workflow_runtime import (
    DeliveryOutcome,
    DurableWorkflowWorker,
    WorkerRetryRequiredError,
)

from app.workflows.models import FailureOutcome, HeadFence, StageLease


class _RejectedLeaseRepository:
    def __init__(self, *, pending: bool) -> None:
        self.pending = pending

    def claim(self, **_: Any) -> StageLease | None:
        return None

    def is_pending_delivery(self, **_: Any) -> bool:
        return self.pending

    def heartbeat(self, **_: Any) -> bool:
        return False

    def record_failure(self, **_: Any) -> FailureOutcome:
        return FailureOutcome.LEASE_REJECTED


class _NeverRuns:
    def process(self, _lease: StageLease) -> dict[str, object]:
        raise AssertionError("Processor must not run without a lease")


class _LostLeaseRepository(_RejectedLeaseRepository):
    def claim(self, **_: Any) -> StageLease | None:
        return StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="stage-1",
            stage_code="RUN_PROPOSAL",
            input_digest="a" * 64,
            lease_token="lease-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=90),
            attempt=1,
            head=HeadFence(
                workflow_generation=1,
                state_version=1,
                founder_snapshot_id="founder-1",
                area_snapshot_id="area-1",
                evidence_snapshot_id=None,
                policy_snapshot_id="policy-1",
                index_generation_id=None,
                seed_registry_id="seed-1",
            ),
        )


class _SlowProcessor:
    def process(self, _lease: StageLease) -> dict[str, object]:
        sleep(0.1)
        return {}


def _delivery() -> PubSubDelivery:
    return PubSubDelivery(
        message_id="message-1",
        logical_topic="WORKFLOW_STAGE_READY",
        payload={
            "stage_run_id": "stage-1",
            "input_digest": "a" * 64,
        },
        attributes={},
    )


def test_active_lease_redelivery_is_retried_instead_of_acked() -> None:
    worker = DurableWorkflowWorker(
        _RejectedLeaseRepository(pending=True),
        _NeverRuns(),
        worker_id="worker-2",
    )

    with pytest.raises(WorkerRetryRequiredError):
        worker.handle(_delivery())


def test_terminal_duplicate_delivery_is_acked() -> None:
    worker = DurableWorkflowWorker(
        _RejectedLeaseRepository(pending=False),
        _NeverRuns(),
        worker_id="worker-2",
    )

    assert worker.handle(_delivery()) == DeliveryOutcome.DUPLICATE_OR_INELIGIBLE


def test_lost_heartbeat_retries_when_stage_is_still_pending() -> None:
    worker = DurableWorkflowWorker(
        _LostLeaseRepository(pending=True),
        _SlowProcessor(),
        worker_id="worker-2",
        heartbeat_interval_seconds=0.01,
    )

    with pytest.raises(WorkerRetryRequiredError):
        worker.handle(_delivery())