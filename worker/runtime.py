from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from app.domain.errors import ContractValidationError
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    StageFailure,
    StageLease,
)

from worker.pubsub import PubSubDelivery


class DeliveryOutcome(StrEnum):
    APPLIED = "APPLIED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    DUPLICATE_OR_INELIGIBLE = "DUPLICATE_OR_INELIGIBLE"


class StageProcessor(Protocol):
    def process(self, lease: StageLease) -> dict[str, object]: ...


class StageExecutionRepository(Protocol):
    def claim(
        self,
        *,
        stage_run_id: str,
        worker_id: str,
        expected_input_digest: str,
    ) -> StageLease | None: ...

    def checkpoint(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        result: dict[str, object],
    ) -> CheckpointOutcome: ...

    def record_failure(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        failure: StageFailure,
    ) -> FailureOutcome: ...


class DurableWorker:
    def __init__(
        self,
        execution: StageExecutionRepository,
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
        try:
            result = self._processor.process(lease)
            outcome = self._execution.checkpoint(
                stage_run_id=stage_run_id,
                lease_token=lease.lease_token,
                input_digest=input_digest,
                result=result,
            )
        except Exception as error:
            failure = self._failure_from_exception(error)
            failure_outcome = self._execution.record_failure(
                stage_run_id=stage_run_id,
                lease_token=lease.lease_token,
                input_digest=input_digest,
                failure=failure,
            )
            if failure_outcome == FailureOutcome.RETRY_SCHEDULED:
                raise
            if failure_outcome == FailureOutcome.TERMINAL_FAILED:
                return DeliveryOutcome.TERMINAL_FAILED
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        return (
            DeliveryOutcome.APPLIED
            if outcome == CheckpointOutcome.APPLIED
            else DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        )

    @staticmethod
    def _failure_from_exception(error: Exception) -> StageFailure:
        if isinstance(error, ContractValidationError):
            return StageFailure(code="CONTRACT_REJECTED", retryable=False)
        if isinstance(error, TimeoutError):
            return StageFailure(code="STAGE_TIMEOUT", retryable=True)
        return StageFailure(code="STAGE_PROCESSING_ERROR", retryable=True)

    @staticmethod
    def _required_string(payload: Mapping[str, object], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Worker payload field is invalid: {field}")
        return value
