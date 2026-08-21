from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from app.workflows.execution_repository import PostgresStageExecutionRepository
from app.workflows.models import CheckpointOutcome, StageLease

from worker.pubsub import PubSubDelivery


class DeliveryOutcome(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE_OR_INELIGIBLE = "DUPLICATE_OR_INELIGIBLE"


class StageProcessor(Protocol):
    def process(self, lease: StageLease) -> dict[str, object]: ...


class DurableWorker:
    def __init__(
        self,
        execution: PostgresStageExecutionRepository,
        processor: StageProcessor,
        *,
        worker_id: str,
    ) -> None:
        self._execution = execution
        self._processor = processor
        self._worker_id = worker_id

    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        if delivery.logical_topic != "WORKFLOW_STAGE_READY":
            raise ValueError(f"Unsupported worker topic: {delivery.logical_topic}")
        stage_run_id = self._required_string(delivery.payload, "stage_run_id")
        input_digest = self._required_string(delivery.payload, "input_digest")
        lease = self._execution.claim(
            stage_run_id=stage_run_id,
            worker_id=self._worker_id,
            expected_input_digest=input_digest,
        )
        if lease is None:
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        result = self._processor.process(lease)
        outcome = self._execution.checkpoint(
            stage_run_id=stage_run_id,
            lease_token=lease.lease_token,
            input_digest=input_digest,
            result=result,
        )
        return (
            DeliveryOutcome.APPLIED
            if outcome == CheckpointOutcome.APPLIED
            else DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        )

    @staticmethod
    def _required_string(payload: Mapping[str, object], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Worker payload field is invalid: {field}")
        return value
